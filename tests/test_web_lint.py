"""Lint-style checks over `web/` — the plan's Group J names the first of
them by name ("a lint-style test grepping `web/` for `playbackRate` and
failing on a hit is cheap and worth having"), and the second is what makes
the front end's own node tests part of `uv run pytest` rather than
something a human has to remember to run.

Neither needs a browser, a device or an optional package: the first is a
text search, and the second shells out to `node` when it exists and skips
when it does not.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from woodshed.library import find_root

WEB = find_root(Path(__file__).resolve().parent) / "web"

#: `web/tests/` is excluded from the playbackRate scan, and only it: a test
#: asserting that a source node never GETS a playbackRate has to be allowed
#: to write the word. Everything else under web/ is scanned, vendor
#: included -- the worklet is exactly where the wrong knob would be
#: reachable.
_SCAN_EXCLUDED = ("tests",)


def _js_files() -> list[Path]:
    return sorted(
        p for p in WEB.rglob("*.js")
        if not any(part in _SCAN_EXCLUDED for part in p.relative_to(WEB).parts)
    )


def _strip_comments_and_strings(source: str) -> str:
    """Blank out block comments, line comments and string/template literals.

    The point of the scan is to catch playbackRate being USED, not
    documented -- docs/03-audio-engine.md's "Never `playbackRate` a
    stretched buffer" is quoted in player.js's own prose precisely so
    nobody re-derives it, and a grep that failed on the warning itself
    would have exactly the wrong incentive.
    """
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    source = re.sub(r"//[^\n]*", " ", source)
    source = re.sub(r"`(?:\\.|[^`\\])*`", '""', source, flags=re.S)
    source = re.sub(r"'(?:\\.|[^'\\\n])*'", '""', source)
    source = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', source)
    return source


def test_web_never_uses_playbackrate() -> None:
    """docs/03-audio-engine.md, "Looping, precisely": "Never `playbackRate`
    a stretched buffer. It is right there and it is the wrong knob: it
    re-introduces trap 1 on top of a correct render." Trap 1 is naive
    resampling, which at 50% drops the whole recording an octave -- plausible
    in a code review and unmistakable in the room.
    """
    hits = []
    for path in _js_files():
        code = _strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), start=1):
            if "playbackRate" in line:
                hits.append(f"{path.relative_to(WEB.parent)}:{lineno}")
    assert hits == [], (
        "playbackRate is the wrong knob for a stretched buffer "
        f"(docs/03-audio-engine.md, trap 1): {', '.join(hits)}"
    )


def test_the_playbackrate_scan_would_actually_catch_one(tmp_path: Path) -> None:
    """A guard on the guard: a scan that silently matched nothing (a bad
    path, an over-eager comment stripper) would pass forever and protect
    nothing."""
    code = _strip_comments_and_strings(
        "// never playbackRate a stretched buffer\n"
        "const s = ctx.createBufferSource();\n"
        "s.playbackRate.value = 0.5;\n"
    )
    lines = [n for n, line in enumerate(code.splitlines(), 1) if "playbackRate" in line]
    assert lines == [3]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    "script", [p.name for p in sorted((WEB / "tests").glob("test_*.mjs"))]
)
def test_web_node_tests_pass(script: str) -> None:
    """Run each of web/tests/*.mjs, which are plain node scripts by
    deliberate choice (see any of their module docs). They exercise real
    player.js/screens code against synthetic worklets and AudioContexts;
    running them from here is what keeps `uv run pytest` the one command
    that says whether the repo is green."""
    result = subprocess.run(
        [shutil.which("node"), str(WEB / "tests" / script)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
