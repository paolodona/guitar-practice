"""Guitar-only isolation via Demucs (Phase 1.5, Group S). Optional heavy
dependency -- the third module CLAUDE.md's Layering section names, beside
`analyze.py` (librosa) and `render.py` (rubberband); nothing above this
line may import it at module top level, and this module's own top level
does not import `demucs` either -- `require_module` (below) is the only
place that does, exactly like the other two.

**Cross-referenced, never copied**, per CLAUDE.md's rule for the sibling
repos: the demucs invocation (model list, `--two-stems`, the flatten-
output cleanup) mirrors `rambass-live/src/rambass/stems.py`'s own
`separate()` -- read for the subprocess shape, not imported (no cross-repo
import exists anywhere in this codebase, and this is not the place to
start). `htdemucs_6s` is the only Demucs model with a guitar stem at all
-- confirmed against that file's own `MODELS` tuple
(`htdemucs_ft`/`htdemucs`/`mdx_extra`/`htdemucs_6s`): the plain four-stem
split (drums/bass/other/vocals) has no guitar output, and nothing there
suggests a six-stem alternative besides this one. Deliberately simpler
than the sibling in one respect: `rambass-live`'s own `separate()` first
looks for a `demucs` console script on PATH, falling back to `python -m
demucs`; here `require_module("demucs", "separate")` already confirms the
package imports in THIS interpreter, so the subprocess always runs via
`sys.executable -m demucs` -- one invocation path, not two, because the
fallback case is the only one this check can actually promise works.

**Isolation happens on the section's own clip, not the whole recording**,
cut with `ffmpeg` first -- the same `-ss`/`-i`/`-t` shape `capture.py`'s
`extract_segment` already uses, just on SOURCE seconds
(`section.start_s`/`end_s`) rather than a segment's frame range. A
section is usually far shorter than the whole song, and running Demucs
(CPU: genuinely slow, `doctor.py`'s S4 check says so) on the whole
recording for one 20-second solo would be needless minutes of work.
Isolation happens BEFORE the speed/pitch stretch, not after -- so
`stem_fingerprint` below carries no `speed`/`semitones`/`crossfade_ms`,
unlike `render.py`'s own `span_fingerprint` (Phase 2, not yet built).

**The fingerprint is `render.py`'s own scheme**, mirrored here since Group
S was inserted ahead of Group I: 8 hex of sha256 over
`(recording.sha256, start_s, end_s, pre_roll_s, SEPARATOR_VERSION)`, each
field at fixed precision, so a boundary nudge of even 10ms misses the
cache exactly like `render.py`'s will -- the same "fingerprint in the
filename, not a separate invalidation step" reasoning, applied one module
early.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import Section, Song
from woodshed.tools import locate_tool, require_module

__all__ = [
    "SEPARATOR_VERSION",
    "GUITAR_MODEL",
    "demucs_available",
    "stem_fingerprint",
    "stem_cache_path",
    "isolate_guitar",
]

#: Bumped by hand whenever the demucs invocation below changes (model,
#: flags, or the ffmpeg cut it operates on) -- `render.py`'s own
#: `RENDERER_VERSION` plays the identical role for the speed/pitch cache.
SEPARATOR_VERSION = 1

#: The only Demucs model with a guitar stem at all -- see module doc.
GUITAR_MODEL = "htdemucs_6s"


def demucs_available() -> bool:
    """Whether `demucs` can be imported in THIS interpreter -- a
    non-raising check for `doctor.py`'s S4 report and `screens/
    practice.js`'s "Guitar only" toggle (S3) to disable itself against,
    without needing to catch `require_module`'s own `WoodshedError` just
    to ask."""
    try:
        import demucs  # noqa: F401
    except ImportError:
        return False
    return True


def stem_fingerprint(song: Song, section: Section, *, pre_roll_s: float = 0.0) -> str:
    """8 hex of sha256 over `(recording.sha256, start_s, end_s, pre_roll_s,
    SEPARATOR_VERSION)` -- `render.py`'s own `span_fingerprint` scheme,
    minus `crossfade_ms`/`speed`/`semitones` (isolation happens BEFORE the
    speed/pitch stretch, not after -- see module doc)."""
    raw = (
        f"{song.recording.sha256}|{section.start_s:.6f}|{section.end_s:.6f}|"
        f"{pre_roll_s:.6f}|{SEPARATOR_VERSION}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def stem_cache_path(repo: Repo, slug: str, section_id: str, fp: str) -> Path:
    """`songs/<slug>/cache/stems/<section_id>-guitar-<fp>.flac` -- a NEW
    cache subdirectory under the existing per-song `cache/`, still "always
    safe to delete", still owed to Phase 2 Group I's `evict()` once it
    exists (S4's own flagged gap -- `evict()` doesn't exist yet either)."""
    return repo.cache_dir(slug) / "stems" / f"{section_id}-guitar-{fp}.flac"


def _run_ffmpeg(argv: list[str], *, action: str) -> None:
    result = subprocess.run(argv, capture_output=True, check=False)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise WoodshedError(f"ffmpeg could not {action}:\n" + "\n".join(tail))


def isolate_guitar(
    repo: Repo,
    song: Song,
    section: Section,
    *,
    pre_roll_s: float = 0.0,
    model: str = GUITAR_MODEL,
    device: str | None = None,
    force: bool = False,
) -> Path:
    """Isolate the guitar out of *section* (plus `pre_roll_s` of lead-in)
    via Demucs, caching the result under `stem_cache_path`. Skips the work
    entirely when `force=False` and the fingerprinted cache file already
    exists -- the same skip-if-cached rule `render.render_section` (Phase
    2) will use.

    Steps: `ffmpeg` cuts `[start_s - pre_roll_s, end_s]` out of the song's
    own source audio -> `demucs -n <model> --two-stems guitar` on that clip
    only, in a scratch temp directory (never under the repo -- this is
    intermediate work, not one of CLAUDE.md's four write categories) ->
    the kept `guitar.wav` is `ffmpeg`-transcoded to FLAC at the final cache
    path; `no_guitar.wav` and the whole scratch directory are discarded.
    """
    fp = stem_fingerprint(song, section, pre_roll_s=pre_roll_s)
    dest = stem_cache_path(repo, song.slug, section.id, fp)
    if not force and dest.is_file():
        return dest

    require_module("demucs", "separate")
    ffmpeg = locate_tool("ffmpeg").path
    source_path = repo.song_dir(song.slug) / song.recording.file
    if not source_path.is_file():
        raise WoodshedError(f"no such audio file: {source_path}")

    clip_start_s = max(0.0, section.start_s - pre_roll_s)
    clip_duration_s = section.end_s - clip_start_s

    with tempfile.TemporaryDirectory(prefix="woodshed-stem-") as scratch:
        scratch_path = Path(scratch)
        clip_path = scratch_path / "clip.wav"
        _run_ffmpeg(
            [
                ffmpeg, "-v", "error", "-nostdin", "-y",
                "-ss", f"{clip_start_s:.6f}",
                "-i", str(source_path),
                "-t", f"{clip_duration_s:.6f}",
                str(clip_path),
            ],
            action=f"cut section {section.id!r} from {source_path}",
        )

        demucs_out = scratch_path / "demucs_out"
        demucs_argv = [
            sys.executable, "-m", "demucs",
            "-n", model,
            "--two-stems", "guitar",
            "-o", str(demucs_out),
        ]
        if device:
            demucs_argv += ["-d", device]
        demucs_argv.append(str(clip_path))
        result = subprocess.run(demucs_argv, capture_output=True, check=False)
        if result.returncode != 0:
            tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-10:]
            raise WoodshedError("demucs failed to isolate the guitar:\n" + "\n".join(tail))

        guitar_wav = demucs_out / model / clip_path.stem / "guitar.wav"
        if not guitar_wav.is_file():
            raise WoodshedError(
                f"demucs produced no guitar.wav for section {section.id!r} "
                f"(expected {guitar_wav})"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(guitar_wav), str(dest)],
            action=f"transcode the isolated guitar for section {section.id!r} to FLAC",
        )

    return dest
