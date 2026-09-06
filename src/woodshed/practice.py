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
from datetime import UTC, date, datetime, timedelta

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
    so a Tier-0 module never needs to know the field exists. A `full_song`
    section is dropped unconditionally alongside them (see Section.
    full_song's docstring): being the longest possible span, it would
    otherwise always win coverage_readiness's "longest covering span"
    tie-break and silently override every other section's contribution.
    """
    reps = list(reps)
    counting = [s for s in song.sections if s.counts_toward_readiness and not s.full_song]
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
            if section.full_song:
                # A rep counter, not a practice target -- never the tool's
                # "next up" suggestion (see manifest.Section.full_song).
                continue
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


# ---------------------------------------------------------------------------
# Progress (Phase 2, K2) -- everything screens/progress.js draws.
#
# All of it is a REPLAY of practice/reps.jsonl, computed per call. Nothing
# below is ever written to disk (CLAUDE.md invariant 6: never a summary in
# song.yaml), which is also what makes it safe to change how a series is
# derived tomorrow -- there is no stored history to migrate, only a
# different reading of the same lines.


#: How many points the readiness series carries by default -- one per week
#: over the default window, which is what the artboard's own axis labels
#: ("26 WEEKS AGO / 13 / NOW") describe.
PROGRESS_WEEKS = 26


@dataclass(frozen=True)
class SectionProgress:
    """One row of the progress table, for one section."""

    id: str
    name: str
    target_speed: float
    reps: int
    cleans: int
    minutes: float
    best_sustained: float
    reached: float
    last_speed: float | None
    last_practised: datetime | None
    days_since: float | None
    cold: bool
    #: (day, that day's highest practised speed) for every day with reps --
    #: a day, not a "session", because the ledger records times and not
    #: sittings, and inventing a session boundary out of gaps would be a
    #: measurement the tool cannot actually make.
    series: list[tuple[date, float]]


@dataclass(frozen=True)
class Progress:
    """The whole progress payload for one song."""

    slug: str
    title: str
    artist: str
    weeks: int
    #: (day, readiness 0..1 AS OF that day) -- each point recomputed from
    #: the reps up to it, so a section learned late never lifts an early
    #: point.
    readiness_series: list[tuple[date, float]]
    sections: list[SectionProgress]
    totals: ledger.Totals
    week: ledger.Totals
    rungs_gained: int


def progress(
    song: manifest.Song,
    reps: Iterable[ledger.Rep],
    *,
    now: datetime | None = None,
    weeks: int = PROGRESS_WEEKS,
    points: int | None = None,
) -> Progress:
    """Assemble the progress payload for *song* from *reps*.

    `points` defaults to one per week over the window, plus the endpoint.
    Sections come back **furthest from target first** -- the artboard's own
    ordering, and the only one that puts the work at the top of the screen.
    """
    reps = list(reps)
    now = now or _utcnow()
    resolved = ledger.resolve(reps)

    rows: list[SectionProgress] = []
    for section in song.sections:
        totals = ledger.totals(resolved, song.slug, section.id)
        last = ledger.last_practised(resolved, song.slug, section.id)
        reps_to_advance = (
            section.reps_to_advance
            if section.reps_to_advance is not None
            else song.practice.reps_to_advance
        )
        rows.append(
            SectionProgress(
                id=section.id,
                name=section.name,
                target_speed=section.target_speed,
                reps=totals.passes,
                cleans=totals.cleans,
                minutes=totals.minutes,
                best_sustained=ledger.best_sustained_speed(
                    resolved, song.slug, section.id, reps_to_advance
                ),
                reached=reached(resolved, song, section, song.practice),
                last_speed=ledger.last_speed(resolved, song.slug, section.id),
                last_practised=last,
                days_since=None if last is None else (now - last).total_seconds() / 86400.0,
                cold=is_cold(resolved, song, section, now=now),
                series=_speed_series(resolved, song.slug, section.id),
            )
        )
    rows.sort(key=lambda row: (row.reached, row.id))

    window_start = now - timedelta(days=7)
    week_reps = [r for r in resolved if _parse_t(r.t) >= window_start]

    return Progress(
        slug=song.slug,
        title=song.title,
        artist=song.artist,
        weeks=weeks,
        readiness_series=_readiness_series(song, resolved, now=now, weeks=weeks, points=points),
        sections=rows,
        totals=ledger.totals(resolved, song.slug),
        week=ledger.totals(week_reps, song.slug),
        rungs_gained=_rungs_gained(song, resolved, since=window_start),
    )


def _parse_t(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _speed_series(
    resolved: list[ledger.Rep], slug: str, section_id: str
) -> list[tuple[date, float]]:
    """(day, that day's highest speed) for every day this section was played."""
    by_day: dict[date, float] = {}
    for rep in resolved:
        if rep.song != slug or rep.section != section_id or not rep.passed:
            continue
        day = _parse_t(rep.t).date()
        by_day[day] = max(by_day.get(day, 0.0), rep.speed)
    return sorted(by_day.items())


def _readiness_series(
    song: manifest.Song,
    resolved: list[ledger.Rep],
    *,
    now: datetime,
    weeks: int,
    points: int | None,
) -> list[tuple[date, float]]:
    """Song readiness recomputed at evenly spaced instants across the window.

    Deliberately a replay rather than a stored curve: `song_readiness` is
    the one definition of the number (CLAUDE.md: readiness is measured over
    covered song time), and reading it at N instants keeps the chart and the
    headline figure the same quantity by construction.
    """
    count = points if points is not None else weeks + 1
    count = max(2, count)
    span = timedelta(weeks=weeks)
    series: list[tuple[date, float]] = []
    for index in range(count):
        at = now - span + span * (index / (count - 1))
        upto = [r for r in resolved if _parse_t(r.t) <= at]
        series.append((at.date(), song_readiness(song, upto, song.practice).ratio))
    return series


def _rungs_gained(song: manifest.Song, resolved: list[ledger.Rep], *, since: datetime) -> int:
    """How many ladder rungs the whole song climbed inside the window.

    Compared, not counted: best-sustained speed before the window against
    best-sustained now, divided by the section's own ladder step. There is
    no "rungs gained" number anywhere on disk to read, and there should not
    be one -- it is a difference between two readings of the ledger.
    """
    before = [r for r in resolved if _parse_t(r.t) < since]
    gained = 0
    for section in song.sections:
        reps_to_advance = (
            section.reps_to_advance
            if section.reps_to_advance is not None
            else song.practice.reps_to_advance
        )
        step = (
            section.ladder_step if section.ladder_step is not None else song.practice.ladder_step
        )
        if step <= 0:
            continue
        was = ledger.best_sustained_speed(before, song.slug, section.id, reps_to_advance)
        is_now = ledger.best_sustained_speed(resolved, song.slug, section.id, reps_to_advance)
        gained += max(0, int(round((is_now - was) / step)))
    return gained
