"""Where the beats of ONE SECTION actually are -- the metronome's maths.

Pure numpy, no librosa, no ffmpeg, no audio device: `beats.py` decodes a
span and hands the samples here, and everything that decides where a click
lands is testable without any of that (CLAUDE.md's layering rule, and the
same split `tempofit.py` already exists to make).

**Why this is per section rather than per song.** Measured 2026-09-12 on
`songs/tutti-in-fila`, the recording Paolo named as "not played in time":
the song stores 116.04 BPM, while fitting each section on its own gives
116.09 (full solo), 116.59 (tapping) and 117.42 (first run) -- a 1.2%
spread. A grid anchored at the song's own t=0 and extended to a section
200s in arrives about 0.8s late. There is nothing subtle about that; it is
simply a different question from the one `analyze.detect_tempo` answers.

**Why the phase correction exists.** Within one 88s section the pulse
wobbles +-40-60ms against a constant grid at that section's own tempo --
and that wobble is the BAND, not the analysis: four independent FFT
parameterisations (n_fft 1024-4096, hop 128-256) agree on the wobble curve
to r=+1.00, rms 2-5ms. At 40% speed a 60ms error is 150ms of audible gap,
so the click follows the curve. It follows it SLOWLY (a piecewise-linear
correction sampled in ~4s windows), which is the difference between
tracking the band and chasing every syncopation.

**Why not librosa's beat tracker.** It returned ONE beat on the 11s "First
run" section: dynamic-programming beat tracking needs a long span, and a
drill is short. The comb -- `tempofit._comb`, a DFT evaluated at arbitrary
tempi, already this repo's chosen instrument for exactly this reason --
holds to 0.02 BPM across parameterisations on the same 11 seconds. So the
metronome needs no dependency that `uv sync` does not already install.

**Why a bpm prior.** Eighth notes are as periodic as quarter notes, and a
click at double time is wrong in a way that sounds plausible. The song's
own `tempo.bpm` settles which metrical level was meant -- but only that:
the section still gets its own tempo, measured here, because being handed
116.04 for a section that is playing 117.42 is the bug this module exists
to fix. A prior that is not a simple multiple of the measured pulse is
ignored outright (it is not describing this music).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# `_comb` is private to tempofit by name only -- it is the repo's own
# tempo instrument (a DFT evaluated at arbitrary pulse rates rather than
# FFT bins), lifted there from rambass-live, and this module is the second
# caller it was always going to have. Importing it beats copying 20 lines
# of batched DFT into a second home where the two could drift apart.
from woodshed.tempofit import _comb, refine_tempo

__all__ = [
    "ENVELOPE_N_FFT",
    "ENVELOPE_HOP",
    "BeatFit",
    "onset_envelope",
    "fit_beats",
]

#: STFT geometry for the onset envelope. 1024/256 at 22050Hz is a 46ms
#: window every 11.6ms -- fine enough that the envelope's own resolution is
#: never the limiting error (the band's +-40ms wobble is four hops wide),
#: coarse enough that an 88-second section is a few thousand frames.
#: MEASURED: n_fft 1024-4096 and hop 128-256 all agree on the fitted tempo
#: to 0.02 BPM and on the wobble curve to rms 2-5ms, so this choice is
#: cheap rather than delicate.
ENVELOPE_N_FFT = 1024
ENVELOPE_HOP = 256

#: The pulse sweep when there is no usable prior. Wide enough for anything
#: a band plays, and it is a PULSE rate, not a tempo: a song at 75 BPM with
#: eighth-note movement is found here at 150 and divided back down.
MIN_PULSE_BPM = 60.0
MAX_PULSE_BPM = 200.0
SWEEP_STEP_BPM = 0.25

#: Metrical levels a pulse can sit at relative to the song's own tempo:
#: half notes, the beat, eighths, triplets, sixteenths.
#:
#: **3/2 and 2/3 are deliberately absent**, and that is a measured
#: decision, not tidiness. Every pulse train has harmonics, so a prior
#: sitting 1.5x off the truth finds one: with a stale prior of 155 against
#: music actually pulsing at 116, the candidate 155*1.5 = 232.5 lands
#: exactly on the 116 pulse's own second harmonic, and the fit reported a
#: confident 154.7 BPM (tests/test_beatfit.py names this case). A genuine
#: 3/2 relationship is a change of METER, not of level -- rare enough to
#: be worth losing, in exchange for a whole class of plausible-sounding
#: wrong clicks becoming impossible.
_METRICAL_RATIOS = (1 / 3, 1 / 2, 1.0, 2.0, 3.0, 4.0)

#: A candidate at one of those levels has to actually hold energy: below
#: this share of the unguided peak's own magnitude, the prior is not
#: describing this music and is dropped.
_PRIOR_MIN_SHARE = 0.25

#: How much better the bare sweep has to fit the audio than the song's own
#: tempo before it is preferred (see `_choose_pulse`). Not a tie-break so
#: much as a statement of what each is: the prior knows the metrical level
#: and is usually the same rate anyway, so it wins level-for-level ties,
#: and loses when the audio disagrees by a fifth or more.
_SWEEP_PENALTY = 1.25

#: A section shorter than this has nothing to fit -- fewer than about eight
#: pulses, which is `refine_tempo`'s own floor for having anything to say.
MIN_SPAN_S = 3.0

#: Phase-correction windows. Long enough to hold ~8 pulses so the local
#: phase is a measurement rather than a guess, short enough to follow a
#: band over a section.
WINDOW_S = 4.0
MIN_PULSES_PER_WINDOW = 8

#: A window has to carry enough onset energy, and enough of it at the
#: pulse rate, for its phase to be a measurement rather than a coin toss.
#:
#: MEASURED 2026-09-12 on tutti-in-fila. Across the solo, every window
#: holds 0.6-1.2x the median window's energy and reads a pulse coherence
#: (|comb| / total energy in the window) of 0.09-0.48. Across the song's
#: first 90 seconds -- a sparse intro -- the first six windows hold 0.1x
#: the median energy and their coherence swings 0.05-0.58 at random.
#: Following those windows is what made the correction wander 333ms there
#: and land the click FURTHER from the music than a plain grid: 144ms mean
#: residual against the song grid's 80ms. Windows below either floor are
#: dropped and the correction is interpolated straight across them, which
#: is the honest reading -- nothing was measured in that stretch, so the
#: click should not move in it.
MIN_WINDOW_ENERGY = 0.35
MIN_WINDOW_COHERENCE = 0.10

#: How far the correction may move between one window centre and the next,
#: as a fraction of the pulse period. The wobble measured on real audio is
#: under a fifth of a beat; anything past half a beat is not a band moving,
#: it is the phase estimate jumping a whole pulse, which is exactly the
#: artifact `tempofit.pulse_wander` refuses to unwrap into existence.
MAX_STEP_FRACTION = 0.35


@dataclass(frozen=True)
class BeatFit:
    """Where the beats are, and how much the answer is worth.

    `beats` are in whatever clock `times` was in -- absolute SOURCE seconds
    when the caller decoded a span out of the middle of a recording, which
    is what `beats.py` always does. Nothing here rebases them.
    """

    bpm: float
    beats: list[float] = field(default_factory=list)
    #: Peak-to-peak of the phase correction, in milliseconds: how far the
    #: band moved against its own average tempo across this section.
    wander_ms: float = 0.0
    #: How far the winning pulse rate stands above the rest of the sweep
    #: (peak magnitude / median magnitude). A relative peak-to-background
    #: ratio, deliberately NOT a probability -- this tool does not invent
    #: measurements it cannot make.
    pulse_ratio: float = 0.0
    #: The rate the pulse was actually measured at, before any division
    #: down to the prior's metrical level. Equal to `bpm` unless a prior
    #: moved the level.
    pulse_bpm: float = 0.0


def onset_envelope(
    samples: np.ndarray,
    sample_rate: int,
    *,
    n_fft: int = ENVELOPE_N_FFT,
    hop: int = ENVELOPE_HOP,
) -> tuple[np.ndarray, np.ndarray]:
    """Spectral flux: how much the spectrum BRIGHTENED at each frame.

    Half-wave-rectified difference of a log-magnitude STFT, summed over
    bins, median removed. This is the same quantity `librosa.onset.
    onset_strength` produces and the same one `tempofit` was written to
    consume -- computed here in twenty lines of numpy so the metronome
    needs no optional dependency at all.

    Returns `(env, times)` with `times` at frame CENTRES, in seconds from
    the first sample. A caller analysing a span cut out of a recording adds
    the span's own start to put both in source seconds.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if samples.size < n_fft:
        return np.zeros(0), np.zeros(0)

    frames = 1 + (samples.size - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    # One strided view of every frame, windowed and transformed in a single
    # batch -- a Python loop over frames is the only slow way to do this.
    index = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    spectrum = np.abs(np.fft.rfft(samples[index] * window, axis=1))
    # log1p rather than raw magnitude: a snare 30dB above the mix floor
    # must not count thirty times a quiet hi-hat, or only the loudest
    # instrument in the section is ever heard by the fit.
    log_magnitude = np.log1p(10.0 * spectrum)
    flux = np.diff(log_magnitude, axis=0, prepend=log_magnitude[:1])
    env = np.maximum(flux, 0.0).sum(axis=1)
    env = np.maximum(env - np.median(env), 0.0)
    times = (np.arange(frames) * hop + n_fft / 2) / sample_rate
    return env.astype(float), times


def _sweep(env: np.ndarray, times: np.ndarray) -> tuple[float, float]:
    """Coarse pulse rate over the whole search range, and how far it
    stands above the background."""
    grid = np.arange(MIN_PULSE_BPM, MAX_PULSE_BPM, SWEEP_STEP_BPM)
    magnitude, _phase = _comb(env, times, grid)
    peak = int(np.argmax(magnitude))
    background = float(np.median(magnitude))
    ratio = float(magnitude[peak] / background) if background > 0 else 0.0
    return float(grid[peak]), ratio


def _pulse_phase(env: np.ndarray, times: np.ndarray, bpm: float) -> float:
    """Where a pulse at *bpm* sits, as an offset in [0, period).

    For an impulse train at ``t_k = t0 + k/f`` the comb's own phase is
    ``-2*pi*f*t0``, so this inverts exactly that -- the same sign
    convention `tempofit.pulse_wander` documents ("positive means behind
    the grid", and the transform runs the other way).
    """
    period = 60.0 / bpm
    _magnitude, phase = _comb(env, times, np.array([bpm]))
    return float((-phase[0] / (2 * np.pi) * period) % period)


def _pulse_with_prior(
    env: np.ndarray,
    times: np.ndarray,
    prior: float,
    sweep_bpm: float,
    sweep_ratio: float,
) -> tuple[float, float]:
    """Pick the pulse rate, given the song's own tempo as a prior.

    Returns `(pulse_bpm, ratio_to_prior)`. Each simple multiple of the
    prior is evaluated directly rather than searched for, which is what
    lets an eighth-note pulse at 232 be found on a song whose tempo is 116
    -- a sweep whose ceiling is a TEMPO ceiling would never look there.

    The prior only decides the metrical LEVEL. The rate itself is always
    measured, because a section playing 117.42 against a song-level 116.04
    is exactly the thing this module exists to fix. And a prior whose
    multiples hold no more energy than the background is not describing
    this music at all (a stale tempo, a wrong song): it is dropped in
    favour of the unguided sweep rather than forced onto the audio.
    """
    candidates = np.array(
        [prior * r for r in _METRICAL_RATIOS if MIN_PULSE_BPM <= prior * r <= 400.0]
    )
    if candidates.size == 0:
        return sweep_bpm, 1.0
    magnitude, _phase = _comb(env, times, candidates)
    winner = int(np.argmax(magnitude))
    # How the winner compares with the unguided peak: `sweep_ratio` is that
    # peak over the sweep's own median, so dividing by it puts this
    # candidate on the same background-relative scale.
    reference, _ = _comb(env, times, np.array([sweep_bpm]))
    if reference[0] > 0 and magnitude[winner] / reference[0] < _PRIOR_MIN_SHARE:
        return sweep_bpm, 1.0
    pulse = refine_tempo(env, times, float(candidates[winner]))
    ratio = float(candidates[winner]) / prior
    # Refinement moves the rate by a few tenths; the level it belongs to
    # does not move with it.
    return pulse, ratio


def _phase_corrections(
    env: np.ndarray,
    times: np.ndarray,
    bpm: float,
    anchor: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-window phase of the pulse, relative to the even grid at *bpm*.

    Returns `(centres, offsets_s)`. Offsets are wrapped into +-half a
    period of the PREVIOUS window rather than of zero, so a band that has
    genuinely moved a third of a beat over a minute is followed, while a
    whole-pulse jump -- always an artifact, never a band -- cannot appear.
    """
    period = 60.0 / bpm
    window = max(WINDOW_S, MIN_PULSES_PER_WINDOW * period)
    span = float(times[-1] - times[0])
    if span < 2 * window:
        return np.zeros(0), np.zeros(0)

    measured: list[tuple[float, float, float, float]] = []  # centre, phase, energy, coherence
    start = float(times[0])
    while start + window <= float(times[-1]) + 1e-9:
        inside = (times >= start) & (times < start + window)
        if inside.sum() >= MIN_PULSES_PER_WINDOW:
            local = (env[inside] * np.exp(-2j * np.pi * times[inside] / period)).sum()
            energy = float(env[inside].sum())
            coherence = abs(local) / energy if energy > 0 else 0.0
            # Phase measured against the same t=0 the anchor is on, so the
            # offset reads as "how far this window is from the even grid".
            phase = (-float(np.angle(local)) / (2 * np.pi) * period - anchor) % period
            measured.append((start + window / 2, phase, energy, coherence))
        start += window / 2
    if not measured:
        return np.zeros(0), np.zeros(0)

    median_energy = float(np.median([row[2] for row in measured]))
    centres: list[float] = []
    offsets: list[float] = []
    previous = 0.0
    for centre, phase, energy, coherence in measured:
        if median_energy > 0 and energy < MIN_WINDOW_ENERGY * median_energy:
            continue
        if coherence < MIN_WINDOW_COHERENCE:
            continue
        # Fold into +-half a period AROUND the previous KEPT window: a
        # whole-pulse jump is always an artifact, never a band (the rule
        # tempofit.pulse_wander states and refuses to unwrap away).
        here = previous + ((phase - previous + period / 2) % period) - period / 2
        step = np.clip(here - previous, -MAX_STEP_FRACTION * period,
                       MAX_STEP_FRACTION * period)
        previous = previous + float(step)
        centres.append(centre)
        offsets.append(previous)
    if not centres:
        return np.zeros(0), np.zeros(0)
    corrections = np.asarray(offsets, dtype=float)
    # Mean removed: the anchor already places the grid, and a constant
    # component here would only fight it.
    return np.asarray(centres, dtype=float), corrections - corrections.mean()


def _choose_pulse(
    env: np.ndarray,
    times: np.ndarray,
    candidates: list[tuple[float, float]],
) -> tuple[float, float, float, np.ndarray, np.ndarray]:
    """Pick the pulse rate that needs the LEAST correction, and return the
    correction it needs.

    The two candidates are the unguided sweep's own refined pulse and the
    one the song's tempo points at; usually they are the same number at
    two metrical levels, and usually either would do. When they disagree
    about the RATE, this is the tiebreak, and it is the one measurement
    that asks the question we actually care about: **which grid does the
    band stay on?** A tempo that is 0.5% out has to be dragged a whole
    beat across a 90-second section, and "drag per second" makes that
    visible where a comb magnitude does not.

    FOUND BY MEASUREMENT 2026-09-12: over tutti-in-fila's first 90s the
    prior's eighth-note candidate refined to 233.07 (a beat rate of
    116.53) where the sweep said 115.97. Both look plausible; the first
    needed 319ms of correction and put the click 131ms from the music on
    average -- worse than no fit at all -- and the second needed 80ms.
    """
    best: tuple[float, float, float, np.ndarray, np.ndarray] | None = None
    best_score = float("inf")
    for index, (pulse_bpm, level) in enumerate(candidates):
        if pulse_bpm <= 0:
            continue
        anchor = _pulse_phase(env, times, pulse_bpm)
        centres, corrections = _phase_corrections(env, times, pulse_bpm, anchor)
        if centres.size >= 2:
            # RMS of the correction: how far the click has to be MOVED from
            # an even grid, not how far it travels getting there. The
            # distinction decides this: a tempo that is 0.5% out ramps
            # smoothly to a huge displacement (small path length per
            # second, enormous rms), while a right tempo over a band that
            # breathes zigzags a little around zero (large path length,
            # small rms). Path length picked the wrong one.
            score = float(np.sqrt(np.mean(corrections**2)))
        else:
            # No usable windows: no evidence either way, so this candidate
            # neither wins on a score it did not earn nor loses to one.
            score = float("inf")
        # The song's own tempo is the last candidate, and it is information:
        # it only loses to the bare sweep when the audio clearly disagrees,
        # never on a tie (usually the two ARE the same rate at two metrical
        # levels, and then the prior is the one that also knows the level).
        weighted = score if index == len(candidates) - 1 else score * _SWEEP_PENALTY
        if weighted < best_score or best is None:
            best_score = weighted
            best = (pulse_bpm, level, anchor, centres, corrections)
    if best is None:
        return 0.0, 1.0, 0.0, np.zeros(0), np.zeros(0)
    if best_score == float("inf"):
        # Every candidate was unmeasurable (a section too short to hold two
        # windows). Prefer the LAST one, which is the prior's if a prior
        # was given: a stored tempo beats a bare sweep when neither can be
        # checked against the audio.
        pulse_bpm, level = candidates[-1]
        anchor = _pulse_phase(env, times, pulse_bpm) if pulse_bpm > 0 else 0.0
        return pulse_bpm, level, anchor, np.zeros(0), np.zeros(0)
    return best


def _correction_at(
    at: np.ndarray,
    centres: np.ndarray,
    corrections: np.ndarray,
    period: float,
) -> np.ndarray:
    """The correction curve sampled at *at*: linear between window centres,
    and linearly EXTRAPOLATED past the first and last.

    Flat extrapolation (np.interp's own edge behaviour) is the wrong
    default here, and measurably so: the first and last half-window of a
    section get no correction at all, and those are exactly the beats
    around the loop seam, where a click that disagrees with the band is
    most obvious. MEASURED on the synthetic 116->121 BPM drift in
    tests/test_beatfit.py: flat edges left the worst beat 46ms out, linear
    edges 26ms. Clamped to one step's worth beyond the edge value so a
    noisy slope at the end of a section cannot run away.
    """
    out = np.interp(at, centres, corrections)
    if centres.size < 2:
        return out
    limit = MAX_STEP_FRACTION * period
    lead = (corrections[1] - corrections[0]) / (centres[1] - centres[0])
    tail = (corrections[-1] - corrections[-2]) / (centres[-1] - centres[-2])
    before = at < centres[0]
    after = at > centres[-1]
    out[before] = corrections[0] + np.clip(
        lead * (at[before] - centres[0]), -limit, limit
    )
    out[after] = corrections[-1] + np.clip(
        tail * (at[after] - centres[-1]), -limit, limit
    )
    return out


def fit_beats(
    env: np.ndarray,
    times: np.ndarray,
    *,
    bpm_prior: float = 0.0,
) -> BeatFit:
    """Fit the beats of one section from its onset envelope.

    `times` carries the clock: pass absolute source seconds and the beats
    come back in absolute source seconds. Degrades to an empty fit --
    never an exception -- on silence, on a span too short to measure, and
    on anything else with no pulse in it: no beats means no click, which
    is the honest answer and the one `beat_grid` already gives for a
    missing tempo.
    """
    env = np.asarray(env, dtype=float)
    times = np.asarray(times, dtype=float)
    if env.size < 2 or times.size != env.size:
        return BeatFit(bpm=0.0)
    if float(env.max()) <= 0.0:
        return BeatFit(bpm=0.0)
    if float(times[-1] - times[0]) < MIN_SPAN_S:
        return BeatFit(bpm=0.0)

    coarse, pulse_ratio = _sweep(env, times)
    candidates = [(refine_tempo(env, times, coarse), 1.0)]
    if bpm_prior > 0:
        candidates.append(_pulse_with_prior(env, times, bpm_prior, coarse, pulse_ratio))
    pulse_bpm, level, anchor, centres, corrections = _choose_pulse(env, times, candidates)
    if pulse_bpm <= 0:
        return BeatFit(bpm=0.0)

    bpm = pulse_bpm / level
    # Everything below works at the PULSE rate, where the energy actually
    # is, and only decimates to the beat at the very end -- measuring the
    # phase of a half-rate grid in music that has no half-rate component
    # would be measuring noise.
    period = 60.0 / pulse_bpm

    first = float(times[0]) + ((anchor - float(times[0])) % period)
    nominal = np.arange(first, float(times[-1]) + period / 2, period)
    if nominal.size == 0:
        return BeatFit(bpm=0.0)
    if centres.size:
        pulses = nominal + _correction_at(nominal, centres, corrections, period)
        wander_ms = float(corrections.max() - corrections.min()) * 1000.0
    else:
        pulses = nominal
        wander_ms = 0.0

    beats = _to_beat_level(pulses, pulse_bpm, bpm, env, times)
    # Strictly increasing is a contract the scheduler relies on: a beat
    # that went backwards would be scheduled in the past, and Web Audio
    # plays a node scheduled in the past IMMEDIATELY -- an audible flam
    # rather than a silent bug.
    beats = [float(b) for b in beats]
    monotonic = [b for i, b in enumerate(beats) if i == 0 or b > beats[i - 1]]

    return BeatFit(
        bpm=round(float(bpm), 3),
        beats=[round(b, 4) for b in monotonic],
        wander_ms=round(wander_ms, 1),
        pulse_ratio=round(float(pulse_ratio), 2),
        pulse_bpm=round(float(pulse_bpm), 3),
    )


def _to_beat_level(
    pulses: np.ndarray,
    pulse_bpm: float,
    bpm: float,
    env: np.ndarray,
    times: np.ndarray,
) -> np.ndarray:
    """The measured pulses, expressed at the beat rate the prior named.

    Only ever a no-op unless a prior moved the metrical level. Both
    directions are real: eighths measured where quarters were meant
    (decimate, below), and a half-time feel where the pulse found is the
    half note and the beat is twice as fast (subdivide -- the beats
    between those pulses are not measured, they are the even division of
    a measured interval, which is what a click is anyway).
    """
    if bpm <= 0 or pulse_bpm <= 0 or pulses.size == 0:
        return pulses
    beats_per_pulse = bpm / pulse_bpm
    if beats_per_pulse > 1.0:
        parts = max(1, int(round(beats_per_pulse)))
        if parts == 1:
            return pulses
        spans = np.diff(pulses, append=pulses[-1] + (60.0 / pulse_bpm))
        fractions = np.arange(parts) / parts
        return (pulses[:, None] + spans[:, None] * fractions[None, :]).ravel()
    return _decimate(pulses, max(1, int(round(1.0 / beats_per_pulse))), env, times)


def _decimate(
    pulses: np.ndarray,
    every: int,
    env: np.ndarray,
    times: np.ndarray,
) -> np.ndarray:
    """Take every *every*-th pulse, choosing the phase with the most energy.

    Only reached when a prior moved the fit down a metrical level (eighths
    measured, quarters wanted). Which of the `every` alternate pulse trains
    carries the beat is a real question with a measurable answer in real
    music -- the kick is on the beat -- and no answer at all in a perfectly
    even click track, where either is right and this simply picks one.
    """
    if every <= 1:
        return pulses
    best_offset, best_energy = 0, -1.0
    for offset in range(every):
        chosen = pulses[offset::every]
        index = np.clip(np.searchsorted(times, chosen), 0, len(env) - 1)
        energy = float(env[index].sum())
        if energy > best_energy:
            best_offset, best_energy = offset, energy
    return pulses[best_offset::every]
