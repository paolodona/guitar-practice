"""`web/ladder.js` mirrors `woodshed.ladder`, and this runs both.

The mirror exists because the browser cannot import a Python module and
there is no ladder endpoint to ask (see web/ladder.js's own module doc).
That is a defensible trade exactly once: the moment nothing compares the
two, it stops being a mirror and becomes a fork, and the failure mode is
the worst kind -- the screen advances you to a rung the ledger's own
`starting_speed` will not put you back on tomorrow.

So: one node process, the same inputs through both implementations, the
same JSON out. Skipped when node is absent; needs no browser and no
optional Python package.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from woodshed import ladder
from woodshed.library import find_root

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

ROOT = find_root()

#: Config shapes worth crossing the boundary with: the default, a fractional
#: step (which is where a float mirror would drift first), a step that does
#: not divide the span evenly, and a degenerate zero step.
CONFIGS = [
    dict(start_speed=50.0, ladder_step=5.0, reps_to_advance=3, target_speed=100.0),
    dict(start_speed=40.0, ladder_step=2.5, reps_to_advance=5, target_speed=90.0),
    dict(start_speed=60.0, ladder_step=7.0, reps_to_advance=2, target_speed=100.0),
    dict(start_speed=55.0, ladder_step=0.0, reps_to_advance=3, target_speed=100.0),
]

_SCRIPT = """
import { rungs, nextRung, startingSpeed, onClean, onRetract, hint } from './web/ladder.js';
const configs = JSON.parse(process.argv[process.argv.length - 1]);
const out = configs.map((c) => {
  const cfg = {
    startSpeed: c.start_speed, ladderStep: c.ladder_step,
    repsToAdvance: c.reps_to_advance, targetSpeed: c.target_speed,
  };
  const all = rungs(cfg);
  return {
    rungs: all,
    next: all.map((r) => nextRung(r, cfg)),
    starting_empty: startingSpeed({}, cfg),
    starting_earned: all.map((r) => startingSpeed({ [r]: c.reps_to_advance }, cfg)),
    clean_sequence: (() => {
      let state = { speed: cfg.startSpeed, cleanAtSpeed: 0 };
      const seen = [];
      for (let i = 0; i < 40; i++) {
        state = onClean(state, cfg);
        seen.push([state.speed, state.cleanAtSpeed]);
      }
      return seen;
    })(),
    retract_from_two: (() => {
      const s = onRetract({ speed: cfg.startSpeed, cleanAtSpeed: 2 }, cfg);
      return [s.speed, s.cleanAtSpeed];
    })(),
    hints: all.map((r) => hint({ speed: r, cleanAtSpeed: 1 }, cfg)),
  };
});
console.log(JSON.stringify(out));
"""


def _from_javascript() -> list[dict]:
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", _SCRIPT, json.dumps(CONFIGS)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _from_python() -> list[dict]:
    out = []
    for raw in CONFIGS:
        cfg = ladder.LadderConfig(**raw)
        all_rungs = ladder.rungs(cfg)
        out.append(
            {
                "rungs": all_rungs,
                "next": [ladder._next_rung(r, cfg) for r in all_rungs],
                "starting_empty": ladder.starting_speed({}, cfg),
                "starting_earned": [
                    ladder.starting_speed({r: cfg.reps_to_advance}, cfg) for r in all_rungs
                ],
                "clean_sequence": _clean_sequence(cfg),
                "retract_from_two": _retract_from_two(cfg),
                "hints": [
                    ladder.LadderState(speed=r, clean_at_speed=1).hint(cfg) for r in all_rungs
                ],
            }
        )
    return out


def _clean_sequence(cfg: ladder.LadderConfig) -> list[list[float]]:
    state = ladder.LadderState(speed=cfg.start_speed, clean_at_speed=0)
    seen = []
    for _ in range(40):
        state = ladder.on_clean(state, cfg)
        seen.append([state.speed, state.clean_at_speed])
    return seen


def _retract_from_two(cfg: ladder.LadderConfig) -> list[float]:
    state = ladder.on_retract(ladder.LadderState(speed=cfg.start_speed, clean_at_speed=2), cfg)
    return [state.speed, state.clean_at_speed]


@pytest.mark.parametrize("index", range(len(CONFIGS)))
def test_ladder_js_agrees_with_ladder_py(index: int) -> None:
    js = _from_javascript()[index]
    py = _from_python()[index]
    assert js == py, f"web/ladder.js and woodshed.ladder disagree for {CONFIGS[index]}"
