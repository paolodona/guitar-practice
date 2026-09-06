// web/tests/test_ended.mjs — Phase 1.5, R1's test contract, run as a plain
// Node script (`node web/tests/test_ended.mjs`), same house style as
// test_seek.mjs (see that file's header for why this repo makes a narrow
// exception to "no JS test framework" for this phase's two highest-risk
// units).
//
// R1's test contract: "a non-looping load that reaches the end fires
// 'ended', never 'pass'". The thing actually at risk lives entirely in
// RealtimeEngine._onWorkletMessage's dispatch table (worklet.js's own half
// — never posting 'boundary' for a loop:false section — is a DSP/streaming
// concern that would need real WASM to exercise, and is exactly the kind
// of thing this repo's manual gate, not an automated test, is the honest
// way to verify per CLAUDE.md's Testing section). So this test drives
// _onWorkletMessage directly with a synthetic 'ended' message, the same
// synthetic-worklet approach test_seek.mjs uses for 'boundary'.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { RealtimeEngine } from '../player.js';

function makeEngine() {
  const engine = new RealtimeEngine({});
  engine._node = { port: { postMessage: () => {} } };
  engine._section = { sectionId: 'sec1' };
  engine._qualified = true;
  return engine;
}

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

test("a non-looping section's 'ended' message fires 'ended', not 'pass'", () => {
  const engine = makeEngine();
  const ended = [];
  const passes = [];
  engine.addEventListener('ended', (e) => ended.push(e.detail));
  engine.addEventListener('pass', (e) => passes.push(e.detail));

  engine._onWorkletMessage({ type: 'ended' }, () => {});

  assert.deepEqual(ended, [{ sectionId: 'sec1' }]);
  assert.deepEqual(passes, [], "'ended' must never produce a 'pass' event");
});

test("'ended' does not touch _qualified (a separate concern from pass-qualification)", () => {
  const engine = makeEngine();
  engine._qualified = false; // e.g. this section had already been seeked into
  engine._onWorkletMessage({ type: 'ended' }, () => {});
  assert.equal(engine._qualified, false, '_onWorkletMessage must only ever change _qualified via _onBoundary');
});

test("'boundary' still reaches _onBoundary via the same dispatch table (unchanged by adding 'ended')", () => {
  const engine = makeEngine();
  const passes = [];
  engine.addEventListener('pass', (e) => passes.push(e.detail));

  engine._onWorkletMessage({ type: 'boundary', contextTime: 1.23 }, () => {});

  assert.deepEqual(passes, [{ sectionId: 'sec1' }]);
});

test("'ready' resolves loadSection's promise exactly once, via the callback, not an event", () => {
  const engine = makeEngine();
  let readyCalls = 0;
  engine._onWorkletMessage({ type: 'ready' }, () => { readyCalls += 1; });
  assert.equal(readyCalls, 1);
});

test("R1's own test contract, enforced as a lint-style check (same pattern as Phase 2's " +
     "planned playbackRate grep): screens/song.js never writes a rep or listens for 'pass'", () => {
  const songJsPath = fileURLToPath(new URL('../screens/song.js', import.meta.url));
  const src = readFileSync(songJsPath, 'utf8');
  assert.ok(!src.includes('/api/rep'), 'song.js must never POST /api/rep -- practice.js owns every rep');
  assert.ok(
    !/addEventListener\(\s*['"]pass['"]/.test(src),
    // Found live 2026-09-06: song.js's preview now loads `loop: true` for
    // a selected section (continuous audition, not R1's original one-shot)
    // -- it can fire 'boundary'/'pass' now, same as practice.js's engine
    // does. The invariant this test actually guards survives unchanged:
    // whatever it loads, this screen must never LISTEN for 'pass' or post
    // a rep. It just no longer follows from "only ever loop:false".
    "song.js must never addEventListener('pass', ...) -- it counts no reps, looped or not"
  );
});
