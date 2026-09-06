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

**Phase 1.5, Group U** adds the capture-first workflow: capture a whole
set against an empty setlist, THEN split and name each piece. Three
functions below own everything after a `Segment` exists and before it is
a bound song -- `extract_segment` (an ffmpeg frame-range cut, the same
subprocess-cut shape `render.py` will use for section spans),
`bind_segment_to_song` (T2's own missing finishing step, shared rather
than duplicated) and `bind_segment_as_new_song`. They import
`woodshed.library`/`woodshed.manifest` at module top level -- unlike
`sections.py`'s deliberate Tier-0 purity, this module was never in that
enumerated stdlib+numpy-only tier (it already needs `pyaudiowpatch`), and
these three functions are, in effect, "write a song.yaml" operations of
the same kind `cli.py`/`server.py` already perform at the top of the
stack, not maths that has to stay provable with nothing installed.

**`capture()` gained a `raw_path` parameter** for this workflow: the
existing `out_dir`/per-segment-WAV behaviour (H2's single-song CLI path)
is completely unchanged when `raw_path` is omitted. When given, the whole
continuous recording is kept as ONE file there instead (CLAUDE.md's fourth
server-write category, `capture/<timestamp>.wav`) and no per-segment files
are written at all -- `extract_segment` cuts a segment out of it later, on
demand, once that segment has a name. The post-recording split/write logic
is factored into `_finish_capture`, which takes plain arrays and paths (no
device, no PyAudioWPatch) specifically so this new branch is exercisable
with synthetic audio, the same "two pure-enough functions carry the actual
risk" reasoning the module docstring above already gives for
`split_on_silence`/`bind_segments`.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from woodshed.errors import WoodshedError
from woodshed.library import Repo, slugify
from woodshed.manifest import Recording, Song, hash_file, save_song, whole_song_section
from woodshed.tools import locate_tool, require_module

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
    "extract_segment",
    "bind_segment_to_song",
    "bind_segment_as_new_song",
]

#: `bind_segment_to_song`'s fallback when no explicit tuning is supplied --
#: the same literal default `cli.py`'s `add`/`capture` subcommands already
#: give `--tuning` (there is no setlist parameter here to derive one from;
#: see the function's own docstring for why that reading of the plan's
#: "setlist's own tuning" note doesn't fit this signature).
_DEFAULT_TUNING = "E standard"


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
    on_overflow: Callable[[], None] | None = None,
    raw_path: str | Path | None = None,
    stop_event: threading.Event | None = None,
    on_stream_ready: Callable[[object], None] | None = None,
) -> Iterator[Segment]:
    """Arm *device*, record until Ctrl-C, *stop_event* is set, or the stream
    ends on its own, then split on silence and yield one `Segment` per
    track found.

    **`stop_event`, added for Phase 1.5's T2** (`POST /api/capture/stop`):
    the CLI's own `KeyboardInterrupt` handling below only ever stops a
    capture running in the process's own MAIN thread -- a background
    thread (`capture_runner.py`'s whole reason to exist) never receives a
    Ctrl-C at all, and there is no safe way to raise one into it from
    outside. Checked once per ~100ms chunk (`stop_event.is_set()`, top of
    the loop, same place `while True:` used to be unconditional), so a
    stop lands within about one chunk, not instantly but never far off
    either. `None` (the default) preserves H2's original CLI behaviour
    exactly -- Ctrl-C is still the only way to stop a foreground capture.

    **`on_stream_ready`, found live 2026-09-06** ("capture stuck at
    'Stopping...' forever"): `stream.read()` below is a BLOCKING call with
    no timeout, and `stop_event` is only ever checked *between* reads. A
    real WASAPI loopback device that stops rendering anything (the common
    case: the backing track finished playing, then Stop was pressed) can
    stop delivering packets entirely, so the read in progress never
    returns and `stop_event` is never seen again -- the capture thread is
    wedged for good, not merely slow. `on_stream_ready`, if given, is
    called once with the raw PyAudio stream object right after it opens,
    so `capture_runner.CaptureRunner.stop()` can hold onto it and force-
    close it from a DIFFERENT thread once a bounded wait shows the capture
    thread never noticed `stop_event` on its own -- closing a PortAudio
    stream out from under a blocked `read()` is the standard way to
    unstick it (the call raises instead of hanging), which the `finally`
    block below already treats as an ordinary end of capture. This is the
    device half's own kind of risk the module docstring already names --
    exercised here only via a fake stream in the test suite, never real
    hardware.

    Two mutually exclusive output modes, selected by *raw_path* -- see
    `_finish_capture` for exactly what each writes:
    - omitted (default, H2's single-song CLI path): each segment is its own
      WAV file (`segment-NNN.wav`) under *out_dir*, unchanged from before
      Group U existed.
    - given (Group U's capture-first path): the whole continuous recording
      is written as ONE file at *raw_path* and no per-segment files are
      written at all; `Segment.start_frame`/`end_frame` stay valid frame
      offsets into that file for `extract_segment` to cut from later.

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
    *on_overflow*, if given, is called (no arguments) the moment an
    overflow is caught -- T2's `GET /api/capture/status` "was an overflow
    seen" flag, reported live rather than only after the fact via a
    finished `Segment.overflowed`.

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
    if on_stream_ready is not None:
        on_stream_ready(stream)

    scratch_path = out_dir / "_capture_scratch.f32"
    overflow_positions: list[int] = []
    frame_position = 0

    try:
        with open(scratch_path, "wb") as scratch:
            while stop_event is None or not stop_event.is_set():
                try:
                    raw = stream.read(chunk_frames, exception_on_overflow=True)
                except OSError:
                    if stop_event is not None and stop_event.is_set():
                        # A read error arriving AFTER stop_event was set is
                        # this function's own force-close (on_stream_ready's
                        # doc above) unsticking a read that was blocked past
                        # the point stop_event was last checked -- a clean
                        # stop, not a real overflow. Don't record fake
                        # silence or flag a dropout for it.
                        break
                    overflow_positions.append(frame_position)
                    if on_overflow is not None:
                        on_overflow()
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
        # Best-effort, and tolerant of a stream this function's OWN
        # on_stream_ready caller may already have force-closed from another
        # thread (see that parameter's doc above) -- a double stop/close on
        # a PortAudio stream raises, and that is never worth surfacing as
        # this capture's own error when the recording itself is already
        # safely on disk.
        try:
            stream.stop_stream()
        except OSError:
            pass
        try:
            stream.close()
        except OSError:
            pass
        p.terminate()

    if not scratch_path.is_file():
        return
    samples = np.fromfile(scratch_path, dtype=np.float32)
    scratch_path.unlink(missing_ok=True)

    yield from _finish_capture(
        samples, device.sample_rate, floor_db, gap_s, overflow_positions,
        out_dir, raw_path,
    )


def _finish_capture(
    samples: np.ndarray,
    sample_rate: int,
    floor_db: float,
    gap_s: float,
    overflow_positions: Sequence[int],
    out_dir: str | Path,
    raw_path: str | Path | None,
) -> Iterator[Segment]:
    """The part of `capture()` that runs after the live recording ends --
    factored out so it can be exercised directly with synthetic audio (no
    PyAudioWPatch, no device), unlike `capture()` itself, which the module
    docstring already leaves unverified without real hardware.

    See `capture()`'s own docstring for the two output modes; this is
    where they're actually implemented.
    """
    if raw_path is not None:
        raw_path = Path(raw_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav_mono_16bit(raw_path, samples, sample_rate)
        for start, end in split_on_silence(samples, sample_rate, floor_db, gap_s):
            overflowed = any(start <= pos < end for pos in overflow_positions)
            yield Segment(
                start_frame=start, end_frame=end, sample_rate=sample_rate,
                overflowed=overflowed,
            )
        return

    out_dir = Path(out_dir)
    existing = len(list(out_dir.glob("segment-*.wav")))
    for offset, (start, end) in enumerate(
        split_on_silence(samples, sample_rate, floor_db, gap_s), start=1
    ):
        overflowed = any(start <= pos < end for pos in overflow_positions)
        _write_wav_mono_16bit(
            out_dir / f"segment-{existing + offset:03d}.wav",
            samples[start:end],
            sample_rate,
        )
        yield Segment(
            start_frame=start, end_frame=end, sample_rate=sample_rate,
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


# ── Phase 1.5, Group U: split now, name later ───────────────────────────────


def extract_segment(raw_audio_path: str | Path, segment: Segment, dest_path: str | Path) -> None:
    """ffmpeg-cuts `[start_frame/sample_rate, end_frame/sample_rate)` out of
    *raw_audio_path* -- the single continuous recording `capture(raw_path=
    ...)` wrote -- into *dest_path* (FLAC).

    A real ffmpeg process, the same subprocess-cut shape `render.py`
    (Phase 2) will use for section spans, not a manual sample copy -- so the
    output is real, playable, seekable audio, and works on whatever format
    the raw file happens to be in.
    """
    raw_audio_path = Path(raw_audio_path)
    dest_path = Path(dest_path)
    if not raw_audio_path.is_file():
        raise WoodshedError(f"no such raw capture recording: {raw_audio_path}")

    start_s = segment.start_frame / segment.sample_rate
    duration_s = segment.duration_s
    ffmpeg = locate_tool("ffmpeg").path
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg, "-v", "error", "-nostdin", "-y",
            "-ss", f"{start_s:.6f}",
            "-i", str(raw_audio_path),
            "-t", f"{duration_s:.6f}",
            str(dest_path),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise WoodshedError(
            f"ffmpeg could not extract a segment from {raw_audio_path}:\n"
            + "\n".join(tail)
        )


def bind_segment_to_song(
    repo: Repo,
    slug: str,
    raw_audio_path: str | Path,
    segment: Segment,
    *,
    tuning: str | None = None,
) -> None:
    """Finish binding one already-captured *segment* to *slug*'s audio.

    This is T2's own missing finishing step ("it never says how a finished
    segment actually becomes the target song's bound audio") and Group U's
    "existing" bind mode both need -- shared here rather than each inventing
    a copy.

    *slug* must already exist somewhere (a setlist's running order, say)
    with no `song.yaml` of its own yet -- `manifest.Song.recording` is a
    required field, so "needs-audio, nothing bound" can only mean no
    song.yaml is on disk at all (see `library.py`/`setlist.py`, and
    `dashboard.js`'s own note that a needs-audio row's title IS its slug
    until bound). Refuses outright when a song.yaml already exists for
    *slug* -- this can never silently overwrite a song that already has
    real audio. An overflowed segment is refused unconditionally, same as
    `bind_segments`' own rule -- not relaxed just because the caller is new.

    *tuning* defaults to `_DEFAULT_TUNING` when not given. The plan's own
    note ("defaults to the setlist's own tuning... `add`'s existing
    default") doesn't fit this signature -- there is no setlist parameter
    here to derive one from, and `add`'s *actual* existing default
    (`cli.py`'s `--tuning`) is the fixed literal `"E standard"`, not
    anything setlist-derived -- so that literal is what's reused. A
    documented judgement call, same pattern P1/R1 already used for a plan
    note that didn't quite match the code it described.
    """
    if segment.overflowed:
        raise WoodshedError(
            "this segment reported an audio callback overflow -- a dropout is "
            "silent, so it is not safe to bind; re-capture it"
        )
    song_path = repo.song_dir(slug) / "song.yaml"
    if song_path.is_file():
        raise WoodshedError(
            f"'{slug}' already has a song.yaml -- bind_segment_to_song refuses "
            "to overwrite a song that already has real audio"
        )

    dest_dir = repo.audio_dir(slug)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}.flac"
    extract_segment(raw_audio_path, segment, dest)

    song = Song(
        slug=slug,
        title=slug,
        artist="",
        recording=Recording(
            file=f"audio/{dest.name}",
            sha256=hash_file(dest),
            duration_s=segment.duration_s,
            tuning=tuning or _DEFAULT_TUNING,
            source="capture",
        ),
        sections=[whole_song_section(segment.duration_s)],
    )
    song_path.parent.mkdir(parents=True, exist_ok=True)
    save_song(song, song_path)

    # Lazy, symmetrical import: cli.py already imports FROM capture.py this
    # same way (inside cmd_capture, not at module top level), so neither
    # module needs the other at import time. Peaks + best-effort tempo
    # auto-detection, same as bind_song_file's own callers get -- found
    # live 2026-09-06, applies here too: a capture-bound song should not
    # need a separate manual `woodshed analyze` either.
    from woodshed.cli import analyze_after_bind
    analyze_after_bind(repo, slug, dest)


def bind_segment_as_new_song(
    repo: Repo,
    raw_audio_path: str | Path,
    segment: Segment,
    *,
    title: str,
    artist: str,
    tuning: str,
) -> str:
    """Bind *segment* as a brand-new song -- Group U's own step, for a
    segment that doesn't correspond to any slug already sitting in a
    setlist. Returns the new slug.

    Refuses a title that slugifies to an existing song, naming the
    collision rather than silently colliding with it. Same unconditional
    overflow refusal as `bind_segment_to_song`.
    """
    if segment.overflowed:
        raise WoodshedError(
            "this segment reported an audio callback overflow -- a dropout is "
            "silent, so it is not safe to bind; re-capture it"
        )
    slug = slugify(title)
    if not slug:
        raise WoodshedError(f"{title!r} does not slugify to anything usable")
    if slug in repo.list_songs():
        raise WoodshedError(
            f"{title!r} slugifies to {slug!r}, which already names a song -- "
            "pick a different title"
        )

    dest_dir = repo.audio_dir(slug)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}.flac"
    extract_segment(raw_audio_path, segment, dest)

    song = Song(
        slug=slug,
        title=title,
        artist=artist,
        recording=Recording(
            file=f"audio/{dest.name}",
            sha256=hash_file(dest),
            duration_s=segment.duration_s,
            tuning=tuning,
            source="capture",
        ),
        sections=[whole_song_section(segment.duration_s)],
    )
    song_path = repo.song_dir(slug) / "song.yaml"
    song_path.parent.mkdir(parents=True, exist_ok=True)
    save_song(song, song_path)

    from woodshed.cli import analyze_after_bind  # see bind_segment_to_song's own note
    analyze_after_bind(repo, slug, dest)
    return slug
