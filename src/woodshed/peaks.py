"""Min/max bucketing for waveform drawing.

The bucketing approach (stride 4 while a bucket has many samples, stride 1
once a bucket is small, and a bucket that would read past the end of the
array reporting silence -- (0.0, 0.0) -- rather than erroring or reading
garbage/wrapping) is lifted conceptually from console.html:1078-1097 (see
the plan, "Tier 2 -- numpy").

Numpy only, per CLAUDE.md's "Layering" -- this module may import numpy but
not pydantic, librosa, or any external binary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from woodshed.library import Repo

#: Above this many samples per bucket, sample every 4th frame instead of
#: every frame -- min/max over a stride-4 subset is visually indistinguishable
#: for waveform drawing and four times cheaper. Below it (a "deep zoom"),
#: fall back to stride 1 so a small bucket is not starved of samples.
_STRIDE4_THRESHOLD = 32

#: The level `GET /api/peaks/<slug>` serves when the caller names none --
#: `multi_resolution`'s own coarsest ("whole song overview") level. Found
#: live 2026-09-06: `screens/song.js`/`screens/practice.js` both fetch
#: `payload.peaks_url` bare, with no `?level=` at all (there is no zoom
#: feature yet for either to pick one from) -- server.py's own `_peaks`
#: used to pass that missing param straight through as `None`, which
#: `read_peaks` then formatted into a `peaks-None.json` path that could
#: never exist, 404ing even once real peaks were cached. This is what a
#: whole-song-width view actually wants regardless.
DEFAULT_LEVEL = 1024


def compute_peaks(samples: np.ndarray, buckets: int) -> list[tuple[float, float]]:
    """Bucket *samples* into *buckets* (min, max) pairs for waveform drawing.

    Each bucket covers ``len(samples) / buckets`` samples of the source
    array (the last bucket may run slightly short or long due to rounding).
    A bucket whose range falls entirely past the end of the array -- more
    buckets requested than there are samples to cover them, a "deep zoom"
    -- reports silence, ``(0.0, 0.0)``, rather than erroring, wrapping, or
    reading whatever garbage follows the array in memory.
    """
    if buckets <= 0:
        return []

    n = len(samples)
    out: list[tuple[float, float]] = []
    # A fixed samples-per-bucket width (at least 1), not a proportional
    # split -- this is what makes "more buckets than samples" (a deep zoom
    # past the file's native resolution) fall off the end of the array
    # instead of stretching the same handful of samples across every
    # bucket. The last bucket absorbs whatever remainder floor division
    # leaves behind, so no trailing samples are silently dropped.
    samples_per_bucket = max(1, n // buckets)

    for i in range(buckets):
        start = i * samples_per_bucket
        if start >= n:
            # Past the end of the data entirely: silence, not a crash.
            out.append((0.0, 0.0))
            continue

        end = n if i == buckets - 1 else min(start + samples_per_bucket, n)
        chunk = samples[start:end]
        stride = 4 if len(chunk) >= _STRIDE4_THRESHOLD else 1
        view = chunk[::stride]
        out.append((float(view.min()), float(view.max())))

    return out


def multi_resolution(
    samples: np.ndarray,
    sample_rate: int,
    levels: tuple[int, ...] = (1024, 4096, 16384),
) -> dict[int, list[tuple[float, float]]]:
    """Compute one ``compute_peaks`` result per requested zoom level.

    *sample_rate* is accepted (and part of the fixed signature) but not
    used to derive the bucket counts -- each level names a bucket count
    directly, one waveform-overview resolution per level.
    """
    del sample_rate  # part of the fixed signature; not needed for bucketing
    return {level: compute_peaks(samples, level) for level in levels}


def write_peaks(
    repo: Repo, slug: str, data: dict[int, list[tuple[float, float]]]
) -> Path:
    """Write one ``cache/peaks-<level>.json`` per level in *data*.

    Returns the path of the last file written. Creates the song's cache
    directory if it does not yet exist -- cache/ is always safe to
    regenerate, per CLAUDE.md's "The repo is the database".
    """
    cache_dir = repo.cache_dir(slug)
    cache_dir.mkdir(parents=True, exist_ok=True)

    last_path = cache_dir
    for level, peaks in data.items():
        path = cache_dir / f"peaks-{level}.json"
        payload = {"level": level, "peaks": [list(pair) for pair in peaks]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        last_path = path

    return last_path


def read_peaks(repo: Repo, slug: str, level: int) -> dict | None:
    """Read the cached peaks for *slug* at *level*, or None if uncached."""
    path = repo.cache_dir(slug) / f"peaks-{level}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
