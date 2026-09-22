"""Integrated loudness measurement and the playback-gain formula it feeds.

Numpy only, per CLAUDE.md's "Layering" -- this module may import numpy but
not pydantic, librosa, scipy, or any external binary. That is a deliberate
choice, not an oversight: `measure()` hand-implements ITU-R BS.1770-4's
K-weighting + gated-block loudness algorithm, the same "don't trust a heavy
library, write the DSP" instinct that already produced `beatfit.py` and
`tempofit.py` instead of leaning on librosa's own beat tracker. Because
nothing here needs an optional dependency, `analyze_after_bind` can call
`measure()` unconditionally, the same tier as `peaks.py` -- there is no
`librosa_available()`-style gate to silently skip, which is exactly the
failure mode that produced a real song with `tempo.bpm: 0.0` (2026-09-22,
see CLAUDE.md's "Installed is a different question from imported").

`measure()` produces a *declaration* (`Song.loudness`, stored in
`song.yaml`, same tier as `Tempo`). `gain_db()` produces a *derived* value
(computed fresh per request in `server.py`, same tier as `shift` -- never
stored, so it can never disagree with a stored copy of itself). Gain is
applied at playback time only, in `web/player.js`'s two engines; nothing in
this module, or anywhere in the render pipeline, ever rescales a sample on
disk. See CLAUDE.md's loudness-matching invariant.
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["TARGET_LUFS", "PEAK_CEILING_DBFS", "GAIN_CLAMP_DB", "measure", "gain_db"]

#: Integrated-loudness reference target. -16 LUFS rather than the more common
#: streaming-platform -14: extra headroom margin for a live loopback capture
#: that came in quiet (found live 2026-09-22: "snow" measured -21.6 dBFS
#: peak), so more of the correction comes from the loudness term rather than
#: being clamped away by the peak ceiling below.
TARGET_LUFS = -16.0

#: Sample-peak safety ceiling. Caps the gain even when the loudness target
#: would ask for more, so a hot-mastered song is never pushed toward
#: clipping. Sample-peak, not true-peak (no oversampling) -- good enough for
#: a practice tool, not a broadcast loudness compliance meter.
PEAK_CEILING_DBFS = -1.0

#: Sanity bound on the computed gain, either direction. Nothing about a
#: quiet or loud recording should ever ask for more than this; a value
#: outside it means the measurement itself is suspect (near-silence, a
#: corrupt decode), not that more gain is the right answer.
GAIN_CLAMP_DB = 24.0

#: BS.1770-4's own absolute and relative gate thresholds, in LUFS/LU.
_ABSOLUTE_GATE_LUFS = -70.0
_RELATIVE_GATE_LU = -10.0

#: Gating block size and hop -- 400ms blocks, 75% overlap (100ms hop), per
#: the standard.
_BLOCK_S = 0.400
_HOP_S = 0.100

#: Floor applied to a measurement that would otherwise be -inf (true
#: digital silence) -- keeps `song.yaml` free of a non-finite float, which
#: neither YAML nor a sane UI wants to render.
_SILENCE_FLOOR_LUFS = -70.0
_SILENCE_FLOOR_DBFS = -120.0


def _shelf_and_highpass_coefficients(sample_rate: int) -> tuple[
    tuple[float, float, float, float, float],
    tuple[float, float, float, float, float],
]:
    """The two cascaded biquads of BS.1770-4's K-weighting filter, as
    ``(b0, b1, b2, a1, a2)`` pairs (``a0`` normalised to 1). The standard
    only publishes coefficients for 48kHz; these are the general analog-
    prototype-derived formulas (stage-1 high shelf at ~1681.97Hz/+3.999dB/
    Q=0.7072, stage-2 high-pass at ~38.13Hz/Q=0.5003) re-warped for
    *sample_rate* via the bilinear transform -- the same approach every
    other from-scratch BS.1770 implementation uses, since the published
    48kHz-only coefficients would silently mismeasure anything decoded at
    44.1kHz (which `load_mono_audio` -- ffmpeg's native rate -- often is).
    """
    f0_shelf, g_db, q_shelf = 1681.9744509555319, 3.99843443804936, 0.7071752369554196
    k = math.tan(math.pi * f0_shelf / sample_rate)
    vh = 10.0 ** (g_db / 20.0)
    vb = vh**0.4996667741545416
    denom = 1.0 + k / q_shelf + k * k
    shelf = (
        (vh + vb * k / q_shelf + k * k) / denom,
        2.0 * (k * k - vh) / denom,
        (vh - vb * k / q_shelf + k * k) / denom,
        2.0 * (k * k - 1.0) / denom,
        (1.0 - k / q_shelf + k * k) / denom,
    )

    f0_hp, q_hp = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0_hp / sample_rate)
    denom = 1.0 + k / q_hp + k * k
    highpass = (1.0, -2.0, 1.0, 2.0 * (k * k - 1.0) / denom, (1.0 - k / q_hp + k * k) / denom)

    return shelf, highpass


def _biquad(
    samples: list[float], b0: float, b1: float, b2: float, a1: float, a2: float
) -> list[float]:
    """Direct Form II Transposed, one channel, plain Python floats -- a
    numpy scalar-per-iteration loop is measurably slower than this for a
    tight per-sample recursion, and this has no feedback shape numpy can
    vectorise without scipy (deliberately not a dependency here, see the
    module docstring). One-time cost per bind/analyze, not per playback --
    a few seconds for a full song is an accepted price, same instinct as
    `doctor.py`'s demucs note ("CPU is genuinely slow... not a hang")."""
    out = [0.0] * len(samples)
    z1 = z2 = 0.0
    for i, x in enumerate(samples):
        y = b0 * x + z1
        z1 = b1 * x - a1 * y + z2
        z2 = b2 * x - a2 * y
        out[i] = y
    return out


def _k_weight(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    shelf, highpass = _shelf_and_highpass_coefficients(sample_rate)
    stage1 = _biquad(samples.tolist(), *shelf)
    stage2 = _biquad(stage1, *highpass)
    return np.asarray(stage2, dtype=np.float64)


def _gated_integrated_lufs(weighted: np.ndarray, sample_rate: int) -> float:
    block_frames = int(round(_BLOCK_S * sample_rate))
    hop_frames = int(round(_HOP_S * sample_rate))
    if block_frames <= 0 or weighted.shape[0] < block_frames:
        return _SILENCE_FLOOR_LUFS

    n_blocks = (weighted.shape[0] - block_frames) // hop_frames + 1
    starts = np.arange(n_blocks) * hop_frames
    # Mean square per 400ms block -- mono, so BS.1770's per-channel weighted
    # sum collapses to this one channel's own mean square (its own weight
    # is 1.0).
    mean_squares = np.array(
        [np.mean(weighted[s : s + block_frames] ** 2) for s in starts]
    )
    with np.errstate(divide="ignore"):
        block_lufs = -0.691 + 10.0 * np.log10(mean_squares)

    absolute_pass = mean_squares[block_lufs >= _ABSOLUTE_GATE_LUFS]
    if absolute_pass.size == 0:
        return _SILENCE_FLOOR_LUFS

    with np.errstate(divide="ignore"):
        ungated_lufs = -0.691 + 10.0 * math.log10(float(np.mean(absolute_pass)))
    relative_gate_lufs = ungated_lufs + _RELATIVE_GATE_LU

    gated_pass = mean_squares[
        (block_lufs >= _ABSOLUTE_GATE_LUFS) & (block_lufs >= relative_gate_lufs)
    ]
    if gated_pass.size == 0:
        return _SILENCE_FLOOR_LUFS

    mean_power = float(np.mean(gated_pass))
    if mean_power <= 0.0:
        return _SILENCE_FLOOR_LUFS
    return max(_SILENCE_FLOOR_LUFS, -0.691 + 10.0 * math.log10(mean_power))


def measure(samples: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """``(integrated_lufs, peak_dbfs)`` for one mono *samples* array
    (float, roughly [-1, 1], the same shape `load_mono_audio`/`peaks.py`
    already decode) at *sample_rate*.

    Pure numpy, no optional dependency: K-weight via `_k_weight` (a high
    shelf + a high pass biquad, ITU-R BS.1770-4's own filter shape), then
    400ms gated block loudness (absolute gate -70 LUFS, relative gate -10 LU
    below the ungated mean -- the same two-stage gating the standard
    specifies). Peak is a plain sample-peak in dBFS, no oversampling.

    Both floors clamp at their own silence sentinel rather than returning
    `-inf`, matching `Tempo.bpm`'s own "no field is ever non-finite" shape.
    """
    if samples.size == 0:
        return _SILENCE_FLOOR_LUFS, _SILENCE_FLOOR_DBFS

    weighted = _k_weight(samples.astype(np.float64), sample_rate)
    integrated_lufs = _gated_integrated_lufs(weighted, sample_rate)

    peak = float(np.max(np.abs(samples)))
    peak_dbfs = (
        _SILENCE_FLOOR_DBFS if peak <= 0.0
        else max(_SILENCE_FLOOR_DBFS, 20.0 * math.log10(peak))
    )

    return integrated_lufs, peak_dbfs


def gain_db(
    integrated_lufs: float,
    peak_dbfs: float,
    *,
    target_lufs: float = TARGET_LUFS,
    peak_ceiling_dbfs: float = PEAK_CEILING_DBFS,
) -> float:
    """The playback gain, in dB, that brings *integrated_lufs* toward
    *target_lufs* without pushing *peak_dbfs* past *peak_ceiling_dbfs*.

    ``0.0`` for the "never measured" sentinel (``integrated_lufs >= 0.0`` --
    real program material is always negative LUFS, the same sentinel trick
    `Tempo.bpm`'s own `bpm <= 0` check already uses). Otherwise
    ``min(target - measured, ceiling - peak)``, clamped to
    ``+-GAIN_CLAMP_DB`` as a sanity bound on the result, not a policy choice.
    """
    if integrated_lufs >= 0.0:
        return 0.0
    wanted = target_lufs - integrated_lufs
    headroom = peak_ceiling_dbfs - peak_dbfs
    return max(-GAIN_CLAMP_DB, min(GAIN_CLAMP_DB, wanted, headroom))
