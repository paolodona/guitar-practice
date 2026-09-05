"""Tests for woodshed.tools: locating ffmpeg/ffprobe/rubberband, and reporting
a missing optional Python package.

Ported from rambass-live/tests/test_audio_tools.py (RAMBASS_FFMPEG ->
WOODSHED_FFMPEG, route "winget" -> "discovered"), plus one new test for the
rubberband install hint, which has no package-manager equivalent to fall back
on -- only a zip on a web page.

No subprocess is ever invoked here: `shutil.which` and the env vars are
monkeypatched, and "binaries" are empty files with the right name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from woodshed.errors import WoodshedError
from woodshed.tools import TOOLS, locate_tool


def _windows() -> bool:
    return sys.platform == "win32"


def _touch_binary(path: Path) -> Path:
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_ffmpeg(tmp_path: Path) -> Path:
    binary = _touch_binary(tmp_path / ("ffmpeg.exe" if _windows() else "ffmpeg"))
    _touch_binary(tmp_path / ("ffprobe.exe" if _windows() else "ffprobe"))
    return binary


def _clear_env(monkeypatch) -> None:
    monkeypatch.delenv("WOODSHED_FFMPEG", raising=False)
    monkeypatch.delenv("WOODSHED_RUBBERBAND", raising=False)


# ── WOODSHED_FFMPEG: an explicit override ────────────────────────────────────


def test_a_directory_in_woodshed_ffmpeg_is_searched(monkeypatch, fake_ffmpeg):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WOODSHED_FFMPEG", str(fake_ffmpeg.parent))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(locate_tool("ffmpeg").path) == fake_ffmpeg
    assert Path(locate_tool("ffprobe").path).stem == "ffprobe"


def test_the_binary_itself_in_woodshed_ffmpeg_works_too(monkeypatch, fake_ffmpeg):
    """Both a directory and the binary itself are natural things to paste in,
    so both are accepted."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("WOODSHED_FFMPEG", str(fake_ffmpeg))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(locate_tool("ffmpeg").path) == fake_ffmpeg
    assert Path(locate_tool("ffprobe").path).stem == "ffprobe"


def test_path_still_wins_when_there_is_no_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert locate_tool("ffmpeg").path == "/usr/bin/ffmpeg"


def test_a_wrong_override_says_so_instead_of_falling_through(monkeypatch, tmp_path):
    """Silently ignoring it is the worst outcome: the message then blames PATH
    for a typo in the variable, and the reader checks the wrong thing."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("WOODSHED_FFMPEG", str(tmp_path / "nope"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(WoodshedError) as caught:
        locate_tool("ffmpeg")
    assert "WOODSHED_FFMPEG" in str(caught.value)


def test_the_install_hint_survives_when_nothing_is_set(monkeypatch, tmp_path):
    """The winget roots are pointed at nothing on purpose: with three routes
    to resolution, "nothing is set" means all three are empty, and on a
    machine that really has the package folder this test would otherwise pass
    or fail depending on whose machine ran it."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "empty"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(WoodshedError) as caught:
        locate_tool("ffmpeg")
    message = str(caught.value)
    assert "winget install Gyan.FFmpeg" in message
    assert "WOODSHED_FFMPEG" in message, "the override is only useful if it is mentioned"


# ── the winget package folder, as a last resort ───────────────────────────────


def _winget_bin(root: Path, package: str, build: str, *, scope: str = "user") -> Path:
    r"""The real winget portable layout, which differs between the two scopes.

    `portablePackageUserRoot` defaults to `%LOCALAPPDATA%\Microsoft\WinGet`,
    `portablePackageMachineRoot` to `%PROGRAMFILES%\WinGet` -- no `Microsoft`
    segment in the machine one. A single pattern for both finds nothing on a
    machine-scope install, and the mistake is invisible in a test that builds
    the tree it expects.
    """
    prefix = root / "Microsoft" / "WinGet" if scope == "user" else root / "WinGet"
    binaries = prefix / "Packages" / package / build / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg", "ffprobe"):
        _touch_binary(binaries / (f"{name}.exe" if _windows() else name))
    return binaries


def test_discovery_is_searched_when_nothing_else_has_it(monkeypatch, tmp_path):
    binaries = _winget_bin(
        tmp_path, "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
        "ffmpeg-9.0-full_build",
    )
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(locate_tool("ffmpeg").path).parent == binaries
    assert Path(locate_tool("ffprobe").path).parent == binaries, "ffprobe sits beside it"


def test_the_machine_scope_winget_root_is_searched_too(monkeypatch, tmp_path):
    """`winget install --scope machine` puts portables under Program Files."""
    binaries = _winget_bin(tmp_path / "pf", "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build",
                           scope="machine")
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(locate_tool("ffmpeg").path).parent == binaries


def test_path_beats_discovery(monkeypatch, tmp_path):
    """A linked ffmpeg is a decision; a package folder left on disk is not. If
    discovery could outrank PATH, upgrading ffmpeg by hand would silently keep
    running the old discovered copy."""
    _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert locate_tool("ffmpeg").path == "/usr/bin/ffmpeg"


def test_a_wrong_override_is_not_rescued_by_discovery(monkeypatch, tmp_path):
    """Same argument as falling through to PATH: if a typo in the variable is
    quietly papered over, the variable stops meaning anything and the reader
    is never told it is wrong."""
    _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    monkeypatch.delenv("WOODSHED_RUBBERBAND", raising=False)
    monkeypatch.setenv("WOODSHED_FFMPEG", str(tmp_path / "nope"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(WoodshedError) as caught:
        locate_tool("ffmpeg")
    assert "WOODSHED_FFMPEG" in str(caught.value)


def test_the_newest_build_wins_when_an_upgrade_left_the_old_one_behind(
    monkeypatch, tmp_path,
):
    """An upgrade does not always remove the previous build directory, and the
    folder name cannot be sorted: "ffmpeg-10.0-full_build" sorts *before*
    "ffmpeg-9.0-full_build", so a lexicographic pick would run last year's
    build for the rest of the decade. Newest mtime, not highest name."""
    old = _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    new = _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-10.0-full_build")
    suffix = ".exe" if _windows() else ""
    import os as _os

    _os.utime(old / f"ffmpeg{suffix}", (1_000_000, 1_000_000))
    _os.utime(new / f"ffmpeg{suffix}", (2_000_000, 2_000_000))
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(locate_tool("ffmpeg").path).parent == new


def test_nothing_anywhere_still_reaches_the_install_hint(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "empty"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(WoodshedError) as caught:
        locate_tool("ffmpeg")
    assert "winget install Gyan.FFmpeg" in str(caught.value)


def test_the_route_taken_is_reported_so_doctor_can_say_which(monkeypatch, tmp_path):
    """`doctor` prints how ffmpeg was found, and "(via WOODSHED_FFMPEG)" for a
    discovered file sends the reader to check a variable that is not set. So
    resolution reports its route rather than doctor guessing it."""
    _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)

    _clear_env(monkeypatch)
    assert locate_tool("ffmpeg").route == "discovered"

    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert locate_tool("ffmpeg").route == "path"

    monkeypatch.setenv("WOODSHED_FFMPEG", str(_winget_roots_probe(tmp_path)))
    assert locate_tool("ffmpeg").route == "env"


def _winget_roots_probe(root: Path) -> Path:
    return next((root / "Microsoft" / "WinGet" / "Packages").glob("*/*/bin"))


# ── rubberband: no winget package, so no discovery and a different hint ──────


def test_rubberband_has_no_discovery_patterns():
    """There is no winget/choco/scoop package for rubberband. If a discovery
    pattern were ever added by mistake, it would search a folder that will
    never exist and add nothing but a wasted glob per lookup."""
    assert TOOLS["rubberband"].winget_patterns == ()


def test_rubberband_install_hint_names_the_actual_download_not_a_package_manager(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("WOODSHED_RUBBERBAND", raising=False)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(WoodshedError) as caught:
        locate_tool("rubberband")
    message = str(caught.value)
    assert "breakfastquay.com/rubberband" in message
    lowered = message.lower()
    for package_manager in ("winget", "choco", "scoop"):
        assert package_manager not in lowered, (
            f"the rubberband hint must not imply a {package_manager} package exists"
        )


def test_an_unknown_tool_name_is_a_woodshed_error_not_a_keyerror():
    with pytest.raises(WoodshedError):
        locate_tool("mp3gain")
