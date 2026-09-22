"""Tests for woodshed.loudness -- integrated loudness measurement (pure
numpy, ITU-R BS.1770-4-style K-weighting + gating) and the gain formula.

Both `measure` and `gain_db` are pure -- no audio device, no optional
dependency, no I/O -- so everything here runs unconditionally, including in
the pure-tier suite CLAUDE.md's Layering section describes.
"""

from __future__ import annotations

import numpy as np
import pytest

from woodshed.loudness import (
    GAIN_CLAMP_DB,
    PEAK_CEILING_DBFS,
    TARGET_LUFS,
    gain_db,
    measure,
)

SAMPLE_RATE = 44100


def _sine(
    amplitude: float, seconds: float = 3.0, freq: float = 440.0, sr: int = SAMPLE_RATE
) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float64)


# ---------------------------------------------------------------------------
# measure -- pure, runs with no librosa/scipy/optional dependency
# ---------------------------------------------------------------------------


def test_measure_of_silence_is_the_silence_floor():
    integrated, peak = measure(np.zeros(SAMPLE_RATE * 2), SAMPLE_RATE)
    assert integrated == pytest.approx(-70.0)
    assert peak == pytest.approx(-120.0)


def test_measure_of_an_empty_array_is_the_silence_floor():
    integrated, peak = measure(np.zeros(0), SAMPLE_RATE)
    assert integrated == pytest.approx(-70.0)
    assert peak == pytest.approx(-120.0)


def test_measure_peak_matches_a_full_scale_sine():
    _, peak = measure(_sine(1.0), SAMPLE_RATE)
    # A sine's peak is its amplitude exactly -- 1.0 -> 0 dBFS.
    assert peak == pytest.approx(0.0, abs=0.05)


def test_measure_peak_halves_to_minus_six_db():
    _, peak = measure(_sine(0.5), SAMPLE_RATE)
    assert peak == pytest.approx(-6.02, abs=0.1)


def test_measure_integrated_loudness_is_monotonic_with_level():
    quiet, _ = measure(_sine(0.01), SAMPLE_RATE)
    mid, _ = measure(_sine(0.1), SAMPLE_RATE)
    loud, _ = measure(_sine(0.5), SAMPLE_RATE)
    assert quiet < mid < loud


def test_measure_integrated_loudness_a_10db_drop_in_amplitude_drops_about_20_lu():
    # Loudness is a power (mean-square) measure -- halving amplitude drops
    # power by ~6dB per octave-of-amplitude in the usual dB sense, but the
    # LUFS formula is 10*log10(power), so a 10x amplitude drop is a 20 LU
    # drop (power drops 100x), not a 10 LU drop.
    loud, _ = measure(_sine(0.5), SAMPLE_RATE)
    quiet, _ = measure(_sine(0.05), SAMPLE_RATE)
    assert (loud - quiet) == pytest.approx(20.0, abs=1.0)


def test_measure_never_returns_a_measurement_below_the_silence_floor():
    integrated, peak = measure(_sine(1e-6), SAMPLE_RATE)
    assert integrated >= -70.0
    assert peak >= -120.0


def test_measure_is_independent_of_sample_rate_for_the_same_signal():
    # The K-weighting filter is re-derived per sample_rate (see
    # `_shelf_and_highpass_coefficients`) -- the same tone at two common
    # sample rates should land within a fraction of a LU of each other.
    a, _ = measure(_sine(0.2, sr=44100, freq=1000.0), 44100)
    b, _ = measure(_sine(0.2, sr=48000, freq=1000.0), 48000)
    assert a == pytest.approx(b, abs=0.5)


def test_measure_handles_mono_capture_length_audio():
    # Regression-shaped: a real captured song is minutes long, not seconds
    # -- confirm nothing about block gating breaks on a longer signal.
    integrated, peak = measure(_sine(0.1, seconds=30.0), SAMPLE_RATE)
    assert integrated < 0.0
    assert peak < 0.0


# ---------------------------------------------------------------------------
# gain_db -- pure arithmetic
# ---------------------------------------------------------------------------


def test_gain_db_is_zero_for_the_never_measured_sentinel():
    assert gain_db(0.0, 0.0) == 0.0


def test_gain_db_boosts_a_quiet_source_toward_the_target():
    # -30 LUFS integrated, -20 dBFS peak -- plenty of headroom before the
    # peak ceiling, so gain should be target - measured exactly.
    gain = gain_db(-30.0, -20.0)
    assert gain == pytest.approx(TARGET_LUFS - (-30.0))


def test_gain_db_is_capped_by_the_peak_ceiling_even_when_target_wants_more():
    # A quiet-but-peaky source: -30 LUFS integrated (wants +14dB to reach
    # -16 LUFS) but only -2 dBFS of headroom before the -1 dBTP ceiling.
    gain = gain_db(-30.0, -2.0)
    assert gain == pytest.approx(PEAK_CEILING_DBFS - (-2.0))
    assert gain < TARGET_LUFS - (-30.0)


def test_gain_db_is_non_positive_for_an_already_loud_source():
    # -8 LUFS is louder than the -16 LUFS target -- no boost needed.
    gain = gain_db(-8.0, -0.5)
    assert gain <= 0.0


def test_gain_db_never_exceeds_the_clamp_in_either_direction():
    assert gain_db(-90.0, -90.0) == pytest.approx(GAIN_CLAMP_DB)
    assert gain_db(-0.01, -0.01) >= -GAIN_CLAMP_DB


def test_gain_db_respects_custom_target_and_ceiling():
    gain = gain_db(-20.0, -10.0, target_lufs=-14.0, peak_ceiling_dbfs=-3.0)
    assert gain == pytest.approx(min(-14.0 - (-20.0), -3.0 - (-10.0)))
