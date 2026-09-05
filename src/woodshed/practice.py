"""Readiness, the cold list, and next-up. `docs/01-architecture.md:44` names
this module; an earlier draft of the plan's Group B left it with no owning
unit (see that group's B9 note) because nothing needed it until Phase 1's
dashboard. It lands here, in Group F, as the module `GET /api/setlist/<slug>`
(F1) depends on for `next_up` and each row's readiness bar.

CLAUDE.md invariant 6: never write a summary into `song.yaml`. Everything
below is derived from the ledger on every call, never cached on disk.

Tier: allowed to depend on `manifest.py` and `ledger.py` (this whole group --
library/manifest/sections/ladder/ledger/practice/server -- needs only
pyyaml, numpy and pydantic; only the five *pure* modules named in
CLAUDE.md's "Layering" section -- sections, ladder, ledger, clock, tuning --
are held to stdlib+numpy alone).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from woodshed import ledger, manifest, sections

#: docs/02-data-model.md's cold-list rule: unpractised for this long, at a
#: reached this high, is "cold" -- not "unlearned".
COLD_DAYS = 14
COLD_REACHED_THRESHOLD = 0.8


def reached(
    reps: Iterable[ledger.Rep], song: manifest.Song, section: manifest.Section, cfg
) -> float:
    """`best_sustained_speed(section) / section.target_speed`, clamped 0..1.

    This is the exact input `sections.coverage_readiness` consumes as its
    `reached` mapping -- something has to produce it, and this is that thing.

    `cfg` needs only a `.reps_to_advance` attribute; callers normally pass
    `song.practice` (a `PracticeDefaults`), since that is where the
    song-level default lives and it always has a value (pydantic fills
    `reps_to_advance=3` even when a hand-written song.yaml omits it). A
    section's own `reps_to_advance` overrides it when set.
    """
    reps_to_advance = (
        section.reps_to_advance if section.reps_to_advance is not None else cfg.reps_to_advance
    )
    if section.target_speed <= 0:
        return 0.0
    best = ledger.best_sustained_speed(reps, song.slug, section.id, reps_to_advance)
    return max(0.0, min(1.0, best / section.target_speed))


def song_readiness(song: manifest.Song, reps: Iterable[ledger.Rep], cfg) -> sections.CoverageResult:
    """A song's readiness, length-weighted over covered song time.

    Drops every section with `counts_toward_readiness is False` before
    handing the rest to `sections.coverage_readiness` -- that function
    takes filtering as the caller's job (see its own docstring) precisely
    so a Tier-0 module never needs to know the field exists.
    """
    reps = list(reps)
    counting = [s for s in song.sections if s.counts_toward_readiness]
    reached_map = {s.id: reached(reps, song, s, cfg) for s in counting}
    return sections.coverage_readiness(counting, reached_map)


def is_cold(
    reps: Iterable[ledger.Rep],
    song: manifest.Song,
    section: manifest.Section,
    *,
    now: datetime,
    days: int = COLD_DAYS,
) -> bool:
    """True iff unpractised for more than `days` AND `reached > 0.8`.

    A section that has never been practised is not cold, it is unlearned --
    `last_practised` returning None short-circuits to False here rather than
    raising on `now - None`. Likewise `reached <= 0.8`, however long ago it
    was last touched, is never cold: that section still needs basic reps,
    not a reminder that it has gone stale. Conflating the two would put
    beginner work in the cold list.
    """
    last = ledger.last_practised(reps, song.slug, section.id)
    if last is None:
        return False
    elapsed_days = (now - last).total_seconds() / 86400.0
    if elapsed_days <= days:
        return False
    return reached(reps, song, section, song.practice) > COLD_REACHED_THRESHOLD


@dataclass(frozen=True)
class Ranked:
    """One section's next-up score, with its three components broken out.

    "Explainable beats clever" (docs/00-spec.md): the UI shows `gap`/`cold`/
    `gig` on hover rather than collapsing them into `score` alone, so `score
    == gap + cold + gig` always -- callers never need to recompute it.
    """

    song_slug: str
    section_id: str
    score: float
    gap: float
    cold: float
    gig: float
    reached: float


def next_up(
    repo,
    setlist: manifest.Setlist,
    reps: Iterable[ledger.Rep],
    cfg,
    *,
    now: datetime | None = None,
) -> list[Ranked]:
    """Rank every section of every bound song in *setlist* by docs/00-spec.md's
    scoring formula, highest first:

        score = (1 - reached) * weight_gap
              + days_since_practised / 14 * weight_cold
              + is_in_next_gig_setlist * weight_gig

    `cfg` needs `.weight_gap` / `.weight_cold` / `.weight_gig` (normally
    `config.Defaults`). A setlist entry whose song has no `song.yaml` yet
    (needs-audio -- nothing bound, so no sections exist to rank) is skipped
    rather than erroring; `next_up` degrades, it does not refuse.

    "Never practised" is scored as the most overdue a section can be
    (`cold` component at its full weight) rather than left undefined --
    `days_since_practised / 14` is clamped to 1.0 either way, so a section
    ten years cold does not out-score one merely never begun.

    `is_in_next_gig_setlist` is `setlist` itself, evaluated against every
    OTHER setlist in `repo`: true only when `setlist.date` is the soonest
    upcoming (>= *now*) date among all of them. A setlist with no date, or
    one that is not the soonest, contributes nothing here -- ranking a
    setlist against itself for "urgency" needs a comparison point, and the
    other setlists on disk are it.
    """
    reps = list(reps)
    now = now or _utcnow()
    is_next_gig = _is_next_gig_setlist(repo, setlist, now)

    ranked: list[Ranked] = []
    for entry in setlist.songs:
        song_path = repo.song_dir(entry.slug) / "song.yaml"
        if not song_path.is_file():
            continue
        song = manifest.load_song(song_path)
        for section in song.sections:
            r = reached(reps, song, section, song.practice)
            gap = (1.0 - r) * cfg.weight_gap

            last = ledger.last_practised(reps, song.slug, section.id)
            if last is None:
                cold_ratio = 1.0
            else:
                elapsed_days = max(0.0, (now - last).total_seconds() / 86400.0)
                cold_ratio = min(elapsed_days / COLD_DAYS, 1.0)
            cold = cold_ratio * cfg.weight_cold

            gig = cfg.weight_gig if is_next_gig else 0.0

            ranked.append(
                Ranked(
                    song_slug=song.slug,
                    section_id=section.id,
                    score=gap + cold + gig,
                    gap=gap,
                    cold=cold,
                    gig=gig,
                    reached=r,
                )
            )
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_next_gig_setlist(repo, setlist: manifest.Setlist, now: datetime) -> bool:
    """True iff *setlist* has a date and it is the soonest upcoming (>= now)
    date among every setlist under `repo.setlists_dir`."""
    if setlist.date is None:
        return False
    today = now.date()
    if setlist.date < today:
        return False
    upcoming: list = []
    for slug in repo.list_setlists():
        other = manifest.load_setlist(repo.setlists_dir / f"{slug}.yaml")
        if other.date is not None and other.date >= today:
            upcoming.append(other.date)
    if not upcoming:
        return False
    return setlist.date == min(upcoming)
