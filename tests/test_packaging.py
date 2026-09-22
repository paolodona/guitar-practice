"""What `uv sync` with no arguments must install.

Two decisions, asserted rather than commented: **demucs and pyaudiowpatch
are core dependencies, not extras.** The failure it fixes is mechanical,
not aesthetic -- `uv run --extra dev pytest` (the quality gate) re-syncs
the environment to exactly the extras named on that line, so a demucs
installed by `uv sync --extra separate` (or a pyaudiowpatch installed by
`uv sync --extra capture`) was uninstalled again by the very next test
run, and the relevant screen went back to offering an install hint for a
package that had been installed twice already.

Pure: stdlib `tomllib`, no import of the package under test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _names(requirements: list[str]) -> set[str]:
    """Distribution names out of PEP 508 requirement strings."""
    out = set()
    for requirement in requirements:
        name = requirement.split(";")[0].strip()
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " "):
            name = name.split(separator)[0]
        out.add(name.strip().lower())
    return out


def test_demucs_and_pyaudiowpatch_are_core_dependencies() -> None:
    core = _names(_pyproject()["project"]["dependencies"])
    assert "demucs" in core
    assert "pyaudiowpatch" in core


def test_no_extra_also_pins_demucs_or_pyaudiowpatch() -> None:
    """Two homes for one package is how it goes stale: the extra would
    keep its own version floor, and a sync naming the extra would look
    like it was doing something."""
    extras = _pyproject()["project"].get("optional-dependencies", {})
    for extra, requirements in extras.items():
        names = _names(requirements)
        assert "demucs" not in names, f"extra {extra!r} still pins demucs"
        assert "pyaudiowpatch" not in names, f"extra {extra!r} still pins pyaudiowpatch"


def test_the_remaining_optional_extras_are_still_extras() -> None:
    """Only demucs and pyaudiowpatch moved. librosa and the MIDI pair stay
    optional -- CLAUDE.md's layering rule is about what the pure modules
    import, and it is unchanged."""
    core = _names(_pyproject()["project"]["dependencies"])
    for still_optional in ("librosa", "mido", "python-rtmidi", "pytest", "ruff"):
        assert still_optional not in core
