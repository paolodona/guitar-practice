"""Tempo-fitting maths: refine a coarse BPM, measure wander, find the grid anchor.

This is the pure-numpy half of tempo analysis, split out of `analyze.py`
specifically so it can be imported -- and tested -- with no librosa installed
(layering rule in CLAUDE.md: only `analyze.py` and `render.py` may import a
heavy or external dependency). `analyze.py`'s `detect_tempo` produces the
onset envelope / onset times this module consumes; everything downstream of
those onsets lives here.

Lifted verbatim from `rambass-live/src/rambass/analyze.py` (that repo settles
on one fixed tempo per song and reports how far a take wandered from it;
Woodshed reuses the same measurements for a different purpose -- refining a
librosa tempo guess into something a beat grid can be drawn from). See that
module's docstring for why a tempogram bin centre is not a measurement.
"""

from __future__ import annotations

import numpy as np


def _comb(env: np.ndarray, times: np.ndarray, bpms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Magnitude and phase of the onset envelope at each candidate pulse rate.

    This is a DFT evaluated at arbitrary frequencies rather than FFT bins, which
    is the whole point: we need a tempo resolution far finer than any bin grid.
    Batched so a fine search over a long song does not allocate a huge matrix.
    """
    env = np.asarray(env, dtype=float)
    env = env - env.mean()
    times = np.asarray(times, dtype=float)
    bpms = np.atleast_1d(np.asarray(bpms, dtype=float))
    mag = np.empty(len(bpms))
    phase = np.empty(len(bpms))
    for start in range(0, len(bpms), 64):
        chunk = bpms[start : start + 64, None] / 60.0
        z = (env[None, :] * np.exp(-2j * np.pi * chunk * times[None, :])).sum(axis=1)
        mag[start : start + 64] = np.abs(z)
        phase[start : start + 64] = np.angle(z)
    return mag, phase


def refine_tempo(
    env: np.ndarray,
    times: np.ndarray,
    coarse_bpm: float,
    *,
    span: float = 0.04,
    coarse_step: float = 0.01,
    fine_step: float = 0.0005,
) -> float:
    """Sharpen a coarse tempo estimate against the recording.

    *span* is fractional, and has to be wider than a tempogram bin is: the
    coarse estimate can be a whole bin out, which near 117 BPM is 5 BPM, so the
    default searches +-4%. Beyond that lies the next metrical level and the
    search would be free to lock onto half or double time.
    """
    times = np.asarray(times, dtype=float)
    if coarse_bpm <= 0 or len(times) < 8:
        return float(coarse_bpm)
    # Refining needs several pulses to average over; a fragment shorter than
    # about eight beats has nothing to say and the peak would be noise.
    if times[-1] - times[0] < 8 * 60.0 / coarse_bpm:
        return float(coarse_bpm)
    lo, hi = coarse_bpm * (1 - span), coarse_bpm * (1 + span)
    grid = np.arange(lo, hi, coarse_step)
    mag, _ = _comb(env, times, grid)
    peak = float(grid[int(np.argmax(mag))])
    fine = np.arange(peak - 2 * coarse_step, peak + 2 * coarse_step, fine_step)
    mag, _ = _comb(env, times, fine)
    return float(fine[int(np.argmax(mag))])


def pulse_wander(
    env: np.ndarray,
    times: np.ndarray,
    bpm: float,
    *,
    window: float = 20.0,
) -> list[tuple[float, float]]:
    """Where the recording's pulse sits against a perfectly even grid.

    One measurement per *window* seconds, in milliseconds, mean removed -- so
    the numbers read as "the band was here relative to the click", not as
    absolute phase. **Positive means behind the grid**, which is worth stating
    because the phase of the transform runs the other way and the sign is easy
    to flip by accident.
    """
    times = np.asarray(times, dtype=float)
    env = np.asarray(env, dtype=float)
    if bpm <= 0 or len(times) < 8:
        return []
    period = 60.0 / bpm
    env = np.maximum(env - np.median(env), 0.0)
    mids: list[float] = []
    phases: list[float] = []
    start = float(times[0])
    while start + window <= float(times[-1]) + 1e-9:
        inside = (times >= start) & (times < start + window)
        if inside.sum() >= 8:
            z = (env[inside] * np.exp(-2j * np.pi * times[inside] / period)).sum()
            mids.append(start + window / 2)
            phases.append(float(np.angle(z)))
        start += window
    if not mids:
        return []
    # Wrapped into +-half a beat, deliberately **not** unwrapped. Unwrapping
    # invents whole-beat jumps out of a noisy window, and a whole beat of
    # "wander" is not a thing a band does -- it is a measurement artifact.
    centred = np.array(phases)
    centred = centred - _circular_mean(centred)
    centred = (centred + np.pi) % (2 * np.pi) - np.pi
    offsets = -centred / (2 * np.pi) * period * 1000.0
    return [(float(m), float(ms)) for m, ms in zip(mids, offsets, strict=True)]


def find_grid_anchor(
    onsets,
    bpm: float,
    *,
    subdivision: int = 4,
    tolerance: float = 0.025,
    beat_tolerance: float = 0.030,
    step: float = 0.002,
    tie_band: float = 0.02,
) -> tuple[float, dict]:
    """Where bar 1 beat 1 sits, measured rather than guessed.

    The measurement has two stages, and the second is the one that matters:

    1. Sweep the anchor across a whole beat and score how many onsets land on a
       subdivision. This alone is *not enough*: every one of the `subdivision`
       positions within the beat scores nearly identically, because a pattern
       shifted by a sixteenth is still perfectly on sixteenths.
    2. Break that tie on how many onsets land on a **beat**, which is the
       question a coarser grid can answer and a finer one cannot.

    Returns ``(anchor, report)``, the anchor in ``[0, beat)``. Which *beat* is
    beat 1 remains a musical question -- this only measures the phase within
    the beat.
    """
    onsets = np.asarray(onsets, dtype=float)
    beat = 60.0 / bpm if bpm > 0 else 0.0
    if beat <= 0 or len(onsets) < 12:
        return 0.0, {"anchors": 0}
    grid_step = beat / subdivision
    candidates = np.arange(0.0, beat, step)
    grid = np.empty(len(candidates))
    on_beat = np.empty(len(candidates))
    for i, anchor in enumerate(candidates):
        off = np.abs(((onsets - anchor + grid_step / 2) % grid_step) - grid_step / 2)
        grid[i] = float((off < tolerance).mean())
        offb = np.abs(((onsets - anchor + beat / 2) % beat) - beat / 2)
        on_beat[i] = float((offb < beat_tolerance).mean())

    best = grid.max()
    tied = grid >= best - tie_band
    scored = np.where(tied, on_beat, -1.0)
    winner = int(np.argmax(scored))
    # Every anchor within *tolerance* of the right one scores identically, so the
    # winner is a plateau and taking its first element biases the anchor early by
    # up to the tolerance. Take the middle, on the circle, since the plateau can
    # straddle the end of the beat.
    plateau = scored >= scored[winner] - 1e-9
    angles = 2 * np.pi * candidates[plateau] / beat
    centre = float(np.angle(np.exp(1j * angles).mean()) / (2 * np.pi) * beat) % beat
    report = {
        "anchor": round(centre, 4),
        "on_subdivision": round(float(grid[winner]), 3),
        "on_beat": round(float(on_beat[winner]), 3),
        "chance_subdivision": round(min(1.0, 2 * tolerance / grid_step), 3),
        "chance_beat": round(min(1.0, 2 * beat_tolerance / beat), 3),
        "tied_candidates": int(tied.sum()),
        "runner_up_on_beat": round(float(_runner_up(candidates, scored, centre, beat)), 3),
    }
    return centre, report


def _runner_up(candidates: np.ndarray, scored: np.ndarray, winner: float, beat: float) -> float:
    """Best on-beat score among tied anchors that are a *different* grid line.

    "Runner-up" has to mean a genuinely different answer. The anchors either
    side of the winner score the same and are the same answer; the number
    worth reporting is how well the next *subdivision* over would have done.
    """
    apart = np.abs(((candidates - winner + beat / 2) % beat) - beat / 2) > 0.03
    others = scored[apart]
    return float(others.max()) if others.size else 0.0


def grid_confidence(times, bpm: float, *, tolerance: float = 0.030) -> dict:
    """How well a finished part sits on beats, against what chance would give.

    Stated at the **beat** level deliberately: a part displaced by a whole
    subdivision scores perfectly on subdivisions and terribly here. Always
    check one level coarser than you quantised to.
    """
    times = np.asarray(times, dtype=float)
    beat = 60.0 / bpm if bpm > 0 else 0.0
    if beat <= 0 or len(times) < 12:
        return {"hits": int(len(times)), "ratio": 0.0}
    off = np.abs(((times + beat / 2) % beat) - beat / 2)
    share = float((off < tolerance).mean())
    chance = min(1.0, 2 * tolerance / beat)
    return {
        "hits": int(len(times)),
        "on_beat": round(share, 3),
        "chance": round(chance, 3),
        "ratio": round(share / chance, 2) if chance else 0.0,
    }


def _circular_mean(angles: np.ndarray) -> float:
    """Mean of angles, done on the circle so it survives the +-pi seam."""
    return float(np.angle(np.exp(1j * np.asarray(angles, dtype=float)).mean()))
