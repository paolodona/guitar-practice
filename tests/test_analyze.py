"""Tests for woodshed.analyze -- tempo detection and the beat grid.

`beat_grid` is pure (no audio, no librosa) and runs unconditionally --
including the degrade path CLAUDE.md requires: `tempo.bpm` 0 or absent gives
an empty grid rather than a divide-by-zero.

`detect_tempo` needs librosa and ffmpeg -- a real decode -- so it is
exercised by one opt-in integration test against a synthetic click track,
marked `needs_librosa` (`uv run --extra analyze pytest -m needs_librosa`).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from woodshed.analyze import beat_grid
from woodshed.manifest import Tempo


def make_tempo(bpm=120.0, grid_offset_s=0.0, source="refined", confidence=0.9) -> Tempo:
    return Tempo(
        bpm=bpm, source=source, grid_offset_s=grid_offset_s,
        time_signature="4/4", confidence=confidence,
    )


# ---------------------------------------------------------------------------
# beat_grid -- pure, runs with no librosa
# ---------------------------------------------------------------------------


def test_beat_grid_spaces_beats_by_the_period():
    tempo = make_tempo(bpm=120.0, grid_offset_s=0.0)
    assert beat_grid(tempo, duration_s=2.0) == pytest.approx([0.0, 0.5, 1.0, 1.5])


def test_beat_grid_starts_at_the_anchor():
    tempo = make_tempo(bpm=120.0, grid_offset_s=0.2)
    grid = beat_grid(tempo, duration_s=1.0)
    assert grid == pytest.approx([0.2, 0.7])


def test_beat_grid_folds_an_out_of_range_offset_into_one_period():
    # grid_offset_s is documented as [0, period) -- see find_grid_anchor --
    # but a hand-edited song.yaml is not guaranteed to respect that.
    tempo = make_tempo(bpm=120.0, grid_offset_s=0.7)  # period is 0.5
    assert beat_grid(tempo, duration_s=0.6)[0] == pytest.approx(0.2)


def test_beat_grid_degrades_to_empty_when_bpm_is_zero():
    assert beat_grid(make_tempo(bpm=0.0), duration_s=120.0) == []


def test_beat_grid_degrades_to_empty_when_tempo_is_absent():
    assert beat_grid(None, duration_s=120.0) == []


def test_beat_grid_degrades_to_empty_for_a_zero_length_song():
    assert beat_grid(make_tempo(bpm=120.0), duration_s=0.0) == []


# ---------------------------------------------------------------------------
# detect_tempo -- needs librosa + ffmpeg, a real decode
# ---------------------------------------------------------------------------


def _write_click_track(path: Path, bpm: float, duration_s: float, sample_rate: int = 22050) -> None:
    """A synthetic click track: a short decaying tone burst at every beat.

    Percussive enough for librosa's onset detector to find reliably, unlike
    a pure sine tone (which carries almost no onset energy).
    """
    n = int(duration_s * sample_rate)
    samples = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    click_len = int(0.02 * sample_rate)
    envelope = np.exp(-np.arange(click_len) / (0.003 * sample_rate))
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / sample_rate)
    click = (envelope * tone).astype(np.float32)
    t = 0.25
    while t < duration_s:
        start = int(t * sample_rate)
        end = min(start + click_len, n)
        samples[start:end] += click[: end - start]
        t += period
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm.tobytes())


# ---------------------------------------------------------------------------
# librosa_available -- pure (a non-raising import check), runs unconditionally
# ---------------------------------------------------------------------------


def test_librosa_available_is_true_when_the_module_imports(monkeypatch):
    import sys
    from types import SimpleNamespace

    from woodshed.analyze import librosa_available

    monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace())

    assert librosa_available() is True


def test_librosa_available_is_false_when_the_module_cannot_be_imported(monkeypatch):
    import builtins

    from woodshed.analyze import librosa_available

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "librosa":
            raise ImportError("no librosa here")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert librosa_available() is False


@pytest.mark.needs_librosa
def test_detect_tempo_recovers_a_known_click_track(tmp_path):
    from woodshed.analyze import detect_tempo

    bpm = 120.0
    wav = tmp_path / "click.wav"
    _write_click_track(wav, bpm, duration_s=60.0)

    tempo = detect_tempo(wav)

    assert tempo.bpm == pytest.approx(bpm, abs=1.0)
    assert tempo.source == "refined"
    assert tempo.confidence is not None
    assert tempo.confidence > 0.5


@pytest.mark.needs_librosa
def test_detect_tempo_reports_a_confidence_matching_a_clean_click_track(tmp_path):
    """A dead-simple click track should land near-perfectly on its own grid."""
    from woodshed.analyze import detect_tempo

    wav = tmp_path / "click.wav"
    _write_click_track(wav, 96.0, duration_s=60.0)

    tempo = detect_tempo(wav)
    assert tempo.confidence > 0.8
