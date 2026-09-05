"""Tests for woodshed.doctor -- see this unit's contract in
.agent_session/001_woodshed-implementation_plan.md (work unit C3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from woodshed import doctor
from woodshed.library import Repo

_EVERY_CHECKED_NAME = (
    "python", "uv", "ffmpeg", "ffprobe", "rubberband",
    "pyyaml", "pydantic", "numpy", "librosa", "pyaudiowpatch",
    "cache", "midi",
)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    (tmp_path / "songs").mkdir()
    (tmp_path / "setlists").mkdir()
    (tmp_path / "practice").mkdir()
    return Repo(root=tmp_path)


def test_run_checks_covers_every_named_tool(repo: Repo) -> None:
    checks = doctor.run_checks(repo)
    names = {c.name for c in checks}
    for expected in _EVERY_CHECKED_NAME:
        assert expected in names, f"{expected!r} has no Check"


def test_run_checks_does_not_crash_with_optional_tools_absent(repo: Repo) -> None:
    # This dev venv has only the three core deps + pytest/ruff installed, so
    # librosa and pyaudiowpatch are genuinely absent here -- exercising the
    # "report absent gracefully" path for real, not a mocked stand-in.
    checks = doctor.run_checks(repo)
    by_name = {c.name: c for c in checks}
    for optional in ("librosa", "pyaudiowpatch"):
        assert optional in by_name
        assert isinstance(by_name[optional].ok, bool)  # reported, not raised


def test_core_deps_are_present_in_this_dev_env(repo: Repo) -> None:
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    for core in ("python", "pyyaml", "pydantic", "numpy"):
        assert by_name[core].ok, by_name[core].detail


def test_cache_check_is_report_only_and_never_fails(repo: Repo) -> None:
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    assert by_name["cache"].ok is True
    assert "GB" in by_name["cache"].detail


def test_midi_check_is_an_honest_placeholder(repo: Repo) -> None:
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    assert "not yet checked" in by_name["midi"].detail.lower()


def test_report_prints_the_browser_note_unconditionally(repo: Repo) -> None:
    text, ok = doctor.report(repo)
    assert isinstance(ok, bool)
    assert "Web MIDI is Chrome/Edge only." in text


def test_report_core_ok_reflects_the_core_checks(repo: Repo) -> None:
    _text, ok = doctor.report(repo)
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    expected_ok = all(by_name[c].ok for c in ("python", "pyyaml", "pydantic", "numpy"))
    assert ok == expected_ok
