// web/tests/test_gx100.mjs — #3's test contract (Group N2), run as a plain
// Node script (`node web/tests/test_gx100.mjs`), same house style as
// test_zoom_pan.mjs: pure arithmetic tested directly, DOM/Web-MIDI wiring
// checked as lint-style source assertions (no browser, no MIDI device here).
//
// memoryToIndex/indexToMemory mirror src/woodshed/gx100.py's own functions
// (this file cannot import a Python module -- the same reason timeline.js
// mirrors clock.py) -- tested here against the SAME hardware-verified table
// docs/05-foot-control.md and tests/test_gx100.py both assert, so a drift
// between the two sides shows up as a failure on whichever side has the
// stale number, not a silent disagreement only discovered at the pedal.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  MAX_PC, DEFAULT_MEMORY, memoryToIndex, indexToMemory, resolvePatchAt,
} from '../gx100.js';

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

// ---- memoryToIndex / indexToMemory ----------------------------------------

const TABLE = [['U01-1', 0], ['U01-2', 1], ['U01-4', 3], ['U02-1', 4], ['U32-4', 127]];

test('memoryToIndex matches the hardware-verified table', () => {
  for (const [memory, index] of TABLE) assert.equal(memoryToIndex(memory), index);
});

test('memoryToIndex is case- and whitespace-tolerant, matching the pedal\'s own printed labels', () => {
  assert.equal(memoryToIndex('u01-1'), 0);
  assert.equal(memoryToIndex('  U01-1  '), 0);
});

test('indexToMemory is the exact inverse, across the whole reachable range', () => {
  for (let index = 0; index <= MAX_PC; index++) {
    assert.equal(memoryToIndex(indexToMemory(index)), index);
  }
});

test('memoryToIndex throws for anything a bare Program Change cannot reach', () => {
  for (const bad of ['U33-1', 'U50-4', 'P01-1', 'not-a-memory', 'U01-5', 'U00-1', '']) {
    assert.throws(() => memoryToIndex(bad), /reach|GX-100 memory/);
  }
});

test('indexToMemory throws outside 0-127', () => {
  for (const bad of [-1, 128, 300]) assert.throws(() => indexToMemory(bad));
});

// ---- resolvePatchAt --------------------------------------------------------

test('resolvePatchAt is the last entry with at_s <= target', () => {
  const changes = [{ at_s: 0, patch: 'U01-1' }, { at_s: 88, patch: 'U02-3' }];
  assert.equal(resolvePatchAt(changes, 0), 'U01-1');
  assert.equal(resolvePatchAt(changes, 50), 'U01-1');
  assert.equal(resolvePatchAt(changes, 88), 'U02-3');
  assert.equal(resolvePatchAt(changes, 200), 'U02-3');
});

test('resolvePatchAt falls back to DEFAULT_MEMORY when empty or nothing qualifies', () => {
  assert.equal(resolvePatchAt([], 10), DEFAULT_MEMORY);
  assert.equal(resolvePatchAt([{ at_s: 10, patch: 'U02-1' }], 5), DEFAULT_MEMORY);
});

test('resolvePatchAt does not assume the list is pre-sorted', () => {
  const changes = [{ at_s: 88, patch: 'U02-3' }, { at_s: 0, patch: 'U01-1' }];
  assert.equal(resolvePatchAt(changes, 50), 'U01-1');
});

// ---- sending: lint-style, no Web MIDI device to actually drive here -------

const gx100JsPath = fileURLToPath(new URL('../gx100.js', import.meta.url));
const src = readFileSync(gx100JsPath, 'utf8');

test('never constructs or sends a Bank Select message -- the pair that wedged the physical unit', () => {
  assert.ok(!/0xB0|0xb0/.test(src), 'no Control Change status byte should appear in this file at all');
  assert.ok(!/CC ?#? ?0\b/i.test(src) || /never/i.test(src), 'no code path should construct CC#0/CC#32');
});

test('sendProgramChange is gated on config.gx100.send_program_changes', () => {
  const match = src.match(/export async function sendProgramChange\([\s\S]*?\n\}/);
  assert.ok(match, 'sendProgramChange not found in gx100.js -- has it been renamed?');
  assert.ok(/send_program_changes/.test(match[0]), 'the toggle must be checked inside the one function that actually sends');
});

test('sendProgramChange range-checks the memory before sending -- never clamps or wraps', () => {
  const match = src.match(/export async function sendProgramChange\([\s\S]*?\n\}/);
  assert.ok(match[0].includes('memoryToIndex('), 'sendProgramChange must resolve through memoryToIndex, which refuses an unreachable memory');
});

// ---- wiring into both load paths (#3's own ask: auto-apply on section AND
// song load) -- lint-style, same reason as above: no DOM to actually drive
// a section load through here ---------------------------------------------

const songJsPath = fileURLToPath(new URL('../screens/song.js', import.meta.url));
const songSrc = readFileSync(songJsPath, 'utf8');
const practiceJsPath = fileURLToPath(new URL('../screens/practice.js', import.meta.url));
const practiceSrc = readFileSync(practiceJsPath, 'utf8');

test('song.js\'s preview load resolves and sends the applicable patch', () => {
  assert.ok(/import\s*\{[^}]*\bsendProgramChange\b[^}]*\}\s*from\s*['"]\.\.\/gx100\.js['"]/.test(songSrc));
  const match = songSrc.match(/async function playPreview\(\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'playPreview not found in song.js -- has it been renamed?');
  assert.ok(match[0].includes('sendProgramChange(resolvePatchAt('), 'playPreview must resolve then send the patch for the section it just loaded');
});

test('practice.js\'s section load resolves and sends the applicable patch', () => {
  assert.ok(/import\s*\{[^}]*\bsendProgramChange\b[^}]*\}\s*from\s*['"]\.\.\/gx100\.js['"]/.test(practiceSrc));
  assert.ok(practiceSrc.includes('sendProgramChange(resolvePatchAt('), 'ensureEngine must resolve then send the patch for the section being practised');
});

test('the patch-change lane commits through POST /api/patch-change, not a second write path', () => {
  assert.ok(songSrc.includes("post('/api/patch-change'"), 'song.js should commit lane edits through the one server endpoint for it');
});

test('the document-level "click elsewhere closes the popup" listener is removed on unmount', () => {
  // FOUND BY REVIEW: every mount() of the song screen adds a fresh
  // document.addEventListener('pointerdown', closePatchPopup) -- leaving
  // it unremoved accumulates one listener per visit to a song page,
  // each one still live and calling into a torn-down screen's closure.
  assert.ok(
    songSrc.includes("document.addEventListener('pointerdown', closePatchPopup)"),
    'the listener itself should still be wired -- this test only guards its cleanup',
  );
  const match = songSrc.match(/return function unmount\(\) \{[\s\S]*?\n  \};/);
  assert.ok(match, 'unmount not found in song.js -- has it been restructured?');
  assert.ok(
    match[0].includes("document.removeEventListener('pointerdown', closePatchPopup)"),
    'unmount must remove the document-level popup-close listener',
  );
});
