// web/tests/test_capture_review.mjs — Phase 1.5, U3's test contract, run as
// a plain Node script (`node web/tests/test_capture_review.mjs`), same
// house style as test_seek.mjs/test_ended.mjs (see either file's header for
// why this repo makes a narrow exception to "no JS test framework" for this
// phase's highest-risk units).
//
// This repo has no DOM/testing library (no jsdom, nothing vendored under
// web/vendor/) to drive a real click through screens/capture.js's actual
// buttons, so — same trade test_seek.mjs/test_ended.mjs already made for
// RealtimeEngine's internal state machine — this drives the two PURE,
// DOM-independent pieces `mountSegmentReview`'s row cards are built on
// directly: `boundaryMoveOrder` (which side of a dragged boundary commits
// first) and `onceGuard` (U3's own "cannot add/discard the same segment
// twice" contract). The actual DOM wiring (buttons really disabling,
// really greying out) is exercised by the manual gate, not this file.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { boundaryMoveOrder, formatElapsed, onceGuard } from '../screens/capture.js';

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

test('boundaryMoveOrder: moving the boundary right updates the right entry first', () => {
  assert.equal(boundaryMoveOrder(150, 100), 'right-first');
});

test('boundaryMoveOrder: moving the boundary left updates the left entry first', () => {
  assert.equal(boundaryMoveOrder(50, 100), 'left-first');
});

test('boundaryMoveOrder: an unmoved boundary (new === old) still updates the right entry first '
     + '(a no-op drag must not throw, and "right-first" is a safe default: shrinking the left '
     + 'entry to its own unchanged end never overlaps anything)', () => {
  assert.equal(boundaryMoveOrder(100, 100), 'right-first');
});

test('onceGuard: the first run() call invokes fn and returns its result', () => {
  const guard = onceGuard();
  const result = guard.run(() => 'ran');
  assert.equal(result, 'ran');
});

test("onceGuard: a second run() call -- U3's own double-click case -- never invokes fn again", () => {
  const guard = onceGuard();
  let calls = 0;
  guard.run(() => { calls += 1; return 'first'; });
  const second = guard.run(() => { calls += 1; return 'second'; });
  assert.equal(calls, 1, 'fn must be called exactly once, not once per click');
  assert.equal(second, null, 'a run() call after the guard is used returns null, not a stale promise');
});

test('onceGuard: reset() un-latches it -- a definitive failure is retryable, not a permanent brick', () => {
  const guard = onceGuard();
  guard.run(() => 'first');
  guard.reset();
  let calls = 0;
  const result = guard.run(() => { calls += 1; return 'retried'; });
  assert.equal(calls, 1);
  assert.equal(result, 'retried');
});

test("onceGuard: two INDEPENDENT guards (two different rows) never interfere with each other", () => {
  const guardA = onceGuard();
  const guardB = onceGuard();
  guardA.run(() => 'a');
  const b = guardB.run(() => 'b');
  assert.equal(b, 'b', "row B's own guard must still be fresh after row A's guard was used");
});

test("U3's own test contract, enforced as a lint-style check (same pattern R1's test already "
     + "used for song.js): screens/capture.js never writes a rep or listens for 'pass' -- this "
     + 'screen only ever loads loop:false sections (the whole-pass scrub strip, one row\'s own '
     + 'preview), never a looped, counted one', () => {
  const captureJsPath = fileURLToPath(new URL('../screens/capture.js', import.meta.url));
  const src = readFileSync(captureJsPath, 'utf8');
  assert.ok(!src.includes('/api/rep'), 'capture.js must never POST /api/rep -- practice.js owns every rep');
  assert.ok(
    !/addEventListener\(\s*['"]pass['"]/.test(src),
    "capture.js must never addEventListener('pass', ...) -- it only ever loads loop:false sections"
  );
});

test('formatElapsed says "unknown" rather than NaN:NaN', () => {
  // Found live 2026-09-06 on the review screen. The duration was missing
  // for an unrelated reason (a stale server with no raw-audio route), but a
  // clock that renders "NaN:NaN" is its own bug whatever fed it.
  assert.equal(formatElapsed(41.2), '0:41');
  assert.equal(formatElapsed(125), '2:05');
  assert.equal(formatElapsed(0), '0:00');
  assert.equal(formatElapsed(-3), '0:00');
  assert.equal(formatElapsed(NaN), '--:--');
  assert.equal(formatElapsed(undefined), '--:--');
  assert.equal(formatElapsed(Infinity), '--:--');
});
