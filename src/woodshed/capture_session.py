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
    "adjust_boundary",
    "merge_segments",
    "split_segment",
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


def _current_session_or_raise(repo: Repo, index: int) -> CaptureSession:
    """Shared first step of every per-segment operation below: there must be
    a session at all before there can be a pending entry in it."""
    session = current_session(repo)
    if session is None:
        raise WoodshedError(f"no such capture segment: {index}")
    return session


def _pending_entry(session: CaptureSession, index: int) -> SessionEntry:
    """The entry at *index*, refusing an unknown index or one that has
    already resolved -- the one lookup `resolve`, `adjust_boundary`,
    `merge_segments` and `split_segment` all need first, with the same
    refusal messages regardless of which of the four is asking."""
    entry = next((e for e in session.entries if e.index == index), None)
    if entry is None:
        raise WoodshedError(f"no such capture segment: {index}")
    if entry.status != STATUS_PENDING:
        raise WoodshedError(f"capture segment {index} is already {entry.status}, not pending")
    return entry


def resolve(repo: Repo, index: int, status: str) -> SessionEntry:
    """Mark entry *index* of the current session as *status* (`"bound"` or
    `"discarded"`). Refuses an unknown index, and refuses one already
    resolved (binding twice, or discarding an already-bound segment, is a
    real error -- never a silent no-op or a misbind through a stale index).
    Deletes the sidecar AND the raw file once every entry has resolved.
    """
    session = _current_session_or_raise(repo, index)
    entry = _pending_entry(session, index)
    resolved = dataclasses.replace(entry, status=status)
    updated_entries = tuple(
        resolved if e.index == index else e for e in session.entries
    )

    updated = dataclasses.replace(session, entries=updated_entries)
    if any(e.status == STATUS_PENDING for e in updated.entries):
        _save(updated)
    else:
        updated.sidecar_path.unlink(missing_ok=True)
        updated.raw_path.unlink(missing_ok=True)
    return resolved


def adjust_boundary(
    repo: Repo, index: int, *, start_frame: int | None = None, end_frame: int | None = None
) -> SessionEntry:
    """Move one pending entry's own `start_frame` and/or `end_frame`.
    Refuses a result where `start_frame >= end_frame`, or where the new
    range would overlap the previous or next PENDING entry's own range --
    dragging a handle past a neighbour is a merge, not an overlap, so this
    refuses and names the fix rather than silently clamping.

    "Previous"/"next" are the neighbouring pending entries in chronological
    order (by `start_frame`), not by index number -- a prior `split_segment`
    can leave a high index number chronologically in the middle of the
    session, and it is time, not index, that overlap is actually about.
    """
    session = _current_session_or_raise(repo, index)
    entry = _pending_entry(session, index)
    new_start = entry.start_frame if start_frame is None else start_frame
    new_end = entry.end_frame if end_frame is None else end_frame
    if new_start >= new_end:
        raise WoodshedError(
            f"segment {index}: start_frame ({new_start}) must be before "
            f"end_frame ({new_end})"
        )

    neighbours = sorted(
        (e for e in session.pending if e.index != index), key=lambda e: e.start_frame
    )
    previous = next(
        (e for e in reversed(neighbours) if e.start_frame < entry.start_frame), None
    )
    following = next((e for e in neighbours if e.start_frame > entry.start_frame), None)
    if previous is not None and new_start < previous.end_frame:
        raise WoodshedError(
            f"segment {index}'s new start would overlap segment {previous.index} "
            "-- merge them instead of overlapping"
        )
    if following is not None and new_end > following.start_frame:
        raise WoodshedError(
            f"segment {index}'s new end would overlap segment {following.index} "
            "-- merge them instead of overlapping"
        )

    updated_entry = dataclasses.replace(entry, start_frame=new_start, end_frame=new_end)
    updated_entries = tuple(
        updated_entry if e.index == index else e for e in session.entries
    )
    _save(dataclasses.replace(session, entries=updated_entries))
    return updated_entry


def merge_segments(repo: Repo, first_index: int, second_index: int) -> SessionEntry:
    """Merge two ADJACENT pending entries (by index -- "merge segment 1 and
    segment 4" has no principled meaning) into one, in either argument
    order: whichever of the two starts earlier in time supplies the merged
    entry's own index and start_frame, the later one supplies the
    end_frame, and its own row is removed from the session entirely (not
    merely marked resolved -- it never existed as its own bound/discarded
    song, so it shouldn't linger as a third status). `overflowed` is the OR
    of both -- a dropout in either half still makes the merged segment
    unsafe to bind.
    """
    if abs(first_index - second_index) != 1:
        raise WoodshedError(
            f"segments {first_index} and {second_index} are not adjacent -- "
            "merge only applies to neighbouring segments"
        )
    session = _current_session_or_raise(repo, first_index)
    entry_a = _pending_entry(session, first_index)
    entry_b = _pending_entry(session, second_index)
    earlier, later = sorted((entry_a, entry_b), key=lambda e: e.start_frame)

    merged = dataclasses.replace(
        earlier,
        end_frame=later.end_frame,
        overflowed=earlier.overflowed or later.overflowed,
    )
    updated_entries = tuple(
        merged if e.index == earlier.index else e
        for e in session.entries
        if e.index != later.index
    )
    _save(dataclasses.replace(session, entries=updated_entries))
    return merged


def split_segment(repo: Repo, index: int, at_frame: int) -> tuple[SessionEntry, SessionEntry]:
    """Split one pending entry into two at *at_frame*, which must fall
    STRICTLY inside its current range -- a boundary at or past either end
    has nothing to split. The first half keeps *index*'s own index; the
    second half gets a fresh index one past the session's current highest
    (across every entry ever recorded in this session, not just the still-
    pending ones, so a resolved entry's own index is never reused either)
    -- appended, not inserted, so it never collides with or renumbers a
    later entry.

    Judgement call: `overflowed` is not split-aware (nothing marks WHICH
    half of a segment saw the overflow) -- both halves inherit the
    original entry's own flag, the conservative reading (a real dropout
    anywhere in the original span keeps both halves flagged rather than
    silently losing the warning off one side).
    """
    session = _current_session_or_raise(repo, index)
    entry = _pending_entry(session, index)
    if not (entry.start_frame < at_frame < entry.end_frame):
        raise WoodshedError(
            f"split point {at_frame} must fall strictly inside segment {index}'s "
            f"range [{entry.start_frame}, {entry.end_frame})"
        )

    new_index = max(e.index for e in session.entries) + 1
    first_half = dataclasses.replace(entry, end_frame=at_frame)
    second_half = dataclasses.replace(entry, index=new_index, start_frame=at_frame)

    updated_entries = (
        tuple(first_half if e.index == index else e for e in session.entries)
        + (second_half,)
    )
    _save(dataclasses.replace(session, entries=updated_entries))
    return first_half, second_half
