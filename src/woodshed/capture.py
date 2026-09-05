"""Recording what the machine plays: WASAPI loopback, splitting on the
gaps, matching by duration. docs/04-sources.md is the full spec; this
module is the "Implementation" section it names.

**Tier**: its own optional extra, `pyaudiowpatch`, imported only from
inside the device-facing functions below (`list_devices`, `default_device`,
`capture`) via `tools.require_module` -- the same layering rule as
`analyze.py` (librosa) and `render.py` (rubberband): nothing above this
module may import `pyaudiowpatch` and this module's own top level does
not either, so importing `woodshed.capture` never fails just because the
optional package is missing.

**Two pure functions carry the actual risk** (`split_on_silence`,
`bind_segments`) and are exercised with synthetic audio in
`tests/test_capture.py` -- no device, no real recording. The device half
(`list_devices`, `default_device`, `capture`) is, in the plan's own words,
"one thin unverified call": there is no WASAPI loopback hardware in this
environment to run it against, so it is written carefully against
PyAudioWPatch's documented API and left unverified rather than faked with
a mock that would prove nothing true about a real device. `doctor.py`'s
loopback-open check (H3) is the one thing that DOES touch the real device,
and even that only opens and immediately closes it.

**Never to Spotify, never to DRM.** This module records an audio DEVICE
and knows nothing about which application produced the sound or which
service it came from -- CLAUDE.md's "Don't" list and docs/04-sources.md's
"Two separate questions" are both explicit about this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from woodshed.errors import WoodshedError
from woodshed.tools import require_module

__all__ = [
    "Device",
    "Segment",
    "TracklistEntry",
    "Binding",
    "split_on_silence",
    "bind_segments",
    "list_devices",
    "default_device",
    "capture",
]


@dataclass(frozen=True)
class Device:
    """One WASAPI loopback-capable input PyAudioWPatch can see."""

    index: int
    name: str
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class Segment:
    """One captured track: a frame range at a fixed sample rate, plus H3's
    overflow flag. `overflowed` is never inferred after the fact -- it is
    true only when the recording loop itself observed an input overflow
    somewhere inside [start_frame, end_frame)."""

    start_frame: int
    end_frame: int
    sample_rate: int
    overflowed: bool = False

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.sample_rate


@dataclass(frozen=True)
class TracklistEntry:
    """One expected track: a slug to bind to, and the duration it should
    be -- from an imported playlist's metadata (Spotify, Phase 3's M1) or
    a song.yaml written by hand. `bind_segments` never invents this; it is
    always supplied by the caller."""

    slug: str
    duration_s: float


@dataclass(frozen=True)
class Binding:
    """One segment matched to one track."""

    slug: str
    segment: Segment


def split_on_silence(
    samples: np.ndarray, sample_rate: int, floor_db: float, gap_s: float
) -> list[tuple[int, int]]:
    """Frame ranges `(start, end)` where `samples` rises above `floor_db`
    and stays there until it drops below for at least `gap_s` seconds.

    docs/04-sources.md: "recording starts on the first sample above the
    noise floor and a segment ends after >= 1.2s below it." A trailing
    segment with no closing silence (the recording simply stopped) is
    still returned, closed at `len(samples)` -- the common case for "last
    track in the set, capture ended right after it."

    Pure, numpy only; `samples` is expected mono float32 in [-1, 1] (the
    same convention `click.py`'s buffers use).
    """
    if samples.size == 0:
        return []

    floor_amp = 10.0 ** (floor_db / 20.0)
    above = np.abs(samples) >= floor_amp
    gap_frames = max(1, int(round(gap_s * sample_rate)))

    segments: list[tuple[int, int]] = []
    in_segment = False
    seg_start = 0
    last_above = -1
    silence_count = 0

    for i, is_above in enumerate(above):
        if is_above:
            if not in_segment:
                in_segment = True
                seg_start = i
            last_above = i
            silence_count = 0
        elif in_segment:
            silence_count += 1
            if silence_count >= gap_frames:
                segments.append((seg_start, last_above + 1))
                in_segment = False

    if in_segment:
        segments.append((seg_start, last_above + 1))
    return segments


def bind_segments(
    segments: Sequence[Segment],
    tracklist: Sequence[TracklistEntry],
    tolerance_s: float = 1.5,
) -> list[Binding]:
    """Match each segment, in order, to the next unclaimed track.

    docs/04-sources.md: "a segment within +/-1.5s of the expected duration
    binds silently; anything further out stops and asks... never bind on
    a guess." Fewer segments than tracks is fine (a capture that stopped
    early -- the rest stay needs-audio); MORE segments than tracks is
    refused outright, because there is no principled way to decide which
    extra segment is spurious. An overflowed segment is refused
    unconditionally (H3, docs/04-sources.md's "What will bite": "a
    dropout is silent... refuse to bind a segment that reported one") --
    never merely warned about, and never silently skipped either: the
    whole binding stops so the capture can be re-run for that one track.
    """
    if len(segments) > len(tracklist):
        raise WoodshedError(
            f"{len(segments)} captured segments but only {len(tracklist)} tracks "
            "in the tracklist -- refusing to guess which is which"
        )

    bindings: list[Binding] = []
    for position, (segment, entry) in enumerate(zip(segments, tracklist, strict=False), start=1):
        if segment.overflowed:
            raise WoodshedError(
                f"segment {position} ({entry.slug!r}) reported an audio callback "
                "overflow -- a dropout is silent, so this segment is not safe to "
                "bind; re-capture it"
            )
        drift = abs(segment.duration_s - entry.duration_s)
        if drift > tolerance_s:
            raise WoodshedError(
                f"segment {position} is {segment.duration_s:.1f}s but {entry.slug!r} "
                f"is expected to be {entry.duration_s:.1f}s ({drift:.1f}s off, more "
                f"than the {tolerance_s:g}s tolerance) -- refusing to guess; check "
                "for a dropped or extra track in the capture"
            )
        bindings.append(Binding(slug=entry.slug, segment=segment))
    return bindings


# ── the device half: unverified without real WASAPI loopback hardware ──────


def list_devices() -> list[Device]:
    """Every WASAPI loopback-capable device PyAudioWPatch can see."""
    pyaudiowpatch = require_module("pyaudiowpatch", "capture")
    p = pyaudiowpatch.PyAudio()
    try:
        return [
            Device(
                index=info["index"],
                name=info["name"],
                sample_rate=int(info["defaultSampleRate"]),
                channels=int(info["maxInputChannels"]),
            )
            for info in p.get_loopback_device_info_generator()
        ]
    finally:
        p.terminate()


def default_device() -> Device:
    """The default render device's own loopback -- "the default render
    device" docs/04-sources.md names as capture's own default."""
    pyaudiowpatch = require_module("pyaudiowpatch", "capture")
    p = pyaudiowpatch.PyAudio()
    try:
        info = p.get_default_wasapi_loopback()
        return Device(
            index=info["index"],
            name=info["name"],
            sample_rate=int(info["defaultSampleRate"]),
            channels=int(info["maxInputChannels"]),
        )
    finally:
        p.terminate()


def capture(
    device: Device,
    out_dir: str | Path,
    *,
    floor_db: float = -50.0,
    gap_s: float = 1.2,
    on_level: Callable[[float], None] | None = None,
) -> Iterator[Segment]:
    """Arm *device*, record until Ctrl-C (or the stream ends on its own),
    then split on silence and yield one `Segment` per track found -- each
    written out to its own WAV file (`segment-NNN.wav`) under *out_dir*.

    **Ring buffer to disk, not memory, DURING the live capture**
    (docs/04-sources.md: "an hour of stereo float32 at 48kHz is 1.4GB"):
    every chunk read from the stream is written straight to a scratch file
    as it arrives, so the live recording itself never holds more than one
    chunk at a time. Splitting re-reads that scratch file once, after
    capture ends -- a bounded, one-time cost, not a live accumulating one.

    **Overflow tracking is per-chunk, not sticky** (H3): PyAudio's blocking
    `read()` raises on an input overflow only when
    `exception_on_overflow=True` (its default is `False`, which would
    silently swallow exactly the dropout docs/04-sources.md says must
    never be silent) -- caught here, the LOST chunk's frame position is
    recorded and written as silence (keeping every later frame position
    correctly aligned with the scratch file's real length), and a segment
    is marked `overflowed` only if one of those lost positions actually
    falls inside it.

    *on_level*, if given, is called with each chunk's peak amplitude
    (0..1) -- the capture screen's level meter, and nothing else reads it.

    Unverified without real hardware -- see the module docstring.
    """
    pyaudiowpatch = require_module("pyaudiowpatch", "capture")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_frames = max(1, device.sample_rate // 10)  # ~100ms chunks

    p = pyaudiowpatch.PyAudio()
    stream = p.open(
        format=pyaudiowpatch.paFloat32,
        channels=device.channels,
        rate=device.sample_rate,
        frames_per_buffer=chunk_frames,
        input=True,
        input_device_index=device.index,
    )

    scratch_path = out_dir / "_capture_scratch.f32"
    overflow_positions: list[int] = []
    frame_position = 0

    try:
        with open(scratch_path, "wb") as scratch:
            while True:
                try:
                    raw = stream.read(chunk_frames, exception_on_overflow=True)
                except OSError:
                    overflow_positions.append(frame_position)
                    silence = np.zeros(chunk_frames, dtype=np.float32)
                    scratch.write(silence.tobytes())
                    frame_position += chunk_frames
                    continue

                chunk = np.frombuffer(raw, dtype=np.float32)
                if device.channels > 1:
                    chunk = chunk.reshape(-1, device.channels).mean(axis=1)
                if on_level is not None and chunk.size:
                    on_level(float(np.max(np.abs(chunk))))
                scratch.write(chunk.astype(np.float32).tobytes())
                frame_position += len(chunk)
    except KeyboardInterrupt:
        pass  # Ctrl-C ends the capture; whatever was recorded still gets split below
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    if not scratch_path.is_file():
        return
    samples = np.fromfile(scratch_path, dtype=np.float32)
    scratch_path.unlink(missing_ok=True)

    existing = len(list(out_dir.glob("segment-*.wav")))
    for offset, (start, end) in enumerate(
        split_on_silence(samples, device.sample_rate, floor_db, gap_s), start=1
    ):
        overflowed = any(start <= pos < end for pos in overflow_positions)
        _write_wav_mono_16bit(
            out_dir / f"segment-{existing + offset:03d}.wav",
            samples[start:end],
            device.sample_rate,
        )
        yield Segment(
            start_frame=start, end_frame=end, sample_rate=device.sample_rate,
            overflowed=overflowed,
        )


def _write_wav_mono_16bit(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """*samples* (float32, [-1, 1]) as a 16-bit mono PCM WAV -- stdlib
    only. Same encoding server.py's `_encode_wav_mono` uses for the click;
    duplicated rather than imported, since `server.py` sits above this
    module and importing it here would be backwards."""
    import wave as wave_module

    clamped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clamped * 32767.0).astype("<i2")
    with wave_module.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm16.tobytes())
