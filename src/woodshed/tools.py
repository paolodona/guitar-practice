"""Locate the external binaries the audio engine shells out to, and report a
missing optional Python package with an install hint that actually works here.

Lifted and adapted from rambass-live/src/rambass/audio.py:28-214 (require_module,
the winget-folder discovery helper, and locate_tool), generalised from a single
hard-coded ffmpeg lookup into a table (`TOOLS`) so the same machinery also finds
`rubberband` -- which has no winget package at all, only a zip on a web page.

Three properties made the original correct and every change here preserves them:
  1. A set-but-WRONG env override is an error, never a silent fall-through to
     PATH or to discovery -- a typo in the variable must not produce "not on
     PATH", which sends the reader to check the one thing that was never wrong.
  2. Discovery (searching common install locations) ranks BELOW an unset-env
     PATH lookup -- a linked binary is a decision, a package folder left on disk
     by an old install is not; if discovery outranked PATH, upgrading by hand
     would silently keep running the old copy.
  3. The reported `route` is honest: something found via bare PATH reports
     route="path", never "env", so a `doctor` command never claims "(via
     WOODSHED_FFMPEG)" about a tool that was simply on PATH.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from woodshed.errors import WoodshedError

#: Where winget puts portable packages, searched as a last resort so a machine
#: with ffmpeg installed and nothing linked works with no environment variable
#: at all. The two scopes do not share a layout -- ``portablePackageUserRoot``
#: defaults to ``%LOCALAPPDATA%\Microsoft\WinGet``, ``portablePackageMachineRoot``
#: to ``%PROGRAMFILES%\WinGet``, no ``Microsoft`` segment in the second -- so one
#: pattern for both would silently find nothing on a machine-scope install.
#: rambass-live/src/rambass/audio.py:98-108.
WINGET_ROOTS = (
    ("LOCALAPPDATA", "Microsoft/WinGet"),
    ("PROGRAMFILES", "WinGet"),
)


@dataclass(frozen=True)
class ToolSpec:
    """What it takes to find one external binary, and what to tell a human
    who does not have it: the env var that overrides discovery, the glob
    patterns (relative to a winget root, `{exe}` filled in per candidate name)
    worth searching when PATH has nothing, and a per-OS install hint."""

    env: str
    winget_patterns: tuple[str, ...] = field(default_factory=tuple)
    install: dict[str, str] = field(default_factory=dict)


TOOLS: dict[str, ToolSpec] = {
    "ffmpeg": ToolSpec(
        env="WOODSHED_FFMPEG",
        winget_patterns=(
            "Packages/Gyan.FFmpeg*/*/bin/{exe}",
            "Packages/Gyan.FFmpeg*/bin/{exe}",
            "Links/{exe}",
        ),
        install={
            "Windows": "winget install Gyan.FFmpeg",
            "macOS": "brew install ffmpeg",
            "Linux": "apt install ffmpeg",
        },
    ),
    # ffprobe sits beside ffmpeg in every distribution this cares about, so it
    # shares ffmpeg's env var, winget patterns and install hint.
    "ffprobe": ToolSpec(
        env="WOODSHED_FFMPEG",
        winget_patterns=(
            "Packages/Gyan.FFmpeg*/*/bin/{exe}",
            "Packages/Gyan.FFmpeg*/bin/{exe}",
            "Links/{exe}",
        ),
        install={
            "Windows": "winget install Gyan.FFmpeg",
            "macOS": "brew install ffmpeg",
            "Linux": "apt install ffmpeg",
        },
    ),
    "rubberband": ToolSpec(
        env="WOODSHED_RUBBERBAND",
        winget_patterns=(),  # no winget package exists for this -- don't imply one
        install={
            "Windows": "download rubberband-4.0.0-gpl-executable-windows.zip from "
            "https://breakfastquay.com/rubberband/ and set WOODSHED_RUBBERBAND "
            "to the folder",
            "macOS": "brew install rubberband",
            "Linux": "apt install rubberband-cli",
        },
    ),
}


@dataclass(frozen=True)
class ToolLocation:
    """Where a tool was found, and which of the three routes found it."""

    path: str
    route: str  # "env" | "path" | "discovered"


def _binary_in(directory: Path, name: str) -> Path | None:
    for suffix in (".exe", ""):
        candidate = directory / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _discover(name: str, spec: ToolSpec) -> list[Path]:
    """Every *name* found via *spec*'s winget patterns, newest build first.

    Sorted by mtime and not by folder name: an upgrade does not always remove
    the previous build directory, and "ffmpeg-10.0-full_build" sorts *before*
    "ffmpeg-9.0-full_build", so a lexicographic pick would keep running an old
    build for the rest of the decade.
    """
    if not spec.winget_patterns:
        return []
    found: list[Path] = []
    for variable, prefix in WINGET_ROOTS:
        root = os.environ.get(variable, "").strip()
        if not root:
            continue
        base = Path(root) / prefix
        if not base.is_dir():
            continue
        for pattern in spec.winget_patterns:
            for suffix in (".exe", ""):
                found.extend(
                    hit
                    for hit in base.glob(pattern.format(exe=f"{name}{suffix}"))
                    if hit.is_file()
                )
    unique = {hit.resolve(): hit for hit in found}
    return sorted(unique.values(), key=lambda hit: hit.stat().st_mtime, reverse=True)


def _not_found_message(name: str, spec: ToolSpec) -> str:
    lines = [f"{name} is not on PATH. Install it:"]
    for platform_name in ("macOS", "Linux", "Windows"):
        hint = spec.install.get(platform_name)
        if hint:
            lines.append(f"  {platform_name}: {hint}")
    lines.append(
        f"Already installed? Set {spec.env} to the folder holding it "
        "(or to the binary itself) instead of editing PATH."
    )
    if spec.winget_patterns:
        lines.append("  (PATH and the usual install locations were both searched.)")
    else:
        lines.append("  (PATH was searched.)")
    return "\n".join(lines)


def locate_tool(name: str) -> ToolLocation:
    """Find *name*, reporting which route found it.

    ``spec.env`` first, then PATH, then discovery. That order is deliberate at
    both ends -- see the module docstring for the three properties this
    preserves. rambass-live/src/rambass/audio.py:163-202.
    """
    if name not in TOOLS:
        raise WoodshedError(f"unknown tool {name!r}; expected one of {sorted(TOOLS)}")
    spec = TOOLS[name]

    override = os.environ.get(spec.env, "").strip().strip('"')
    if override:
        base = Path(override)
        if base.is_file() and base.stem == name:
            return ToolLocation(str(base), "env")
        directory = base.parent if base.is_file() else base
        found = _binary_in(directory, name)
        if found is not None:
            # A directory override names one tool; a sibling (ffprobe beside
            # ffmpeg) is found the same way under the same variable.
            return ToolLocation(str(found), "env")
        raise WoodshedError(
            f"{spec.env} is set to {override!r} but there is no {name} there.\n"
            f"  it should be the directory holding {name}, or the {name} binary itself"
        )

    path = shutil.which(name)
    if path:
        return ToolLocation(path, "path")

    for candidate in _discover(name, spec):
        return ToolLocation(str(candidate), "discovered")

    raise WoodshedError(_not_found_message(name, spec))


# ── require_module: a missing optional Python package, not a missing binary ──
#
# rambass-live/src/rambass/audio.py:28-66.


def _pip_module_present() -> bool:
    """Whether this interpreter can run ``python -m pip``."""
    return importlib.util.find_spec("pip") is not None


def _install_target(extra: str) -> str:
    """The right-hand side of the install command for *extra*."""
    return "-e ." if extra in ("", "core") else f"-e '.[{extra}]'"


def _installer_prefixes() -> list[str]:
    """Package installers that exist here, best first.

    Resolved rather than hard-coded: a venv seeded by ``uv venv`` has no pip,
    so a hint reading ``pip install -e '.[analyze]'`` names a command that does
    not exist, and nothing in the message lets the reader tell that apart from
    a genuinely missing package. pip first when it works, because it is what
    the docs say; the interpreter form next, for an unactivated venv that has
    the module and no console script; uv last.
    """
    found: list[str] = []
    if shutil.which("pip"):
        found.append("pip")
    elif _pip_module_present():
        found.append("python -m pip")
    if shutil.which("uv"):
        found.append("uv pip")
    return found


def _install_hint(extra: str) -> str:
    """The install command for *extra* that works in this environment."""
    target = _install_target(extra)
    prefixes = _installer_prefixes()
    if not prefixes:
        return f"python -m ensurepip --upgrade, then:  pip install {target}"
    return f"{prefixes[0]} install {target}"


def _install_alternatives(extra: str) -> list[str]:
    """Other installers that would also work here, in preference order."""
    target = _install_target(extra)
    return [f"{prefix} install {target}" for prefix in _installer_prefixes()[1:]]


def require_module(name: str, extra: str):
    """Import *name* or explain which extra installs it."""
    try:
        return __import__(name)
    except ImportError as exc:
        lines = [
            f"this command needs the '{name}' package.",
            f"  install it with:  {_install_hint(extra)}",
        ]
        lines += [f"  (or:              {alt})" for alt in _install_alternatives(extra)]
        raise WoodshedError("\n".join(lines)) from exc
