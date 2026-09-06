"""Tests for woodshed.cli -- see this unit's contract in
.agent_session/001_woodshed-implementation_plan.md (work unit C3).

A local ``repo`` fixture (not the one in tests/conftest.py) builds a real,
minimal library tree with a root marker `find_root` can discover, and
`monkeypatch.chdir`s into it -- `cli._repo()` calls `library.find_root()`
which walks upward from the current directory, exactly as it would from a
real terminal session.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from woodshed import cli
from woodshed.ledger import read as ledger_read
from woodshed.library import Repo
from woodshed.manifest import load_song


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    (tmp_path / "songs").mkdir()
    (tmp_path / "setlists").mkdir()
    (tmp_path / "practice").mkdir()
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return Repo(root=tmp_path)


def _write_wav(path: Path, *, seconds: float = 1.0, rate: int = 44100) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * frames)


def _add(repo: Repo, tmp_path: Path, *, seconds: float = 1.0, **extra: str) -> str:
    """Run `woodshed add` on a freshly synthesised wav; returns the slug."""
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=seconds)
    argv = ["add", str(source), "--title", "Test Song", "--artist", "Nobody"]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", value]
    rc = cli.main(argv)
    assert rc == 0
    return "test-song"


# ── the command list is honest ───────────────────────────────────────────
def test_help_lists_every_command_from_the_architecture_doc() -> None:
    help_text = cli.build_parser().format_help()
    for name in (
        "add", "analyze", "section", "setlist", "capture", "render",
        "status", "log", "serve", "doctor", "scan",
    ):
        assert name in help_text, f"{name!r} missing from --help"


def test_no_subcommand_prints_help_and_returns_1(capsys: pytest.CaptureFixture) -> None:
    rc = cli.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "usage" in out.lower()


@pytest.mark.parametrize(
    "name", ["render", "status", "scan"]
)
def test_not_yet_implemented_commands_refuse_cleanly(
    name: str, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main([name, "whatever", "--extra", "flags", "too"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("woodshed: ")
    assert err.count("\n") == 1  # exactly one line
    assert "not built yet" in err


# ── setlist (Phase 1, F1's CLI surface) ─────────────────────────────────


def test_setlist_create_then_list(repo: Repo, capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["setlist", "create", "The Gig", "--tuning", "Eb standard", "--slug", "gig"])
    assert rc == 0
    capsys.readouterr()
    rc = cli.main(["setlist", "list"])
    assert rc == 0
    assert "The Gig" in capsys.readouterr().out


def test_setlist_create_unknown_tuning_refuses(repo: Repo) -> None:
    # argparse's own `choices=` check, not a WoodshedError -- same reasoning
    # as test_analyze_bpm_and_tap_together_refuses: a fixed list of tuning
    # names, not free text, is the whole point of this unit.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["setlist", "create", "The Gig", "--tuning", "Eb", "--slug", "gig"])
    assert excinfo.value.code == 2


def test_setlist_add_song_derives_shift_by_default(repo: Repo) -> None:
    cli.main(["setlist", "create", "Gig", "--tuning", "Eb standard", "--slug", "gig"])
    rc = cli.main(["setlist", "add-song", "gig", "cant-stop"])
    assert rc == 0
    from woodshed.setlist import load
    assert load(repo, "gig").songs[0].shift is None


def test_setlist_shift_sets_then_clears(repo: Repo) -> None:
    cli.main(["setlist", "create", "Gig", "--tuning", "Eb standard", "--slug", "gig"])
    cli.main(["setlist", "add-song", "gig", "cant-stop"])
    rc = cli.main(["setlist", "shift", "gig", "cant-stop", "-2"])
    assert rc == 0
    from woodshed.setlist import load
    assert load(repo, "gig").songs[0].shift == -2
    cli.main(["setlist", "shift", "gig", "cant-stop", "none"])
    assert load(repo, "gig").songs[0].shift is None


def test_setlist_rm_song(repo: Repo) -> None:
    cli.main(["setlist", "create", "Gig", "--tuning", "E standard", "--slug", "gig"])
    cli.main(["setlist", "add-song", "gig", "cant-stop"])
    rc = cli.main(["setlist", "rm-song", "gig", "cant-stop"])
    assert rc == 0
    from woodshed.setlist import load
    assert load(repo, "gig").songs == []


# ── capture (Phase 1, H2) -- the device half needs real hardware; only the
#    argument-parsing / degrade paths are exercised here. ──────────────────


def test_capture_with_no_title_refuses_cleanly(repo: Repo, capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["capture"])
    assert rc == 2
    assert "a title is required" in capsys.readouterr().err


def test_capture_queue_names_the_missing_prerequisite(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["capture", "--queue", "gig"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Spotify import" in err


def test_capture_unknown_tuning_refuses(repo: Repo) -> None:
    # argparse's own `choices=` check -- rejected before any device work is
    # attempted, so this doesn't need real hardware either.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture", "Some Title", "--tuning", "Eb"])
    assert excinfo.value.code == 2


def test_capture_list_devices_reports_the_missing_extra_cleanly(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    # Exercises the real require_module degrade path when pyaudiowpatch is
    # absent, or the real device listing when `uv sync --extra capture` has
    # installed it -- whichever this venv actually has, not a mocked
    # stand-in either way, so this asserts the shape true of both rather
    # than pinning one specific machine's state.
    try:
        import pyaudiowpatch  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    rc = cli.main(["capture", "--list-devices"])
    if installed:
        assert rc == 0
    else:
        assert rc == 2
        assert "pyaudiowpatch" in capsys.readouterr().err


# ── WoodshedError -> one line on stderr, exit 2 ──────────────────────────
def test_woodshederror_prints_one_line_and_exits_2(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["add", str(repo.root / "does-not-exist.wav")])
    assert rc == 2
    err = capsys.readouterr().err
    assert err == "woodshed: no such file: " + str(repo.root / "does-not-exist.wav") + "\n"


# ── add ───────────────────────────────────────────────────────────────────
def test_add_creates_the_expected_song_yaml(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=2.0)

    song_path = repo.song_dir(slug) / "song.yaml"
    assert song_path.is_file()

    song = load_song(song_path)
    assert song.slug == slug
    assert song.title == "Test Song"
    assert song.artist == "Nobody"
    assert song.recording.file == "audio/source.wav"
    assert (repo.song_dir(slug) / song.recording.file).is_file()
    assert song.recording.duration_s == pytest.approx(2.0, abs=0.05)
    assert song.recording.tuning == "E standard"
    assert song.tempo.source == "manual"
    # A default "Whole song" section, so there is always something to
    # Practice without first having to draw a section by hand.
    assert len(song.sections) == 1
    assert song.sections[0].id == "whole-song"
    assert song.sections[0].full_song is True
    assert song.sections[0].start_s == 0.0
    assert song.sections[0].end_s == song.recording.duration_s


def test_add_refuses_a_missing_file(repo: Repo) -> None:
    rc = cli.main(["add", "nope.wav"])
    assert rc == 2


def test_add_unknown_tuning_refuses(repo: Repo, tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["add", str(source), "--tuning", "Eb"])
    assert excinfo.value.code == 2


def test_add_refuses_a_slug_that_already_exists(repo: Repo, tmp_path: Path) -> None:
    _add(repo, tmp_path)
    source2 = tmp_path / "source2.wav"
    _write_wav(source2)
    rc = cli.main(["add", str(source2), "--title", "Test Song"])
    assert rc == 2


# ── bind_song_file: the shared function cmd_add and POST /api/song/upload
#    both call (T1) ────────────────────────────────────────────────────────


def test_bind_song_file_writes_the_expected_song_yaml(repo: Repo, tmp_path: Path) -> None:
    source = tmp_path / "raw.wav"
    _write_wav(source, seconds=3.0)

    song = cli.bind_song_file(
        repo, source, title="Direct Call", artist="Someone", tuning="Eb standard",
    )

    assert song.slug == "direct-call"
    song_path = repo.song_dir("direct-call") / "song.yaml"
    assert song_path.is_file()
    reloaded = load_song(song_path)
    assert reloaded.artist == "Someone"
    assert reloaded.recording.tuning == "Eb standard"
    assert reloaded.recording.duration_s == pytest.approx(3.0, abs=0.05)
    assert (repo.song_dir("direct-call") / reloaded.recording.file).is_file()


def test_bind_song_file_refuses_a_missing_source(repo: Repo, tmp_path: Path) -> None:
    with pytest.raises(Exception, match="no such file"):
        cli.bind_song_file(repo, tmp_path / "nope.wav", title="Ghost")


def test_bind_song_file_refuses_an_existing_slug(repo: Repo, tmp_path: Path) -> None:
    _add(repo, tmp_path)  # binds "test-song"
    source2 = tmp_path / "source2.wav"
    _write_wav(source2)

    with pytest.raises(Exception, match="already exists"):
        cli.bind_song_file(repo, source2, title="Test Song")


def test_bind_song_file_uses_dest_filename_when_given(repo: Repo, tmp_path: Path) -> None:
    """The server's own path: the incoming bytes land in a generated temp
    file first, so `source.name` there is not the browser's real filename."""
    source = tmp_path / "tmpABC123.wav"
    _write_wav(source, seconds=1.0)

    song = cli.bind_song_file(
        repo, source, title="Renamed", tuning="E standard", dest_filename="original.wav",
    )

    assert song.recording.file == "audio/original.wav"
    assert (repo.song_dir(song.slug) / "audio" / "original.wav").is_file()


# ── section: add / update / rm, mirroring POST /api/section ─────────────
#
# `_add` now writes a default "whole-song" section (see
# test_add_creates_the_expected_song_yaml) -- every test below that counts
# or indexes `song.sections` accounts for that one entry being there
# already, rather than assuming a fresh song starts with none.
def test_section_add_writes_a_span(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["section", slug, "add", "Full solo", "10", "20"])
    assert rc == 0

    song = load_song(repo.song_dir(slug) / "song.yaml")
    assert len(song.sections) == 2  # the default whole-song entry, plus this one
    section = next(s for s in song.sections if s.id == "full-solo")
    assert section.name == "Full solo"
    assert section.start_s == 10.0
    assert section.end_s == 20.0
    assert section.snapped == "free"
    assert section.target_speed == 100.0


def test_section_add_full_song_and_lead_in_beats(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    # A different name AND a different span from the default's own "Whole
    # song" (0-30): the name alone would slugify to the same "whole-song"
    # id `_add` already used (refused as a duplicate id), and the same
    # 0-30 span would ALSO be refused as an exact-duplicate span -- neither
    # is what this test means to exercise, which is just that --full-song/
    # --lead-in-beats round-trip.
    rc = cli.main([
        "section", slug, "add", "Second full pass", "5", "25",
        "--full-song", "--lead-in-beats", "8",
    ])
    assert rc == 0
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "second-full-pass")
    assert section.full_song is True
    assert section.lead_in_beats == 8


def test_section_update_can_clear_full_song(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    cli.main(["section", slug, "add", "Second full pass", "5", "25", "--full-song"])
    rc = cli.main(["section", slug, "update", "second-full-pass", "--no-full-song"])
    assert rc == 0
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "second-full-pass")
    assert section.full_song is False


def test_section_add_refuses_an_exact_duplicate_span(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    assert cli.main(["section", slug, "add", "Full solo", "10", "20"]) == 0
    rc = cli.main(["section", slug, "add", "Same span again", "10", "20"])
    assert rc == 2


def test_section_add_allows_overlap_and_nesting(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    assert cli.main(["section", slug, "add", "Full solo", "10", "20"]) == 0
    rc = cli.main(["section", slug, "add", "Solo, tapping", "10", "15"])
    assert rc == 0
    song = load_song(repo.song_dir(slug) / "song.yaml")
    assert len(song.sections) == 3  # whole-song (default) + full-solo + solo-tapping


def test_section_update_changes_a_field(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    cli.main(["section", slug, "add", "Full solo", "10", "20"])
    rc = cli.main(["section", slug, "update", "full-solo", "--end-s", "25"])
    assert rc == 0
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "full-solo")
    assert section.end_s == 25.0
    assert section.start_s == 10.0  # untouched fields survive


def test_section_update_refuses_an_invalid_span(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    cli.main(["section", slug, "add", "Full solo", "10", "20"])
    rc = cli.main(["section", slug, "update", "full-solo", "--end-s", "5"])
    assert rc == 2
    # unchanged on refusal
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "full-solo")
    assert section.end_s == 20.0


def test_section_update_unknown_id_refuses(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["section", slug, "update", "nope", "--end-s", "5"])
    assert rc == 2


def test_section_rm_removes_a_span(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    cli.main(["section", slug, "add", "Full solo", "10", "20"])
    rc = cli.main(["section", slug, "rm", "full-solo"])
    assert rc == 0
    song = load_song(repo.song_dir(slug) / "song.yaml")
    # "add"'s own default whole-song section is untouched -- only
    # full-solo was named for removal.
    assert [s.id for s in song.sections] == ["whole-song"]


def test_section_rm_unknown_id_refuses(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["section", slug, "rm", "nope"])
    assert rc == 2


def test_section_with_no_subcommand_prints_its_own_help(
    repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["section", slug])
    assert rc == 1
    out = capsys.readouterr().out
    assert "add" in out and "update" in out and "rm" in out


# ── log ────────────────────────────────────────────────────────────────
def test_log_appends_exactly_one_ledger_line(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    cli.main(["section", slug, "add", "Verse", "0", "5"])

    rc = cli.main(["log", slug, "verse", "--speed", "55", "--clean"])
    assert rc == 0

    reps = list(ledger_read(repo))
    assert len(reps) == 1
    rep = reps[0]
    assert rep.song == slug
    assert rep.section == "verse"
    assert rep.speed == 55.0
    assert rep.clean is True
    assert rep.passed is True
    assert rep.source == "keyboard"

    rc = cli.main(["log", slug, "verse", "--speed", "40", "--no-pass"])
    assert rc == 0
    reps = list(ledger_read(repo))
    assert len(reps) == 2
    assert reps[-1].passed is False
    assert reps[-1].clean is False


def test_log_unknown_section_refuses_and_appends_nothing(
    repo: Repo, tmp_path: Path
) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["log", slug, "no-such-section", "--speed", "50"])
    assert rc == 2
    assert not repo.ledger_path().exists()


def test_log_unknown_song_refuses(repo: Repo) -> None:
    rc = cli.main(["log", "no-such-song", "verse", "--speed", "50"])
    assert rc == 2


# ── doctor, through the CLI wiring (see tests/test_doctor.py for the module) ──
def test_cmd_doctor_runs_and_prints_a_report(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["doctor"])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "woodshed doctor" in out
    assert "Web MIDI is Chrome/Edge only." in out


# ── analyze, through the CLI wiring (see tests/test_analyze.py for the module) ──
def _write_click_wav(
    path: Path, *, bpm: float = 100.0, seconds: float = 30.0, rate: int = 22050
) -> None:
    """A percussive click track, unlike `_write_wav`'s silence -- there has
    to be something for `detect_tempo` to find a tempo in."""
    import numpy as np

    n = int(seconds * rate)
    samples = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    click_len = int(0.02 * rate)
    envelope = np.exp(-np.arange(click_len) / (0.003 * rate))
    tone = np.sin(2 * np.pi * 1200.0 * np.arange(click_len) / rate)
    click = (envelope * tone).astype(np.float32)
    t = 0.25
    while t < seconds:
        start = int(t * rate)
        end = min(start + click_len, n)
        samples[start:end] += click[: end - start]
        t += period
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


@pytest.mark.needs_librosa
def test_analyze_writes_a_refined_tempo_and_caches_peaks(repo: Repo, tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_click_wav(source, bpm=100.0, seconds=30.0)
    argv = ["add", str(source), "--title", "Test Song", "--artist", "Nobody", "--bpm", "99"]
    assert cli.main(argv) == 0
    slug = "test-song"

    rc = cli.main(["analyze", slug])
    assert rc == 0

    song = load_song(repo.song_dir(slug) / "song.yaml")
    assert song.tempo.source == "refined"
    assert song.tempo.bpm == pytest.approx(100.0, abs=1.0)
    assert song.tempo.confidence is not None

    assert (repo.cache_dir(slug) / "peaks-1024.json").is_file()
    assert (repo.cache_dir(slug) / "peaks-4096.json").is_file()
    assert (repo.cache_dir(slug) / "peaks-16384.json").is_file()


def test_analyze_unknown_song_refuses(repo: Repo) -> None:
    rc = cli.main(["analyze", "no-such-song"])
    assert rc == 2


# ── analyze --bpm / --tap: manual and tapped, no librosa needed ──────────
def test_analyze_bpm_sets_manual_tempo_and_forces_confidence_null(
    repo: Repo, tmp_path: Path
) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["analyze", slug, "--bpm", "140", "--grid-offset", "0.1"])
    assert rc == 0

    song = load_song(repo.song_dir(slug) / "song.yaml")
    assert song.tempo.bpm == 140.0
    assert song.tempo.source == "manual"
    assert song.tempo.grid_offset_s == pytest.approx(0.1)
    assert song.tempo.confidence is None
    # peaks are cached regardless of which tempo path was used
    assert (repo.cache_dir(slug) / "peaks-1024.json").is_file()


def test_analyze_tap_computes_bpm_from_intervals_and_forces_confidence_null(
    repo: Repo, tmp_path: Path
) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    taps = [str(i * 0.5) for i in range(9)]  # 0.5s apart -> 120 bpm
    argv = ["analyze", slug]
    for t in taps:
        argv += ["--tap", t]
    assert cli.main(argv) == 0

    song = load_song(repo.song_dir(slug) / "song.yaml")
    assert song.tempo.bpm == pytest.approx(120.0, abs=0.1)
    assert song.tempo.source == "tapped"
    assert song.tempo.confidence is None


def test_analyze_tap_with_one_tap_refuses(repo: Repo, tmp_path: Path) -> None:
    slug = _add(repo, tmp_path, seconds=30.0)
    rc = cli.main(["analyze", slug, "--tap", "0.0"])
    assert rc == 2


def test_analyze_bpm_and_tap_together_refuses(repo: Repo, tmp_path: Path) -> None:
    # argparse's own mutually-exclusive-group check, not a WoodshedError --
    # it exits directly rather than returning, same exit code 2 either way.
    slug = _add(repo, tmp_path, seconds=30.0)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["analyze", slug, "--bpm", "120", "--tap", "0.0", "--tap", "0.5"])
    assert excinfo.value.code == 2
