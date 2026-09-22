"""What `uv sync` with no arguments must install.

Three decisions, asserted rather than commented: **demucs, pyaudiowpatch and
librosa are core dependencies, not extras.** The failure each one fixes is
mechanical, not aesthetic -- `uv run --extra dev pytest` (the quality gate)
re-syncs the environment to exactly the extras named on that line, so a
demucs installed by `uv sync --extra separate` (or a pyaudiowpatch installed
by `uv sync --extra capture`, or a librosa installed by `uv sync --extra
analyze`) was uninstalled again by the very next test run, and the relevant
screen went back to offering an install hint -- or, in librosa's case, a
silent `bpm: 0.0` with no error at all -- for a package that had been
installed twice already.

Pure: stdlib `tomllib`, no import of the package under test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

#: Heavy or platform-specific packages promoted from an extra to `dependencies`.
PROMOTED = ("demucs", "pyaudiowpatch", "librosa")

#: Packages that stay genuine extras -- CLAUDE.md's layering rule is about
#: what the pure modules import, and it is unchanged by any promotion above.
STILL_OPTIONAL = ("mido", "python-rtmidi", "pytest", "ruff")


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


def test_promoted_packages_are_core_dependencies() -> None:
    core = _names(_pyproject()["project"]["dependencies"])
    for name in PROMOTED:
        assert name in core, f"{name!r} should be a core dependency"


def test_no_extra_also_pins_a_promoted_package() -> None:
    """Two homes for one package is how it goes stale: the extra would
    keep its own version floor, and a sync naming the extra would look
    like it was doing something."""
    extras = _pyproject()["project"].get("optional-dependencies", {})
    for extra, requirements in extras.items():
        names = _names(requirements)
        for name in PROMOTED:
            assert name not in names, f"extra {extra!r} still pins {name!r}"


def test_the_remaining_optional_extras_are_still_extras() -> None:
    core = _names(_pyproject()["project"]["dependencies"])
    for still_optional in STILL_OPTIONAL:
        assert still_optional not in core
