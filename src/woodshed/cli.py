"""``woodshed`` -- the command line for the practice tool.

Run ``woodshed`` with no arguments (or ``--help``) for the command list, or
``woodshed <cmd> -h`` for one command. ``woodshed doctor`` first, if something
is not working.

Every command here is a thin wrapper around a function the server also
calls -- see docs/01-architecture.md's "one action table" rule and
CLAUDE.md's "the repo is the database": nothing here duplicates validation
that ``manifest.py`` or ``sections.py`` already does.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from woodshed import ledger, sections
from woodshed.errors import WoodshedError
from woodshed.ledger import Rep
from woodshed.library import Repo, find_root, slugify
from woodshed.manifest import (
    Recording,
    Section,
    Song,
    Tempo,
    hash_file,
    load_song,
    save_song,
    whole_song_section,
)
from woodshed.tuning import KNOWN_TUNINGS

#: Fallback for `woodshed serve --port`'s default if `woodshed.server` cannot
#: be imported (see `_serve_default_port`) -- docs/01-architecture.md's own
#: example. `woodshed.server.DEFAULT_PORT` is the real single source of
#: truth whenever the module is importable.
_FALLBACK_PORT = 8477

EPILOGUE = """\
typical order of work for one song:
  woodshed add "path/to/song.wav" --title "Can't Stop" --artist "..."
  woodshed section cant-stop add "Full solo" 178.4 262.9
  woodshed serve                       # draw the rest, loop, and count reps

or check the toolchain first:
  woodshed doctor
"""

#: name -> the phase that will build it (docs/07-roadmap.md), for the honest
#: "not built yet" refusal on a command this unit does not implement.
_NOT_YET_IMPLEMENTED = {
    "render": "Phase 2 -- the offline render cache",
    "status": "Phase 2 -- the progress dashboard",
    "scan": "Phase 3 -- library scan and file binding",
}


def use_utf8() -> None:
    """Make stdout and stderr able to carry the characters the CLI prints.

    Lifted from rambass-live/src/rambass/cli.py:71 (verbatim except the
    docstring's example command). Measured on Paolo's machine before this
    existed: on Windows a console that has not been switched to UTF-8 hands
    Python a ``cp1252`` stdout, so the *first* box-drawing or unicode-flat
    character raises ``UnicodeEncodeError`` and the command produces nothing
    at all -- with an error message that points at a codec rather than a
    codepage. Setting ``PYTHONIOENCODING=utf-8`` fixes it for one person on
    one machine; this fixes it everywhere.

    Everything here is best-effort on purpose: ``sys.stdout`` can be ``None``
    (pythonw, a frozen build) or something without ``reconfigure`` (a pipe
    wrapper, a test double), and none of that may take the CLI down before it
    has printed a word.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or getattr(stream, "encoding", "").lower() in (
                "utf-8", "utf8"):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


def _say(message: str = "") -> None:
    print(message)


def _repo() -> Repo:
    return Repo(root=find_root())


# ── commands: library ────────────────────────────────────────────────────
def _guess_title(path: Path) -> str:
    """A readable title from a bare filename, for `add` with no --title."""
    return path.stem.replace("_", " ").replace("-", " ").strip().title()


def _wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        rate = f.getframerate()
        return f.getnframes() / rate if rate else 0.0


def _ffprobe_duration_s(path: Path) -> float:
    # Imported lazily: tools.py is fine to import from the CLI tier, but a
    # command that never touches a non-wav file should not pay for it.
    from woodshed.tools import locate_tool

    try:
        location = locate_tool("ffprobe")
    except WoodshedError as exc:
        raise WoodshedError(
            f"can't read the duration of {path.name}: ffprobe is needed for "
            f"anything that isn't a .wav.\n{exc}"
        ) from exc
    result = subprocess.run(
        [location.path, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False,
    )
    text = (result.stdout or "").strip()
    if result.returncode != 0 or not text:
        raise WoodshedError(
            f"ffprobe could not read the duration of {path} "
            f"({(result.stderr or '').strip() or 'no output'})"
        )
    return float(text)


def _read_duration_s(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        return _wav_duration_s(path)
    return _ffprobe_duration_s(path)


def analyze_after_bind(repo: Repo, slug: str, audio_path: Path, *, auto_tempo: bool = True) -> None:
    """Peaks + best-effort tempo auto-detection for a freshly bound song --
    called automatically by `bind_song_file` and `capture.py`'s
    `bind_segment_to_song`/`bind_segment_as_new_song` so a waveform
    renders and a real tempo grid exists without a separate, manual
    `woodshed analyze` step (found live 2026-09-06: nothing about this
    needs an optional dependency except the tempo GUESS itself).

    Peaks are ALWAYS written: decoding is ffmpeg (already a hard
    requirement -- `_read_duration_s` already uses it for anything that
    isn't a `.wav`) and bucketing is pure numpy (`peaks.py`, Tier 2), no
    optional dependency involved. Tempo is auto-detected via librosa only
    when *auto_tempo* is true (the caller's own signal that nothing more
    specific was requested -- `bind_song_file` passes `bpm is None`, so an
    explicit `--bpm`/upload-form tempo is never silently overridden) AND
    librosa is actually installed; either being false leaves the song's
    existing tempo alone entirely, same graceful degrade `doctor.py`'s own
    optional checks use elsewhere -- never a hard failure just because the
    optional analysis piece is missing or not asked for.
    """
    from woodshed import peaks as peaks_module
    from woodshed.analyze import load_mono_audio

    samples, sr = load_mono_audio(audio_path)
    peaks_module.write_peaks(repo, slug, peaks_module.multi_resolution(samples, sr))

    if not auto_tempo:
        return
    from woodshed.analyze import detect_tempo, librosa_available
    if not librosa_available():
        return
    try:
        tempo = detect_tempo(audio_path)
    except WoodshedError as exc:
        _say(f"  ({slug}: tempo auto-detect skipped -- {exc})")
        return
    path = repo.song_dir(slug) / "song.yaml"
    song = load_song(path)
    song.tempo = tempo
    save_song(song, path)


def bind_song_file(
    repo: Repo,
    source: Path,
    *,
    title: str,
    artist: str = "",
    album: str | None = None,
    tuning: str = "E standard",
    slug: str | None = None,
    bpm: float | None = None,
    grid_offset_s: float = 0.0,
    time_signature: str = "4/4",
    dest_filename: str | None = None,
) -> Song:
    """Bind *source* as a new song: hash it, read its duration (ffprobe for
    anything that isn't a `.wav`), write `songs/<slug>/audio/<file>` plus a
    minimal `song.yaml`, then run `analyze_after_bind` automatically.
    Shared by `cmd_add` (CLI) and `POST /api/song/upload` (T1, Phase 1.5 --
    the first thing that can do this from a browser) -- CLAUDE.md's "one
    action table" instinct extended to "one binding function," not a
    second copy that can drift.

    Refuses a slug that already exists, same as `cmd_add` always did.
    *dest_filename* is the name to give the copied file under `audio/` when
    it differs from `source.name` -- the upload endpoint writes the
    incoming bytes to a generated temp file first, so `source.name` there
    is not the browser's own filename. *bpm* left as `None` (its own
    default) means "no explicit tempo was requested" -- `analyze_after_bind`
    reads exactly that to decide whether auto-detection is even allowed to
    run; an explicit `bpm` always wins outright, written as the song's
    tempo and never touched afterward.
    """
    if not source.is_file():
        raise WoodshedError(f"no such file: {source}")

    resolved_slug = slug or slugify(title)
    if not resolved_slug:
        raise WoodshedError(f"{title!r} does not slugify to anything usable; pass a slug")
    if resolved_slug in repo.list_songs():
        raise WoodshedError(
            f"songs/{resolved_slug}/song.yaml already exists. Pass a different slug."
        )

    audio_dir = repo.audio_dir(resolved_slug)
    audio_dir.mkdir(parents=True, exist_ok=True)
    dest = audio_dir / (dest_filename or source.name)
    if dest.resolve() != source.resolve():
        shutil.copy2(source, dest)

    duration_s = _read_duration_s(dest)
    song = Song(
        slug=resolved_slug,
        title=title,
        artist=artist,
        album=album,
        recording=Recording(
            file=f"audio/{dest.name}",
            sha256=hash_file(dest),
            duration_s=duration_s,
            tuning=tuning,
        ),
        tempo=Tempo(
            bpm=bpm if bpm is not None else 120.0, source="manual",
            grid_offset_s=grid_offset_s, time_signature=time_signature,
        ),
        sections=[whole_song_section(duration_s)],
    )
    song_path = repo.song_dir(resolved_slug) / "song.yaml"
    save_song(song, song_path)

    analyze_after_bind(repo, resolved_slug, dest, auto_tempo=(bpm is None))
    return load_song(song_path)  # re-read: analyze_after_bind may have updated tempo


def cmd_add(args: argparse.Namespace) -> int:
    repo = _repo()
    source = Path(args.path)
    if not source.is_file():
        raise WoodshedError(f"no such file: {source}")

    title = args.title or _guess_title(source)
    song = bind_song_file(
        repo, source,
        title=title, artist=args.artist, album=args.album, tuning=args.tuning,
        slug=args.slug, bpm=args.bpm, grid_offset_s=args.grid_offset,
        time_signature=args.time_signature,
    )
    path = repo.song_dir(song.slug) / "song.yaml"
    _say(f"added {song.slug!r}: {path}")
    if song.tempo.source == "manual":
        _say(f"  {song.recording.duration_s:.1f}s, {song.recording.tuning}, "
             f"bpm {song.tempo.bpm:g} (placeholder -- pass --bpm/--tap, or "
             f"`woodshed doctor` names the installer for auto-detection)")
    else:
        _say(f"  {song.recording.duration_s:.1f}s, {song.recording.tuning}, "
             f"bpm {song.tempo.bpm:g} (source: {song.tempo.source}, peaks cached)")
    return 0


# ── commands: sections ───────────────────────────────────────────────────
def _load_song(repo: Repo, needle: str) -> tuple[str, Path, Song]:
    slug = repo.find_song(needle)
    path = repo.song_dir(slug) / "song.yaml"
    return slug, path, load_song(path)


def _validate_and_save(song: Song, path: Path, new_sections: list[Section]) -> None:
    # The one call the server's POST /api/section will also make -- never
    # duplicate what sections.validate already checks (CLAUDE.md invariant 2).
    sections.validate(new_sections, song.recording.duration_s)
    song.sections = new_sections
    save_song(song, path)


def cmd_section_add(args: argparse.Namespace) -> int:
    repo = _repo()
    slug, path, song = _load_song(repo, args.slug)
    section_id = args.id or slugify(args.name)
    new_section = Section(
        id=section_id,
        name=args.name,
        start_s=args.start_s,
        end_s=args.end_s,
        snapped=args.snapped,
        target_speed=args.target_speed,
        ladder_step=args.ladder_step,
        reps_to_advance=args.reps_to_advance,
        notes=args.notes,
        patch=args.patch,
        lead_in_beats=args.lead_in_beats,
        full_song=args.full_song,
    )
    _validate_and_save(song, path, [*song.sections, new_section])
    _say(f"{slug}: added section {section_id!r} ({args.start_s:g}s-{args.end_s:g}s)")
    return 0


def cmd_section_update(args: argparse.Namespace) -> int:
    repo = _repo()
    slug, path, song = _load_song(repo, args.slug)
    index = next((i for i, s in enumerate(song.sections) if s.id == args.id), None)
    if index is None:
        raise WoodshedError(f"{slug}: no section {args.id!r}")

    overrides = {
        "name": args.name,
        "start_s": args.start_s,
        "end_s": args.end_s,
        "snapped": args.snapped,
        "target_speed": args.target_speed,
        "ladder_step": args.ladder_step,
        "reps_to_advance": args.reps_to_advance,
        "notes": args.notes,
        "patch": args.patch,
        "lead_in_beats": args.lead_in_beats,
        "full_song": args.full_song,
    }
    merged = song.sections[index].model_dump()
    merged.update({k: v for k, v in overrides.items() if v is not None})
    # Re-validate through the model constructor, not model_copy -- model_copy
    # does not re-run _check_span, and a bad edit (end_s <= start_s) must be
    # refused here exactly as it would be on first creation.
    updated = Section.model_validate(merged)

    new_sections = list(song.sections)
    new_sections[index] = updated
    _validate_and_save(song, path, new_sections)
    _say(f"{slug}: updated section {args.id!r}")
    return 0


def cmd_section_rm(args: argparse.Namespace) -> int:
    repo = _repo()
    slug, path, song = _load_song(repo, args.slug)
    remaining = [s for s in song.sections if s.id != args.id]
    if len(remaining) == len(song.sections):
        raise WoodshedError(f"{slug}: no section {args.id!r}")
    song.sections = remaining
    save_song(song, path)
    _say(f"{slug}: removed section {args.id!r}")
    return 0


# ── commands: setlists (Phase 1, F1's CLI surface -- setlist.py itself
#    landed with the server routes, but the command line was left unwired) ──
def cmd_setlist_list(args: argparse.Namespace) -> int:  # noqa: ARG001 -- fixed signature
    from woodshed.setlist import list_setlists

    repo = _repo()
    slugs = repo.list_setlists()
    if not slugs:
        _say('no setlists yet -- `woodshed setlist create "The Gig" --tuning "Eb standard"`')
        return 0
    for slug, setlist in zip(slugs, list_setlists(repo), strict=True):
        _say(f"{slug}: {setlist.name!r} ({setlist.tuning}, {len(setlist.songs)} songs)")
    return 0


def cmd_setlist_create(args: argparse.Namespace) -> int:
    from woodshed.manifest import Setlist
    from woodshed.setlist import create

    slug = args.slug or slugify(args.name)
    if not slug:
        raise WoodshedError(f"{args.name!r} does not slugify to anything usable; pass --slug")
    create(_repo(), slug, Setlist(
        name=args.name, tuning=args.tuning, date=args.date, venue=args.venue or "",
    ))
    _say(f"created setlist {slug!r}: {args.name!r} ({args.tuning})")
    return 0


def cmd_setlist_add_song(args: argparse.Namespace) -> int:
    from woodshed.setlist import add_song, load, save

    repo = _repo()
    setlist = add_song(load(repo, args.setlist), args.song, shift=args.shift)
    save(repo, args.setlist, setlist)
    _say(f"{args.setlist}: added {args.song!r}" +
         (f" (shift {args.shift:+d})" if args.shift is not None else " (shift: derived)"))
    return 0


def cmd_setlist_rm_song(args: argparse.Namespace) -> int:
    from woodshed.setlist import load, remove_song, save

    repo = _repo()
    setlist = remove_song(load(repo, args.setlist), args.song)
    save(repo, args.setlist, setlist)
    _say(f"{args.setlist}: removed {args.song!r}")
    return 0


def cmd_setlist_shift(args: argparse.Namespace) -> int:
    from woodshed.setlist import set_shift

    shift = None if args.shift.lower() == "none" else int(args.shift)
    updated = set_shift(_repo(), args.setlist, args.song, shift)
    entry = next(e for e in updated.songs if e.slug == args.song)
    _say(f"{args.setlist}/{args.song}: shift set to "
         f"{'derived' if entry.shift is None else f'{entry.shift:+d}'}")
    return 0


# ── commands: the ledger ─────────────────────────────────────────────────
def cmd_log(args: argparse.Namespace) -> int:
    repo = _repo()
    slug, _path, song = _load_song(repo, args.slug)
    if args.section not in {s.id for s in song.sections}:
        raise WoodshedError(f"{slug}: no section {args.section!r}")

    rep = Rep(
        id=str(uuid.uuid4()),
        t=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        song=slug,
        section=args.section,
        speed=args.speed,
        semitones=args.semitones,
        passed=not args.no_pass,
        clean=args.clean,
        loop_s=args.loop_s,
        setlist=args.setlist,
        source=args.source,
    )
    ledger.append(repo, rep)
    verb = "clean pass" if rep.clean else ("pass" if rep.passed else "attempt")
    _say(f"{slug}/{args.section}: logged a {verb} at {rep.speed:g}%")
    return 0


# ── commands: analysis ────────────────────────────────────────────────────
def cmd_analyze(args: argparse.Namespace) -> int:
    # analyze.py's own top-level imports need no librosa (require_module is
    # only called inside detect_tempo) -- so beat_grid/load_mono_audio are
    # always safe to import; only the auto-detect branch below reaches for
    # detect_tempo, and only that branch pays for a missing librosa. Same
    # lazy-import reasoning as cmd_serve's `from woodshed.server import
    # make_server`: a command that does not need the optional piece must
    # still run without it installed.
    from woodshed import peaks as peaks_module
    from woodshed.analyze import beat_grid, load_mono_audio
    from woodshed.tempofit import tap_tempo

    repo = _repo()
    slug, path, song = _load_song(repo, args.slug)
    audio_path = repo.song_dir(slug) / song.recording.file
    if not audio_path.is_file():
        raise WoodshedError(f"{slug}: no such audio file: {audio_path}")

    grid_offset = args.grid_offset if args.grid_offset is not None else 0.0
    if args.bpm is not None:
        # A typed number is not a fit -- confidence is null, always. See
        # Tempo.confidence's "null when typed by hand" and docs/07-roadmap.md's
        # "manual override, ... recording which it was".
        tempo = Tempo(bpm=args.bpm, source="manual", grid_offset_s=grid_offset,
                       time_signature=args.time_signature, confidence=None)
    elif args.tap:
        bpm = tap_tempo(args.tap)
        if bpm <= 0:
            raise WoodshedError("--tap needs at least two tap timestamps")
        tempo = Tempo(bpm=round(bpm, 2), source="tapped", grid_offset_s=grid_offset,
                       time_signature=args.time_signature, confidence=None)
    else:
        from woodshed.analyze import detect_tempo  # the one librosa-gated path
        tempo = detect_tempo(audio_path)

    song.tempo = tempo
    save_song(song, path)

    # One decode serves both tempo detection and the peaks cache --
    # docs/01-architecture.md:111 lists them together for exactly this
    # reason -- and peaks are cached regardless of which tempo path was
    # used above, since they have nothing to do with tempo at all. A second
    # ffmpeg pass on the auto-detect path is the price of detect_tempo's
    # fixed, audio-in-tempo-out signature (see analyze.py); it is a few
    # seconds once per `woodshed analyze`, not a hot path.
    samples, sr = load_mono_audio(audio_path)
    peaks_module.write_peaks(repo, slug, peaks_module.multi_resolution(samples, sr))

    grid = beat_grid(tempo, song.recording.duration_s)
    _say(f"{slug}: bpm {tempo.bpm:g} (source: {tempo.source}, "
         f"confidence {tempo.confidence:.2f})" if tempo.confidence is not None
         else f"{slug}: bpm {tempo.bpm:g} (source: {tempo.source})")
    _say(f"  grid offset {tempo.grid_offset_s:.3f}s, {len(grid)} beats over "
         f"{song.recording.duration_s:.1f}s, peaks cached")
    return 0


# ── commands: the server ─────────────────────────────────────────────────
def cmd_serve(args: argparse.Namespace) -> int:
    repo = _repo()
    # Lazy import: server.py is a sibling unit's file and this keeps a
    # missing/not-yet-built server.py from breaking every other command --
    # only `serve` itself needs it, exactly the reason cmd_doctor imports
    # doctor lazily below.
    try:
        from woodshed.server import make_server
    except ImportError as exc:
        raise WoodshedError(
            "the server isn't available yet (woodshed.server.make_server) -- "
            f"{exc}"
        ) from exc

    server = make_server(repo, port=args.port)
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    _say(f"woodshed serving at {address}  (ctrl-c stops it, or POST /api/shutdown)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _say("\ninterrupted")
    finally:
        server.server_close()
    return 0


# ── commands: doctor ──────────────────────────────────────────────────────
def cmd_doctor(args: argparse.Namespace) -> int:
    from woodshed.doctor import report

    text, ok = report(_repo())
    _say(text)
    return 0 if ok else 1


# ── commands: capture (Phase 1, H2) ─────────────────────────────────────
def cmd_capture(args: argparse.Namespace) -> int:
    # capture.py's own top-level import needs no pyaudiowpatch (require_module
    # is only called inside the device functions) -- same lazy-import
    # reasoning as cmd_analyze's librosa-gated branch.
    from woodshed.capture import capture as run_capture
    from woodshed.capture import default_device, list_devices

    if args.list_devices:
        for device in list_devices():
            _say(f"[{device.index}] {device.name}  ({device.sample_rate} Hz, "
                 f"{device.channels}ch)")
        return 0

    if args.queue:
        raise WoodshedError(
            "--queue needs an imported tracklist (title/artist/duration per "
            "track) to bind captured segments against -- that's Phase 3's M1 "
            "(Spotify import), not built yet. Capture one song at a time for "
            'now: `woodshed capture "Song title" --artist ...`.'
        )

    if args.split:
        return _cmd_capture_split(args)

    if not args.title:
        raise WoodshedError('a title is required: `woodshed capture "Song title" --artist ...` '
                             "(or --list-devices to see what's available)")

    repo = _repo()
    title = args.title
    slug = args.slug or slugify(title)
    if not slug:
        raise WoodshedError(f"{title!r} does not slugify to anything usable; pass --slug")
    if slug in repo.list_songs():
        raise WoodshedError(
            f"songs/{slug}/song.yaml already exists. Pass --slug to capture this "
            "as a different song."
        )

    if args.device:
        needle = args.device.lower()
        device = next((d for d in list_devices() if needle in d.name.lower()), None)
        if device is None:
            raise WoodshedError(
                f"no loopback device matching {args.device!r} -- "
                "`woodshed capture --list-devices` lists what's available"
            )
    else:
        device = default_device()

    _say(f"arming {device.name!r} ({device.sample_rate} Hz) -- press play over "
         "there now (one track only); recording stops after "
         f"{args.gap_s:g}s of silence, or ctrl-c")
    out_dir = repo.song_dir(slug) / "audio"
    segments = list(run_capture(
        device, out_dir, floor_db=args.floor_db, gap_s=args.gap_s,
    ))

    if not segments:
        raise WoodshedError("nothing captured -- no sound crossed the noise floor")
    if len(segments) > 1:
        raise WoodshedError(
            f"captured {len(segments)} segments but expected exactly one -- "
            "either more than one track played, or noise crossed the floor "
            "mid-silence. The WAV files are still under "
            f"{out_dir} for inspection; re-run once only the intended track "
            "will play."
        )
    segment = segments[0]
    if segment.overflowed:
        raise WoodshedError(
            "this capture reported an audio callback overflow -- a dropout is "
            "silent, so it is not safe to bind; re-capture it"
        )

    dest = out_dir / "segment-001.wav"
    song = Song(
        slug=slug,
        title=title,
        artist=args.artist,
        album=args.album,
        recording=Recording(
            file=f"audio/{dest.name}",
            sha256=hash_file(dest),
            duration_s=segment.duration_s,
            tuning=args.tuning,
            source="capture",
        ),
        tempo=Tempo(
            bpm=args.bpm if args.bpm is not None else 120.0, source="manual",
            grid_offset_s=args.grid_offset, time_signature=args.time_signature,
        ),
        sections=[whole_song_section(segment.duration_s)],
    )
    path = repo.song_dir(slug) / "song.yaml"
    save_song(song, path)

    analyze_after_bind(repo, slug, dest, auto_tempo=(args.bpm is None))
    song = load_song(path)  # re-read: analyze_after_bind may have updated tempo
    _say(f"captured {segment.duration_s:.1f}s -> {slug!r}: {path}")
    if song.tempo.source == "manual":
        _say(f"  bpm {song.tempo.bpm:g} (placeholder -- pass --bpm, or "
             "`woodshed doctor` names the installer for auto-detection)")
    else:
        _say(f"  bpm {song.tempo.bpm:g} (source: {song.tempo.source}, peaks cached)")
    return 0


# ── commands: capture --split, capture-bind (Phase 1.5, Group U, U4) ─────
#
# CLI parity for the capture-first workflow `screens/capture.js` and
# `server.py`'s `/api/capture/*` routes already give the browser -- "lower
# priority, may be cut without blocking this phase's gate" per the plan,
# built anyway since both read/write the SAME on-disk session state U2
# defines (`capture_session.py`), not a second bookkeeping scheme.
#
# One naming departure from the plan's own text, worth saying rather than
# silently diverging: the plan describes `woodshed capture bind <index>
# "<title>" ...` as a SUBCOMMAND of `capture`. argparse cannot host that
# alongside `capture`'s own existing bare `title` positional (H2's single-
# song flow) without an ambiguity between "the next token is a sub-command
# name" and "the next token is the title" -- so binding is its own
# top-level command, `capture-bind`, instead.
def _cmd_capture_split(args: argparse.Namespace) -> int:
    """`woodshed capture --split`: record until Ctrl-C (or `--gap-s`
    trailing silence), split on silence, and print one line per detected
    segment -- never binding anything itself. Writes the SAME
    `capture_session.py` sidecar `POST /api/capture/start|stop` does
    (`capture_runner.CaptureRunner._run`'s own `capture(raw_path=...)` +
    `start_session` call, mirrored here rather than imported, since this
    runs in the foreground on the CLI's own main thread, not a background
    one)."""
    if args.title:
        raise WoodshedError(
            "--split records a whole capture session and doesn't take a title -- "
            'omit it, or drop --split and use `woodshed capture "Song title" '
            "--artist ...` for a single song"
        )

    from woodshed import capture_session
    from woodshed.capture import capture as run_capture
    from woodshed.capture import default_device, list_devices

    if args.device:
        needle = args.device.lower()
        device = next((d for d in list_devices() if needle in d.name.lower()), None)
        if device is None:
            raise WoodshedError(
                f"no loopback device matching {args.device!r} -- "
                "`woodshed capture --list-devices` lists what's available"
            )
    else:
        device = default_device()

    repo = _repo()
    repo.capture_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    raw_path = repo.capture_dir / f"{timestamp}.wav"

    _say(f"arming {device.name!r} ({device.sample_rate} Hz) -- press play over "
         f"there now; ctrl-c (or {args.gap_s:g}s of trailing silence) stops the capture")
    segments = list(run_capture(
        device, raw_path.parent, floor_db=args.floor_db, gap_s=args.gap_s,
        raw_path=raw_path,
    ))

    if not segments:
        raw_path.unlink(missing_ok=True)
        raise WoodshedError("nothing captured -- no sound crossed the noise floor")

    session = capture_session.start_session(repo, raw_path, segments)
    for entry in session.entries:
        flag = "  OVERFLOW -- not safe to bind, re-capture it" if entry.overflowed else ""
        _say(f"[{entry.index}] {entry.duration_s:.1f}s{flag}")
    _say(
        f'{len(session.entries)} segment(s) captured -- `woodshed capture-bind <index> '
        '"<title>" --artist ... --tuning ...` to bind one'
    )
    return 0


def cmd_capture_bind(args: argparse.Namespace) -> int:
    """`woodshed capture-bind <index> "<title>" --artist ... --tuning ... [--setlist
    <slug>]` -- binds one pending segment from the CURRENT capture session
    (`capture_session.current_session`) as a brand-new song, calling the
    SAME `bind_segment_as_new_song` `POST /api/capture/bind`'s `mode="new"`
    calls (`server.py`'s own docstring for that route names it explicitly)
    -- not a second binding path that could drift from the server's."""
    from woodshed import capture_session
    from woodshed.capture import Segment, bind_segment_as_new_song
    from woodshed.setlist import add_song
    from woodshed.setlist import load as load_setlist
    from woodshed.setlist import save as save_setlist

    repo = _repo()
    session = capture_session.current_session(repo)
    if session is None:
        raise WoodshedError(
            "no capture session pending -- `woodshed capture --split` first"
        )
    entry = next((e for e in session.pending if e.index == args.index), None)
    if entry is None:
        raise WoodshedError(f"no such pending capture segment: {args.index}")

    segment = Segment(
        start_frame=entry.start_frame, end_frame=entry.end_frame,
        sample_rate=entry.sample_rate, overflowed=entry.overflowed,
    )
    slug = bind_segment_as_new_song(
        repo, session.raw_path, segment,
        title=args.title, artist=args.artist, tuning=args.tuning,
    )
    capture_session.resolve(repo, args.index, capture_session.STATUS_BOUND)

    _say(f"bound segment {args.index} ({segment.duration_s:.1f}s) -> {slug!r}")
    if args.setlist:
        setlist = load_setlist(repo, args.setlist)
        setlist = add_song(setlist, slug)
        save_setlist(repo, args.setlist, setlist)
        _say(f"  added to setlist {args.setlist!r}")
    return 0


# ── commands: not yet implemented ────────────────────────────────────────
def _make_stub(name: str, phase: str) -> Callable[[argparse.Namespace], int]:
    def _cmd(args: argparse.Namespace) -> int:  # noqa: ARG001 - fixed signature
        raise WoodshedError(
            f"`woodshed {name}` is not built yet -- {phase}. "
            "See docs/07-roadmap.md for the build order."
        )
    return _cmd


# ── parser ────────────────────────────────────────────────────────────────
def _add_section_common_args(parser: argparse.ArgumentParser, *, required: bool) -> None:
    speed_kw = {"type": float}
    step_kw = {"type": float, "default": None}
    reps_kw = {"type": int, "default": None}
    if required:
        speed_kw["default"] = 100.0
    parser.add_argument("--snapped", choices=["beat", "bar", "free"],
                         default="free" if required else None,
                         help="how the boundary was placed (default: free)")
    parser.add_argument("--target-speed", dest="target_speed",
                         help="the speed (percent) this section is worked toward",
                         **speed_kw)
    parser.add_argument("--ladder-step", dest="ladder_step",
                         help="per-section override of the song's ladder step",
                         **step_kw)
    parser.add_argument("--reps-to-advance", dest="reps_to_advance",
                         help="per-section override of reps needed to advance",
                         **reps_kw)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--patch", default=None,
                         help="a gx100 patch id, if this section names one")
    parser.add_argument("--lead-in-beats", dest="lead_in_beats", type=int, default=None,
                         help="per-section override of the song's pre_roll_beats")
    parser.add_argument("--full-song", dest="full_song",
                         action=argparse.BooleanOptionalAction,
                         default=False if required else None,
                         help="a whole-song entry: reps count, but it is excluded "
                              "from next/prev cycling and from the readiness bar "
                              "(default: false)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="woodshed",
        description="Slow a recording down, drop it to the band's tuning, "
                     "loop a section, and count the reps.",
        epilog=EPILOGUE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- add --
    p = sub.add_parser("add", help="bind an audio file: hash it, read its "
                                    "duration, write a minimal song.yaml")
    p.add_argument("path", help="path to the audio file")
    p.add_argument("--title", help="song title (default: guessed from the filename)")
    p.add_argument("--artist", default="", help="artist name")
    p.add_argument("--album", default=None)
    p.add_argument("--tuning", choices=KNOWN_TUNINGS, default="E standard",
                    help="what the RECORDING is in -- not what you play it in")
    p.add_argument("--slug", default=None, help="override the derived slug")
    p.add_argument("--bpm", type=float, default=None,
                    help="tempo, if known -- omit to auto-detect (needs librosa) "
                         "or fall back to a 120bpm placeholder")
    p.add_argument("--grid-offset", dest="grid_offset", type=float, default=0.0,
                    help="where bar 1 beat 1 lands in the file, in seconds")
    p.add_argument("--time-signature", dest="time_signature", default="4/4")
    p.set_defaults(func=cmd_add)

    # -- section --
    # `slug` sits on the GROUP parser, not each subcommand -- so the shape on
    # the command line matches docs/01-architecture.md's
    # `woodshed section <slug> add "Full solo" 178.4 262.9` exactly.
    p = sub.add_parser("section", help="create, update or delete a section")
    p.add_argument("slug", help="song reference (slug, or a fuzzy title)")
    section_sub = p.add_subparsers(dest="section_command", metavar="<subcommand>")

    sp = section_sub.add_parser("add", help="add a new section")
    sp.add_argument("name", help="section name, e.g. \"Full solo\"")
    sp.add_argument("start_s", type=float, help="start, in seconds into the recording")
    sp.add_argument("end_s", type=float, help="end, in seconds into the recording")
    sp.add_argument("--id", default=None, help="section id (default: slugified name)")
    _add_section_common_args(sp, required=True)
    sp.set_defaults(func=cmd_section_add)

    sp = section_sub.add_parser("update", help="update fields of an existing section")
    sp.add_argument("id", help="the section id to update")
    sp.add_argument("--name", default=None)
    sp.add_argument("--start-s", dest="start_s", type=float, default=None)
    sp.add_argument("--end-s", dest="end_s", type=float, default=None)
    _add_section_common_args(sp, required=False)
    sp.set_defaults(func=cmd_section_update)

    sp = section_sub.add_parser("rm", help="delete a section")
    sp.add_argument("id", help="the section id to delete")
    sp.set_defaults(func=cmd_section_rm)

    # -- log --
    p = sub.add_parser("log", help="append a rep to practice/reps.jsonl")
    p.add_argument("slug", help="song reference (slug, or a fuzzy title)")
    p.add_argument("section", help="the section id practised")
    p.add_argument("--speed", type=float, required=True, help="percent of original tempo")
    p.add_argument("--semitones", type=int, default=0, help="transpose applied for this rep")
    p.add_argument("--clean", action="store_true", help="mark the pass clean (confirmed)")
    p.add_argument("--no-pass", dest="no_pass", action="store_true",
                    help="the loop was seeked or paused -- log an attempt, not a pass")
    p.add_argument("--loop-s", dest="loop_s", type=float, default=0.0,
                    help="wall-clock length of the pass, in seconds")
    p.add_argument("--setlist", default=None, help="which running order this was practised for")
    p.add_argument("--source", choices=["midi", "keyboard", "ui", "auto"],
                    default="keyboard", help="which input filed this rep (default: keyboard)")
    p.set_defaults(func=cmd_log)

    # -- serve --
    p = sub.add_parser("serve", help="start the local web app")
    # Imported here, not at module scope: cli.py keeps its imports lazy (see
    # cmd_serve), and the default must be the one constant server.py itself
    # binds to, not a second copy of it -- rambass-live/src/rambass/cli.py's
    # `_add_serve_args` makes the identical choice for the same reason.
    try:
        from woodshed.server import DEFAULT_PORT as _default_port
    except ImportError:
        _default_port = _FALLBACK_PORT
    p.add_argument("--port", type=int, default=_default_port,
                    help=f"port to serve on (default {_default_port})")
    p.set_defaults(func=cmd_serve)

    # -- doctor --
    p = sub.add_parser("doctor", help="check ffmpeg / rubberband / librosa / "
                                       "the toolchain are usable")
    p.set_defaults(func=cmd_doctor)

    # -- analyze --
    p = sub.add_parser("analyze", help="detect tempo and grid offset, cache peaks")
    p.add_argument("slug", help="song reference (slug, or a fuzzy title)")
    override = p.add_mutually_exclusive_group()
    override.add_argument("--bpm", type=float, default=None,
                           help="skip detection: a typed bpm (source: manual, "
                                "confidence: null)")
    override.add_argument("--tap", type=float, action="append", default=None,
                           metavar="SECONDS",
                           help="skip detection: a tap timestamp in seconds -- "
                                "repeat for each tap (needs at least two); bpm is "
                                "the median of the last 8 inter-tap intervals "
                                "(source: tapped, confidence: null)")
    p.add_argument("--grid-offset", dest="grid_offset", type=float, default=None,
                   help="where bar 1 beat 1 lands in the file, in seconds "
                        "(only used with --bpm/--tap; ignored for detection)")
    p.add_argument("--time-signature", dest="time_signature", default="4/4",
                   help="only used with --bpm/--tap; detection always assumes 4/4")
    p.set_defaults(func=cmd_analyze)

    # -- setlist --
    p = sub.add_parser("setlist", help="create or edit a setlist")
    setlist_sub = p.add_subparsers(dest="setlist_command", metavar="<subcommand>")

    sp = setlist_sub.add_parser("list", help="list every setlist")
    sp.set_defaults(func=cmd_setlist_list)

    sp = setlist_sub.add_parser("create", help="create a new setlist")
    sp.add_argument("name", help='the setlist\'s name, e.g. "Ramba S.S. -- the set"')
    sp.add_argument("--tuning", choices=KNOWN_TUNINGS, required=True,
                     help="the BAND's tuning -- what makes the transpose")
    sp.add_argument("--slug", default=None,
                     help="override the derived slug (default: slugified name)")
    sp.add_argument("--date", default=None, help="YYYY-MM-DD, optional; drives the countdown")
    sp.add_argument("--venue", default=None)
    sp.set_defaults(func=cmd_setlist_create)

    sp = setlist_sub.add_parser("add-song", help="add a song to a setlist's running order")
    sp.add_argument("setlist", help="the setlist's slug")
    sp.add_argument("song", help="the song's slug")
    sp.add_argument("--shift", type=int, default=None,
                     help="explicit override; omit to derive from the setlist's tuning")
    sp.set_defaults(func=cmd_setlist_add_song)

    sp = setlist_sub.add_parser("rm-song", help="remove a song from a setlist")
    sp.add_argument("setlist", help="the setlist's slug")
    sp.add_argument("song", help="the song's slug")
    sp.set_defaults(func=cmd_setlist_rm_song)

    sp = setlist_sub.add_parser("shift", help="set (or clear) a song's shift override")
    sp.add_argument("setlist", help="the setlist's slug")
    sp.add_argument("song", help="the song's slug")
    sp.add_argument("shift", help="an integer, or 'none' to derive from the setlist's tuning")
    sp.set_defaults(func=cmd_setlist_shift)

    # -- capture --
    p = sub.add_parser("capture", help="arm loopback, split a playlist on the gaps")
    p.add_argument("title", nargs="?", default=None,
                    help="song title for a single-song capture (omit with --list-devices)")
    p.add_argument("--artist", default="")
    p.add_argument("--album", default=None)
    p.add_argument("--tuning", choices=KNOWN_TUNINGS, default="E standard",
                    help="what the RECORDING is in -- not what you play it in")
    p.add_argument("--slug", default=None, help="override the derived slug")
    p.add_argument("--bpm", type=float, default=None,
                    help="tempo, if known -- omit to auto-detect (needs librosa) "
                         "or fall back to a 120bpm placeholder")
    p.add_argument("--grid-offset", dest="grid_offset", type=float, default=0.0)
    p.add_argument("--time-signature", dest="time_signature", default="4/4")
    p.add_argument("--device", default=None, help="substring match on the device name "
                                                    "(default: the system's default render device)")
    p.add_argument("--floor-db", dest="floor_db", type=float, default=-50.0,
                    help="the noise floor, in dBFS (default: -50)")
    p.add_argument("--gap-s", dest="gap_s", type=float, default=1.2,
                    help="silence, in seconds, that ends a segment (default: 1.2)")
    p.add_argument("--list-devices", action="store_true",
                    help="list loopback-capable devices and exit")
    p.add_argument("--queue", default=None, metavar="SETLIST",
                    help="capture a whole setlist's tracklist in one pass "
                         "(needs Phase 3's Spotify import; not yet available)")
    p.add_argument("--split", action="store_true",
                    help="record a whole set, split on silence, and print the "
                         "segments found -- binds nothing; follow with "
                         "`woodshed capture-bind` (Phase 1.5, Group U, U4)")
    p.set_defaults(func=cmd_capture)

    # -- capture-bind: U4's own CLI parity for POST /api/capture/bind's
    #    mode="new" --
    p = sub.add_parser(
        "capture-bind",
        help="bind one pending `capture --split` segment as a new song",
    )
    p.add_argument("index", type=int,
                    help="the segment's index, from `capture --split`'s own output")
    p.add_argument("title", help="song title")
    p.add_argument("--artist", default="")
    p.add_argument("--tuning", choices=KNOWN_TUNINGS, default="E standard",
                    help="what the RECORDING is in -- not what you play it in")
    p.add_argument("--setlist", default=None, metavar="SLUG",
                    help="also add the new song to this setlist")
    p.set_defaults(func=cmd_capture_bind)

    # -- not yet implemented, registered so `--help` is honest about the
    #    tool's eventual shape (docs/01-architecture.md's full command list) --
    stub_help = {
        "render": "render a section into the offline cache (not yet implemented)",
        "status": "the dashboard, as text (not yet implemented)",
        "scan": "scan config.yaml's library_paths for audio to bind (not yet implemented)",
    }
    for name, phase in _NOT_YET_IMPLEMENTED.items():
        p = sub.add_parser(name, help=stub_help[name])
        p.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        p.set_defaults(func=_make_stub(name, phase))

    _remember_group_parsers(parser)
    return parser


def _remember_group_parsers(parser: argparse.ArgumentParser) -> None:
    """Let a bare command group print *its own* help instead of the root's.

    Lifted from rambass-live/src/rambass/cli.py:2827. `woodshed section
    cant-stop` with no further subcommand used to print the top-level usage,
    which lists every command except `add`/`update`/`rm` -- so it read as
    something that had not been built. Each group parser records itself, and
    `main` prints the help for the deepest one the arguments actually
    reached.
    """
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for group in action.choices.values():
            if any(isinstance(inner, argparse._SubParsersAction)
                   for inner in group._actions):
                group.set_defaults(_group_parser=group)
                _remember_group_parsers(group)


def main(argv: list[str] | None = None) -> int:
    # First, before anything can try to print a box-drawing or unicode char.
    use_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        getattr(args, "_group_parser", parser).print_help()
        return 1
    try:
        return args.func(args)
    except WoodshedError as exc:
        print(f"woodshed: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Something downstream closed the pipe -- `woodshed section ... | head`
        # is the usual case. Point stdout at /dev/null so the interpreter's
        # own flush on exit does not raise a second time, and exit quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    # Anything else is a traceback, which is a bug (CLAUDE.md): a WoodshedError
    # is the only refusal this CLI raises on purpose, and nothing here widens
    # that contract by guessing at other exception types to swallow.


if __name__ == "__main__":
    raise SystemExit(main())
