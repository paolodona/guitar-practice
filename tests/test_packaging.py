"""What `uv sync` with no arguments must install.

One decision, asserted rather than commented: **demucs is a core
dependency, not an extra.** The failure it fixes is mechanical, not
aesthetic -- `uv run --extra dev pytest` (the quality gate) re-syncs the
environment to exactly the extras named on that line, so a demucs
installed by `uv sync --extra separate` was uninstalled again by the very
next test run, and the practice screen went back to offering an install
hint for a package that had been installed twice already.

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


def test_demucs_is_a_core_dependency() -> None:
    assert "demucs" in _names(_pyproject()["project"]["dependencies"])


def test_no_extra_also_pins_demucs() -> None:
    """Two homes for one package is how it goes stale: the extra would
    keep its own version floor, and a sync naming the extra would look
    like it was doing something."""
    extras = _pyproject()["project"].get("optional-dependencies", {})
    for extra, requirements in extras.items():
        assert "demucs" not in _names(requirements), f"extra {extra!r} still pins demucs"


def test_the_heavy_optional_extras_are_still_extras() -> None:
    """Only demucs moved. librosa, pyaudiowpatch and the MIDI pair stay
    optional -- CLAUDE.md's layering rule is about what the pure modules
    import, and it is unchanged."""
    core = _names(_pyproject()["project"]["dependencies"])
    for still_optional in ("librosa", "pyaudiowpatch", "mido", "python-rtmidi", "pytest", "ruff"):
        assert still_optional not in core
