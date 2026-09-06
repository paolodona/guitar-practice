"""The practice ledger: ``practice/reps.jsonl``.

CLAUDE.md invariant 5: the ledger is append-only and it is the only
irreplaceable file. Invariant 6: never write a summary into ``song.yaml`` --
every count on every screen is an aggregate of this file, computed on read.

Tier: stdlib only (see CLAUDE.md's "Layering" -- ``ledger`` must stay provably
pure). No pydantic, no numpy even, just ``dataclasses`` and hand-written
``to_dict``/``from_dict`` mapping so the on-disk key ``pass`` (a Python
keyword, hence the field ``passed``) is exactly what docs/02-data-model.md
fixes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

Source = Literal["midi", "keyboard", "ui", "auto"]


class _HasLedgerPath(Protocol):
    """Structural stand-in for ``library.Repo``.

    ``library.py`` is built by a parallel unit and this module must not
    depend on it to satisfy the layering rule (stdlib only) -- the same
    reason ``sections.py`` depends on the ``Span`` Protocol rather than
    ``manifest.Section``. Anything with a ``ledger_path()`` method works.
    """

    def ledger_path(self) -> Path: ...


@dataclass(frozen=True)
class Rep:
    """One line of ``practice/reps.jsonl``.

    ``passed`` is the Python-side name for the on-disk key ``pass`` (``pass``
    is a reserved word). ``retracted``/``retracts`` describe a retraction: a
    retraction line has ``retracted=True`` and ``retracts`` set to the uuid4
    ``id`` of the rep it undoes -- never inferred from (song, section,
    speed), which cannot express "undo THAT specific one" once semitones can
    change mid-session.
    """

    id: str
    t: str
    song: str
    section: str
    speed: float
    semitones: int
    passed: bool
    clean: bool
    loop_s: float
    setlist: str | None
    source: Source
    retracted: bool = False
    retracts: str | None = None


@dataclass(frozen=True)
class Totals:
    passes: int
    cleans: int
    minutes: float


# Held across append() calls so a UI rep and a MIDI rep -- both genuinely
# concurrent under ThreadingHTTPServer -- cannot interleave their writes to
# the one file that cannot be reconstructed.
_APPEND_LOCK = threading.Lock()


def _rep_to_dict(rep: Rep) -> dict:
    """Build the on-disk dict, in the field order docs/02-data-model.md prints.

    ``id`` is appended after the documented fields (docs/02-data-model.md's
    printed example predates the by-id retraction design and does not show
    it, but every line needs its own id for a later retraction to name).
    ``retracted``/``retracts`` are only present on a retraction line, matching
    the data model's "present and true on a line that undoes the previous
    rep" -- an ordinary pass carries neither key.
    """
    d: dict = {
        "t": rep.t,
        "song": rep.song,
        "section": rep.section,
        "speed": rep.speed,
        "semitones": rep.semitones,
        "pass": rep.passed,
        "clean": rep.clean,
        "loop_s": rep.loop_s,
        "setlist": rep.setlist,
        "source": rep.source,
        "id": rep.id,
    }
    if rep.retracted:
        d["retracted"] = True
        d["retracts"] = rep.retracts
    return d


def _rep_from_dict(d: dict) -> Rep:
    """Inverse of ``_rep_to_dict``. Raises KeyError/TypeError on a bad dict --
    callers (``read``) treat that as one unparseable line, never a crash."""
    return Rep(
        id=d["id"],
        t=d["t"],
        song=d["song"],
        section=d["section"],
        speed=d["speed"],
        semitones=d["semitones"],
        passed=d["pass"],
        clean=d["clean"],
        loop_s=d["loop_s"],
        setlist=d.get("setlist"),
        source=d["source"],
        retracted=d.get("retracted", False),
        retracts=d.get("retracts"),
    )


def append(repo: _HasLedgerPath, rep: Rep) -> None:
    """Append one rep as one JSON line. Append-only: never opens "w", never
    reads the file first. Creates ``practice/`` if it is missing."""
    path = repo.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_rep_to_dict(rep), separators=(",", ":")) + "\n"
    with _APPEND_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def read(repo: _HasLedgerPath) -> Iterator[Rep]:
    """Yield every parseable rep in file order. A corrupt line is skipped and
    counted (logged), never raised -- the lines around it still come back."""
    path = repo.ledger_path()
    if not path.exists():
        return
    skipped = 0
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rep = _rep_from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                skipped += 1
                logger.warning(
                    "ledger: skipping unparseable line %d in %s: %s", lineno, path, exc
                )
                continue
            yield rep
    if skipped:
        logger.warning("ledger: skipped %d unparseable line(s) in %s", skipped, path)


def resolve(reps: Iterable[Rep]) -> list[Rep]:
    """Apply retractions and return the surviving reps, in file order.

    A retraction cancels the rep whose ``id`` its ``retracts`` names -- not
    the "most recent rep matching (song, section, speed)", which is
    ambiguous (see ``Rep``'s docstring). The retraction line itself is never
    part of the output.
    """
    reps = list(reps)
    retracted_ids = {r.retracts for r in reps if r.retracted and r.retracts}
    return [r for r in reps if not r.retracted and r.id not in retracted_ids]


def _matching(reps: Iterable[Rep], song: str, section: str | None) -> list[Rep]:
    return [
        r
        for r in resolve(reps)
        if r.song == song and (section is None or r.section == section)
    ]


def clean_by_speed(reps: Iterable[Rep], song: str, section: str) -> dict[float, int]:
    """Count of clean, resolved reps at each speed for one song/section."""
    counts: dict[float, int] = {}
    for r in _matching(reps, song, section):
        if r.clean:
            counts[r.speed] = counts.get(r.speed, 0) + 1
    return counts


def best_sustained_speed(
    reps: Iterable[Rep], song: str, section: str, reps_to_advance: int
) -> float:
    """The highest speed with at least ``reps_to_advance`` clean reps, post-resolve.
    0.0 if no speed has reached that many."""
    counts = clean_by_speed(reps, song, section)
    qualifying = [speed for speed, n in counts.items() if n >= reps_to_advance]
    return max(qualifying) if qualifying else 0.0


def _parse_t(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def last_practised(reps: Iterable[Rep], song: str, section: str | None = None) -> datetime | None:
    """max(t) among the resolved reps matching song (and section, if given)."""
    matching = _matching(reps, song, section)
    if not matching:
        return None
    return max(_parse_t(r.t) for r in matching)


def last_speed(reps: Iterable[Rep], song: str, section: str) -> float | None:
    """The `speed` of the most recently timestamped resolved rep for this
    song/section, clean or not -- None if never practised.

    Found live 2026-09-06: a `full_song` section is a rep counter, not a
    ladder target (manifest.Section.full_song's own docstring -- it never
    earns a rung via `ladder.starting_speed`), so it needs a different
    "where do I resume" answer -- literally wherever the last pass left
    off, not the highest earned rung. Deliberately ignores `clean`: an
    unclean pass still records the speed that was actually being played,
    and there is nothing else on disk to remember it by.
    """
    matching = _matching(reps, song, section)
    if not matching:
        return None
    return max(matching, key=lambda r: _parse_t(r.t)).speed


def totals(reps: Iterable[Rep], song: str, section: str | None = None) -> Totals:
    """passes, cleans and minutes practised, aggregated over resolved reps."""
    matching = _matching(reps, song, section)
    passes = sum(1 for r in matching if r.passed)
    cleans = sum(1 for r in matching if r.clean)
    minutes = sum(r.loop_s for r in matching) / 60.0
    return Totals(passes=passes, cleans=cleans, minutes=minutes)
