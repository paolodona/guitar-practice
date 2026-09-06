"""``woodshed doctor`` -- check the toolchain before a practice session.

The ``Check`` dataclass and the report shape are lifted from
rambass-live/src/rambass/doctor.py:14-20 and :147-169 (``Check``, and
``report``'s ok/MISS rendering). What is checked is this phase's own list --
see docs/01-architecture.md's "A CLI, and why it exists first" section and
CLAUDE.md's "Layering": five external things can each be absent here
(ffmpeg, rubberband, a WASM stretcher, a MIDI device, a loopback-capable
audio device) and this is where that is said out loud, naming the route
that found each one.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass

from woodshed.config import load_config
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.tools import locate_tool

#: Printed unconditionally at the end of the report (see the module and
#: cli.py's docstrings) -- cheaper to say now than to debug a silent dead
#: pedal later, and it does not depend on anything that could be absent.
_BROWSER_NOTE = "Web MIDI is Chrome/Edge only."

#: Checks whose failure means the core commands (add, section, log) cannot
#: run at all -- everything else here is an optional extra.
_CORE_CHECKS = ("python", "pyyaml", "pydantic", "numpy")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    needed_for: str
    fix: str = ""


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _cache_usage_bytes(repo: Repo) -> int:
    """Total bytes under every song's cache/ dir. Report only -- see
    docs/01-architecture.md; eviction against the budget is a later phase."""
    total = 0
    for slug in repo.list_songs():
        cache_dir = repo.cache_dir(slug)
        if not cache_dir.is_dir():
            continue
        for entry in cache_dir.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    return total


def _tool_checks() -> list[Check]:
    checks: list[Check] = []
    for tool, needed in (
        ("ffmpeg", "decoding non-wav audio, extracting durations"),
        ("ffprobe", "reading the duration of non-wav audio"),
        ("rubberband", "the offline render cache (Phase 2) -- Rubber Band, "
                        "--fine, --formant; see docs/03-audio-engine.md"),
    ):
        try:
            location = locate_tool(tool)
        except WoodshedError as exc:
            checks.append(Check(tool, False, str(exc).splitlines()[0], needed, str(exc)))
            continue
        checks.append(Check(
            tool, True, f"{location.path}  (via {location.route})", needed,
        ))
    return checks


def _loopback_check() -> Check:
    """H3, docs/04-sources.md's "What will bite": exclusive-mode apps bypass
    the shared mix and produce silence -- "doctor checks the loopback opens
    and reports it." Degrades (not a failure) when pyaudiowpatch itself is
    absent -- there is nothing to open yet, which is `pyaudiowpatch`'s own
    check's job above, not this one's to repeat."""
    if not _module("pyaudiowpatch"):
        return Check(
            "loopback", True, "skipped -- pyaudiowpatch not installed",
            "capture.py's device half (Phase 1)",
        )
    try:
        import pyaudiowpatch

        p = pyaudiowpatch.PyAudio()
        try:
            info = p.get_default_wasapi_loopback()
            stream = p.open(
                format=pyaudiowpatch.paFloat32,
                channels=int(info["maxInputChannels"]),
                rate=int(info["defaultSampleRate"]),
                input=True,
                input_device_index=info["index"],
            )
            stream.close()
        finally:
            p.terminate()
    except Exception as exc:  # noqa: BLE001 -- any device-open failure is "MISS", not a crash
        return Check(
            "loopback", False, f"could not open the default loopback device: {exc}",
            "capturing what the machine plays (Phase 1)",
            "an exclusive-mode app (some ASIO paths, a DAW holding the device) "
            "may be bypassing the shared mix -- close it and retry",
        )
    return Check("loopback", True, f"opened {info['name']!r} and closed it",
                  "capturing what the machine plays (Phase 1)")


def _module_checks() -> list[Check]:
    checks: list[Check] = []
    for module, name, needed in (
        ("yaml", "pyyaml", "reading song.yaml / setlist.yaml / config.yaml"),
        ("pydantic", "pydantic", "validating song.yaml / setlist.yaml / config.yaml"),
        ("numpy", "numpy", "peaks and the pure section maths"),
    ):
        present = _module(module)
        checks.append(Check(
            name, present, "installed" if present else "missing", needed,
            "" if present else "uv sync  (these three are core, not an extra)",
        ))
    for module, name, needed, extra in (
        ("librosa", "librosa", "tempo detection and the beat grid (Phase 1)", "analyze"),
        ("pyaudiowpatch", "pyaudiowpatch", "loopback capture (Phase 1)", "capture"),
    ):
        present = _module(module)
        checks.append(Check(
            name, present, "installed" if present else "missing (optional)", needed,
            "" if present else f"uv sync --extra {extra}",
        ))
    checks.append(_demucs_check())
    return checks


def _demucs_check() -> Check:
    """S4 (Phase 1.5, Group S): guitar-only isolation's own dependency.
    Reported like the other optional extras above, but ALSO names Demucs's
    own CPU cost when it IS installed -- `rambass-live`'s own docs already
    measure `htdemucs_ft` at roughly 4x `htdemucs`'s own time on CPU, and
    the first isolation of a section is exactly where that would otherwise
    read as a hang rather than as slow-but-working."""
    present = _module("demucs")
    if present:
        detail = (
            "installed (CPU is genuinely slow -- htdemucs_ft runs roughly "
            "4x htdemucs's own time; the first isolation of a section can "
            "take a while, that is not a hang)"
        )
    else:
        detail = "missing (optional)"
    return Check(
        "demucs", present, detail, "guitar-only isolation (Phase 1.5, Group S)",
        "" if present else "uv sync --extra separate",
    )


def run_checks(repo: Repo) -> list[Check]:
    """Every check this phase can make. Never raises: an absent optional
    tool or package is reported, not an exception (see the module
    docstring and docs/01-architecture.md's `require_module` rule)."""
    checks: list[Check] = []

    checks.append(Check(
        "python", sys.version_info >= (3, 12),
        f"{sys.version.split()[0]} at {sys.executable}",
        "everything", "install Python 3.12 or newer",
    ))

    uv_path = shutil.which("uv")
    checks.append(Check(
        "uv", uv_path is not None, uv_path or "not found on PATH",
        "`uv sync` / `uv run woodshed ...`, this project's env and runner",
        "https://docs.astral.sh/uv/getting-started/installation/",
    ))

    checks.extend(_tool_checks())
    checks.extend(_module_checks())
    checks.append(_loopback_check())

    config = load_config(repo)
    used_bytes = _cache_usage_bytes(repo)
    budget_gb = config.render.cache_max_gb
    checks.append(Check(
        "cache", True,
        f"{used_bytes / 1e9:.2f} GB used of a {budget_gb:g} GB budget "
        "(report only -- eviction is a later phase)",
        "keeping songs/*/cache/ from filling the disk",
    ))

    # midi.js (Web MIDI in) does not exist yet in this phase -- an honest
    # placeholder, not a fabricated pass/fail for something unbuilt (see
    # CLAUDE.md's "the tool never judges the playing" and this unit's brief:
    # inventing a measurement the tool cannot make is the failure mode to
    # avoid).
    checks.append(Check(
        "midi", True, "not yet checked -- Web MIDI (midi.js) is not built in this phase",
        "the foot controller (Phase 3)",
    ))

    return checks


def report(repo: Repo) -> tuple[str, bool]:
    """Formatted report and whether the core toolchain is usable."""
    checks = run_checks(repo)
    width = max(len(c.name) for c in checks)
    lines = ["woodshed doctor", ""]
    core_ok = True
    for check in checks:
        mark = "ok  " if check.ok else "MISS"
        lines.append(f"[{mark}] {check.name.ljust(width)}  {check.detail}")
        if not check.ok:
            lines.append(f"{'':>7} needed for: {check.needed_for}")
            if check.fix:
                lines.append(f"{'':>7} fix: {check.fix}")
            if check.name in _CORE_CHECKS:
                core_ok = False
    lines += [
        "",
        "core commands (add, section, log, serve) work with the base install.",
        "analyze needs [analyze] (librosa); capture needs [capture] "
        "(pyaudiowpatch); render and reading non-wav audio need ffmpeg and "
        "rubberband; guitar-only isolation needs [separate] (demucs).",
        "",
        _BROWSER_NOTE,
    ]
    return "\n".join(lines), core_ok
