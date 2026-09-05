"""The rehearsal click: a tone per beat, generated from the tempo grid.

docs/00-spec.md:213: "An optional click on the lead-in (and, if wanted,
throughout), generated from the tempo grid. `rambass-live/src/rambass/click.py`
already does this." Woodshed only ever needs that one stem -- there is no
drumstick count-in here (that repo's ``sticks.wav`` is a different, musical
part; Woodshed's spec never asks for one) -- so only the small numpy DSP
helpers are lifted verbatim, not the sibling's ``Timeline``/``render_sticks``
machinery.

**Why not lift `Timeline`.** `rambass-live`'s grid can change time signature
or tempo bar by bar; Woodshed's is one ``bpm`` plus one ``grid_offset_s`` for
the whole song (CLAUDE.md's seconds-not-bars invariant -- a tempo can change
there, never here), so the bar-and-beat walk `Timeline` exists for collapses
to arithmetic anyone can read in a few lines: see `_beat_times`.

**Numpy only, deliberately** (CLAUDE.md's "Layering"): this module takes
``bpm`` / ``grid_offset_s`` / ``time_signature`` as plain values, not a
``Tempo`` object -- `analyze.py` sits behind the librosa line and nothing
below that line may import it, even for a function as pure as
`beat_grid`, so the tiny beat-position walk is duplicated here rather than
shared. Degrades exactly the way `analyze.beat_grid` does: ``bpm <= 0``
means silence, never a divide-by-zero or an exception.
"""

from __future__ import annotations

import numpy as np

#: Tonal click pitches. High = beat 1.
DOWNBEAT_HZ = 1600.0
BEAT_HZ = 1000.0


def _beat_times(bpm: float, grid_offset_s: float, duration_s: float) -> list[float]:
    """Every beat position in ``[0, duration_s)``. Mirrors `analyze.beat_grid`
    exactly (same degrade rule, same anchor-folding) -- kept here rather than
    imported, see the module docstring."""
    if bpm <= 0 or duration_s <= 0:
        return []
    period = 60.0 / bpm
    first = grid_offset_s % period
    beats = []
    t = first
    while t < duration_s:
        beats.append(t)
        t += period
    return beats


def _beats_per_bar(time_signature: str) -> int:
    """The numerator of e.g. ``"4/4"``, ``"3/4"``, ``"6/8"``.

    A garbled or missing value degrades to 4 rather than raising -- a
    malformed `time_signature` field should cost the accent pattern, not
    the whole click.
    """
    try:
        numerator, _, _ = time_signature.partition("/")
        return max(1, int(numerator))
    except (ValueError, AttributeError):
        return 4


def render_click(
    bpm: float,
    grid_offset_s: float,
    time_signature: str,
    duration_s: float,
    *,
    sample_rate: int = 48000,
    accent_downbeat: bool = True,
    click_ms: float = 28.0,
    level_db: float = -9.0,
    accent_db: float = -4.0,
) -> np.ndarray:
    """The click track over ``[0, duration_s)``: one tone per beat.

    ``bpm <= 0`` (or absent, see `manifest.Tempo`'s degrade default) renders
    silence at the right length rather than refusing -- CLAUDE.md: "if
    tempo.bpm is 0 or absent, ... no click." Gain is independent of the
    music (G2's job is mixing it in; this only ever renders the click
    stem on its own).
    """
    length = max(1, int(duration_s * sample_rate) + 1)
    buffer = np.zeros(length, dtype=np.float32)
    beats = _beat_times(bpm, grid_offset_s, duration_s)
    if not beats:
        return buffer

    beats_per_bar = _beats_per_bar(time_signature)
    for i, t in enumerate(beats):
        downbeat = accent_downbeat and i % beats_per_bar == 0
        tone = _tick(DOWNBEAT_HZ if downbeat else BEAT_HZ, click_ms, sample_rate)
        tone = tone * _db(accent_db if downbeat else level_db)
        index = int(t * sample_rate)
        end = min(len(buffer), index + len(tone))
        if end > index:
            buffer[index:end] += tone[: end - index]

    return _guard(buffer)


# ── DSP helpers, lifted verbatim from rambass-live/src/rambass/click.py ──
def _tick(frequency: float, click_ms: float, sample_rate: int) -> np.ndarray:
    """A short sine burst with a fast decay -- cuts through a mix."""
    length = max(4, int(click_ms / 1000.0 * sample_rate))
    t = np.arange(length, dtype=np.float32) / sample_rate
    envelope = np.exp(-t * (4000.0 / click_ms))
    # A touch of second harmonic makes it audible through in-ear monitors.
    wave = np.sin(2 * np.pi * frequency * t) + 0.3 * np.sin(4 * np.pi * frequency * t)
    return (wave * envelope).astype(np.float32)


def _biquad_bandpass(
    signal: np.ndarray, sample_rate: int, frequency: float, q: float
) -> np.ndarray:
    """RBJ-cookbook bandpass (constant skirt gain), applied in place of scipy.

    Not used by `render_click` above -- Woodshed has no drumstick count-in
    to voice -- but lifted per the plan alongside the other four helpers,
    since it is one of the small DSP primitives the sibling built this way
    specifically so the click stays scipy-free.
    """
    w0 = 2.0 * np.pi * frequency / sample_rate
    alpha = np.sin(w0) / (2.0 * q)
    b0, b1, b2 = q * alpha, 0.0, -q * alpha
    a0, a1, a2 = 1.0 + alpha, -2.0 * np.cos(w0), 1.0 - alpha
    b0, b1, b2 = b0 / a0, b1 / a0, b2 / a0
    a1, a2 = a1 / a0, a2 / a0

    out = np.zeros_like(signal)
    x1 = x2 = y1 = y2 = 0.0
    for index, sample in enumerate(signal):
        value = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, sample
        y2, y1 = y1, value
        out[index] = value
    return out


def _one_pole_lowpass(signal: np.ndarray, sample_rate: int, cutoff: float) -> np.ndarray:
    """Gentle 6 dB/octave roll-off. Not used by `render_click` (see
    `_biquad_bandpass`'s note) but lifted for the same reason."""
    alpha = float(np.exp(-2.0 * np.pi * cutoff / sample_rate))
    out = np.zeros_like(signal)
    previous = 0.0
    for index, sample in enumerate(signal):
        previous = (1.0 - alpha) * sample + alpha * previous
        out[index] = previous
    return out


def _guard(buffer: np.ndarray) -> np.ndarray:
    """Peak-limit to -0.09 dBFS, never louder. Silence stays silence."""
    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > 0.99:
        buffer = buffer * (0.99 / peak)
    return buffer.astype(np.float32)


def _db(value: float) -> float:
    return float(10.0 ** (value / 20.0))
