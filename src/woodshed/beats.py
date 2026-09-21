"""The metronome's beat list for ONE section: decode a span, fit it, cache it.

The glue between `beatfit.py` (all the maths, numpy only, no files) and
the practice screen. This module is on the external-dependency side of
CLAUDE.md's layering line, beside `analyze.py` / `render.py` /
`separate.py`, and for the same single reason: it shells out to ffmpeg.
There is no librosa here and none behind it -- the metronome works on a
fresh clone with `uv sync` and nothing else, which is the whole point of
`beatfit` being written in numpy.

**Per section, deliberately.** The song-level `tempo.bpm` is one number
for a whole recording; a commercial take is not one tempo. MEASURED on
`songs/tutti-in-fila` (Paolo's own example of a song "not played in
time"): the stored 116.04 BPM against sections that fit 116.09, 116.59
and 117.42, so a grid run out from the song's own t=0 arrives at the solo
roughly 0.8s late. Fitting each section on its own is not a refinement of
that, it is a different question -- and it is the one a click has to
answer. `beatfit`'s module doc carries the rest of the measurements.

**The analysed span is wider than the section.** `ANALYSIS_MARGIN_S`
before and after: the lead-in wants clicks (a count-in is what a pre-roll
is FOR), and the fit itself is steadier when the section's own edges are
not the edges of the data. Clamped to the recording, like every other
span cut in this repo.

**The cache is cache.** `songs/<slug>/cache/beats/<section>-<fp>.json`,
fingerprinted over the recording, the boundaries and the song's tempo
prior, so a nudged boundary or a re-analysed tempo misses it by
construction -- `render.py`'s scheme, `separate.py`'s scheme, this one
too. Always safe to delete; never anything a human typed.

**Nothing here writes a click into audio.** The click is scheduled live
in the browser, against these numbers. `rambass-live/src/rambass/click.py`
opens with the reason and it holds unchanged: a click baked into a
backing track is the one mistake in this area that cannot be undone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from woodshed.atomic import published_atomically
from woodshed.beatfit import fit_beats, onset_envelope
from woodshed.errors import WoodshedError
from woodshed.manifest import Section, Song
from woodshed.tools import locate_tool

if TYPE_CHECKING:
    from woodshed.library import Repo

__all__ = [
    "DETECTOR_VERSION",
    "ANALYSIS_MARGIN_S",
    "SAMPLE_RATE",
    "beats_fingerprint",
    "beats_cache_path",
    "section_beats",
]

#: Bumped by hand whenever the fit below would answer differently for the
#: same audio -- a changed `beatfit` constant, a changed margin, a changed
#: sample rate. Same role as `render.RENDERER_VERSION` and
#: `separate.SEPARATOR_VERSION`, and the same mechanism: it is IN the
#: fingerprint, so a bump invalidates every cached answer without a
#: separate sweep.
DETECTOR_VERSION = 1

#: How much recording either side of the section is analysed with it. Four
#: seconds is about eight beats at a rock tempo -- enough to cover any
#: lead-in the pre-roll asks for (`pre_roll_beats` defaults to 4) and
#: enough that the phase correction has a window to work with at both
#: edges, which is where a click that disagrees with the band is most
#: audible (the loop seam is there).
ANALYSIS_MARGIN_S = 4.0

#: The rate the fit runs at. 22050 is `analyze.detect_tempo`'s own choice
#: and is far above what an onset envelope needs; the beats come out in
#: seconds, so nothing downstream ever sees it.
SAMPLE_RATE = 22050


def beats_fingerprint(song: Song, section: Section, *, margin_s: float = ANALYSIS_MARGIN_S) -> str:
    """8 hex of sha256 over everything that could change the answer.

    The song's `tempo.bpm` is in here because it is the PRIOR the fit uses
    to settle the metrical level (`beatfit.fit_beats`): re-analysing a
    song can move the click by an octave, and serving the old answer for
    the new tempo would be a silent disagreement between two files.
    """
    raw = (
        f"{song.recording.sha256}|{section.start_s:.6f}|{section.end_s:.6f}|"
        f"{margin_s:.3f}|{song.tempo.bpm:.3f}|{SAMPLE_RATE}|{DETECTOR_VERSION}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def beats_cache_path(repo: Repo, slug: str, section_id: str, fp: str) -> Path:
    """`songs/<slug>/cache/beats/<section_id>-<fp>.json` -- beside
    `cache/stems/` and `cache/peaks-*.json`, under the same "always safe to
    delete" rule."""
    return repo.cache_dir(slug) / "beats" / f"{section_id}-{fp}.json"


def analysis_span(song: Song, section: Section, *, margin_s: float = ANALYSIS_MARGIN_S
                  ) -> tuple[float, float]:
    """`(start_s, duration_s)` of the span to decode, clamped to the
    recording at both ends -- there is no audio before 0, and asking
    ffmpeg for time past the end of a file returns a short read that would
    then fit against silence."""
    start = max(0.0, section.start_s - margin_s)
    end = min(song.recording.duration_s, section.end_s + margin_s)
    return start, max(0.0, end - start)


def section_beats(
    repo: Repo,
    song: Song,
    section: Section,
    *,
    margin_s: float = ANALYSIS_MARGIN_S,
    force: bool = False,
) -> dict:
    """Where the beats of *section* are, in SOURCE seconds.

    Returns the same dict that is cached and served: see `_payload` for the
    shape. Cheap on a warm cache (one file read, no decode); about a second
    cold for a 90-second section, which is why the endpoint that calls it
    can afford to be synchronous.
    """
    fp = beats_fingerprint(song, section, margin_s=margin_s)
    cache = beats_cache_path(repo, song.slug, section.id, fp)
    if not force:
        cached = _read_cache(cache)
        if cached is not None:
            return cached

    source_path = repo.song_dir(song.slug) / song.recording.file
    if not source_path.is_file():
        raise WoodshedError(f"no such audio file: {source_path}")
    # Named but unused beyond the check: `load_mono_audio` locates ffmpeg
    # itself. Asking here means a machine without it is told so by THIS
    # module's own error, in the terms of the thing being asked for.
    locate_tool("ffmpeg")

    start_s, duration_s = analysis_span(song, section, margin_s=margin_s)
    # Imported inside the function, not at module top level: analyze.py is
    # the librosa-tier module, and while its own top level imports nothing
    # heavy today, this module must not be the reason that changes.
    from woodshed.analyze import load_mono_audio

    samples, sample_rate = load_mono_audio(
        source_path, SAMPLE_RATE, start_s=start_s, duration_s=duration_s
    )
    env, times = onset_envelope(samples, sample_rate)
    # Straight onto the recording's own clock, once, here: everything
    # downstream (the cache file, the endpoint, the click scheduler) is in
    # source seconds and nothing re-bases it. CLAUDE.md's two-clock rule.
    fit = fit_beats(env, times + start_s, bpm_prior=max(0.0, song.tempo.bpm))

    payload = _payload(section, start_s, duration_s, fit)
    _write_cache(cache, payload)
    return payload


def _payload(section: Section, start_s: float, duration_s: float, fit) -> dict:
    return {
        "version": DETECTOR_VERSION,
        "section_id": section.id,
        # The span ANALYSED, not the section: the beats run past both ends
        # of the section by the margin, and a caller that wants to know why
        # should be able to see it rather than infer it.
        "analysis_start_s": round(start_s, 4),
        "analysis_end_s": round(start_s + duration_s, 4),
        "bpm": fit.bpm,
        "pulse_bpm": fit.pulse_bpm,
        "beats": fit.beats,
        "wander_ms": fit.wander_ms,
        "pulse_ratio": fit.pulse_ratio,
    }


def _read_cache(path: Path) -> dict | None:
    """The cached answer, or None if there isn't a usable one.

    A cache file that cannot be parsed is not an error: `cache/` is always
    safe to delete, so it is always safe to be wrong, and the cost of a
    half-written or hand-edited file is one re-analysis rather than a
    failed request.
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != DETECTOR_VERSION:
        return None
    return payload


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Renamed onto the path rather than written in place -- the same rule
    # atomic.py exists for, and the same failure it was written after: a
    # reader must never be served a prefix of a file that is still being
    # written (src/woodshed/atomic.py has the measurement).
    with published_atomically(path) as scratch:
        scratch.write_text(json.dumps(payload, indent=2), encoding="utf-8")
