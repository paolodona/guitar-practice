// web/tests/test_practice_seek.mjs — Phase 1.5, P2's test contract, run as
// a plain Node script (`node web/tests/test_practice_seek.mjs`), same house
// style as test_seek.mjs/test_ended.mjs/test_capture_review.mjs.
//
// P2's contract: "clicking mid-waveform moves the playhead to the clicked
// fraction and does not itself count a rep." This repo has no DOM library
// to drive a real pointerdown event through screens/practice.js's actual
// waveHost with (same trade the other files above already made), so this
// drives `computeSeekPosition` — the pure pixel/view -> source-seconds/
// fraction math `seekToClientX` is built on — directly, and separately
// asserts (as a lint-style check against `seekToClientX`'s own extracted
// source, since practice.js LEGITIMATELY posts /api/rep elsewhere, in
// onPass, so a whole-file check like R1's/U3's would be the wrong shape
// here) that it never counts a rep.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { computeSeekPosition } from '../screens/practice.js';

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

const VIEW = { startS: 10, endS: 20, widthPx: 200 };
const RECT = { left: 50 };

test('a click at the container\'s own left edge maps to the view\'s start, fraction 0', () => {
  const { sourceS, frac } = computeSeekPosition(50, RECT, VIEW);
  assert.equal(sourceS, 10);
  assert.equal(frac, 0);
});

test('a click at the container\'s own right edge maps to the view\'s end, fraction 1', () => {
  const { sourceS, frac } = computeSeekPosition(250, RECT, VIEW);
  assert.equal(sourceS, 20);
  assert.equal(frac, 1);
});

test('a click at the midpoint maps to the midpoint, fraction 0.5', () => {
  const { sourceS, frac } = computeSeekPosition(150, RECT, VIEW);
  assert.equal(sourceS, 15);
  assert.equal(frac, 0.5);
});

test('a click before the container clamps to the view\'s start, not a negative fraction', () => {
  const { sourceS, frac } = computeSeekPosition(0, RECT, VIEW);
  assert.equal(sourceS, 10);
  assert.equal(frac, 0);
});

test('a click past the container clamps to the view\'s end, not a fraction above 1', () => {
  const { sourceS, frac } = computeSeekPosition(9999, RECT, VIEW);
  assert.equal(sourceS, 20);
  assert.equal(frac, 1);
});

// Found live 2026-09-06, Paolo: a click landed BEFORE where he clicked --
// root cause was practice.js's own `root` being CSS-scaled (`transform:
// scale(s)`) to fit the window. clientX/rect.left/rect.width are viewport
// pixels (scale-aware, via getBoundingClientRect()); view.widthPx is
// waveHost.clientWidth, a LAYOUT size the transform never touches. These
// two tests pin the fix: rect.width rescales the viewport offset into
// view.widthPx's own units before it reaches positionAt().

test('a scaled-down container (on-screen half the view\'s layout width, e.g. '
     + 'the practice screen\'s stage at scale(0.5)) still maps its own on-screen '
     + 'midpoint to the view\'s midpoint, not a quarter of the way in -- the exact '
     + '"lands before the click" symptom this fix closes, pinned against a real '
     + 'scale factor rather than the RECT/VIEW fixture\'s coincidental 1:1 default', () => {
  // view.widthPx is 200 layout px; this container only occupies 100 SCREEN
  // px on screen (rect.width) -- its on-screen midpoint is rect.left + 50.
  const scaledRect = { left: 50, width: 100 };
  const { sourceS, frac } = computeSeekPosition(100, scaledRect, VIEW);
  assert.equal(sourceS, 15);
  assert.equal(frac, 0.5);
});

test('the SAME scaled container\'s on-screen right edge still maps to the view\'s '
     + 'end, not somewhere past it', () => {
  const scaledRect = { left: 50, width: 100 };
  const { sourceS, frac } = computeSeekPosition(150, scaledRect, VIEW);
  assert.equal(sourceS, 20);
  assert.equal(frac, 1);
});

test("P2's own test contract, enforced as a lint-style check against seekToClientX's own "
     + 'extracted source (practice.js legitimately posts /api/rep elsewhere, in onPass, so '
     + "a whole-file check would be the wrong shape here): a waveform click never counts a "
     + 'rep — seekToClientX touches neither repCount nor /api/rep nor onPass', () => {
  const practiceJsPath = fileURLToPath(new URL('../screens/practice.js', import.meta.url));
  const src = readFileSync(practiceJsPath, 'utf8');
  const match = src.match(/function seekToClientX\(clientX\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'seekToClientX not found in practice.js -- has it been renamed?');
  const body = match[0];
  assert.ok(!body.includes('repCount'), 'seekToClientX must never touch repCount');
  assert.ok(!body.includes('/api/rep'), 'seekToClientX must never POST /api/rep -- onPass owns every rep');
  assert.ok(!body.includes('onPass'), 'seekToClientX must never call onPass directly');
});
