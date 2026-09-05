"""Tempo detection: the librosa-gated half of the analysis pipeline.

`docs/01-architecture.md:111`: ``woodshed analyze <slug>`` produces tempo,
grid offset, and peaks -- one decode serves all three, which is why
`cmd_analyze` (cli.py) calls both `detect_tempo` (here) and
`woodshed.peaks.multi_resolution` off the same decoded samples.

The pipeline is *onset envelope -> coarse (librosa) -> refine (tempofit) ->
onset times -> anchor (tempofit)*. Everything downstream of the onset
envelope/times is pure numpy and lives in `tempofit.py` so it is testable
with no librosa installed (CLAUDE.md's layering rule: only this module and
`render.py` may import a heavy or external dependency, and the split exists
*because* the maths that decides where every beat and grid line lands must
be provable without the optional dependency that only ever supplies a coarse
first guess). See `tempofit.py`'s module doc and
`rambass-live/src/rambass/analyze.py`, which this is adapted from.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from woodshed.errors import WoodshedError
from woodshed.manifest import Tempo
from woodshed.tempofit import find_grid_anchor, refine_tempo
from woodshed.tools import locate_tool, require_module


def load_mono_audio(path: str | Path, sample_rate: int = 22050) -> tuple[np.ndarray, int]:
    """Decode any audio file to a mono float32 array via ffmpeg.

    Not librosa's own loader: ffmpeg already has to be on this machine for
    `render.py`, decodes more formats reliably than librosa's audioread/
    soundfile backends do on Windows, and this way `cmd_analyze` can decode
    once and feed both `detect_tempo` and the peaks pipeline from the same
    samples. Lifted conceptually from `rambass-live/src/rambass/audio.py`'s
    `load_mono` (identical approach, renamed to sit beside `detect_tempo`
    rather than beside `require_module`, since nothing outside analyze.py
    needs it yet).
    """
    path = Path(path)
    if not path.is_file():
        raise WoodshedError(f"no such audio file: {path}")
    ffmpeg = locate_tool("ffmpeg").path
    result = subprocess.run(
        [
            ffmpeg, "-v", "error", "-nostdin",
            "-i", str(path),
            "-ac", "1", "-ar", str(sample_rate),
            "-f", "f32le", "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise WoodshedError(f"ffmpeg could not decode {path}:\n" + "\n".join(tail))
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float32)
    if samples.size == 0:
        raise WoodshedError(f"{path} decoded to zero samples")
    return samples, sample_rate


def detect_tempo(path: str | Path, *, sample_rate: int = 22050) -> Tempo:
    """Measure the tempo and the grid anchor of a recording.

    ``librosa.beat.beat_track`` gives a coarse tempo from a tempogram bin --
    see `tempofit.refine_tempo`'s docstring for why that is not a
    measurement -- refined here against the same onset envelope, then
    ``find_grid_anchor`` locates where bar 1 beat 1 sits from the discrete
    onset times (not the envelope: the anchor search needs a list of instants,
    which is the "onsets-vs-envelope difference" the plan calls load-bearing).
    ``confidence`` is the anchor's own ``on_beat`` score -- how much of the
    recording actually lands on the beat at that anchor, which is what a typed
    number can never honestly report (see ``Tempo.confidence``'s "null when
    typed by hand").
    """
    librosa = require_module("librosa", "analyze")
    samples, sr = load_mono_audio(path, sample_rate)

    onset_env = librosa.onset.onset_strength(y=samples, sr=sr, aggregate=np.median)
    times = librosa.times_like(onset_env, sr=sr)
    coarse_tempo, _beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="time")
    coarse = float(np.atleast_1d(coarse_tempo)[0])
    bpm = refine_tempo(onset_env, times, coarse)

    onset_times = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")
    anchor, report = find_grid_anchor(onset_times, bpm)

    return Tempo(
        bpm=round(bpm, 2),
        source="refined",
        grid_offset_s=round(float(anchor), 4),
        time_signature="4/4",
        confidence=report.get("on_beat"),
    )


def beat_grid(tempo: Tempo | None, duration_s: float) -> list[float]:
    """Every beat position in ``[0, duration_s)``, from ``bpm`` + ``grid_offset_s``.

    Pure -- no audio, no librosa, safe to call from anything. Degrades to an
    empty grid when there is nothing to derive one from (CLAUDE.md: "if
    tempo.bpm is 0 or absent, ... no grid, no click, no bar ruler"), rather
    than dividing by zero.
    """
    if tempo is None or tempo.bpm <= 0 or duration_s <= 0:
        return []
    period = 60.0 / tempo.bpm
    # grid_offset_s is the anchor within ONE beat -- [0, period) by
    # construction, see find_grid_anchor -- but a hand-typed value is not
    # guaranteed to be, so fold it into range rather than trust it.
    first = tempo.grid_offset_s % period
    beats = []
    t = first
    while t < duration_s:
        beats.append(round(t, 6))
        t += period
    return beats
