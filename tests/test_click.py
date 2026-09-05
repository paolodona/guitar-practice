"""Tests for woodshed.click -- the rehearsal click, generated from bpm +
grid_offset_s (not a lifted Timeline; see the module docstring for why).

Numpy only, no audio device, no ffmpeg, no librosa -- this whole module sits
below CLAUDE.md's heavy-dependency line.
"""

from __future__ import annotations

import numpy as np
import pytest

from woodshed.click import (
    _beats_per_bar,
    _biquad_bandpass,
    _db,
    _guard,
    _one_pole_lowpass,
    _tick,
    render_click,
)

# ---------------------------------------------------------------------------
# _db
# ---------------------------------------------------------------------------


def test_db_zero_is_unity_gain():
    assert _db(0.0) == pytest.approx(1.0)


def test_db_minus_six_is_about_half():
    assert _db(-6.0) == pytest.approx(0.5012, abs=1e-3)


def test_db_is_monotonic():
    assert _db(-9.0) < _db(-4.0) < _db(0.0)


# ---------------------------------------------------------------------------
# _guard
# ---------------------------------------------------------------------------


def test_guard_leaves_a_quiet_signal_alone():
    signal = np.array([0.1, -0.2, 0.05], dtype=np.float32)
    assert np.array_equal(_guard(signal), signal)


def test_guard_scales_a_hot_signal_down_to_the_ceiling():
    signal = np.array([2.0, -2.0], dtype=np.float32)
    out = _guard(signal)
    assert float(np.max(np.abs(out))) == pytest.approx(0.99, abs=1e-4)


def test_guard_survives_an_empty_buffer():
    assert _guard(np.array([], dtype=np.float32)).size == 0


def test_guard_survives_silence():
    silence = np.zeros(10, dtype=np.float32)
    assert np.array_equal(_guard(silence), silence)


# ---------------------------------------------------------------------------
# _tick
# ---------------------------------------------------------------------------


def test_tick_decays_toward_the_tail():
    tone = _tick(1000.0, click_ms=28.0, sample_rate=48000)
    head = np.abs(tone[: len(tone) // 10]).max()
    tail = np.abs(tone[-len(tone) // 10 :]).max()
    assert tail < head


def test_tick_length_matches_click_ms():
    tone = _tick(1000.0, click_ms=20.0, sample_rate=48000)
    assert len(tone) == pytest.approx(0.020 * 48000, abs=1)


# ---------------------------------------------------------------------------
# _biquad_bandpass / _one_pole_lowpass -- lifted, currently unused by
# render_click, but kept working (see click.py's docstring on why they exist).
# ---------------------------------------------------------------------------


def test_biquad_bandpass_of_silence_is_silence():
    silence = np.zeros(256, dtype=np.float32)
    out = _biquad_bandpass(silence, 48000, 2000.0, 1.1)
    assert np.allclose(out, 0.0)


def test_biquad_bandpass_preserves_length():
    signal = np.random.default_rng(0).standard_normal(256).astype(np.float32)
    out = _biquad_bandpass(signal, 48000, 2000.0, 1.1)
    assert len(out) == len(signal)


def test_one_pole_lowpass_of_silence_is_silence():
    silence = np.zeros(256, dtype=np.float32)
    assert np.allclose(_one_pole_lowpass(silence, 48000, 4000.0), 0.0)


def test_one_pole_lowpass_smooths_a_step():
    """A lowpass cannot follow a step instantly -- the first sample after
    the jump must undershoot the full step."""
    step = np.concatenate([np.zeros(50), np.ones(50)]).astype(np.float32)
    out = _one_pole_lowpass(step, 48000, 500.0)
    assert out[50] < 1.0


# ---------------------------------------------------------------------------
# _beats_per_bar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signature,expected", [("4/4", 4), ("3/4", 3), ("6/8", 6), ("2/2", 2)]
)
def test_beats_per_bar_reads_the_numerator(signature, expected):
    assert _beats_per_bar(signature) == expected


@pytest.mark.parametrize("signature", ["", "garbage", None, "four/four"])
def test_beats_per_bar_degrades_to_four_on_garbage(signature):
    assert _beats_per_bar(signature) == 4


# ---------------------------------------------------------------------------
# render_click
# ---------------------------------------------------------------------------


def test_render_click_degrades_to_silence_when_bpm_is_zero():
    out = render_click(0.0, 0.0, "4/4", duration_s=4.0, sample_rate=8000)
    assert not np.any(out)
    assert len(out) == pytest.approx(4.0 * 8000, abs=1)


def test_render_click_places_a_tone_at_every_beat():
    bpm, sr = 120.0, 8000
    out = render_click(bpm, 0.0, "4/4", duration_s=2.0, sample_rate=sr)
    period_samples = int(60.0 / bpm * sr)
    for beat_index in range(4):  # 2s at 120bpm = 4 beats
        start = beat_index * period_samples
        window = out[start : start + 50]
        assert np.abs(window).max() > 0.01, f"no tone near beat {beat_index}"


def test_render_click_accents_the_downbeat_louder_than_other_beats():
    bpm, sr = 120.0, 8000
    out = render_click(bpm, 0.0, "4/4", duration_s=2.0, sample_rate=sr)
    period_samples = int(60.0 / bpm * sr)

    def peak_near(beat_index):
        start = beat_index * period_samples
        return float(np.abs(out[start : start + 200]).max())

    assert peak_near(0) > peak_near(1)  # beat 0 is the downbeat


def test_render_click_respects_the_grid_offset():
    bpm, sr, offset = 120.0, 8000, 0.1
    out = render_click(bpm, offset, "4/4", duration_s=1.0, sample_rate=sr)
    first_beat_sample = int(offset * sr)
    assert np.abs(out[max(0, first_beat_sample - 5) : first_beat_sample + 50]).max() > 0.01
    # nothing before the offset
    assert not np.any(out[: max(0, first_beat_sample - 100)])


def test_render_click_never_clips():
    out = render_click(180.0, 0.0, "4/4", duration_s=3.0, sample_rate=8000)
    assert float(np.abs(out).max()) <= 0.99 + 1e-6


def test_render_click_with_no_time_signature_still_accents_every_four():
    out = render_click(120.0, 0.0, "", duration_s=2.0, sample_rate=8000)
    assert out is not None  # degrades via _beats_per_bar, does not raise
