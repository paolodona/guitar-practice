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
    "pyyaml", "pydantic", "numpy", "librosa", "pyaudiowpatch", "demucs",
    "cache", "midi", "loopback",
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
    # Whether librosa/pyaudiowpatch are actually present depends on which
    # extras this venv has synced (`uv sync --extra analyze --extra
    # capture`) -- either way, run_checks must report a bool, never raise.
    checks = doctor.run_checks(repo)
    by_name = {c.name: c for c in checks}
    for optional in ("librosa", "pyaudiowpatch", "demucs"):
        assert optional in by_name
        assert isinstance(by_name[optional].ok, bool)  # reported, not raised


def test_demucs_check_names_the_cpu_cost_when_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """S4 (Phase 1.5, Group S): Demucs on CPU is genuinely slow -- the
    first isolation of a section should read as slow-but-working, not as
    a hang."""
    monkeypatch.setattr(doctor, "_module", lambda name: True)
    check = doctor._demucs_check()
    assert check.ok is True
    assert "CPU" in check.detail
    assert "htdemucs_ft" in check.detail


def test_demucs_check_names_the_separate_extra_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "_module", lambda name: False)
    check = doctor._demucs_check()
    assert check.ok is False
    assert check.fix == "uv sync --extra separate"


def test_core_deps_are_present_in_this_dev_env(repo: Repo) -> None:
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    for core in ("python", "pyyaml", "pydantic", "numpy"):
        assert by_name[core].ok, by_name[core].detail


def test_cache_check_passes_while_under_the_budget(repo: Repo) -> None:
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    assert by_name["cache"].ok is True
    assert "GB" in by_name["cache"].detail


def test_cache_check_fails_when_the_budget_is_already_exceeded(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 2, K3: `evict` exists now, so this is no longer report-only.
    Over budget is a MISS naming what actually reclaims it -- and saying
    that `cache/` is safe to delete by hand, which is the one thing a
    person needs to know when a disk is full at 11pm."""
    monkeypatch.setattr(doctor, "_cache_usage_bytes", lambda _repo: 999 * 10**9)
    check = {c.name: c for c in doctor.run_checks(repo)}["cache"]
    assert check.ok is False
    assert "safe to delete" in check.fix


def test_midi_check_names_the_configured_input_and_the_browser(repo: Repo) -> None:
    """Web MIDI lives in the browser, so Python cannot see a pedal from
    here. Reporting what config.yaml is looking FOR, and where that
    matching happens, is the honest check -- inventing a pass/fail for a
    device this process cannot enumerate is exactly the measurement
    CLAUDE.md forbids."""
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    check = by_name["midi"]
    assert check.ok is True
    assert "Chrome" in check.detail or "browser" in check.detail.lower()


def test_loopback_check_reports_honestly_either_way(repo: Repo) -> None:
    # H3's loopback-open check must degrade, not fail, when pyaudiowpatch is
    # absent -- and actually open/close the device for real when
    # `uv sync --extra capture` has installed it. Assert the shape true of
    # both branches rather than pinning one specific machine's state.
    try:
        import pyaudiowpatch  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    assert by_name["loopback"].ok is True
    if installed:
        assert "not installed" not in by_name["loopback"].detail
    else:
        assert "pyaudiowpatch not installed" in by_name["loopback"].detail
    assert "loopback" not in doctor._CORE_CHECKS  # never gates core_ok


def test_report_prints_the_browser_note_unconditionally(repo: Repo) -> None:
    text, ok = doctor.report(repo)
    assert isinstance(ok, bool)
    assert "Web MIDI is Chrome/Edge only." in text


def test_report_core_ok_reflects_the_core_checks(repo: Repo) -> None:
    _text, ok = doctor.report(repo)
    by_name = {c.name: c for c in doctor.run_checks(repo)}
    expected_ok = all(by_name[c].ok for c in ("python", "pyyaml", "pydantic", "numpy"))
    assert ok == expected_ok
