"""Tests for woodshed.beatfit -- the per-section beat fit the metronome plays.

Pure: numpy only, no ffmpeg, no librosa, no audio device. Every signal here
is synthesised in-process, so the numbers the tests assert are the numbers
the maths actually has to hit.

**The measurements these tests exist to protect** (probed 2026-09-12 on
`songs/tutti-in-fila`, the recording Paolo named as "not played in time"):

  * The song-level tempo is 116.04 BPM; fitting each section on its own
    gives 116.09 (full solo), 116.59 (tapping), 117.42 (first run) -- a
    1.2% spread. A grid anchored at t=0 and extended to a section 200s in
    is ~0.8s out by the time it gets there. Hence: fit per section.
  * Within one 88s section the pulse wobbles +-40-60ms against a constant
    grid, and four independent FFT parameterisations (n_fft 1024-4096, hop
    128-256) agree on that wobble curve to r=+1.00, rms 2-5ms. The wobble
    is the band, not the analysis. Hence: a slow phase correction, not a
    constant grid.
  * librosa's own `beat_track` returned ONE beat on the 11s "First run"
    section. Dynamic-programming beat tracking collapses on short spans,
    which is exactly the length of a drill. Hence: a comb fit, which is
    what `tempofit._comb` already does, and no new dependency.
"""

from __future__ import annotations

import numpy as np
import pytest

from woodshed.beatfit import (
    ENVELOPE_HOP,
    ENVELOPE_N_FFT,
    fit_beats,
    onset_envelope,
)

SR = 22050


def click_track(
    bpm: float,
    duration_s: float,
    *,
    sr: int = SR,
    offset_s: float = 0.0,
    bpm_end: float | None = None,
    click_ms: float = 12.0,
) -> tuple[np.ndarray, list[float]]:
    """A click track and the exact times of its clicks.

    `bpm_end` ramps the tempo linearly across the track (a band speeding
    up), which is the drift case a constant grid cannot follow.
    """
    samples = np.zeros(int(duration_s * sr), dtype=np.float32)
    length = max(4, int(click_ms / 1000 * sr))
    t = np.arange(length, dtype=np.float32) / sr
    # A short noisy transient: broadband, so a spectral-flux envelope sees
    # it the way it sees a real drum hit rather than one lucky bin.
    rng = np.random.default_rng(7)
    tone = (np.sin(2 * np.pi * 1000 * t) + 0.4 * rng.standard_normal(length)).astype(np.float32)
    tone *= np.exp(-t * 220.0).astype(np.float32)

    times: list[float] = []
    position = offset_s
    while position < duration_s:
        times.append(position)
        if bpm_end is None:
            period = 60.0 / bpm
        else:
            # Where we are through the track decides the local tempo.
            frac = min(1.0, max(0.0, position / duration_s))
            period = 60.0 / (bpm + (bpm_end - bpm) * frac)
        start = int(position * sr)
        end = min(len(samples), start + length)
        if end > start:
            samples[start:end] += tone[: end - start]
        position += period
    return samples, times


def worst_error_ms(beats: list[float], truth: list[float]) -> float:
    """How far the worst click lands from the beat it is meant to mark.

    Every TRUE click is measured against its nearest fitted beat, so a fit
    that simply omits half the beats cannot score well by being sparse.
    """
    if not beats:
        return float("inf")
    fitted = np.asarray(beats)
    errors = [float(np.min(np.abs(fitted - t))) for t in truth]
    return max(errors) * 1000.0


# ---------------------------------------------------------------------------
# onset_envelope -- pure numpy spectral flux
# ---------------------------------------------------------------------------


def test_onset_envelope_peaks_at_the_clicks():
    samples, times = click_track(120.0, 8.0, offset_s=0.25)
    env, env_times = onset_envelope(samples, SR)
    assert len(env) == len(env_times)
    # Every click should sit within one hop of a local maximum of the
    # envelope -- that is the whole contract of an onset envelope.
    hop_s = ENVELOPE_HOP / SR
    for click in times:
        window = np.abs(env_times - click) <= 2 * hop_s
        assert env[window].max() > np.median(env) * 5, f"no onset seen at {click:.3f}s"


def test_onset_envelope_is_zero_on_silence():
    env, _times = onset_envelope(np.zeros(SR * 2, dtype=np.float32), SR)
    assert float(env.max()) == 0.0


def test_onset_envelope_times_are_frame_centres():
    samples, _ = click_track(120.0, 4.0)
    _env, times = onset_envelope(samples, SR)
    assert times[0] == pytest.approx(ENVELOPE_N_FFT / 2 / SR)
    assert times[1] - times[0] == pytest.approx(ENVELOPE_HOP / SR)


def test_onset_envelope_handles_audio_shorter_than_one_frame():
    env, times = onset_envelope(np.zeros(64, dtype=np.float32), SR)
    assert len(env) == len(times) == 0


# ---------------------------------------------------------------------------
# fit_beats -- steady tempo
# ---------------------------------------------------------------------------


def test_fit_beats_recovers_a_known_tempo_and_phase():
    samples, truth = click_track(116.0, 30.0, offset_s=0.37)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)
    assert fit.bpm == pytest.approx(116.0, abs=0.2)
    # 20ms is a quarter of the 80ms hop-to-hop resolution an envelope at
    # hop 256 / 22050Hz can even see, and well inside the +-40ms the band
    # itself moves on the real recording.
    assert worst_error_ms(fit.beats, truth) < 20.0


def test_fit_beats_covers_the_whole_analysed_span():
    samples, truth = click_track(116.0, 30.0, offset_s=0.37)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)
    assert len(fit.beats) >= len(truth) - 2
    assert fit.beats[0] < 1.0
    assert fit.beats[-1] > 29.0


def test_fit_beats_returns_strictly_increasing_beats():
    """A click scheduler assumes this: a beat that goes backwards would
    schedule a node in the past, which Web Audio plays IMMEDIATELY."""
    samples, _truth = click_track(116.0, 40.0, offset_s=0.2, bpm_end=121.0)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)
    gaps = np.diff(fit.beats)
    assert (gaps > 0).all()


# ---------------------------------------------------------------------------
# fit_beats -- the drift case, which is why the phase correction exists
# ---------------------------------------------------------------------------


def test_fit_beats_follows_a_band_that_speeds_up():
    """The measured case: the pulse moves against any constant grid.

    A 40s take drifting 116 -> 121 BPM is roughly twice the wobble
    tutti-in-fila's solo actually shows, chosen so the difference between
    following and not following is unambiguous rather than marginal.
    """
    samples, truth = click_track(116.0, 40.0, offset_s=0.2, bpm_end=121.0)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)
    assert worst_error_ms(fit.beats, truth) < 45.0

    # ... and the constant grid it beats, built from the SAME fitted tempo
    # so the only difference under test is the phase correction.
    period = 60.0 / fit.bpm
    flat = list(np.arange(fit.beats[0], times[-1], period))
    assert worst_error_ms(flat, truth) > 120.0


def test_a_sparse_stretch_does_not_drag_the_click_off_the_steady_one():
    """FOUND BY MEASUREMENT 2026-09-12: following every window made the
    click WORSE than a plain grid across tutti-in-fila's first 90s -- a
    sparse intro, windows holding a tenth of the median energy and a
    coherence swinging 0.05-0.58 at random, correction wandering 333ms.
    A window with nothing in it is not a measurement, and the correction
    is interpolated straight across it instead."""
    quiet = np.zeros(int(SR * 12), dtype=np.float32)
    rng = np.random.default_rng(11)
    for hit in (1.3, 4.9, 7.1, 10.4):  # a few unrelated, arrhythmic events
        start = int(hit * SR)
        quiet[start:start + 400] += rng.standard_normal(400).astype(np.float32) * 0.2
    steady, truth = click_track(116.0, 30.0, offset_s=0.31)
    samples = np.concatenate([quiet, steady])
    truth = [t + 12.0 for t in truth]

    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)

    in_steady = [b for b in fit.beats if b > 13.0]
    assert worst_error_ms(in_steady, [t for t in truth if t > 13.5]) < 20.0
    assert fit.wander_ms < 40.0


def test_fit_beats_reports_the_wander_it_corrected_for():
    samples, _truth = click_track(116.0, 40.0, offset_s=0.2, bpm_end=121.0)
    env, times = onset_envelope(samples, SR)
    drifting = fit_beats(env, times)

    samples, _truth = click_track(116.0, 40.0, offset_s=0.2)
    env, times = onset_envelope(samples, SR)
    steady = fit_beats(env, times)

    assert drifting.wander_ms > steady.wander_ms
    assert steady.wander_ms < 20.0


# ---------------------------------------------------------------------------
# fit_beats -- the metrical-level guard
# ---------------------------------------------------------------------------


def test_a_bpm_prior_keeps_the_fit_on_the_right_metrical_level():
    """Eighth notes are as periodic as quarter notes, and a click at
    double time is wrong in a way that sounds plausible. The song's own
    tempo is the prior that settles which level was meant -- probed on the
    real recording, where a low-band envelope locked onto 174 BPM (1.5x)
    with no prior at all."""
    samples, truth = click_track(196.0, 20.0, offset_s=0.1)  # eighths at 98
    env, times = onset_envelope(samples, SR)

    unguided = fit_beats(env, times)
    assert unguided.bpm == pytest.approx(196.0, abs=1.0)

    guided = fit_beats(env, times, bpm_prior=98.0)
    assert guided.bpm == pytest.approx(98.0, abs=0.5)
    assert worst_error_ms(truth, guided.beats) < 20.0
    assert float(np.median(np.diff(guided.beats))) == pytest.approx(60.0 / 98.0, abs=0.01)


def test_a_prior_reaches_pulse_rates_above_the_sweep_ceiling():
    """The sweep's 200 BPM ceiling is a TEMPO ceiling -- a pulse can sit
    above it (eighths on a 116 BPM song pulse at 232). Each multiple of
    the prior is evaluated directly rather than searched for, so the
    ceiling never decides what a song is allowed to be playing."""
    samples, truth = click_track(232.0, 20.0, offset_s=0.1)
    env, times = onset_envelope(samples, SR)

    guided = fit_beats(env, times, bpm_prior=116.0)
    assert guided.bpm == pytest.approx(116.0, abs=0.5)
    # Every beat it plays lands ON a click -- it takes every other pulse,
    # never the gaps between them. WHICH alternate pulse is beat 1 is not
    # something an even click track can answer, and this does not pretend
    # to: the assertion is that no beat falls between clicks.
    assert worst_error_ms(truth, guided.beats) < 20.0
    assert float(np.median(np.diff(guided.beats))) == pytest.approx(60.0 / 116.0, abs=0.01)


def test_a_bpm_prior_still_corrects_a_song_level_tempo_that_is_slightly_off():
    """The prior fixes the metrical LEVEL; the section still gets to have
    its own tempo. tutti-in-fila's "First run" fits 117.42 against a
    song-level 116.04, and playing that section to 116.04 is the bug."""
    samples, truth = click_track(118.0, 25.0, offset_s=0.3)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times, bpm_prior=116.0)
    assert fit.bpm == pytest.approx(118.0, abs=0.3)
    assert worst_error_ms(fit.beats, truth) < 20.0


def test_a_wildly_wrong_prior_does_not_drag_the_fit_off_the_music():
    """A prior more than the search span away from the truth must not win:
    refining within +-6% of a nonsense number would produce a confident,
    wrong click. Falls back to the unguided sweep instead."""
    samples, truth = click_track(116.0, 25.0, offset_s=0.3)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times, bpm_prior=155.0)
    assert fit.bpm == pytest.approx(116.0, abs=0.5)
    assert worst_error_ms(fit.beats, truth) < 20.0


# ---------------------------------------------------------------------------
# fit_beats -- degrades, never raises
# ---------------------------------------------------------------------------


def test_fit_beats_on_silence_reports_no_beats_rather_than_dividing_by_zero():
    env, times = onset_envelope(np.zeros(SR * 10, dtype=np.float32), SR)
    fit = fit_beats(env, times)
    assert fit.beats == []
    assert fit.bpm == 0.0
    assert fit.pulse_ratio == 0.0


def test_fit_beats_on_a_span_too_short_to_measure_reports_no_beats():
    samples, _truth = click_track(116.0, 1.0)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)
    assert fit.beats == []


def test_fit_beats_on_a_short_section_still_fits_it():
    """11 seconds is the length of tutti-in-fila's "First run" -- the span
    librosa's beat tracker gave up on."""
    samples, truth = click_track(117.4, 11.0, offset_s=0.15)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times)
    assert fit.bpm == pytest.approx(117.4, abs=0.5)
    assert worst_error_ms(fit.beats, truth) < 20.0
    assert len(fit.beats) >= 20


def test_a_confident_pulse_scores_higher_than_noise():
    """`pulse_ratio` is how far the winning tempo stands above the rest of
    the sweep -- a relative peak-to-background ratio, deliberately NOT a
    probability (this tool does not invent measurements it cannot make)."""
    samples, _truth = click_track(116.0, 25.0)
    env, times = onset_envelope(samples, SR)
    clicks = fit_beats(env, times)

    rng = np.random.default_rng(3)
    noise = rng.standard_normal(SR * 25).astype(np.float32) * 0.1
    env, times = onset_envelope(noise, SR)
    hiss = fit_beats(env, times)

    assert clicks.pulse_ratio > hiss.pulse_ratio
    assert clicks.pulse_ratio > 2.0


def test_beats_are_in_the_clock_the_times_are_in():
    """The envelope's `times` are absolute SOURCE seconds when the caller
    decoded a span out of the middle of a recording, and the beats must
    come back in that same clock -- CLAUDE.md's two-clock rule, applied to
    the one place this module could silently rebase them."""
    samples, truth = click_track(116.0, 20.0, offset_s=0.25)
    env, times = onset_envelope(samples, SR)
    fit = fit_beats(env, times + 145.7)
    shifted = [t + 145.7 for t in truth]
    assert worst_error_ms(fit.beats, shifted) < 20.0
    assert fit.beats[0] > 145.0
