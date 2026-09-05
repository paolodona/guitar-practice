"""Tests for woodshed.peaks: min/max bucketing for waveform drawing.

Covers the test contract from the C1 unit brief: compute_peaks returns
exactly `buckets` (min, max) tuples with min <= max; multi_resolution
returns one entry per requested level; write_peaks/read_peaks round-trip;
read_peaks on an uncached slug/level returns None rather than raising; and
a deep zoom -- more buckets than samples -- does not crash and reports
silence, (0, 0), for the buckets that would read past the end of the
array, rather than erroring or wrapping/reading garbage.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from woodshed.library import Repo
from woodshed.peaks import compute_peaks, multi_resolution, read_peaks, write_peaks

# ── compute_peaks ─────────────────────────────────────────────────────────


def _sine(n: int, freq: float = 220.0, sample_rate: int = 44100) -> np.ndarray:
    t = np.arange(n) / sample_rate
    return np.sin(2 * np.pi * freq * t).astype(np.float64)


def test_compute_peaks_returns_exactly_buckets_tuples() -> None:
    samples = _sine(44100)
    result = compute_peaks(samples, buckets=100)

    assert len(result) == 100


def test_compute_peaks_each_tuple_has_min_lte_max() -> None:
    samples = _sine(44100)
    result = compute_peaks(samples, buckets=200)

    for lo, hi in result:
        assert lo <= hi


def test_compute_peaks_captures_full_amplitude_range() -> None:
    # A full-scale sine over many periods should have some bucket touching
    # close to +1 and some bucket touching close to -1.
    samples = _sine(44100, freq=220.0)
    result = compute_peaks(samples, buckets=50)

    los, his = zip(*result, strict=True)
    assert min(los) < -0.9
    assert max(his) > 0.9


def test_compute_peaks_handles_small_bucket_via_stride_one() -> None:
    # Few samples per bucket (buckets close to len(samples)) exercises the
    # stride-1 path rather than the stride-4 path used for large buckets.
    samples = np.array([0.1, -0.2, 0.3, -0.4, 0.5, -0.6, 0.7, -0.8], dtype=np.float64)
    result = compute_peaks(samples, buckets=8)

    assert len(result) == 8
    for lo, hi in result:
        assert lo <= hi


def test_compute_peaks_zero_buckets_returns_empty_list() -> None:
    samples = _sine(1000)
    assert compute_peaks(samples, buckets=0) == []


def test_compute_peaks_empty_samples_reports_silence_everywhere() -> None:
    samples = np.array([], dtype=np.float64)
    result = compute_peaks(samples, buckets=10)

    assert result == [(0.0, 0.0)] * 10


def test_compute_peaks_deep_zoom_more_buckets_than_samples_does_not_crash() -> None:
    # The trap named in the plan: asking for more buckets than there are
    # samples must not error, wrap around, or read garbage past the end of
    # the array -- buckets past the data report silence, (0, 0).
    samples = _sine(10)
    result = compute_peaks(samples, buckets=1000)

    assert len(result) == 1000
    # Every tuple is well-formed (min <= max), including the silent tail.
    for lo, hi in result:
        assert lo <= hi
    # The buckets clearly past the 10 samples of data are silence.
    assert result[-1] == (0.0, 0.0)
    assert result[500] == (0.0, 0.0)


# ── multi_resolution ──────────────────────────────────────────────────────


def test_multi_resolution_returns_one_entry_per_level() -> None:
    samples = _sine(200_000)
    result = multi_resolution(samples, sample_rate=44100)

    assert set(result.keys()) == {1024, 4096, 16384}


def test_multi_resolution_honours_custom_levels() -> None:
    samples = _sine(200_000)
    result = multi_resolution(samples, sample_rate=44100, levels=(64, 256))

    assert set(result.keys()) == {64, 256}
    for lo, hi in result[64]:
        assert lo <= hi


def test_multi_resolution_each_entry_matches_compute_peaks_bucket_count() -> None:
    samples = _sine(200_000)
    result = multi_resolution(samples, sample_rate=44100, levels=(1024,))

    assert len(result[1024]) == 1024


# ── write_peaks / read_peaks round-trip ───────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(root=tmp_path)
    r.songs_dir.mkdir()
    r.song_dir("test-song").mkdir()
    return r


def test_write_then_read_peaks_round_trips_same_data(repo: Repo) -> None:
    data = {1024: [(0.0, 0.0), (-0.5, 0.5), (-1.0, 1.0)]}

    write_peaks(repo, "test-song", data)
    loaded = read_peaks(repo, "test-song", 1024)

    assert loaded is not None
    assert loaded == {"level": 1024, "peaks": [[0.0, 0.0], [-0.5, 0.5], [-1.0, 1.0]]}


def test_write_peaks_writes_one_file_per_level(repo: Repo) -> None:
    data = {
        1024: [(0.0, 0.0)],
        4096: [(0.0, 0.0)],
        16384: [(0.0, 0.0)],
    }

    write_peaks(repo, "test-song", data)

    cache = repo.cache_dir("test-song")
    assert (cache / "peaks-1024.json").is_file()
    assert (cache / "peaks-4096.json").is_file()
    assert (cache / "peaks-16384.json").is_file()


def test_write_peaks_returns_a_path(repo: Repo) -> None:
    data = {1024: [(0.0, 0.0)]}
    result = write_peaks(repo, "test-song", data)

    assert isinstance(result, Path)


def test_read_peaks_returns_none_when_uncached(repo: Repo) -> None:
    assert read_peaks(repo, "test-song", 1024) is None


def test_read_peaks_returns_none_for_uncached_slug(repo: Repo) -> None:
    write_peaks(repo, "test-song", {1024: [(0.0, 0.0)]})

    assert read_peaks(repo, "some-other-song", 1024) is None


def test_read_peaks_returns_none_for_uncached_level_of_cached_slug(repo: Repo) -> None:
    write_peaks(repo, "test-song", {1024: [(0.0, 0.0)]})

    assert read_peaks(repo, "test-song", 4096) is None


def test_write_peaks_creates_cache_dir_if_missing(repo: Repo) -> None:
    assert not repo.cache_dir("test-song").exists()

    write_peaks(repo, "test-song", {1024: [(0.0, 0.0)]})

    assert repo.cache_dir("test-song").is_dir()
