"""Tests for woodshed.separate (Phase 1.5, Group S, S1): guitar-only
isolation via Demucs.

Test contract (this unit's own docstring, mirroring render.py's future
suite -- see the plan's Group S section): the ffmpeg/demucs argv is
asserted, the demucs subprocess (and both ffmpeg calls) mocked; moving a
section boundary misses the isolated-stem cache; renders are skipped when
the fingerprinted file already exists and `force` is False.

Never a real Demucs invocation here -- see `demucs_available`'s own
handling for how a genuinely missing (or genuinely present) `demucs`
package is told apart, without ever running it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import woodshed.separate as separate_module
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import Recording, Section, Song, Tempo
from woodshed.separate import (
    GUITAR_MODEL,
    demucs_available,
    isolate_guitar,
    stem_cache_path,
    stem_fingerprint,
)


def _song(slug: str = "solo-song", sha256: str = "a" * 64) -> Song:
    return Song(
        slug=slug,
        title="Solo Song",
        artist="Nobody",
        album=None,
        recording=Recording(file="audio/track.wav", sha256=sha256, duration_s=200.0,
                             tuning="E standard"),
        tempo=Tempo(bpm=120.0, source="manual", grid_offset_s=0.0, time_signature="4/4"),
    )


def _section(section_id: str = "solo-full", start_s: float = 60.0, end_s: float = 90.0) -> Section:
    return Section(
        id=section_id, name="Solo", start_s=start_s, end_s=end_s,
        snapped="free", target_speed=100.0,
    )


# ── demucs_available() ───────────────────────────────────────────────────


def test_demucs_available_is_false_when_the_module_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "demucs", raising=False)

    import builtins
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "demucs":
            raise ImportError("no demucs here")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert demucs_available() is False


def test_demucs_available_is_true_when_the_module_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "demucs", SimpleNamespace())
    assert demucs_available() is True


# ── stem_fingerprint() / stem_cache_path() ───────────────────────────────


def test_stem_fingerprint_is_8_hex_chars() -> None:
    fp = stem_fingerprint(_song(), _section(), pre_roll_s=1.0)
    assert len(fp) == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_stem_fingerprint_is_stable_for_identical_inputs() -> None:
    song, section = _song(), _section()
    assert stem_fingerprint(song, section, pre_roll_s=1.0) == stem_fingerprint(
        song, section, pre_roll_s=1.0
    )


def test_stem_fingerprint_changes_when_end_s_moves_by_10ms() -> None:
    song = _song()
    a = stem_fingerprint(song, _section(end_s=90.0), pre_roll_s=1.0)
    b = stem_fingerprint(song, _section(end_s=90.01), pre_roll_s=1.0)
    assert a != b


def test_stem_fingerprint_changes_when_the_recording_changes() -> None:
    section = _section()
    a = stem_fingerprint(_song(sha256="a" * 64), section, pre_roll_s=1.0)
    b = stem_fingerprint(_song(sha256="b" * 64), section, pre_roll_s=1.0)
    assert a != b


def test_stem_fingerprint_changes_with_pre_roll() -> None:
    song, section = _song(), _section()
    a = stem_fingerprint(song, section, pre_roll_s=0.0)
    b = stem_fingerprint(song, section, pre_roll_s=2.0)
    assert a != b


def test_stem_cache_path_shape(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    path = stem_cache_path(repo, "solo-song", "solo-full", "abcd1234")
    assert path == repo.song_dir("solo-song") / "cache" / "stems" / "solo-full-guitar-abcd1234.flac"


# ── isolate_guitar() ──────────────────────────────────────────────────────


def _fake_subprocess_run(calls: list):
    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if len(argv) >= 2 and argv[1] == "-m" and "demucs" in argv:
            out_dir = Path(argv[argv.index("-o") + 1])
            model = argv[argv.index("-n") + 1]
            clip_path = Path(argv[-1])
            stem_dir = out_dir / model / clip_path.stem
            stem_dir.mkdir(parents=True, exist_ok=True)
            (stem_dir / "guitar.wav").write_bytes(b"fake guitar wav")
            (stem_dir / "no_guitar.wav").write_bytes(b"fake no_guitar wav")
        else:
            dest = Path(argv[-1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake audio bytes")
        return SimpleNamespace(returncode=0, stderr=b"")

    return fake_run


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Repo, Song, list]:
    repo = Repo(root=tmp_path)
    song = _song()
    audio_path = repo.song_dir(song.slug) / song.recording.file
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake source audio")

    monkeypatch.setitem(sys.modules, "demucs", SimpleNamespace())
    monkeypatch.setattr(
        separate_module, "locate_tool", lambda name: SimpleNamespace(path="ffmpeg", route="path")
    )
    calls: list = []
    monkeypatch.setattr(separate_module.subprocess, "run", _fake_subprocess_run(calls))
    return repo, song, calls


def test_isolate_guitar_writes_the_cache_file_and_returns_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()

    dest = isolate_guitar(repo, song, section, pre_roll_s=1.0)

    fp = stem_fingerprint(song, section, pre_roll_s=1.0)
    assert dest == stem_cache_path(repo, song.slug, section.id, fp)
    assert dest.is_file()
    assert len(calls) == 3  # ffmpeg cut, demucs, ffmpeg transcode


def test_isolate_guitar_ffmpeg_cut_argv_uses_source_seconds_with_pre_roll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section(start_s=60.0, end_s=90.0)

    isolate_guitar(repo, song, section, pre_roll_s=2.0)

    cut_argv = calls[0]
    assert cut_argv[0] == "ffmpeg"
    assert cut_argv[cut_argv.index("-ss") + 1] == "58.000000"  # 60 - 2 pre-roll
    assert cut_argv[cut_argv.index("-t") + 1] == "32.000000"  # 90 - 58
    assert cut_argv[cut_argv.index("-i") + 1] == str(repo.song_dir(song.slug) / song.recording.file)


def test_isolate_guitar_clamps_pre_roll_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A section starting at 1s with a 5s pre-roll never asks ffmpeg to
    seek before the start of the file."""
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section(start_s=1.0, end_s=10.0)

    isolate_guitar(repo, song, section, pre_roll_s=5.0)

    cut_argv = calls[0]
    assert cut_argv[cut_argv.index("-ss") + 1] == "0.000000"
    assert cut_argv[cut_argv.index("-t") + 1] == "10.000000"


def test_isolate_guitar_demucs_argv_uses_two_stems_guitar_and_the_given_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()

    isolate_guitar(repo, song, section, model="htdemucs_6s", device="cpu")

    demucs_argv = calls[1]
    assert demucs_argv[0] == sys.executable
    assert demucs_argv[1] == "-m"
    assert demucs_argv[2] == "demucs"
    assert demucs_argv[demucs_argv.index("-n") + 1] == "htdemucs_6s"
    assert demucs_argv[demucs_argv.index("--two-stems") + 1] == "guitar"
    assert demucs_argv[demucs_argv.index("-d") + 1] == "cpu"


def test_isolate_guitar_default_model_is_htdemucs_6s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    isolate_guitar(repo, song, _section())
    assert calls[1][calls[1].index("-n") + 1] == GUITAR_MODEL


def test_isolate_guitar_skips_the_work_when_already_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()
    fp = stem_fingerprint(song, section, pre_roll_s=0.0)
    cached = stem_cache_path(repo, song.slug, section.id, fp)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"already there")

    dest = isolate_guitar(repo, song, section)

    assert dest == cached
    assert calls == []  # no subprocess run at all -- genuinely skipped


def test_isolate_guitar_force_reruns_even_when_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()
    fp = stem_fingerprint(song, section, pre_roll_s=0.0)
    cached = stem_cache_path(repo, song.slug, section.id, fp)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"stale")

    isolate_guitar(repo, song, section, force=True)

    assert len(calls) == 3


def test_isolate_guitar_moving_the_section_boundary_misses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named for the stale-loop class of bug it prevents, same reasoning
    as render.py's own fingerprint (see module doc)."""
    repo, song, calls = _setup(tmp_path, monkeypatch)
    original = _section(end_s=90.0)
    fp = stem_fingerprint(song, original, pre_roll_s=0.0)
    cached = stem_cache_path(repo, song.slug, original.id, fp)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"stale -- belongs to the OLD boundary")

    moved = _section(end_s=90.01)
    dest = isolate_guitar(repo, song, moved)

    assert dest != cached
    assert len(calls) == 3  # actually re-ran, did not reuse the stale file


def test_isolate_guitar_raises_when_demucs_module_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, _calls = _setup(tmp_path, monkeypatch)
    monkeypatch.delitem(sys.modules, "demucs", raising=False)

    import builtins
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "demucs":
            raise ImportError("no demucs here")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(WoodshedError, match="demucs"):
        isolate_guitar(repo, song, _section())


def test_isolate_guitar_raises_on_a_nonzero_demucs_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, _calls = _setup(tmp_path, monkeypatch)

    def failing_run(argv, **kwargs):
        if len(argv) >= 2 and argv[1] == "-m" and "demucs" in argv:
            return SimpleNamespace(returncode=1, stderr=b"demucs: boom")
        dest = Path(argv[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake audio bytes")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(separate_module.subprocess, "run", failing_run)

    with pytest.raises(WoodshedError, match="demucs"):
        isolate_guitar(repo, song, _section())


def test_isolate_guitar_refuses_a_missing_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    song = _song()  # audio file never written
    monkeypatch.setitem(sys.modules, "demucs", SimpleNamespace())
    monkeypatch.setattr(
        separate_module, "locate_tool", lambda name: SimpleNamespace(path="ffmpeg", route="path")
    )

    with pytest.raises(WoodshedError, match="no such audio file"):
        isolate_guitar(repo, song, _section())
