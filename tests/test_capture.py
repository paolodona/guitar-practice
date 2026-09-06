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

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import woodshed.capture as capture_module
from woodshed.capture import (
    Segment,
    TracklistEntry,
    _finish_capture,
    bind_segment_as_new_song,
    bind_segment_to_song,
    bind_segments,
    extract_segment,
    split_on_silence,
)
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import load_song

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


# ---------------------------------------------------------------------------
# _finish_capture() -- the post-recording half of capture(), factored out so
# it is exercisable with synthetic audio (Phase 1.5, Group U).
# ---------------------------------------------------------------------------


def test_finish_capture_legacy_mode_writes_one_wav_per_segment(tmp_path: Path) -> None:
    gap_frames = int(1.2 * SR)
    samples = np.concatenate([_tone(300), _silence(gap_frames), _tone(150)])
    out_dir = tmp_path / "audio"
    out_dir.mkdir()

    segments = list(_finish_capture(samples, SR, -50.0, 1.2, [], out_dir, None))

    assert len(segments) == 2
    assert sorted(p.name for p in out_dir.glob("segment-*.wav")) == [
        "segment-001.wav",
        "segment-002.wav",
    ]


def test_finish_capture_legacy_mode_numbers_after_existing_segments(tmp_path: Path) -> None:
    out_dir = tmp_path / "audio"
    out_dir.mkdir()
    (out_dir / "segment-001.wav").write_bytes(b"")

    list(_finish_capture(_tone(300), SR, -50.0, 1.2, [], out_dir, None))

    assert (out_dir / "segment-002.wav").is_file()


def test_finish_capture_raw_path_mode_writes_one_file_and_no_segment_files(
    tmp_path: Path,
) -> None:
    gap_frames = int(1.2 * SR)
    samples = np.concatenate([_tone(300), _silence(gap_frames), _tone(150)])
    out_dir = tmp_path / "audio"
    out_dir.mkdir()
    raw_path = tmp_path / "capture" / "one.wav"

    segments = list(_finish_capture(samples, SR, -50.0, 1.2, [], out_dir, raw_path))

    assert len(segments) == 2
    assert raw_path.is_file()
    assert list(out_dir.glob("segment-*.wav")) == []


def test_finish_capture_raw_path_mode_segment_frames_index_into_the_raw_file(
    tmp_path: Path,
) -> None:
    gap_frames = int(1.2 * SR)
    samples = np.concatenate([_tone(300), _silence(gap_frames), _tone(150)])
    raw_path = tmp_path / "capture" / "one.wav"

    segments = list(_finish_capture(samples, SR, -50.0, 1.2, [], tmp_path, raw_path))

    assert (segments[0].start_frame, segments[0].end_frame) == (0, 300)
    assert segments[1].start_frame == 300 + gap_frames


def test_finish_capture_raw_path_mode_flags_an_overflowed_segment(tmp_path: Path) -> None:
    gap_frames = int(1.2 * SR)
    samples = np.concatenate([_tone(300), _silence(gap_frames), _tone(150)])
    raw_path = tmp_path / "capture" / "one.wav"
    overflow_at_frame_150 = [150]  # inside the first segment (0-300), not the second

    segments = list(
        _finish_capture(samples, SR, -50.0, 1.2, overflow_at_frame_150, tmp_path, raw_path)
    )

    assert segments[0].overflowed is True
    assert segments[1].overflowed is False


# ---------------------------------------------------------------------------
# extract_segment() -- an ffmpeg frame-range cut (Phase 1.5, Group U). Argv
# asserted, subprocess mocked -- same style the plan asks render.py's own
# tests (Phase 2) to use.
# ---------------------------------------------------------------------------


def _fake_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, stderr: bytes = b""
) -> dict:
    """Patches `locate_tool` and `subprocess.run` inside capture.py; returns
    a dict that `["argv"]` is filled in with once `extract_segment` runs."""
    captured: dict = {}
    monkeypatch.setattr(
        capture_module, "locate_tool", lambda name: SimpleNamespace(path="ffmpeg", route="path")
    )

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(capture_module.subprocess, "run", fake_run)
    return captured


def test_extract_segment_refuses_a_missing_raw_file(tmp_path: Path) -> None:
    seg = Segment(start_frame=0, end_frame=1000, sample_rate=1000)
    with pytest.raises(WoodshedError, match="no such raw capture recording"):
        extract_segment(tmp_path / "missing.wav", seg, tmp_path / "out.flac")


def test_extract_segment_argv_is_an_ffmpeg_frame_range_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw.wav"
    raw.write_bytes(b"fake wav bytes")
    dest = tmp_path / "out" / "solo.flac"
    seg = Segment(start_frame=48_000, end_frame=48_000 + 24_000, sample_rate=48_000)
    captured = _fake_ffmpeg(monkeypatch)

    extract_segment(raw, seg, dest)

    argv = captured["argv"]
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-ss") + 1] == "1.000000"
    assert argv[argv.index("-i") + 1] == str(raw)
    assert argv[argv.index("-t") + 1] == "0.500000"
    assert argv[-1] == str(dest)


def test_extract_segment_raises_on_a_nonzero_ffmpeg_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw.wav"
    raw.write_bytes(b"x")
    seg = Segment(start_frame=0, end_frame=1000, sample_rate=1000)
    _fake_ffmpeg(monkeypatch, returncode=1, stderr=b"ffmpeg: boom")

    with pytest.raises(WoodshedError, match="ffmpeg"):
        extract_segment(raw, seg, tmp_path / "out.flac")


# ---------------------------------------------------------------------------
# bind_segment_to_song() / bind_segment_as_new_song() (Phase 1.5, Group U)
# -- extract_segment itself is mocked here (covered on its own above), so
# these focus purely on the refuse/write rules.
# ---------------------------------------------------------------------------


def _fake_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(raw_audio_path, segment, dest_path) -> None:
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake-flac-bytes")

    monkeypatch.setattr(capture_module, "extract_segment", fake)


def test_bind_segment_to_song_refuses_an_overflowed_segment(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    with pytest.raises(WoodshedError, match="overflow"):
        bind_segment_to_song(repo, "cant-stop", tmp_path / "raw.wav", _seg(120.0, overflowed=True))


def test_bind_segment_to_song_refuses_a_slug_that_already_has_a_song(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    song_dir = repo.song_dir("cant-stop")
    song_dir.mkdir(parents=True)
    (song_dir / "song.yaml").write_text("slug: cant-stop\n", encoding="utf-8")
    _fake_extract(monkeypatch)

    with pytest.raises(WoodshedError, match="already has a song.yaml"):
        bind_segment_to_song(repo, "cant-stop", tmp_path / "raw.wav", _seg(120.0))


def test_bind_segment_to_song_writes_a_fresh_song_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    _fake_extract(monkeypatch)

    bind_segment_to_song(
        repo, "cant-stop", tmp_path / "raw.wav", _seg(123.4), tuning="Eb standard"
    )

    song = load_song(repo.song_dir("cant-stop") / "song.yaml")
    assert song.slug == "cant-stop"
    assert song.title == "cant-stop"  # no nicer title exists anywhere yet
    assert song.recording.tuning == "Eb standard"
    assert song.recording.source == "capture"
    assert abs(song.recording.duration_s - 123.4) < 0.01


def test_bind_segment_to_song_defaults_tuning_when_not_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    _fake_extract(monkeypatch)

    bind_segment_to_song(repo, "cant-stop", tmp_path / "raw.wav", _seg(60.0))

    song = load_song(repo.song_dir("cant-stop") / "song.yaml")
    assert song.recording.tuning == "E standard"


def test_bind_segment_as_new_song_refuses_an_overflowed_segment(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    with pytest.raises(WoodshedError, match="overflow"):
        bind_segment_as_new_song(
            repo, tmp_path / "raw.wav", _seg(90.0, overflowed=True),
            title="New Song", artist="", tuning="E standard",
        )


def test_bind_segment_as_new_song_refuses_a_title_that_collides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    song_dir = repo.song_dir("cant-stop")
    song_dir.mkdir(parents=True)
    (song_dir / "song.yaml").write_text("slug: cant-stop\n", encoding="utf-8")
    _fake_extract(monkeypatch)

    with pytest.raises(WoodshedError, match="already names a song"):
        bind_segment_as_new_song(
            repo, tmp_path / "raw.wav", _seg(90.0),
            title="Cant Stop", artist="RHCP", tuning="E standard",
        )


def test_bind_segment_as_new_song_writes_song_yaml_and_returns_the_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    _fake_extract(monkeypatch)

    slug = bind_segment_as_new_song(
        repo, tmp_path / "raw.wav", _seg(45.6),
        title="Perché No", artist="Someone", tuning="D standard",
    )

    assert slug == "perche-no"
    song = load_song(repo.song_dir(slug) / "song.yaml")
    assert song.title == "Perché No"
    assert song.artist == "Someone"
    assert song.recording.tuning == "D standard"
    assert song.recording.source == "capture"
