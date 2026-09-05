"""Tests for woodshed.capture's two PURE functions (Phase 1, H1).

docs/04-sources.md: "recording starts on the first sample above the noise
floor and a segment ends after >= 1.2s below it" (split_on_silence);
"each segment is bound to the next unclaimed track... within +/-1.5s...
anything further out stops and asks... never bind on a guess" and
(docs/04-sources.md's "What will bite") "a dropout is silent -- refuse to
bind a segment that reported one" (bind_segments, H3's overflow rule).

Synthetic audio only -- the device half (list_devices/capture) needs real
hardware and is not exercised here; see capture.py's module doc.
"""

from __future__ import annotations

import numpy as np
import pytest

from woodshed.capture import Segment, TracklistEntry, bind_segments, split_on_silence
from woodshed.errors import WoodshedError

SR = 1000  # a low, convenient sample rate for hand-checkable frame counts


def _tone(n: int, amplitude: float = 0.5) -> np.ndarray:
    return np.full(n, amplitude, dtype=np.float32)


def _silence(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.float32)


# ---------------------------------------------------------------------------
# split_on_silence()
# ---------------------------------------------------------------------------


def test_split_on_silence_empty_input() -> None:
    assert split_on_silence(np.array([], dtype=np.float32), SR, -50.0, 1.2) == []


def test_split_on_silence_all_silence_yields_nothing() -> None:
    samples = _silence(500)
    assert split_on_silence(samples, SR, -50.0, 1.2) == []


def test_split_on_silence_one_segment_no_trailing_silence() -> None:
    # A tone that never drops below the floor: one open segment, closed at
    # end-of-input even though the 1.2s gap never actually happened.
    samples = _tone(300)
    assert split_on_silence(samples, SR, -50.0, 1.2) == [(0, 300)]


def test_split_on_silence_splits_two_tones_on_a_long_enough_gap() -> None:
    gap_frames = int(1.2 * SR)  # 1200 frames
    samples = np.concatenate([_tone(200), _silence(gap_frames), _tone(150)])
    segments = split_on_silence(samples, SR, -50.0, 1.2)
    assert segments == [(0, 200), (200 + gap_frames, 200 + gap_frames + 150)]


def test_split_on_silence_a_short_gap_does_not_split() -> None:
    # A gap shorter than gap_s is just a quiet passage inside one track,
    # not a boundary between two.
    short_gap = int(0.5 * SR)
    samples = np.concatenate([_tone(200), _silence(short_gap), _tone(150)])
    segments = split_on_silence(samples, SR, -50.0, 1.2)
    assert len(segments) == 1
    assert segments[0][0] == 0
    assert segments[0][1] == 200 + short_gap + 150


def test_split_on_silence_leading_and_trailing_silence_excluded() -> None:
    lead = _silence(100)
    trail = _silence(100)
    samples = np.concatenate([lead, _tone(200), trail])
    segments = split_on_silence(samples, SR, -50.0, 1.2)
    assert segments == [(100, 300)]


# ---------------------------------------------------------------------------
# bind_segments() -- match by duration, in order; never guess.
# ---------------------------------------------------------------------------


def _seg(duration_s: float, *, overflowed: bool = False) -> Segment:
    return Segment(
        start_frame=0, end_frame=int(duration_s * SR), sample_rate=SR, overflowed=overflowed
    )


def test_bind_segments_matches_in_order_within_tolerance() -> None:
    segments = [_seg(180.0), _seg(240.5)]
    tracklist = [
        TracklistEntry(slug="a", duration_s=180.9),
        TracklistEntry(slug="b", duration_s=240.0),
    ]
    bindings = bind_segments(segments, tracklist, tolerance_s=1.5)
    assert [b.slug for b in bindings] == ["a", "b"]


def test_bind_segments_binds_fewer_segments_than_tracks() -> None:
    # A capture that stopped early binds what it has; the rest stay needs-audio.
    segments = [_seg(180.0)]
    tracklist = [
        TracklistEntry(slug="a", duration_s=180.0),
        TracklistEntry(slug="b", duration_s=240.0),
    ]
    bindings = bind_segments(segments, tracklist)
    assert [b.slug for b in bindings] == ["a"]


def test_bind_segments_refuses_more_segments_than_tracks() -> None:
    segments = [_seg(180.0), _seg(240.0)]
    tracklist = [TracklistEntry(slug="a", duration_s=180.0)]
    with pytest.raises(WoodshedError):
        bind_segments(segments, tracklist)


def test_bind_segments_refuses_a_duration_outside_tolerance() -> None:
    # Named for the failure mode docs/04-sources.md calls out: a whole
    # album shifted by one must stop and ask, not bind on a guess.
    segments = [_seg(180.0)]
    tracklist = [TracklistEntry(slug="a", duration_s=190.0)]
    with pytest.raises(WoodshedError):
        bind_segments(segments, tracklist, tolerance_s=1.5)


def test_bind_segments_exactly_at_tolerance_binds() -> None:
    segments = [_seg(180.0)]
    tracklist = [TracklistEntry(slug="a", duration_s=181.5)]
    bindings = bind_segments(segments, tracklist, tolerance_s=1.5)
    assert bindings[0].slug == "a"


def test_bind_segments_refuses_an_overflowed_segment() -> None:
    """H3: a dropout is silent -- refuse to bind, never merely warn."""
    segments = [_seg(180.0, overflowed=True)]
    tracklist = [TracklistEntry(slug="a", duration_s=180.0)]
    with pytest.raises(WoodshedError, match="overflow"):
        bind_segments(segments, tracklist)


def test_bind_segments_clean_segment_beside_an_overflowed_one_still_binds() -> None:
    """Named test from the plan's own contract: a clean segment beside an
    overflowed one is not collaterally refused -- only the bad one is."""
    segments = [_seg(180.0), _seg(240.0, overflowed=True)]
    tracklist = [
        TracklistEntry(slug="a", duration_s=180.0),
        TracklistEntry(slug="b", duration_s=240.0),
    ]
    with pytest.raises(WoodshedError):
        bind_segments(segments, tracklist)
    # The first (clean) segment on its own still binds fine.
    bindings = bind_segments(segments[:1], tracklist[:1])
    assert bindings[0].slug == "a"
