"""Tests for woodshed.clock -- the two-clocks boundary (CLAUDE.md invariant 3).

Source time is seconds in the original file; playback time is seconds in the
rendered, stretched section (where pre-roll occupies the head). These tests
pin the conversion at that boundary so the two clocks can never be silently
mixed up.
"""

from __future__ import annotations

import itertools

import pytest

from woodshed.clock import Render, grid_to_playback, to_playback, to_source


def make_render(
    start_s: float = 10.0,
    end_s: float = 20.0,
    pre_roll_s: float = 2.0,
    speed: float = 0.5,
    crossfade_ms: float = 10.0,
) -> Render:
    return Render(
        start_s=start_s,
        end_s=end_s,
        pre_roll_s=pre_roll_s,
        speed=speed,
        crossfade_ms=crossfade_ms,
    )


# ---------------------------------------------------------------------------
# Round-trip identity across a fuzz of speeds and pre-rolls.
# ---------------------------------------------------------------------------


SPEEDS = [0.4, 0.5, 0.62, 0.75, 0.881, 1.0]
PRE_ROLLS = [0.0, 1.0, 2.5, 4.0, 8.0]
SOURCE_POINTS_OFFSETS = [0.0, 0.001, 1.0, 3.3, 7.999, 10.0]


@pytest.mark.parametrize(
    "speed,pre_roll_s,offset",
    list(itertools.product(SPEEDS, PRE_ROLLS, SOURCE_POINTS_OFFSETS)),
)
def test_round_trip_identity(speed: float, pre_roll_s: float, offset: float) -> None:
    r = make_render(start_s=10.0, end_s=20.0, pre_roll_s=pre_roll_s, speed=speed)
    t = r.start_s - r.pre_roll_s + offset
    playback = to_playback(t, r)
    back = to_source(playback, r)
    assert back == pytest.approx(t, abs=1e-9)


# ---------------------------------------------------------------------------
# to_playback at section boundaries matches loop_start / loop_end.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("pre_roll_s", PRE_ROLLS)
def test_to_playback_at_start_s_is_loop_start(speed: float, pre_roll_s: float) -> None:
    r = make_render(pre_roll_s=pre_roll_s, speed=speed)
    assert to_playback(r.start_s, r) == pytest.approx(r.loop_start, abs=1e-9)


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("pre_roll_s", PRE_ROLLS)
def test_to_playback_at_end_s_is_loop_end(speed: float, pre_roll_s: float) -> None:
    # crossfade_ms=0 here: loop_end's crossfade subtraction is its own
    # documented behaviour, pinned separately below (test_loop_end_subtracts
    # _exactly_crossfade); with no crossfade, end_s maps exactly to loop_end.
    r = make_render(pre_roll_s=pre_roll_s, speed=speed, crossfade_ms=0.0)
    assert to_playback(r.end_s, r) == pytest.approx(r.loop_end, abs=1e-9)


# ---------------------------------------------------------------------------
# Named test: at speed 0.5, a 30s section renders 60s of playback.
# ---------------------------------------------------------------------------


def test_speed_half_thirty_second_section_renders_sixty_seconds() -> None:
    r = Render(start_s=0.0, end_s=30.0, pre_roll_s=0.0, speed=0.5, crossfade_ms=0.0)
    # With no pre-roll and no crossfade, the section duration divided by
    # speed is exactly the playback duration: 30s / 0.5 = 60s.
    assert r.loop_start == pytest.approx(0.0, abs=1e-9)
    assert r.loop_end == pytest.approx(60.0, abs=1e-9)
    assert r.total == pytest.approx(60.0, abs=1e-9)


def test_speed_half_thirty_second_section_with_crossfade() -> None:
    crossfade_ms = 10.0
    r = Render(start_s=0.0, end_s=30.0, pre_roll_s=0.0, speed=0.5, crossfade_ms=crossfade_ms)
    # loop_end is crossfade_ms/1000 short of the raw 60s stretch, but total
    # (the full rendered buffer) still covers the whole 60s.
    assert r.loop_end == pytest.approx(60.0 - crossfade_ms / 1000, abs=1e-9)
    assert r.total == pytest.approx(60.0, abs=1e-9)


# ---------------------------------------------------------------------------
# loop_end is exactly crossfade_ms/1000 less than it would be without the
# crossfade term.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("pre_roll_s", PRE_ROLLS)
@pytest.mark.parametrize("crossfade_ms", [0.0, 10.0, 25.0, 100.0])
def test_loop_end_subtracts_exactly_crossfade(
    speed: float, pre_roll_s: float, crossfade_ms: float
) -> None:
    r = make_render(pre_roll_s=pre_roll_s, speed=speed, crossfade_ms=crossfade_ms)
    r_no_crossfade = make_render(pre_roll_s=pre_roll_s, speed=speed, crossfade_ms=0.0)
    assert r_no_crossfade.loop_end - r.loop_end == pytest.approx(crossfade_ms / 1000, abs=1e-9)


# ---------------------------------------------------------------------------
# Render property formulas, pinned directly.
# ---------------------------------------------------------------------------


def test_loop_start_formula() -> None:
    r = make_render(pre_roll_s=2.5, speed=0.4)
    assert r.loop_start == pytest.approx(2.5 / 0.4, abs=1e-9)


def test_loop_end_formula() -> None:
    r = make_render(start_s=10.0, end_s=20.0, pre_roll_s=2.5, speed=0.4, crossfade_ms=10.0)
    expected = (2.5 / 0.4) + (20.0 - 10.0) / 0.4 - 10.0 / 1000
    assert r.loop_end == pytest.approx(expected, abs=1e-9)


def test_total_formula() -> None:
    r = make_render(start_s=10.0, end_s=20.0, pre_roll_s=2.5, speed=0.4, crossfade_ms=10.0)
    expected = (2.5 + (20.0 - 10.0)) / 0.4
    assert r.total == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# to_source is the exact inverse of to_playback's formula.
# ---------------------------------------------------------------------------


def test_to_playback_formula() -> None:
    r = make_render(start_s=10.0, end_s=20.0, pre_roll_s=2.0, speed=0.5)
    t = 11.0
    expected = (t - (r.start_s - r.pre_roll_s)) / r.speed
    assert to_playback(t, r) == pytest.approx(expected, abs=1e-9)


def test_to_source_formula() -> None:
    r = make_render(start_s=10.0, end_s=20.0, pre_roll_s=2.0, speed=0.5)
    playback = 5.0
    expected = playback * r.speed + (r.start_s - r.pre_roll_s)
    assert to_source(playback, r) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# grid_to_playback maps a whole grid through to_playback, in order.
# ---------------------------------------------------------------------------


def test_grid_to_playback_maps_each_point() -> None:
    r = make_render(start_s=10.0, end_s=20.0, pre_roll_s=2.0, speed=0.5)
    grid = [8.0, 10.0, 14.0, 20.0]
    result = grid_to_playback(grid, r)
    assert result == [pytest.approx(to_playback(g, r), abs=1e-9) for g in grid]


def test_grid_to_playback_empty() -> None:
    r = make_render()
    assert grid_to_playback([], r) == []


def test_grid_to_playback_preserves_order_and_length() -> None:
    r = make_render()
    grid = [12.0, 8.0, 15.0]
    result = grid_to_playback(grid, r)
    assert len(result) == len(grid)
    assert result[0] == pytest.approx(to_playback(grid[0], r), abs=1e-9)
    assert result[1] == pytest.approx(to_playback(grid[1], r), abs=1e-9)
    assert result[2] == pytest.approx(to_playback(grid[2], r), abs=1e-9)


# ---------------------------------------------------------------------------
# Render is frozen (immutable).
# ---------------------------------------------------------------------------


def test_render_is_frozen() -> None:
    r = make_render()
    with pytest.raises(AttributeError):
        r.speed = 0.9  # type: ignore[misc]


def test_render_default_crossfade_ms() -> None:
    r = Render(start_s=0.0, end_s=10.0, pre_roll_s=0.0, speed=1.0)
    assert r.crossfade_ms == 10.0
