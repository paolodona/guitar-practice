"""Tests for woodshed.practice: readiness, the cold list, next-up.

Written test-first (Phase 1, Group F). `docs/01-architecture.md:44` names
this module; an earlier draft of the plan omitted a unit for it entirely,
so it lands here as a documented prerequisite of F1's dashboard endpoint
(see the plan's Group B9 note and Group F section).

Uses real `manifest.Song`/`Section`/`Setlist` and a real `library.Repo`
rooted at `tmp_path` (test_server.py's pattern), rather than a Span-protocol
stand-in like test_sections.py -- practice.py is allowed to depend on
manifest.py (CLAUDE.md's layering only requires the five *pure* modules,
which does not include practice, to stay stdlib+numpy only).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from woodshed.ledger import Rep
from woodshed.library import Repo
from woodshed.manifest import (
    Recording,
    Section,
    Setlist,
    SetlistEntry,
    Song,
    save_setlist,
    save_song,
)
from woodshed.practice import is_cold, next_up, reached, song_readiness


def _song(slug: str = "cant-stop", **sections_kwargs) -> Song:
    section = Section(
        id="solo",
        name="Solo",
        start_s=10.0,
        end_s=20.0,
        snapped="free",
        target_speed=100.0,
        **sections_kwargs,
    )
    return Song(
        slug=slug,
        title="Can't Stop",
        artist="RHCP",
        recording=Recording(
            file="audio/x.flac", sha256="a" * 64, duration_s=200.0, tuning="E standard"
        ),
        sections=[section],
    )


def _rep(
    song: str, section: str, speed: float, *, clean: bool, t: str = "2026-09-05T12:00:00Z"
) -> Rep:
    return Rep(
        id=f"{song}-{section}-{speed}-{t}-{clean}",
        t=t,
        song=song,
        section=section,
        speed=speed,
        semitones=0,
        passed=True,
        clean=clean,
        loop_s=10.0,
        setlist=None,
        source="ui",
    )


# ---------------------------------------------------------------------------
# reached()


def test_reached_zero_with_no_reps() -> None:
    song = _song()
    assert reached([], song, song.sections[0], song.practice) == 0.0


def test_reached_is_best_sustained_over_target_clamped() -> None:
    song = _song()  # target_speed 100
    reps = [_rep("cant-stop", "solo", 60.0, clean=True) for _ in range(3)]
    assert reached(reps, song, song.sections[0], song.practice) == 0.6


def test_reached_clamps_to_one_even_past_target() -> None:
    section = Section(
        id="solo", name="Solo", start_s=0, end_s=10, snapped="free", target_speed=50.0,
    )
    song = _song().model_copy(update={"sections": [section]})
    reps = [_rep("cant-stop", "solo", 100.0, clean=True) for _ in range(3)]
    assert reached(reps, song, section, song.practice) == 1.0


def test_reached_uses_section_reps_to_advance_override() -> None:
    section = Section(
        id="solo", name="Solo", start_s=0, end_s=10, snapped="free",
        target_speed=100.0, reps_to_advance=1,
    )
    song = _song().model_copy(update={"sections": [section]})
    reps = [_rep("cant-stop", "solo", 70.0, clean=True)]  # only one clean rep
    # song.practice.reps_to_advance defaults to 3 -- would be 0.0 without the
    # section-level override taking precedence.
    assert reached(reps, song, section, song.practice) == 0.7


# ---------------------------------------------------------------------------
# song_readiness() -- delegates to sections.coverage_readiness


def test_song_readiness_excludes_non_counting_sections() -> None:
    counted = Section(id="a", name="A", start_s=0, end_s=10, snapped="free", target_speed=100.0)
    excluded = Section(
        id="b", name="B", start_s=20, end_s=30, snapped="free", target_speed=100.0,
        counts_toward_readiness=False,
    )
    song = _song().model_copy(update={"sections": [counted, excluded]})
    result = song_readiness(song, [], song.practice)
    assert {i.span_id for i in result.intervals} == {"a"}


def test_song_readiness_excludes_full_song_even_if_it_counts() -> None:
    # A whole-song entry is the longest possible span, so it would
    # otherwise always win coverage_readiness's "longest covering span"
    # tie-break and silently override every other section's contribution.
    solo = Section(id="solo", name="Solo", start_s=10, end_s=20, snapped="free", target_speed=100.0)
    whole = Section(
        id="whole", name="Whole song", start_s=0, end_s=200, snapped="free",
        target_speed=100.0, full_song=True,  # counts_toward_readiness left at its True default
    )
    song = _song().model_copy(update={"sections": [solo, whole]})
    result = song_readiness(song, [], song.practice)
    assert {i.span_id for i in result.intervals} == {"solo"}


# ---------------------------------------------------------------------------
# is_cold()


def test_is_cold_false_when_never_practised() -> None:
    song = _song()
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert is_cold([], song, song.sections[0], now=now) is False


def test_is_cold_false_below_reached_threshold_however_old() -> None:
    # reached <= 0.8 is "unlearned", never "cold" -- conflating them would put
    # beginner work in the cold list.
    song = _song()  # target_speed 100
    old = "2020-01-01T00:00:00Z"
    reps = [_rep("cant-stop", "solo", 60.0, clean=True, t=old) for _ in range(3)]  # reached 0.6
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert is_cold(reps, song, song.sections[0], now=now) is False


def test_is_cold_true_past_threshold_and_days() -> None:
    song = _song()
    old = "2020-01-01T00:00:00Z"
    reps = [_rep("cant-stop", "solo", 90.0, clean=True, t=old) for _ in range(3)]  # reached 0.9
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert is_cold(reps, song, song.sections[0], now=now) is True


def test_is_cold_false_within_the_window() -> None:
    song = _song()
    recent = "2026-09-04T00:00:00Z"
    reps = [_rep("cant-stop", "solo", 90.0, clean=True, t=recent) for _ in range(3)]
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert is_cold(reps, song, song.sections[0], now=now) is False


# ---------------------------------------------------------------------------
# next_up()


def _repo_with_song(tmp_path, song: Song) -> Repo:
    repo = Repo(root=tmp_path)
    (repo.song_dir(song.slug) / "audio").mkdir(parents=True, exist_ok=True)
    save_song(song, repo.song_dir(song.slug) / "song.yaml")
    repo.setlists_dir.mkdir(parents=True, exist_ok=True)
    return repo


def test_next_up_ranks_never_played_above_at_target(tmp_path) -> None:
    played = Section(
        id="played", name="Played", start_s=0, end_s=10, snapped="free", target_speed=50.0
    )
    fresh = Section(
        id="fresh", name="Fresh", start_s=20, end_s=30, snapped="free", target_speed=100.0
    )
    song = _song().model_copy(update={"sections": [played, fresh]})
    repo = _repo_with_song(tmp_path, song)
    reps = [_rep("cant-stop", "played", 50.0, clean=True) for _ in range(3)]  # at target
    setlist = Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="cant-stop")])

    class Cfg:
        weight_gap = 1.0
        weight_cold = 1.0
        weight_gig = 1.0

    ranked = next_up(repo, setlist, reps, Cfg())
    by_id = {r.section_id: r for r in ranked}
    assert by_id["fresh"].score > by_id["played"].score


def test_next_up_never_suggests_a_full_song_section(tmp_path) -> None:
    whole = Section(
        id="whole", name="Whole song", start_s=0, end_s=200, snapped="free",
        target_speed=100.0, full_song=True,
    )
    song = _song().model_copy(update={"sections": [whole]})
    repo = _repo_with_song(tmp_path, song)
    setlist = Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="cant-stop")])

    class Cfg:
        weight_gap = weight_cold = weight_gig = 1.0

    assert next_up(repo, setlist, [], Cfg()) == []


def test_next_up_skips_songs_with_no_song_yaml(tmp_path) -> None:
    repo = Repo(root=tmp_path)
    repo.setlists_dir.mkdir(parents=True, exist_ok=True)
    setlist = Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="ghost")])

    class Cfg:
        weight_gap = weight_cold = weight_gig = 1.0

    assert next_up(repo, setlist, [], Cfg()) == []


def test_next_up_parts_sum_to_score(tmp_path) -> None:
    song = _song()
    repo = _repo_with_song(tmp_path, song)
    setlist = Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="cant-stop")])

    class Cfg:
        weight_gap = 2.0
        weight_cold = 3.0
        weight_gig = 5.0

    ranked = next_up(repo, setlist, [], Cfg())
    assert len(ranked) == 1
    r = ranked[0]
    assert r.score == r.gap + r.cold + r.gig


def test_next_up_gig_component_only_for_the_next_dated_setlist(tmp_path) -> None:
    song = _song()
    repo = _repo_with_song(tmp_path, song)
    near = Setlist(name="Gig", tuning="E standard", date=date(2026, 10, 1),
                    songs=[SetlistEntry(slug="cant-stop")])
    far = Setlist(name="Someday", tuning="E standard", date=date(2027, 1, 1),
                  songs=[SetlistEntry(slug="cant-stop")])
    save_setlist(near, repo.setlists_dir / "gig.yaml")
    save_setlist(far, repo.setlists_dir / "someday.yaml")

    class Cfg:
        weight_gap = 0.0
        weight_cold = 0.0
        weight_gig = 5.0

    now = datetime(2026, 9, 5, tzinfo=UTC)
    near_ranked = next_up(repo, near, [], Cfg(), now=now)
    far_ranked = next_up(repo, far, [], Cfg(), now=now)
    assert near_ranked[0].gig == 5.0
    assert far_ranked[0].gig == 0.0
