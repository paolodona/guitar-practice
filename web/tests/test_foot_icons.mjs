// web/tests/test_foot_icons.mjs — Phase 1.5, Q1's own test contract, run as
// a plain Node script (`node web/tests/test_foot_icons.mjs`), same house
// style as test_seek.mjs/test_ended.mjs/test_capture_review.mjs.
//
// Q1's contract: "a completeness test — every ACTIONS entry that is a foot
// chip has a matching icon — belongs beside actions.js's existing 'one
// action table' discipline: an icon silently missing for a real chip is the
// same class of bug as a missing CC." This imports both real tables
// (actions.js's ACTIONS, practice.js's FOOT_ICONS) and cross-checks them —
// no hand-typed list of action names to drift out of sync with either file.
//
// "Is a foot chip" was simply `cc != null` until restart_section joined the
// strip live 2026-09-10 with no CC of its own (docs/05-foot-control.md's
// six-CC pedal vocabulary is unaffected — see actions.js's own `chip` doc):
// `spec.chip` is now the one predicate, matching screens/practice.js's own
// foot-strip build.

import assert from 'node:assert/strict';
import { ACTIONS } from '../actions.js';
import { FOOT_ICONS } from '../screens/practice.js';

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

test('every ACTIONS entry that is a foot chip has a matching, non-empty FOOT_ICONS entry', () => {
  const footActions = Object.entries(ACTIONS).filter(([, spec]) => spec.chip);
  assert.ok(footActions.length > 0, 'no foot chips found -- ACTIONS itself looks wrong, not this test');
  for (const [name, spec] of footActions) {
    assert.ok(
      typeof FOOT_ICONS[name] === 'string' && FOOT_ICONS[name].length > 0,
      `ACTIONS.${name} (chip: true) has no icon in FOOT_ICONS`
    );
  }
});

test('FOOT_ICONS carries no icon for an action that is not actually a foot chip '
     + '(a stale entry left behind by a renamed/removed action is the same kind of drift, '
     + 'just in the other direction)', () => {
  const footNames = new Set(Object.entries(ACTIONS).filter(([, spec]) => spec.chip).map(([name]) => name));
  for (const name of Object.keys(FOOT_ICONS)) {
    assert.ok(footNames.has(name), `FOOT_ICONS.${name} names an action that is not a foot chip (or does not exist) in ACTIONS`);
  }
});
