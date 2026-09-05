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
)

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
    "setlist": "Phase 1 -- setlists and per-song transpose",
    "capture": "Phase 1 -- loopback recording",
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


def cmd_add(args: argparse.Namespace) -> int:
    repo = _repo()
    source = Path(args.path)
    if not source.is_file():
        raise WoodshedError(f"no such file: {source}")

    title = args.title or _guess_title(source)
    slug = args.slug or slugify(title)
    if not slug:
        raise WoodshedError(f"{title!r} does not slugify to anything usable; pass --slug")
    if slug in repo.list_songs():
        raise WoodshedError(
            f"songs/{slug}/song.yaml already exists. Pass --slug to add this as "
            "a different song."
        )

    audio_dir = repo.audio_dir(slug)
    audio_dir.mkdir(parents=True, exist_ok=True)
    dest = audio_dir / source.name
    if dest.resolve() != source.resolve():
        shutil.copy2(source, dest)

    song = Song(
        slug=slug,
        title=title,
        artist=args.artist,
        album=args.album,
        recording=Recording(
            file=f"audio/{dest.name}",
            sha256=hash_file(dest),
            duration_s=_read_duration_s(dest),
            tuning=args.tuning,
        ),
        tempo=Tempo(
            bpm=args.bpm,
            source="manual",
            grid_offset_s=args.grid_offset,
            time_signature=args.time_signature,
        ),
    )
    path = repo.song_dir(slug) / "song.yaml"
    save_song(song, path)
    _say(f"added {slug!r}: {path}")
    _say(f"  {song.recording.duration_s:.1f}s, {song.recording.tuning}, "
         f"bpm {song.tempo.bpm:g} (source: manual -- run `woodshed analyze "
         f"{slug}` to refine it, or edit song.yaml by hand)")
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
    # Lazy import: analyze.py is behind the librosa line (CLAUDE.md's
    # layering rule), so only `analyze` itself pays for a missing librosa --
    # same reasoning as cmd_serve's lazy `from woodshed.server import
    # make_server` below. require_module (inside detect_tempo) gives the
    # honest "pip install woodshed[analyze]" message; this just keeps every
    # other command importable without librosa on the machine at all.
    from woodshed import peaks as peaks_module
    from woodshed.analyze import beat_grid, detect_tempo, load_mono_audio

    repo = _repo()
    slug, path, song = _load_song(repo, args.slug)
    audio_path = repo.song_dir(slug) / song.recording.file
    if not audio_path.is_file():
        raise WoodshedError(f"{slug}: no such audio file: {audio_path}")

    tempo = detect_tempo(audio_path)
    song.tempo = tempo
    save_song(song, path)

    # One decode serves both tempo detection and the peaks cache --
    # docs/01-architecture.md:111 lists them together for exactly this
    # reason. A second ffmpeg pass is the price of detect_tempo's fixed,
    # audio-in-tempo-out signature (see analyze.py); it is a few seconds
    # once per `woodshed analyze`, not a hot path.
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
    p.add_argument("--tuning", default="E standard",
                    help="what the RECORDING is in -- not what you play it in")
    p.add_argument("--slug", default=None, help="override the derived slug")
    p.add_argument("--bpm", type=float, default=120.0,
                    help="placeholder tempo -- `woodshed analyze` will refine it")
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
    p.set_defaults(func=cmd_analyze)

    # -- not yet implemented, registered so `--help` is honest about the
    #    tool's eventual shape (docs/01-architecture.md's full command list) --
    stub_help = {
        "setlist": "create or edit a setlist (not yet implemented)",
        "capture": "arm loopback, split a playlist on the gaps (not yet implemented)",
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
