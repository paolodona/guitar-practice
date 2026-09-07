"""The offline render cache: one FLAC file per `(section, speed, semitones)`
-- CLAUDE.md invariants 8 and 9, docs/03-audio-engine.md's whole reason to
exist. Practice plays a pre-rendered, decoded `AudioBuffer` with a native,
sample-exact loop; a real-time stretcher cannot put the seam in the same
place twice (docs/03-audio-engine.md, "Trap 3").

**Phase 2, Group I -- pulled forward into Phase 1.5, 2026-09-06 (Paolo's own
call).** The plan's Group S flagged a real phase-ordering gap: S2
("`render_section` gains a `source` parameter") and S4 ("`evict()` is
extended to also walk `cache/stems/`") both extend THIS module, which did
not exist yet. Rather than deferring S2/S4 into Phase 2 proper, this module
is built now, to its own already-fixed Phase 2 spec (the plan's "Module map,
with the signatures fixed now") -- just earlier than originally scheduled.
`evict` below already walks the whole `cache/` tree (render files AND
`cache/stems/`, Group S's own subdirectory) from this first pass, rather
than needing a later "extension" commit -- see its own docstring.

`rubberband` binary, optional -- the second module (beside `separate.py`'s
demucs) CLAUDE.md's Layering section names as allowed a heavy/external
dependency. Nothing above this line may import it at module top level, and
this module's own top level does not shell out to it either --
`tools.locate_tool` is the only place that resolves the binary, called
lazily inside `render_section`.

**`render_section` is `ffmpeg` (cut `[start - pre_roll, end]`) -> `rubberband
--time <1/speed> --pitch <semitones> --formant --fine` -> an equal-power
crossfade of the last `crossfade_ms` into the head of the loop region,
baked in with numpy -> `ffmpeg`-encoded FLAC.** See `_bake_crossfade` for
exactly what "baked in" means: `clock.Render.loop_start`/`loop_end` are
computed once (the module CLAUDE.md invariant 3 owns) and used to trim the
file to `loop_end` after blending -- the tail beyond it is never played by
a native loop (`buffer.loopEnd = loop_end`), so keeping it around would
only be wasted bytes.

**The fingerprint is the fix for a silent stale loop** (docs/03-audio-engine.md):
`(section_id, speed, semitones)` alone does not see a dragged boundary --
the rendered file also bakes in `start_s`, `end_s`, `pre_roll_s`,
`crossfade_ms` and which source file was on disk. Putting the fingerprint
*in the filename* makes a stale render impossible rather than merely
unlikely, and needs no invalidation step that could be forgotten: a changed
span simply misses the cache. `RENDERER_VERSION` is bumped by hand whenever
the argv or the crossfade maths changes -- same discipline as `separate.py`'s
`SEPARATOR_VERSION`.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import wave as wave_module
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from woodshed import ladder
from woodshed.clock import Render, pre_roll_seconds
from woodshed.errors import WoodshedError
from woodshed.ladder import LadderConfig, LadderState
from woodshed.library import Repo
from woodshed.manifest import Section, Song, effective_pre_roll_beats, load_song
from woodshed.tools import locate_tool

__all__ = [
    "RENDERER_VERSION",
    "span_fingerprint",
    "cache_key",
    "cache_path",
    "render_section",
    "plan_ahead",
    "evict",
]

#: Bumped by hand whenever the rubberband argv or the crossfade maths below
#: changes -- see the module docstring and `separate.SEPARATOR_VERSION`.
RENDERER_VERSION = 2

#: `<section_id>@<speed>x<semitones>st[-guitar]-<fp>.flac`, directly under
#: `cache/<slug>/` (never a subdirectory -- unlike `separate.py`'s
#: `cache/stems/`, this is the tool's original cache shape). The `-guitar`
#: segment is Group S2's own addition -- absent entirely for `source="mix"`,
#: so a pre-S2 filename is still a perfectly valid match with `source=None`.
_RENDER_RE = re.compile(
    r"^(?P<section_id>.+)@(?P<speed>-?\d+(?:\.\d+)?)x"
    r"(?P<semitones>[+-]\d+)st(?:-(?P<source>guitar))?-(?P<fp>[0-9a-f]{8})\.flac$"
)
#: `<section_id>-guitar-<fp>.flac`, under `cache/stems/` -- separate.py's
#: own shape, reaped here too (see `evict`'s docstring).
_STEM_RE = re.compile(r"^(?P<section_id>.+)-guitar-(?P<fp>[0-9a-f]{8})\.flac$")


def _fmt_speed(speed_pct: float) -> str:
    """Render a whole-number speed without a trailing ".0" -- same
    formatting `ladder._fmt` uses for the same reason, duplicated rather
    than imported since that one is private to its own module."""
    if speed_pct == int(speed_pct):
        return str(int(speed_pct))
    return str(speed_pct)


def span_fingerprint(
    song: Song, section: Section, *, pre_roll_s: float = 0.0, crossfade_ms: float = 10.0
) -> str:
    """8 hex of sha256 over `(recording.sha256, start_s, end_s, pre_roll_s,
    crossfade_ms, pre_roll_every_pass, RENDERER_VERSION)`, each field at
    fixed precision -- a boundary nudge of even 10ms misses the cache.
    Mirrors `separate.stem_fingerprint`'s own shape (that one predates this
    by one module, having been built first -- see the module docstring).

    `pre_roll_every_pass` is read off the song rather than passed in (it is
    a song-level setting with no section override, unlike the pre-roll
    LENGTH) and it belongs in the hash because it moves where the crossfade
    is baked -- see `_bake_crossfade`. Two renders of the same span, one
    replaying its lead-in and one not, are different files.
    """
    raw = (
        f"{song.recording.sha256}|{section.start_s:.6f}|{section.end_s:.6f}|"
        f"{pre_roll_s:.6f}|{crossfade_ms:.3f}|{int(song.practice.pre_roll_every_pass)}|"
        f"{RENDERER_VERSION}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def cache_key(
    section_id: str, speed_pct: float, semitones: int, fp: str, *, source: str = "mix"
) -> str:
    """`"solo@60x-1st-3f9a2c11.flac"` -- the signed semitone count's own
    sign character is the only separator between it and the speed, so
    `-1` reads as `-1st` rather than `--1st`; a positive or zero shift
    reads `+2st`/`+0st`.

    **Group S2**: `source="guitar"` inserts a `-guitar` segment before the
    fingerprint (`"solo@60x-1st-guitar-3f9a2c11.flac"`), so a guitar-only
    render never collides with the mix's own -- both may exist, cached,
    at once. `source="mix"` (the default) is BYTE-FOR-BYTE what `cache_key`
    produced before S2 existed: every render made before this parameter was
    added keeps hitting the exact same cache file, not silently orphaned by
    a format change.
    """
    base = f"{section_id}@{_fmt_speed(speed_pct)}x{semitones:+d}st"
    if source == "guitar":
        return f"{base}-guitar-{fp}.flac"
    return f"{base}-{fp}.flac"


def cache_path(
    repo: Repo, slug: str, section_id: str, speed_pct: float, semitones: int, fp: str,
    *, source: str = "mix",
) -> Path:
    return repo.cache_dir(slug) / cache_key(section_id, speed_pct, semitones, fp, source=source)


def _run(argv: list[str], *, action: str) -> None:
    result = subprocess.run(argv, capture_output=True, check=False)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise WoodshedError(f"could not {action}:\n" + "\n".join(tail))


#: PCM sample width (bytes) -> (numpy dtype to read as, full-scale divisor).
#: 24-bit has no native numpy dtype, so it is handled separately below.
_PCM_SCALE = {1: (np.uint8, 128.0), 2: ("<i2", 32768.0), 4: ("<i4", 2147483648.0)}


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """*(samples, sample_rate)* -- `samples` is float32 in [-1, 1], shape
    `(n_frames, channels)`. Stdlib `wave` + numpy only, matching
    CLAUDE.md's Layering for every module below the heavy-dependency line
    except this one -- no soundfile/scipy, same restriction
    `server.py`'s own `_encode_wav_mono` already keeps for the click.
    Handles 8/16/24/32-bit PCM since rubberband's own output width is not
    contractually fixed -- see the module's "Before starting" note."""
    with wave_module.open(str(path), "rb") as reader:
        channels = reader.getnchannels()
        sampwidth = reader.getsampwidth()
        sample_rate = reader.getframerate()
        raw = reader.readframes(reader.getnframes())

    if sampwidth == 3:
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        as_i32 = (
            arr[:, 0].astype(np.int32)
            | (arr[:, 1].astype(np.int32) << 8)
            | (arr[:, 2].astype(np.int8).astype(np.int32) << 16)
        )
        flat = as_i32.astype(np.float32) / 8388608.0
    elif sampwidth in _PCM_SCALE:
        dtype, scale = _PCM_SCALE[sampwidth]
        flat = np.frombuffer(raw, dtype=dtype).astype(np.float32)
        if sampwidth == 1:
            flat = flat - 128.0
        flat = flat / scale
    else:
        raise WoodshedError(f"unsupported WAV sample width: {sampwidth} bytes ({path})")

    samples = flat.reshape(-1, channels)
    return samples, sample_rate


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """16-bit PCM, *samples* shape `(n_frames, channels)` in [-1, 1] --
    matches `server.py`'s own `_encode_wav_mono` convention (int16, the
    only width this module ever WRITES; it reads wider ones, never writes
    them -- the final `ffmpeg` transcode to FLAC is what actually persists,
    so 16-bit here is an intermediate, not the cache's own bit depth)."""
    channels = samples.shape[1]
    clamped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clamped * 32767.0).astype("<i2")
    with wave_module.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm16.tobytes())


def _bake_crossfade(samples: np.ndarray, sample_rate: int, render: Render) -> np.ndarray:
    """Equal-power crossfade of the tail into the head of the loop region,
    then trim to `loop_end` -- docs/03-audio-engine.md's "Looping,
    precisely": "10ms equal-power crossfade, baked into the rendered file
    (so the browser just loops)".

    The tail (`render.total`'s last `crossfade_ms`) fades OUT
    (`cos(theta)`) while the head (`render.loop_start`'s first
    `crossfade_ms`) fades IN (`sin(theta)`) over the SAME `crossfade_ms`
    window, and the blend overwrites the head in place. The file is then
    trimmed to `loop_end` samples: a native loop (`loopStart=loop_start,
    loopEnd=loop_end`) never plays past `loop_end` even on its first pass,
    so the true (unblended) tail beyond it is never heard and would only
    be wasted bytes if kept.

    `samples` and the three sample-index conversions below are all in the
    SAME clock -- render's own PLAYBACK seconds (CLAUDE.md invariant 3) --
    computed once from `render` and nowhere re-derived.
    """
    total_samples = len(samples)
    crossfade_n = int(round(render.crossfade_ms / 1000.0 * sample_rate))
    loop_start_n = int(round(render.loop_start * sample_rate))
    loop_end_n = min(int(round(render.loop_end * sample_rate)), total_samples)

    if crossfade_n <= 0 or loop_end_n <= loop_start_n:
        return samples[: max(loop_end_n, 0)]

    tail_start_n = max(0, total_samples - crossfade_n)
    tail = samples[tail_start_n:total_samples]
    head = samples[loop_start_n : loop_start_n + crossfade_n]
    n = min(len(tail), len(head), crossfade_n)
    if n <= 0:
        return samples[:loop_end_n]

    theta = np.linspace(0.0, np.pi / 2, n, dtype=np.float64)
    fade_in = np.sin(theta).astype(np.float32)[:, None]
    fade_out = np.cos(theta).astype(np.float32)[:, None]
    blended = head[:n] * fade_in + tail[:n] * fade_out

    out = samples.copy()
    out[loop_start_n : loop_start_n + n] = blended
    return out[:loop_end_n]


def render_section(
    repo: Repo,
    song: Song,
    section: Section,
    speed_pct: float,
    semitones: int,
    *,
    crossfade_ms: float = 10.0,
    pre_roll_s: float = 0.0,
    force: bool = False,
    source: str = "mix",
) -> Path:
    """Render `section` at `speed_pct`/`semitones`, caching the result under
    `cache_path`. Skips the work entirely when `force=False` and the
    fingerprinted cache file already exists.

    `speed_pct` is a PERCENT (60.0 means 60%) everywhere in this module
    except the argv passed to `rubberband`, which wants a ratio -- see
    docs/03-audio-engine.md's "The two knobs": `--time` is `1 / (speed_pct
    / 100)` (**never a resample -- trap 1**), `--pitch` is the raw
    semitone count.

    **Group S2**: `source="guitar"` isolates the guitar first
    (`separate.isolate_guitar`, itself cached and fingerprinted the same
    way) and feeds THAT clip into rubberband instead of the original
    recording -- the isolation happens before the speed/pitch stretch, not
    after (`separate.py`'s own module doc). `span_fingerprint` does not
    take `source`: the SPAN's identity (what to cut, from where in time)
    is the same regardless of which audio it is eventually cut from, only
    `cache_key`/`cache_path` (the OUTPUT file) need to tell the two apart.
    `source="mix"` (the default) is unchanged from before this parameter
    existed -- every assertion in this module's own test suite about the
    mix path still holds bit-for-bit.
    """
    fp = span_fingerprint(song, section, pre_roll_s=pre_roll_s, crossfade_ms=crossfade_ms)
    dest = cache_path(repo, song.slug, section.id, speed_pct, semitones, fp, source=source)
    if not force and dest.is_file():
        return dest

    ffmpeg = locate_tool("ffmpeg").path
    rubberband = locate_tool("rubberband").path

    clip_start_s = max(0.0, section.start_s - pre_roll_s)
    clip_duration_s = section.end_s - clip_start_s
    speed = speed_pct / 100.0
    ratio = 1.0 / speed

    with tempfile.TemporaryDirectory(prefix="woodshed-render-") as scratch:
        scratch_path = Path(scratch)

        if source == "guitar":
            from woodshed.separate import isolate_guitar  # see the docstring above

            clip_path = isolate_guitar(repo, song, section, pre_roll_s=pre_roll_s, force=force)
        else:
            recording_path = repo.song_dir(song.slug) / song.recording.file
            if not recording_path.is_file():
                raise WoodshedError(f"no such audio file: {recording_path}")
            clip_path = scratch_path / "clip.wav"
            _run(
                [
                    ffmpeg, "-v", "error", "-nostdin", "-y",
                    "-ss", f"{clip_start_s:.6f}",
                    "-i", str(recording_path),
                    "-t", f"{clip_duration_s:.6f}",
                    str(clip_path),
                ],
                action=f"cut section {section.id!r} from {recording_path}",
            )

        stretched_path = scratch_path / "stretched.wav"
        _run(
            [
                rubberband,
                "--time", f"{ratio:.6f}",
                "--pitch", str(semitones),
                "--formant", "--fine",
                str(clip_path), str(stretched_path),
            ],
            action=f"stretch section {section.id!r}",
        )

        samples, sample_rate = _read_wav(stretched_path)
        render = Render(
            start_s=section.start_s, end_s=section.end_s, pre_roll_s=pre_roll_s,
            speed=speed, crossfade_ms=crossfade_ms,
            # Where the loop wraps back to decides where the crossfade is
            # baked (_bake_crossfade), so the flag has to reach the clock:
            # an every-pass render folds its tail over sample 0, a
            # once-only render over the post-lead-in head.
            pre_roll_every_pass=song.practice.pre_roll_every_pass,
        )
        baked = _bake_crossfade(samples, sample_rate, render)

        baked_path = scratch_path / "baked.wav"
        _write_wav(baked_path, baked, sample_rate)

        dest.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(baked_path), str(dest)],
            action=f"encode the render of section {section.id!r} to FLAC",
        )

    return dest


def plan_ahead(
    repo: Repo, song: Song, section: Section, state: LadderState, cfg: LadderConfig, semitones: int
) -> list[Path]:
    """Render the rung ABOVE `state.speed` (per `ladder.rungs(cfg)`) ahead
    of time, so it is already decoded by the time practice actually
    advances there (docs/03-audio-engine.md: "Render ahead"). Returns `[]`
    once `state.speed` is already at or past `cfg.target_speed` -- nothing
    left to pre-render.

    Synchronous -- **this module owns no threads** (matching `capture.py`'s
    own split: `capture_runner.py` is where backgrounding lives, not
    `capture.py` itself). The caller (`render_runner.RenderRunner`, called
    from `server.py`) is what runs this on a background thread.
    """
    next_rung = next((r for r in ladder.rungs(cfg) if r > state.speed), None)
    if next_rung is None:
        return []
    pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
    path = render_section(
        repo, song, section, next_rung, semitones,
        crossfade_ms=song.practice.loop_crossfade_ms, pre_roll_s=pre_roll_s,
    )
    return [path]


def evict(repo: Repo, max_gb: float, *, keep: Iterable[Path] = ()) -> list[Path]:
    """Delete cache files, oldest (by **mtime**, not atime) first, until the
    whole repo's cache usage is back under `max_gb` -- or until nothing is
    left. Returns every path actually deleted, oldest first.

    **`keep` is never evicted, whatever the budget says.** The server runs
    this after every render, and with a budget smaller than one file the
    sweep would otherwise delete the very render the request in flight is
    waiting for -- whereupon its 202 poll starts it again, forever (found
    by review 2026-09-07). You do not evict the thing you just made.

    **mtime, not atime**: NTFS last-access updates are off by default on
    Windows, so an atime-ordered LRU silently degenerates to arbitrary
    order -- see the module map's own note on this, carried over unchanged.

    **Reaps orphans first, regardless of budget**: a file whose
    `section_id` no longer exists in its song, or whose CURRENT
    fingerprint (recomputed from the song's present state) no longer
    matches the one baked into its own filename -- the exact class of
    staleness a dragged boundary produces (docs/03-audio-engine.md) -- is
    never worth keeping even under budget.

    **Walks `cache/stems/` too**, from this first pass -- `separate.py`'s
    own cache subdirectory (Phase 1.5, Group S, built one module before
    this one), which this module did not exist to cover until now. This is
    what the plan's Group S flagged as S4's own job ("`evict()` is
    extended to also walk `cache/stems/`"); it is folded into this
    function's very first version instead of a later extension pass,
    since both were built in the same session -- see the module docstring.
    `cache/peaks-*.json` is a different cache (`peaks.py`'s own, with no
    fingerprint scheme) and is left alone entirely.
    """
    deleted: list[Path] = []
    budget_bytes = max_gb * 1e9
    candidates: list[Path] = []
    # Resolved, because the caller's path and a path built from rglob need
    # to compare equal for the same file.
    protected = {Path(p).resolve() for p in keep}

    for slug in repo.list_songs():
        cache_dir = repo.cache_dir(slug)
        if not cache_dir.is_dir():
            continue
        try:
            song = load_song(repo.song_dir(slug) / "song.yaml")
        except (WoodshedError, ValidationError, OSError):
            song = None

        for path in cache_dir.rglob("*.flac"):
            if not path.is_file():
                continue
            if path.resolve() in protected:
                # Not even as an orphan: `keep` is the render a request is
                # waiting on right now, and its fingerprint is by
                # definition the current one.
                continue
            if song is not None and _is_orphan(song, path):
                path.unlink(missing_ok=True)
                deleted.append(path)
                continue
            candidates.append(path)

    candidates = [p for p in candidates if p.is_file()]
    candidates.sort(key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in candidates)
    for path in candidates:
        if total <= budget_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        deleted.append(path)
        total -= size

    return deleted


def _is_orphan(song: Song, path: Path) -> bool:
    """Whether `path` (a cache file already confirmed to belong to `song`'s
    own cache tree) no longer matches any of `song`'s current sections --
    either the section itself is gone, or its span/pre-roll/crossfade has
    moved since this file was rendered (`evict`'s own docstring)."""
    stem_match = _STEM_RE.match(path.name)
    if stem_match is not None:
        return _stem_is_orphan(song, path, stem_match)
    render_match = _RENDER_RE.match(path.name)
    if render_match is not None:
        return _render_is_orphan(song, render_match)
    return False  # not a shape this module recognises -- leave it alone


def _find_section(song: Song, section_id: str) -> Section | None:
    return next((s for s in song.sections if s.id == section_id), None)


def _render_is_orphan(song: Song, match: re.Match) -> bool:
    section = _find_section(song, match.group("section_id"))
    if section is None:
        return True
    pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
    expected = span_fingerprint(
        song, section, pre_roll_s=pre_roll_s, crossfade_ms=song.practice.loop_crossfade_ms
    )
    return expected != match.group("fp")


def _stem_is_orphan(song: Song, path: Path, match: re.Match) -> bool:
    del path  # not needed -- kept for symmetry with _render_is_orphan's signature
    section = _find_section(song, match.group("section_id"))
    if section is None:
        return True
    from woodshed.separate import stem_fingerprint  # heavy-module-adjacent; see module doc

    pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
    expected = stem_fingerprint(song, section, pre_roll_s=pre_roll_s)
    return expected != match.group("fp")
