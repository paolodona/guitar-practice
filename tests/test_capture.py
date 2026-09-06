"""Tests for woodshed.capture's two PURE functions (Phase 1, H1).

docs/04-sources.md: "recording starts on the first sample above the noise
floor and a segment ends after >= 1.2s below it" (split_on_silence);
"each segment is bound to the next unclaimed track... within +/-1.5s...
anything further out stops and asks... never bind on a guess" and
(docs/04-sources.md's "What will bite") "a dropout is silent -- refuse to
bind a segment that reported one" (bind_segments, H3's overflow rule).

Synthetic audio only -- `list_devices`/`default_device`/the actual device
I/O inside `capture()` need real hardware and are not exercised here; see
capture.py's module doc. Phase 1.5's T2 added one exception: `capture()`'s
own `stop_event`/`on_overflow` CONTROL FLOW is tested against a fake
`pyaudiowpatch` module installed into `sys.modules` (just enough surface
for `PyAudio()`/`.open()`/`paFloat32`) with a synthetic stream -- the real
device is still never touched, but the loop's own logic (does a stop
request actually stop it, does an overflow callback actually fire) is.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import woodshed.capture as capture_module
from woodshed.capture import (
    Device,
    Segment,
    TracklistEntry,
    _finish_capture,
    bind_segment_as_new_song,
    bind_segment_to_song,
    bind_segments,
    capture,
    extract_segment,
    split_on_silence,
)
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import load_song

SR = 1000  # a low, convenient sample rate for hand-checkable frame counts


@pytest.fixture(autouse=True)
def _no_auto_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """`bind_segment_to_song`/`bind_segment_as_new_song` now call
    `cli.analyze_after_bind` automatically (found live 2026-09-06) -- a
    no-op here by default, since most tests below use `_fake_extract`'s
    placeholder bytes (`b"fake-flac-bytes"`), not real audio, and a real
    ffmpeg decode of that would fail for reasons that have nothing to do
    with what any single test is actually checking. The dedicated
    `analyze_after_bind` tests further down re-enable it against real
    synthetic audio instead."""
    monkeypatch.setattr("woodshed.cli.analyze_after_bind", lambda *a, **k: None)


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
    # A default "Whole song" section, same as cli.py's bind_song_file --
    # there is always something to Practice without drawing one by hand.
    assert len(song.sections) == 1
    assert song.sections[0].id == "whole-song"
    assert song.sections[0].full_song is True
    assert song.sections[0].end_s == song.recording.duration_s


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
    assert len(song.sections) == 1
    assert song.sections[0].full_song is True


# ---------------------------------------------------------------------------
# capture()'s stop_event/on_overflow control flow (Phase 1.5, T2)
#
# Still no real hardware here -- a FAKE `pyaudiowpatch` module (just enough
# surface for `capture()`'s own calls: `PyAudio()`, `.open()`, `paFloat32`)
# installed into `sys.modules` so `require_module` finds something to
# import. This tests the LOOP'S OWN CONTROL FLOW (does it actually stop
# when told to, does on_overflow actually fire) -- the same "the control
# flow is pure enough to test with a fake" reasoning P1/R1 already used
# for a synthetic AudioWorkletNode, applied here to a synthetic stream
# instead of a real device.
# ---------------------------------------------------------------------------


def _install_fake_pyaudiowpatch(monkeypatch: pytest.MonkeyPatch, stream) -> None:
    fake_module = SimpleNamespace(
        paFloat32=1,
        PyAudio=lambda: SimpleNamespace(open=lambda **kwargs: stream, terminate=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake_module)


def test_capture_stops_via_stop_event_not_only_keyboardinterrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_event = threading.Event()
    chunk = _tone(100, amplitude=0.5).tobytes()  # chunk_frames = 1000 // 10 = 100
    call_count = 0

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            stop_event.set()  # "the stop button was pressed" during the 3rd chunk
        return chunk

    fake_stream = SimpleNamespace(read=fake_read, stop_stream=lambda: None, close=lambda: None)
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    raw_path = tmp_path / "raw.wav"

    segments = list(capture(device, tmp_path, raw_path=raw_path, stop_event=stop_event))

    assert call_count == 3  # stopped after the chunk that set the event, not a 4th read
    assert raw_path.is_file()
    assert len(segments) == 1  # one steady tone, no silence gap to split on
    assert segments[0].end_frame - segments[0].start_frame == 300  # 3 * 100 frames
    assert segments[0].overflowed is False


def test_capture_reports_overflow_live_via_on_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_event = threading.Event()
    chunk = _tone(100, amplitude=0.5).tobytes()
    call_count = 0

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("simulated input overflow")
        if call_count == 3:
            stop_event.set()
        return chunk

    fake_stream = SimpleNamespace(read=fake_read, stop_stream=lambda: None, close=lambda: None)
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    overflow_calls = []

    list(capture(
        device, tmp_path, raw_path=tmp_path / "raw.wav", stop_event=stop_event,
        on_overflow=lambda: overflow_calls.append(True),
    ))

    assert overflow_calls == [True]  # fired exactly once, live, not just baked into the Segment


def test_capture_on_level_still_fires_per_chunk_with_stop_event_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop_event is additive -- it must not disturb on_level, H3's
    existing live level-meter callback."""
    stop_event = threading.Event()
    chunk = _tone(100, amplitude=0.5).tobytes()
    call_count = 0

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            stop_event.set()
        return chunk

    fake_stream = SimpleNamespace(read=fake_read, stop_stream=lambda: None, close=lambda: None)
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    levels = []

    list(capture(
        device, tmp_path, raw_path=tmp_path / "raw.wav", stop_event=stop_event,
        on_level=levels.append,
    ))

    assert levels == [pytest.approx(0.5), pytest.approx(0.5)]


def test_capture_calls_on_stream_ready_with_the_open_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`capture_runner.CaptureRunner.stop()`'s force-close path (found live
    2026-09-06) needs the stream object itself, handed out the moment it
    opens -- before a single chunk has been read, so a wedge on the very
    first read is still reachable."""
    stop_event = threading.Event()
    chunk = _tone(100, amplitude=0.5).tobytes()

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        stop_event.set()
        return chunk

    fake_stream = SimpleNamespace(read=fake_read, stop_stream=lambda: None, close=lambda: None)
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    ready_with = []

    list(capture(
        device, tmp_path, raw_path=tmp_path / "raw.wav", stop_event=stop_event,
        on_stream_ready=ready_with.append,
    ))

    assert ready_with == [fake_stream]


def test_capture_treats_a_read_error_after_stop_event_as_a_clean_stop_not_an_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates `CaptureRunner.stop()`'s force-close: `stream.close()` from
    another thread makes the NEXT `read()` raise OSError, same shape as a
    real overflow -- but `stop_event` is already set by then (that is the
    precondition for the force-close to run at all), so this must NOT be
    recorded as a dropout or padded with fake silence, unlike a genuine
    mid-recording overflow (see the sibling test above)."""
    stop_event = threading.Event()
    chunk = _tone(100, amplitude=0.5).tobytes()
    call_count = 0

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            stop_event.set()  # the "force-close" landed between reads 2 and 3
            raise OSError("stream closed")
        return chunk

    fake_stream = SimpleNamespace(read=fake_read, stop_stream=lambda: None, close=lambda: None)
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    overflow_calls = []

    segments = list(capture(
        device, tmp_path, raw_path=tmp_path / "raw.wav", stop_event=stop_event,
        on_overflow=lambda: overflow_calls.append(True),
    ))

    assert overflow_calls == []  # not recorded as an overflow
    assert len(segments) == 1
    assert segments[0].overflowed is False
    assert segments[0].end_frame - segments[0].start_frame == 100  # 1 real chunk, no padding


def test_capture_finally_tolerates_a_stream_already_force_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `finally` block's own `stop_stream()`/`close()` must not blow up
    a capture that otherwise finished cleanly, if `CaptureRunner.stop()`
    already force-closed the same stream out from under it."""
    stop_event = threading.Event()

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        stop_event.set()
        return _tone(100, amplitude=0.5).tobytes()

    def raising_stop_stream() -> None:
        raise OSError("Stream not open")

    def raising_close() -> None:
        raise OSError("Stream not open")

    fake_stream = SimpleNamespace(
        read=fake_read, stop_stream=raising_stop_stream, close=raising_close,
    )
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    segments = list(capture(device, tmp_path, raw_path=tmp_path / "raw.wav", stop_event=stop_event))

    assert len(segments) == 1  # never raised, despite the "already closed" stream


def test_capture_stop_event_none_preserves_original_keyboardinterrupt_only_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stop_event at all (the default) -- the loop only ends via
    KeyboardInterrupt, exactly H2's original CLI behaviour."""
    chunk = _tone(100, amplitude=0.5).tobytes()
    call_count = 0

    def fake_read(n: int, exception_on_overflow: bool = True) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise KeyboardInterrupt
        return chunk

    fake_stream = SimpleNamespace(read=fake_read, stop_stream=lambda: None, close=lambda: None)
    _install_fake_pyaudiowpatch(monkeypatch, fake_stream)

    device = Device(index=0, name="Fake Loopback", sample_rate=SR, channels=1)
    segments = list(capture(device, tmp_path, raw_path=tmp_path / "raw.wav"))

    assert call_count == 3
    assert len(segments) == 1
    assert segments[0].end_frame - segments[0].start_frame == 200  # 2 successful reads
