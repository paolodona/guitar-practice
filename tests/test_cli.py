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
from woodshed.errors import WoodshedError
from woodshed.ledger import read as ledger_read
from woodshed.library import Repo
from woodshed.manifest import Tempo, load_song


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    (tmp_path / "songs").mkdir()
    (tmp_path / "setlists").mkdir()
    (tmp_path / "practice").mkdir()
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return Repo(root=tmp_path)


@pytest.fixture(autouse=True)
def _no_auto_tempo_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """`bind_song_file`'s automatic `analyze_after_bind` step (found live
    2026-09-06) would otherwise try REAL librosa tempo detection on every
    test's synthetic (often silent) audio whenever librosa happens to be
    installed on the machine running these tests -- slow, and liable to
    behave unpredictably on audio with no real onsets to find. Every test
    in this file gets a deterministic "librosa not installed" unless it
    explicitly overrides this itself (see the dedicated
    `analyze_after_bind` tests below). The existing `needs_librosa`-marked
    `cmd_analyze` tests are unaffected -- that command calls `detect_tempo`
    directly and never consults `librosa_available` at all."""
    monkeypatch.setattr("woodshed.analyze.librosa_available", lambda: False)


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


def test_every_command_the_architecture_names_is_real_now(
    capsys: pytest.CaptureFixture,
) -> None:
    """docs/01-architecture.md's full command list, all built as of
    2026-09-06 -- `render` was the last stub. `--help` is the contract a
    person actually reads, so assert against that."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for name in ("add", "analyze", "section", "setlist", "capture", "render",
                 "status", "log", "serve", "doctor", "scan"):
        assert name in out, f"{name} is missing from --help"
    assert "not yet implemented" not in out


def test_the_honest_refusal_machinery_still_works_if_something_is_registered(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_NOT_YET_IMPLEMENTED` is empty now, and the machinery around it is
    kept rather than deleted -- the next command named before it is written
    should refuse in one line with an exit code, not traceback. Registering
    one here is how that stays true."""
    monkeypatch.setitem(cli._NOT_YET_IMPLEMENTED, "teleport", "Phase 9 -- physics")
    rc = cli.main(["teleport", "whatever", "--extra", "flags", "too"])
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


# ── capture --split, capture-bind (Phase 1.5, Group U, U4) -- CLI parity for
#    the capture-first workflow the server/browser already have. The device
#    itself is always mocked here -- same "only the argument-parsing / degrade
#    paths need real hardware" limit the H2 tests above already accept.


def _fake_device():
    from woodshed.capture import Device

    return Device(index=0, name="Fake Loopback", sample_rate=48000, channels=2)


def test_capture_split_with_a_title_refuses_cleanly(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["capture", "--split", "Some Title"])
    assert rc == 2
    assert "doesn't take a title" in capsys.readouterr().err


def test_capture_split_prints_one_line_per_segment(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from woodshed.capture import Segment

    monkeypatch.setattr("woodshed.capture.default_device", _fake_device)

    def fake_capture(device, out_dir, *, floor_db, gap_s, raw_path=None, **kw):
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(b"fake raw audio")
        yield Segment(start_frame=0, end_frame=48000 * 10, sample_rate=48000)
        yield Segment(
            start_frame=48000 * 12, end_frame=48000 * 20, sample_rate=48000, overflowed=True
        )

    monkeypatch.setattr("woodshed.capture.capture", fake_capture)

    rc = cli.main(["capture", "--split"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[0] 10.0s" in out
    assert "[1] 8.0s" in out
    assert "OVERFLOW" in out
    assert "capture-bind" in out


def test_capture_split_refuses_when_nothing_captured(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("woodshed.capture.default_device", _fake_device)
    written_raw_path = {}

    def fake_capture(device, out_dir, *, floor_db, gap_s, raw_path=None, **kw):
        written_raw_path["path"] = Path(raw_path)
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(b"")
        return iter(())

    monkeypatch.setattr("woodshed.capture.capture", fake_capture)

    rc = cli.main(["capture", "--split"])
    assert rc == 2
    assert not written_raw_path["path"].exists()  # cleaned up, not left as debris


def test_capture_bind_refuses_with_no_pending_session(repo: Repo) -> None:
    rc = cli.main(["capture-bind", "0", "New Song"])
    assert rc == 2


def _start_fake_session(repo: Repo, *, duration_s: float = 30.0):
    from woodshed import capture_session
    from woodshed.capture import Segment

    raw_path = repo.capture_dir / "20260101-000000.wav"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"fake raw audio")
    return capture_session.start_session(
        repo, raw_path,
        [Segment(start_frame=0, end_frame=int(48000 * duration_s), sample_rate=48000)],
    )


def test_capture_bind_binds_the_named_segment(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from woodshed import capture_session

    _start_fake_session(repo)
    monkeypatch.setattr("woodshed.cli.analyze_after_bind", lambda *a, **k: None)

    def fake_extract(raw_audio_path, segment, dest_path) -> None:
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake-flac-bytes")

    monkeypatch.setattr("woodshed.capture.extract_segment", fake_extract)

    rc = cli.main(
        ["capture-bind", "0", "New Song", "--artist", "Someone", "--tuning", "Eb standard"]
    )

    assert rc == 0
    song = load_song(repo.song_dir("new-song") / "song.yaml")
    assert song.title == "New Song"
    assert song.artist == "Someone"
    assert song.recording.tuning == "Eb standard"
    # the session's own sidecar is gone -- every entry resolved
    assert capture_session.current_session(repo) is None


def test_capture_bind_unknown_index_refuses(repo: Repo) -> None:
    _start_fake_session(repo)

    rc = cli.main(["capture-bind", "5", "New Song"])
    assert rc == 2


def test_capture_bind_adds_to_a_setlist_when_given(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from woodshed.manifest import Setlist
    from woodshed.setlist import create as create_setlist
    from woodshed.setlist import load as load_setlist

    create_setlist(repo, "gig", Setlist(name="Gig", tuning="E standard"))
    _start_fake_session(repo)
    monkeypatch.setattr("woodshed.cli.analyze_after_bind", lambda *a, **k: None)
    monkeypatch.setattr(
        "woodshed.capture.extract_segment",
        lambda raw, seg, dest: Path(dest).parent.mkdir(parents=True, exist_ok=True)
        or Path(dest).write_bytes(b"x"),
    )

    rc = cli.main(["capture-bind", "0", "New Song", "--setlist", "gig"])

    assert rc == 0
    setlist = load_setlist(repo, "gig")
    assert any(entry.slug == "new-song" for entry in setlist.songs)


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
    # analyze_after_bind's own peaks half -- always runs, no librosa needed.
    assert (repo.cache_dir("direct-call") / "peaks-1024.json").is_file()


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


# ── analyze_after_bind: automatic peaks + best-effort tempo (found live
#    2026-09-06 -- a song bound with no analysis had no waveform at all
#    until a separate, manual `woodshed analyze` was run by hand) ────────


def test_analyze_after_bind_writes_peaks_regardless_of_librosa(
    repo: Repo, tmp_path: Path
) -> None:
    """Peaks need only ffmpeg (already required) and pure numpy -- no
    optional dependency at all, so this file's own autouse fixture
    (librosa "not installed") must not stop it."""
    source = tmp_path / "raw.wav"
    _write_wav(source, seconds=2.0)
    song = cli.bind_song_file(repo, source, title="Peaks Only", tuning="E standard")

    assert (repo.cache_dir(song.slug) / "peaks-1024.json").is_file()
    assert (repo.cache_dir(song.slug) / "peaks-4096.json").is_file()
    assert (repo.cache_dir(song.slug) / "peaks-16384.json").is_file()


def test_bind_song_file_auto_detects_tempo_when_bpm_not_given_and_librosa_available(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("woodshed.analyze.librosa_available", lambda: True)
    detected = Tempo(bpm=128.0, source="refined", grid_offset_s=0.05,
                      time_signature="4/4", confidence=0.87)
    monkeypatch.setattr("woodshed.analyze.detect_tempo", lambda path: detected)

    source = tmp_path / "raw.wav"
    _write_wav(source, seconds=2.0)
    song = cli.bind_song_file(repo, source, title="Auto Tempo", tuning="E standard")

    assert song.tempo.source == "refined"
    assert song.tempo.bpm == 128.0
    assert song.tempo.confidence == 0.87


def test_bind_song_file_explicit_bpm_skips_auto_detect_even_when_librosa_available(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("woodshed.analyze.librosa_available", lambda: True)
    calls = []
    monkeypatch.setattr(
        "woodshed.analyze.detect_tempo",
        lambda path: calls.append(path) or Tempo(bpm=999.0, source="refined"),
    )

    source = tmp_path / "raw.wav"
    _write_wav(source, seconds=2.0)
    song = cli.bind_song_file(repo, source, title="Manual Tempo", bpm=140.0, tuning="E standard")

    assert calls == []  # detect_tempo never even called -- the explicit choice wins outright
    assert song.tempo.bpm == 140.0
    assert song.tempo.source == "manual"


def test_bind_song_file_degrades_gracefully_when_detect_tempo_fails(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine analysis failure (not just a missing package) must not
    block the bind itself -- peaks and the song still land, tempo just
    keeps its manual placeholder."""
    monkeypatch.setattr("woodshed.analyze.librosa_available", lambda: True)

    def failing_detect(path):
        raise WoodshedError("simulated analysis failure")

    monkeypatch.setattr("woodshed.analyze.detect_tempo", failing_detect)

    source = tmp_path / "raw.wav"
    _write_wav(source, seconds=2.0)
    song = cli.bind_song_file(repo, source, title="Failed Tempo", tuning="E standard")

    assert song.tempo.source == "manual"
    assert song.tempo.bpm == 120.0
    assert (repo.cache_dir(song.slug) / "peaks-1024.json").is_file()


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


# ── status (Phase 2, K3) ─────────────────────────────────────────────────


def _status_song(repo: Repo, slug: str, title: str) -> None:
    """A song on disk with two sections, no audio file needed -- `status`
    reads song.yaml and the ledger, never the recording itself."""
    from woodshed.manifest import Recording, Section, Song, save_song

    song = Song(
        slug=slug,
        title=title,
        artist="Nobody",
        recording=Recording(
            file="audio/x.wav", sha256="a" * 64, duration_s=200.0, tuning="E standard"
        ),
        sections=[
            Section(id="intro", name="Intro", start_s=0.0, end_s=20.0,
                    snapped="free", target_speed=100.0),
            Section(id="solo", name="Solo", start_s=20.0, end_s=60.0,
                    snapped="free", target_speed=90.0),
        ],
    )
    repo.song_dir(slug).mkdir(parents=True, exist_ok=True)
    save_song(song, repo.song_dir(slug) / "song.yaml")


def test_status_with_no_arguments_lists_every_song(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    _status_song(repo, "cant-stop", "Can't Stop")
    _status_song(repo, "sultans", "Sultans of Swing")
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Can't Stop" in out
    assert "Sultans of Swing" in out


def test_status_on_an_empty_repo_says_so_rather_than_printing_a_blank(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["status"])
    assert rc == 0
    assert "no songs" in capsys.readouterr().out.lower()


def test_status_for_one_song_breaks_out_its_sections(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    _status_song(repo, "cant-stop", "Can't Stop")
    for _ in range(3):
        cli.main(["log", "cant-stop", "solo", "--speed", "60", "--clean"])
    capsys.readouterr()
    rc = cli.main(["status", "cant-stop"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Solo" in out and "Intro" in out
    assert "60" in out  # the best sustained speed, earned by three clean reps


def test_status_setlist_lists_its_rows_and_the_next_up_suggestion(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    _status_song(repo, "cant-stop", "Can't Stop")
    cli.main(["setlist", "create", "The Gig", "--tuning", "Eb standard", "--slug", "gig"])
    cli.main(["setlist", "add-song", "gig", "cant-stop"])
    capsys.readouterr()
    rc = cli.main(["status", "--setlist", "gig"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "The Gig" in out
    assert "Can't Stop" in out
    assert "next up" in out.lower()
    assert "-1" in out  # the derived shift: an E record in an Eb band


def test_status_setlist_unknown_slug_refuses_with_exit_2(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["status", "--setlist", "no-such-setlist"])
    assert rc == 2
    assert capsys.readouterr().err.startswith("woodshed: ")


def test_status_writes_nothing(repo: Repo, capsys: pytest.CaptureFixture) -> None:
    """The whole command is a read (CLAUDE.md: the server -- and the CLI --
    write exactly four things, and a text dashboard is none of them)."""
    import hashlib

    _status_song(repo, "cant-stop", "Can't Stop")
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in repo.root.rglob("*") if p.is_file()
    }
    cli.main(["status"])
    cli.main(["status", "cant-stop"])
    after = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in repo.root.rglob("*") if p.is_file()
    }
    assert after == before


# ── scan / import (Phase 3, Group M's CLI surface) ───────────────────────


def _needs_audio_song(repo: Repo, slug: str, title: str, artist: str = "RHCP") -> None:
    from woodshed.sources import Track, import_tracks

    import_tracks(repo, [Track(spotify_id="x", title=title, artist=artist,
                               album=None, duration_s=100.0)])
    assert (repo.song_dir(slug) / "song.yaml").is_file()


def test_scan_lists_candidates_and_binds_nothing_on_its_own(
    repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "RHCP - Cant Stop.wav"
    _write_wav(source, seconds=1.0)
    (repo.root / "config.yaml").write_text(
        f"library_paths:\n  - {library.as_posix()}\n", encoding="utf-8"
    )
    _needs_audio_song(repo, "can-t-stop", "Can't Stop")
    capsys.readouterr()

    rc = cli.main(["scan", "can-t-stop"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Cant Stop.wav" in out
    # Still needs audio: a scan SUGGESTS, and this one bound nothing.
    song = load_song(repo.song_dir("can-t-stop") / "song.yaml")
    assert not (repo.song_dir("can-t-stop") / song.recording.file).is_file()


def test_scan_bind_is_an_explicit_index_and_actually_binds(
    repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_wav(library / "RHCP - Cant Stop.wav", seconds=1.0)
    (repo.root / "config.yaml").write_text(
        f"library_paths:\n  - {library.as_posix()}\n", encoding="utf-8"
    )
    _needs_audio_song(repo, "can-t-stop", "Can't Stop")
    capsys.readouterr()

    rc = cli.main(["scan", "can-t-stop", "--bind", "1"])
    assert rc == 0
    song = load_song(repo.song_dir("can-t-stop") / "song.yaml")
    bound = repo.song_dir("can-t-stop") / song.recording.file
    assert bound.is_file()
    assert song.recording.sha256  # hashed on binding, empty before it
    assert song.recording.duration_s == pytest.approx(1.0, abs=0.01)
    # The Spotify metadata survives the bind -- it is the same song.
    assert song.recording.spotify_id == "x"


def test_scan_with_no_library_paths_says_what_to_configure(
    repo: Repo, capsys: pytest.CaptureFixture
) -> None:
    _needs_audio_song(repo, "can-t-stop", "Can't Stop")
    capsys.readouterr()
    rc = cli.main(["scan", "can-t-stop"])
    assert rc == 0
    assert "library_paths" in capsys.readouterr().out


def test_scan_bind_out_of_range_refuses_rather_than_picking_something(
    repo: Repo, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_wav(library / "RHCP - Cant Stop.wav", seconds=1.0)
    (repo.root / "config.yaml").write_text(
        f"library_paths:\n  - {library.as_posix()}\n", encoding="utf-8"
    )
    _needs_audio_song(repo, "can-t-stop", "Can't Stop")
    assert cli.main(["scan", "can-t-stop", "--bind", "9"]) == 2


def test_import_without_credentials_says_how_to_connect(
    repo: Repo, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WOODSHED_HOME", str(tmp_path / "home"))
    rc = cli.main(["import", "https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--connect" in err


def test_import_connect_without_a_client_id_names_config_yaml(
    repo: Repo, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WOODSHED_HOME", str(tmp_path / "home"))
    rc = cli.main(["import", "--connect"])
    assert rc == 2
    assert "client_id" in capsys.readouterr().err


# ── render (Phase 2, Group I's CLI surface -- the last stub) ─────────────


def _renderable_song(repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A real song with a real (silent) wav, and rubberband/ffmpeg mocked."""
    from types import SimpleNamespace

    from woodshed import render as render_module

    source = tmp_path / "source.wav"
    _write_wav(source, seconds=30.0)
    rc = cli.main(["add", str(source), "--title", "Test Song", "--artist", "Nobody"])
    assert rc == 0
    slug = "test-song"
    cli.main(["section", slug, "add", "Solo", "5", "15"])

    monkeypatch.setattr(
        render_module, "locate_tool",
        lambda name: SimpleNamespace(path=name, route="path"),
    )

    def fake_run(argv, **kwargs):
        import numpy as np

        dest = Path(argv[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "--time" in argv:
            render_module._write_wav(
                dest, np.zeros((8000, 1), dtype=np.float32), 8000
            )
        else:
            dest.write_bytes(b"fake audio bytes")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(render_module.subprocess, "run", fake_run)
    return slug


def test_render_writes_a_cache_file_for_one_speed(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    slug = _renderable_song(repo, monkeypatch, tmp_path)
    capsys.readouterr()
    rc = cli.main(["render", slug, "solo", "--speed", "60"])
    assert rc == 0
    cached = sorted(repo.cache_dir(slug).glob("solo@60x*.flac"))
    assert len(cached) == 1
    assert "60" in capsys.readouterr().out


def test_render_takes_several_speeds_at_once(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The whole reason this command is worth having: "render the next three
    rungs before I get on the train" is one invocation, not three."""
    slug = _renderable_song(repo, monkeypatch, tmp_path)
    rc = cli.main(["render", slug, "solo", "--speed", "50", "55", "60"])
    assert rc == 0
    assert len(sorted(repo.cache_dir(slug).glob("solo@*.flac"))) == 3


def test_render_ladder_renders_every_rung_up_to_the_target(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _renderable_song(repo, monkeypatch, tmp_path)
    cli.main(["section", slug, "update", "solo", "--target-speed", "60"])
    rc = cli.main(["render", slug, "solo", "--ladder"])
    assert rc == 0
    names = sorted(p.name.split("@")[1].split("x")[0]
                   for p in repo.cache_dir(slug).glob("solo@*.flac"))
    assert names == ["50", "55", "60"]


def test_render_all_sections_when_none_is_named(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _renderable_song(repo, monkeypatch, tmp_path)
    cli.main(["section", slug, "add", "Intro", "0", "4"])
    rc = cli.main(["render", slug, "--speed", "60"])
    assert rc == 0
    assert len(sorted(repo.cache_dir(slug).glob("*@60x*.flac"))) == 3  # + whole song


def test_render_evict_trims_the_cache_and_says_what_it_deleted(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    slug = _renderable_song(repo, monkeypatch, tmp_path)
    cli.main(["render", slug, "solo", "--speed", "60"])
    capsys.readouterr()
    # A budget of zero evicts everything -- the point is that the command
    # reaches render.evict at all, which nothing but the server did before.
    rc = cli.main(["render", "--evict", "--max-gb", "0"])
    assert rc == 0
    assert sorted(repo.cache_dir(slug).glob("*.flac")) == []
    assert "freed" in capsys.readouterr().out


def test_render_unknown_section_refuses(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _renderable_song(repo, monkeypatch, tmp_path)
    assert cli.main(["render", slug, "no-such-section", "--speed", "60"]) == 2
