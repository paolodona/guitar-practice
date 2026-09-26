// web/tests/test_rung_hint.mjs — practice.js's `rungHint`, the guard that
// stops a section with no real rung ladder (start_speed >= target_speed,
// e.g. a `full_song` "whole song" entry meant to always play at 100%) from
// showing "Next rung 100% after 3 more clean reps" -- Paolo, 2026-09-26,
// after the dial got nudged below that section's own 100% target.

import assert from 'node:assert/strict';
import { rungHint } from '../screens/practice.js';
import { LADDER_DEFAULTS } from '../ladder.js';

function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

test('an ordinary ladder still reports the next rung', () => {
  const cfg = { ...LADDER_DEFAULTS, startSpeed: 50, ladderStep: 5, targetSpeed: 100 };
  assert.equal(rungHint(50, cfg), 55);
});

test('at target on an ordinary ladder reports no next rung', () => {
  const cfg = { ...LADDER_DEFAULTS, startSpeed: 50, ladderStep: 5, targetSpeed: 100 };
  assert.equal(rungHint(100, cfg), null);
});

test('start_speed === target_speed: no rung, even sitting right at it', () => {
  const cfg = { ...LADDER_DEFAULTS, startSpeed: 100, ladderStep: 5, targetSpeed: 100 };
  assert.equal(rungHint(100, cfg), null);
});

test('start_speed === target_speed: still no rung after the dial is dropped below it', () => {
  // The bug: nextRung() alone finds the single generated rung (the target
  // itself) and reports it as "next", even though this section was never
  // laddered -- there is nothing three clean reps at 80% should advance
  // toward.
  const cfg = { ...LADDER_DEFAULTS, startSpeed: 100, ladderStep: 5, targetSpeed: 100 };
  assert.equal(rungHint(80, cfg), null);
});

test('start_speed above target_speed is the same degenerate case', () => {
  const cfg = { ...LADDER_DEFAULTS, startSpeed: 100, ladderStep: 5, targetSpeed: 90 };
  assert.equal(rungHint(80, cfg), null);
});
