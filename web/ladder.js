/**
 * ladder.js — the speed ladder in the browser: `woodshed.ladder`'s pure
 * percent-domain rules (src/woodshed/ladder.py), plus the one thing the
 * Python side has no opinion about — WHEN they are applied, which is at a
 * loop boundary and nowhere else.
 *
 * K1's file. Two things it is not:
 *
 *  - It is not a second set of rules. Every function below is a direct
 *    mirror of the Python of the same name, and `tests/test_ladder_mirror.py`
 *    runs both over the same inputs and asserts they agree — a mirror that
 *    nothing compares is a fork waiting to happen. It exists because this
 *    file cannot import a Python module and there is no ladder endpoint to
 *    ask; if one is ever added, this becomes a cache rather than a source.
 *  - It does not hold a HISTORICAL count. `cleanAtSpeed` is progress toward
 *    the NEXT rung: it lives in `0 .. repsToAdvance` and resets to 0 on
 *    advance. Total cleans at a speed is a different number, it belongs to
 *    `ledger.clean_by_speed`, and conflating the two is the exact mistake
 *    ladder.py's own docstring spends a paragraph on.
 *
 * **Auto-confirm is on by default** (this unit's own brief). The tool never
 * judges the playing (CLAUDE.md) — it cannot hear whether a pass was
 * clean — so the question is only which way the default falls. It falls
 * towards counting: a lap you played is assumed clean, and the human's
 * judgement enters as a RETRACTION (`x`, or the foot's CC85) when it
 * wasn't. That keeps the hands on the guitar for the common case and makes
 * the tool's claim honest either way: it is still a human saying "that one
 * didn't count", never the tool deciding it did. With `autoConfirm` off,
 * the opposite default applies and only a lap explicitly confirmed (`c`)
 * counts — the Phase 0 behaviour, kept because a section being drilled at
 * the edge of what you can play wants it.
 */

/** @typedef {{startSpeed: number, ladderStep: number, repsToAdvance: number, targetSpeed: number}} LadderConfig */
/** @typedef {{speed: number, cleanAtSpeed: number}} LadderState */

/** ladder.LadderConfig's own defaults, mirrored. */
export const LADDER_DEFAULTS = {
  startSpeed: 50,
  ladderStep: 5,
  repsToAdvance: 3,
  targetSpeed: 100,
};

/** Float slack for comparing two percents that came from repeated addition. */
const EPS = 1e-9;

/**
 * startSpeed, startSpeed+ladderStep, ... up to and including targetSpeed.
 * @param {LadderConfig} cfg
 * @returns {number[]}
 */
export function rungs(cfg) {
  if (cfg.ladderStep <= 0) return [cfg.startSpeed];
  const result = [];
  let rung = cfg.startSpeed;
  while (rung < cfg.targetSpeed) {
    result.push(rung);
    rung += cfg.ladderStep;
  }
  result.push(cfg.targetSpeed);
  return result;
}

/**
 * The rung above `speed`, capped at targetSpeed; null when already there.
 * @param {number} speed
 * @param {LadderConfig} cfg
 * @returns {number | null}
 */
export function nextRung(speed, cfg) {
  if (speed >= cfg.targetSpeed - EPS) return null;
  const found = rungs(cfg).find((r) => r > speed + EPS);
  return found === undefined ? cfg.targetSpeed : found;
}

/**
 * The rung ABOVE the highest one with >= repsToAdvance cleans recorded,
 * capped at targetSpeed; else cfg.startSpeed. Returning the earned rung
 * itself would make you re-earn work `onClean` already advanced past.
 * @param {Record<number, number> | Map<number, number>} cleanBySpeed
 * @param {LadderConfig} cfg
 */
export function startingSpeed(cleanBySpeed, cfg) {
  const get = (rung) => (
    cleanBySpeed instanceof Map ? cleanBySpeed.get(rung) : cleanBySpeed[rung]
  ) ?? 0;
  const all = rungs(cfg);
  let highest = null;
  all.forEach((rung, index) => {
    if (get(rung) >= cfg.repsToAdvance) highest = index;
  });
  if (highest === null) return cfg.startSpeed;
  return all[Math.min(highest + 1, all.length - 1)];
}

/**
 * Record a clean rep: increment cleanAtSpeed; advance a rung and reset the
 * counter once it reaches repsToAdvance. Never advances past targetSpeed.
 * @param {LadderState} state
 * @param {LadderConfig} cfg
 * @returns {LadderState}
 */
export function onClean(state, cfg) {
  const cleanAtSpeed = state.cleanAtSpeed + 1;
  if (cleanAtSpeed < cfg.repsToAdvance) return { speed: state.speed, cleanAtSpeed };
  const next = nextRung(state.speed, cfg);
  return { speed: next === null ? state.speed : next, cleanAtSpeed: 0 };
}

/**
 * Record a retraction: drop cleanAtSpeed by one, floored at 0. Never
 * resets it outright and never changes speed — a retraction punishes the
 * last rep's progress, not the whole rung.
 * @param {LadderState} state
 * @param {LadderConfig} _cfg - unused; kept for symmetry with onClean
 * @returns {LadderState}
 */
export function onRetract(state, _cfg) {
  return { speed: state.speed, cleanAtSpeed: Math.max(0, state.cleanAtSpeed - 1) };
}

/** Reps still needed to advance off this rung, floored at 0. */
export function remaining(state, cfg) {
  return Math.max(0, cfg.repsToAdvance - state.cleanAtSpeed);
}

/** "-> 60% after 1 more" — ladder.LadderState.hint, mirrored. */
export function hint(state, cfg) {
  const next = nextRung(state.speed, cfg);
  const left = remaining(state, cfg);
  if (next === null) return `-> target after ${left} more`;
  // No _fmt mirror needed: ladder._fmt exists only to stop Python printing
  // "60.0", and JS's own String(60) is already "60".
  return `-> ${next}% after ${left} more`;
}

/**
 * The rules above, wired to the loop boundary — the half ladder.py has no
 * opinion about, because a Python module cannot see a seam go by.
 *
 * A screen owns one of these, calls `pass_()` from the engine's own 'pass'
 * event (never from a timer of its own — player.js decides what a pass is,
 * CLAUDE.md's "one action table" discipline applied to the rep contract),
 * and reads the state back for display. Nothing here writes to the ledger:
 * the screen POSTs `/api/rep`, because the ledger is the irreplaceable file
 * and exactly one place should be appending to it.
 *
 * Fires 'advance' — CustomEvent<{from, to, state}> — when a pass moved the
 * rung. That is always at a loop boundary, by construction: `pass_()` is
 * the only thing that can advance a rung, and the engine only calls it at
 * a seam. Speed changes therefore never land mid-loop, which is
 * docs/03-audio-engine.md's rule and the reason this class exists rather
 * than a handful of loose variables on the screen.
 * @extends EventTarget
 */
export class Ladder extends EventTarget {
  /**
   * @param {{cfg: LadderConfig, speed?: number, cleanAtSpeed?: number,
   *          autoConfirm?: boolean}} options
   */
  constructor({ cfg, speed, cleanAtSpeed = 0, autoConfirm = true }) {
    super();
    this.cfg = { ...LADDER_DEFAULTS, ...cfg };
    this.speed = speed ?? this.cfg.startSpeed;
    this.cleanAtSpeed = cleanAtSpeed;
    this.autoConfirm = autoConfirm;
    /** Armed by confirm() for whichever lap is in flight; cleared by every
     *  pass, so a confirmation never carries over into the next lap. */
    this.pendingClean = false;
    /** Set by dirty() for the lap in flight — the way a human says "not
     *  that one" while auto-confirm is on. */
    this.pendingDirty = false;
  }

  /** @returns {LadderState} */
  get state() {
    return { speed: this.speed, cleanAtSpeed: this.cleanAtSpeed };
  }

  /** Reps still needed to advance off this rung. */
  get remaining() { return remaining(this.state, this.cfg); }

  /** "-> 60% after 1 more". */
  get hint() { return hint(this.state, this.cfg); }

  /** Arm "that one was clean" for the lap in flight (the `c` key). */
  confirm() { this.pendingClean = true; this.pendingDirty = false; }

  /** Arm "that one was NOT clean" for the lap in flight — the meaningful
   *  half of auto-confirm, and a no-op when auto-confirm is off (nothing
   *  counts unless confirmed there anyway). */
  dirty() { this.pendingDirty = true; this.pendingClean = false; }

  /**
   * A pass completed. Applies the ladder's rules and returns what happened
   * so the caller can log exactly one ledger line for it.
   * @returns {{clean: boolean, advanced: {from: number, to: number} | null,
   *            state: LadderState}}
   */
  pass_() {
    const clean = this.pendingDirty ? false : (this.autoConfirm || this.pendingClean);
    this.pendingClean = false;
    this.pendingDirty = false;
    if (!clean) return { clean, advanced: null, state: this.state };

    const before = this.speed;
    const next = onClean(this.state, this.cfg);
    this.speed = next.speed;
    this.cleanAtSpeed = next.cleanAtSpeed;
    const advanced = next.speed === before ? null : { from: before, to: next.speed };
    if (advanced) {
      this.dispatchEvent(new CustomEvent('advance', { detail: { ...advanced, state: this.state } }));
    }
    return { clean, advanced, state: this.state };
  }

  /** A rep was retracted (the `x` key, or CC85). Drops progress by one;
   *  never drops the rung, and never below zero. */
  retract() {
    const next = onRetract(this.state, this.cfg);
    this.cleanAtSpeed = next.cleanAtSpeed;
    return this.state;
  }

  /** A manual speed change (the slider, `speed_up`/`speed_down`). Progress
   *  toward the next rung belongs to the rung you earned it on, so it
   *  resets — otherwise two presses of `speed_down` and one clean rep would
   *  advance you off a rung you never practised. */
  setSpeed(pct) {
    if (pct === this.speed) return this.state;
    this.speed = pct;
    this.cleanAtSpeed = 0;
    return this.state;
  }
}
