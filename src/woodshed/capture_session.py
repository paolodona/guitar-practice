"""Bookkeeping for one capture-and-stop cycle under `capture/` (Phase 1.5,
Group U). Owns the on-disk sidecar that tracks which segments split from a
raw recording are still pending -- so `GET /api/capture/segments` can be
computed by reading disk, per request, the same way every other screen is
(CLAUDE.md: "the repo is the database"), rather than held in server memory
that a restart would silently lose along with real, unrepeatable audio.

One raw recording -> one sidecar, `<raw file>.json` beside it (same stem,
`.wav` swapped for `.json`) -- a JSON array of every segment split from
that recording, each carrying a `status` ("pending" | "bound" |
"discarded"). `resolve()` flips one entry's status and deletes BOTH the
sidecar and the raw file once every entry has resolved -- CLAUDE.md's
capture/ lifecycle rule ("kept until every segment split from it has been
bound to a song or explicitly discarded").

Entries are never removed or renumbered, only marked -- `index` is a
stable, permanent position in this session's own list, so a client that
fetched `GET /api/capture/segments` before some OTHER segment resolved
still refers to the right one; "no such pending segment" is a real refusal
(see `resolve`), never a silent misbind through a shifted index.

`current_session` looks at "the most recent raw file" only (the newest
`*.json` sidecar under `capture/`, by filename -- capture-and-stop cycles
are named so this sorts chronologically): CLAUDE.md's single-capture-at-a-
time rule means there is normally exactly one active cycle, but an OLDER
cycle with segments nobody got around to resolving becomes unreachable
through this API once a newer one starts (still on disk, just not
returned by GET /api/capture/segments) -- a known, accepted limitation,
not silently pretended away.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from woodshed.errors import WoodshedError
from woodshed.library import Repo

STATUS_PENDING = "pending"
STATUS_BOUND = "bound"
STATUS_DISCARDED = "discarded"

__all__ = [
    "SessionEntry",
    "CaptureSession",
    "start_session",
    "current_session",
    "resolve",
]


@dataclass(frozen=True)
class SessionEntry:
    """One segment split from a capture session's raw recording."""

    index: int
    start_frame: int
    end_frame: int
    sample_rate: int
    overflowed: bool
    status: str

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.sample_rate


@dataclass(frozen=True)
class CaptureSession:
    """One capture-and-stop cycle: the raw recording, and every segment
    split from it (not just the still-pending ones -- see `pending`)."""

    raw_path: Path
    sidecar_path: Path
    entries: tuple[SessionEntry, ...]

    @property
    def pending(self) -> list[SessionEntry]:
        return [e for e in self.entries if e.status == STATUS_PENDING]


def _sidecar_path(raw_path: str | Path) -> Path:
    return Path(raw_path).with_suffix(".json")


def _save(session: CaptureSession) -> None:
    session.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "raw_file": session.raw_path.name,
        "entries": [dataclasses.asdict(e) for e in session.entries],
    }
    session.sidecar_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load(sidecar_path: Path) -> CaptureSession:
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    raw_path = sidecar_path.parent / data["raw_file"]
    entries = tuple(SessionEntry(**entry) for entry in data["entries"])
    return CaptureSession(raw_path=raw_path, sidecar_path=sidecar_path, entries=entries)


def start_session(repo: Repo, raw_path: str | Path, segments) -> CaptureSession:
    """Record a just-finished capture-and-stop cycle: *segments* (from
    `capture(raw_path=...)`) all start out pending. Refuses if a sidecar for
    *raw_path* already exists -- this is called exactly once per raw file,
    right after `capture()`'s generator is exhausted."""
    raw_path = Path(raw_path)
    sidecar_path = _sidecar_path(raw_path)
    if sidecar_path.exists():
        raise WoodshedError(f"a capture session already exists for {raw_path}")
    entries = tuple(
        SessionEntry(
            index=i,
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
            sample_rate=segment.sample_rate,
            overflowed=segment.overflowed,
            status=STATUS_PENDING,
        )
        for i, segment in enumerate(segments)
    )
    session = CaptureSession(raw_path=raw_path, sidecar_path=sidecar_path, entries=entries)
    _save(session)
    return session


def current_session(repo: Repo) -> CaptureSession | None:
    """The most recent capture-and-stop cycle that still has a sidecar on
    disk -- `None` if there has never been one, or its every segment has
    already resolved (`resolve` deletes the sidecar once none are pending)."""
    capture_dir = repo.capture_dir
    if not capture_dir.is_dir():
        return None
    sidecars = sorted(capture_dir.glob("*.json"))
    if not sidecars:
        return None
    return _load(sidecars[-1])


def resolve(repo: Repo, index: int, status: str) -> SessionEntry:
    """Mark entry *index* of the current session as *status* (`"bound"` or
    `"discarded"`). Refuses an unknown index, and refuses one already
    resolved (binding twice, or discarding an already-bound segment, is a
    real error -- never a silent no-op or a misbind through a stale index).
    Deletes the sidecar AND the raw file once every entry has resolved.
    """
    session = current_session(repo)
    if session is None:
        raise WoodshedError(f"no such capture segment: {index}")
    updated_entries = []
    resolved: SessionEntry | None = None
    for entry in session.entries:
        if entry.index == index:
            if entry.status != STATUS_PENDING:
                raise WoodshedError(
                    f"capture segment {index} is already {entry.status}, not pending"
                )
            entry = dataclasses.replace(entry, status=status)
            resolved = entry
        updated_entries.append(entry)
    if resolved is None:
        raise WoodshedError(f"no such capture segment: {index}")

    updated = dataclasses.replace(session, entries=tuple(updated_entries))
    if any(e.status == STATUS_PENDING for e in updated.entries):
        _save(updated)
    else:
        updated.sidecar_path.unlink(missing_ok=True)
        updated.raw_path.unlink(missing_ok=True)
    return resolved
