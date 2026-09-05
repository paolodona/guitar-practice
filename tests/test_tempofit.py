"""Tests for woodshed.tempofit -- the pure-numpy tempo-fitting maths.

Deliberately importable and runnable with **no librosa installed**: the split
between this module and `analyze.py` exists so the maths that decides where
every beat and grid line lands is testable on a bare numpy install. The
Phase 1 gate runs this file under
`uv run --no-project --with pytest --with numpy --with pyyaml --with pydantic
pytest tests/test_tempofit.py` to prove it.

Lifted from `rambass-live/tests/test_analyze.py`, which tests the same
functions for the same reason in that repo.
"""

from __future__ import annotations

import numpy as np
import pytest

from woodshed.tempofit import (
    find_grid_anchor,
    grid_confidence,
    pulse_wander,
    refine_tempo,
    tap_tempo,
)

FRAME = 0.005  # seconds per envelope frame, about what librosa gives at hop 256


def envelope(beat_times, *, duration, width=0.012):
    """A synthetic onset envelope: a narrow bump at each beat."""
    times = np.arange(0.0, duration, FRAME)
    env = np.zeros_like(times)
    for beat in beat_times:
        env += np.exp(-0.5 * ((times - beat) / width) ** 2)
    return env, times


def even_beats(bpm, duration, *, first=0.25, shift_after=None, shift=0.0):
    period = 60.0 / bpm
    out = []
    t = first
    while t < duration:
        out.append(t + (shift if shift_after is not None and t >= shift_after else 0.0))
        t += period
    return out


# ---------------------------------------------------------------------------
# tap_tempo
# ---------------------------------------------------------------------------


def test_tap_tempo_reads_steady_taps():
    taps = [i * 0.5 for i in range(9)]  # 9 taps, 8 intervals of 0.5s -> 120 bpm
    assert tap_tempo(taps) == pytest.approx(120.0, abs=0.01)


def test_tap_tempo_discards_one_fumbled_interval():
    """A double-hit early on must not drag the reading off 120 bpm."""
    taps = [0.0, 0.05, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]  # 0.05s fumble, then steady
    assert tap_tempo(taps) == pytest.approx(120.0, abs=0.5)


def test_tap_tempo_uses_only_the_last_eight_intervals():
    """A stale early interval outside the window must not still count."""
    # A slow start (1.0s intervals) settling into a steady 0.5s (120bpm) tap.
    taps = [0.0, 1.0, 2.0] + [2.0 + i * 0.5 for i in range(1, 9)]
    assert tap_tempo(taps) == pytest.approx(120.0, abs=0.5)


def test_tap_tempo_needs_at_least_two_taps():
    assert tap_tempo([1.0]) == 0.0
    assert tap_tempo([]) == 0.0


def test_tap_tempo_is_order_independent():
    taps = [2.0, 0.0, 1.0, 3.0]
    assert tap_tempo(taps) == tap_tempo(sorted(taps))


# ---------------------------------------------------------------------------
# refine_tempo
# ---------------------------------------------------------------------------


def test_refine_tempo_beats_the_bin_grid_it_was_given():
    """The whole point: a coarse estimate a bin out is pulled back to the truth.

    117.45 for a 116 BPM song is exactly the failure this exists for -- that is
    a tempogram bin centre, and it is 1.2% wrong, which is three seconds of
    slip across a five-minute song.
    """
    truth = 116.02
    env, times = envelope(even_beats(truth, 240.0), duration=240.0)
    assert refine_tempo(env, times, 117.45) == pytest.approx(truth, abs=0.02)


def test_refine_tempo_does_not_wander_off_to_half_or_double_time():
    truth = 132.0
    env, times = envelope(even_beats(truth, 180.0), duration=180.0)
    assert refine_tempo(env, times, 130.0) == pytest.approx(truth, abs=0.02)


def test_refine_tempo_leaves_a_hopeless_input_alone():
    env, times = envelope([0.5, 1.0], duration=1.5)
    assert refine_tempo(env, times, 120.0) == 120.0


# ---------------------------------------------------------------------------
# pulse_wander
# ---------------------------------------------------------------------------


def test_a_click_locked_take_reads_as_no_wander():
    env, times = envelope(even_beats(120.0, 200.0), duration=200.0)
    offsets = [ms for _, ms in pulse_wander(env, times, 120.0)]
    assert offsets, "expected several windows"
    assert max(abs(ms) for ms in offsets) < 3.0


def test_wander_finds_a_band_that_moved_mid_song():
    """Second half played 40 ms behind: mean removed, that reads as +-20 ms."""
    beats = even_beats(120.0, 200.0, shift_after=100.0, shift=0.040)
    env, times = envelope(beats, duration=200.0)
    measured = pulse_wander(env, times, 120.0)
    early = [ms for t, ms in measured if t < 100.0]
    late = [ms for t, ms in measured if t > 100.0]
    assert np.mean(late) - np.mean(early) == pytest.approx(40.0, abs=6.0)


def test_wander_needs_something_to_measure():
    env, times = envelope([0.5], duration=1.0)
    assert pulse_wander(env, times, 120.0) == []


def test_wander_never_reports_more_than_half_a_beat():
    """Phase is wrapped, not unwrapped, on purpose.

    An unwrapped phase can invent a whole-beat jump out of one noisy window,
    and "the band was a beat late for twenty seconds" is a measurement
    artifact, not a thing that happened. Half a beat at 120 BPM is 250 ms.
    """
    rng = np.random.default_rng(7)
    beats = []
    t = 0.3
    while t < 300.0:
        beats.append(t + rng.normal(0, 0.02))
        t += 0.5
    env, times = envelope(beats, duration=300.0)
    offsets = [ms for _, ms in pulse_wander(env, times, 120.0, window=10.0)]
    assert offsets
    assert max(abs(ms) for ms in offsets) <= 250.0


# ---------------------------------------------------------------------------
# find_grid_anchor / grid_confidence
# ---------------------------------------------------------------------------


def _song_onsets(bpm, bars=40, anchor=0.0, pattern=(0, 3, 4, 8, 12)):
    """A bar's worth of onsets, offset by a known anchor.

    The pattern is deliberately **asymmetric**: hits on all four beats plus
    one on a sixteenth. A pattern on every eighth would be identical to itself
    shifted by an eighth, so its anchor is genuinely ambiguous -- which is a
    property of that music, not a bug.
    """
    beat = 60.0 / bpm
    six = beat / 4
    out = []
    for b in range(bars):
        for s in pattern:
            out.append(anchor + b * 4 * beat + s * six)
    return np.array(out)


def test_find_grid_anchor_recovers_a_known_anchor():
    bpm = 116.03
    anchor, report = find_grid_anchor(_song_onsets(bpm, anchor=0.352), bpm)
    assert anchor == pytest.approx(0.352, abs=0.01)
    assert report["on_beat"] > report["runner_up_on_beat"]


def test_find_grid_anchor_breaks_the_sixteenth_tie_on_beats():
    """The failure this exists for: four candidates tie on the 16th grid.

    A pattern shifted by a sixteenth is still perfectly on sixteenths, so the
    subdivision score cannot tell them apart -- only the beat can.
    """
    bpm = 120.0
    beat, truth = 0.5, 0.25
    onsets = _song_onsets(bpm, anchor=truth)  # asymmetric, so it IS recoverable
    anchor, report = find_grid_anchor(onsets, bpm)
    assert report["tied_candidates"] > 1, "expected the tie this test is about"
    # any whole-sixteenth error would still be 'on the grid' -- reject them
    assert min(abs(anchor - truth), abs(anchor - truth + beat)) < 0.02


def test_find_grid_anchor_refuses_without_enough_onsets():
    anchor, report = find_grid_anchor([0.1, 0.6, 1.1], 120.0)
    assert anchor == 0.0 and report["anchors"] == 0


def test_grid_confidence_sees_a_whole_subdivision_displacement():
    """The coarse-grid check. A part on the wrong sixteenth scores at chance."""
    bpm, beat = 120.0, 0.5
    right = _song_onsets(bpm, pattern=(0, 4, 8, 12))  # on every beat
    wrong = right + beat / 4  # one sixteenth late
    assert grid_confidence(right, bpm)["ratio"] > 3.0
    assert grid_confidence(wrong, bpm)["ratio"] < 1.0
