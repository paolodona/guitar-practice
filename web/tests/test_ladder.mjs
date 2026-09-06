// web/tests/test_ladder.mjs — K1's test contract for web/ladder.js, run as
// a plain node script (see test_seek.mjs for why this repo's front-end
// tests are not under a framework).
//
// The pure rules below are asserted here AND compared against
// src/woodshed/ladder.py itself by tests/test_ladder_mirror.py, which runs
// both over the same inputs. This file owns the cases that are about the
// BROWSER's half — auto-confirm, when an advance is allowed to happen —
// which the Python has no opinion about.

import assert from 'node:assert/strict';
import {
  Ladder,
  nextRung,
  onClean,
  onRetract,
  rungs,
  startingSpeed,
} from '../ladder.js';

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`ok - ${name}`); }
  catch (err) { failures += 1; console.error(`FAIL - ${name}`); console.error(err); }
}

const CFG = { startSpeed: 50, ladderStep: 5, repsToAdvance: 3, targetSpeed: 100 };

test('rungs run from the start speed to the target inclusive', () => {
  assert.deepEqual(rungs(CFG), [50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]);
  assert.deepEqual(rungs({ ...CFG, ladderStep: 0 }), [50]);
});

test('nothing advances past the target speed', () => {
  assert.equal(nextRung(100, CFG), null);
  assert.deepEqual(onClean({ speed: 100, cleanAtSpeed: 2 }, CFG), { speed: 100, cleanAtSpeed: 0 });
});

test('starting speed is the rung ABOVE the one already earned', () => {
  // Returning 55 would make you re-earn work onClean already advanced past.
  assert.equal(startingSpeed({ 55: 3 }, CFG), 60);
  assert.equal(startingSpeed({}, CFG), 50);
  assert.equal(startingSpeed({ 55: 2 }, CFG), 50);
  assert.equal(startingSpeed({ 100: 9 }, CFG), 100);
});

test('a retraction drops progress by one, never to zero', () => {
  // The punishment it avoids: two good reps undone by one bad one.
  assert.deepEqual(onRetract({ speed: 55, cleanAtSpeed: 2 }, CFG), { speed: 55, cleanAtSpeed: 1 });
  assert.deepEqual(onRetract({ speed: 55, cleanAtSpeed: 0 }, CFG), { speed: 55, cleanAtSpeed: 0 });
});

// ---- the browser's half -------------------------------------------------

test('auto-confirm is on by default: a pass you played counts', () => {
  const ladder = new Ladder({ cfg: CFG });
  assert.equal(ladder.autoConfirm, true);
  const result = ladder.pass_();
  assert.equal(result.clean, true);
  assert.equal(ladder.cleanAtSpeed, 1);
});

test('with auto-confirm on, the human says "not that one" instead', () => {
  const ladder = new Ladder({ cfg: CFG });
  ladder.dirty();
  assert.equal(ladder.pass_().clean, false);
  assert.equal(ladder.cleanAtSpeed, 0);
  // And it only applies to the lap it was armed for.
  assert.equal(ladder.pass_().clean, true);
});

test('with auto-confirm off, only a confirmed lap counts', () => {
  const ladder = new Ladder({ cfg: CFG, autoConfirm: false });
  assert.equal(ladder.pass_().clean, false);
  ladder.confirm();
  assert.equal(ladder.pass_().clean, true);
  // A confirmation never carries over into the next lap.
  assert.equal(ladder.pass_().clean, false);
});

test('the advance happens on the pass, and fires once with both speeds', () => {
  const ladder = new Ladder({ cfg: CFG, speed: 55 });
  const events = [];
  ladder.addEventListener('advance', (e) => events.push(e.detail));
  ladder.pass_();
  ladder.pass_();
  assert.deepEqual(events, []);
  const third = ladder.pass_();
  assert.deepEqual(third.advanced, { from: 55, to: 60 });
  assert.equal(events.length, 1);
  assert.equal(events[0].to, 60);
  assert.equal(ladder.cleanAtSpeed, 0);
});

test('a rung is only ever advanced from pass_(), so never mid-loop', () => {
  // docs/03-audio-engine.md: "Speed changes at the boundary, never
  // mid-loop." Nothing else on this class can move `speed` upward.
  const ladder = new Ladder({ cfg: CFG });
  const before = ladder.speed;
  ladder.confirm();
  ladder.dirty();
  ladder.retract();
  assert.equal(ladder.speed, before);
});

test('a manual speed change resets progress toward the next rung', () => {
  // Otherwise two presses of speed_down and one clean rep would advance
  // you off a rung you never actually practised.
  const ladder = new Ladder({ cfg: CFG, speed: 60, cleanAtSpeed: 2 });
  ladder.setSpeed(50);
  assert.equal(ladder.cleanAtSpeed, 0);
  assert.equal(ladder.speed, 50);
});

test('retract undoes one rep of progress on the live ladder too', () => {
  const ladder = new Ladder({ cfg: CFG });
  ladder.pass_();
  ladder.pass_();
  assert.equal(ladder.cleanAtSpeed, 2);
  ladder.retract();
  assert.equal(ladder.cleanAtSpeed, 1);
  assert.equal(ladder.speed, 50);
});

test('hint names the next rung and how many more it wants', () => {
  assert.equal(new Ladder({ cfg: CFG, speed: 55 }).hint, '-> 60% after 3 more');
  assert.equal(new Ladder({ cfg: CFG, speed: 100 }).hint, '-> target after 3 more');
});

if (failures) process.exitCode = 1;
