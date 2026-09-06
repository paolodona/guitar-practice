// web/tests/test_seek.mjs — Phase 1.5, P1's test contract, run as a plain
// Node script (`node web/tests/test_seek.mjs`), not under a framework.
//
// This is a deliberate, narrow departure from this repo's established "no
// JS test framework" choice (see the plan's Group D report and CLAUDE.md's
// Testing section, which scope automated tests to the Python suite plus a
// human manual gate for the front end). P1 is flagged in the plan itself as
// one of the two highest-risk units in this phase ("the worklet
// seek/pass-qualification interaction"), and its own test contract asks for
// exactly what this file does — so the exception is made here, once,
// rather than adopting a framework wholesale for one unit.
//
// A synthetic (fake) worklet stands in for the real AudioWorkletNode: the
// thing actually at risk here — seek disqualifying the in-flight lap, and a
// later natural wrap re-qualifying the next one — lives entirely in
// player.js's RealtimeEngine bookkeeping (_qualified / _onBoundary), not in
// the WASM DSP the real worklet drives. Testing at this level exercises the
// real logic without needing a real AudioContext, a real AudioWorkletNode,
// or the WASM binary at all.
//
// EventTarget/CustomEvent are real globals in Node 18+ (this repo pins
// Python 3.12+ / uv; the machine this test ran on is Node 20), so
// player.js imports cleanly under plain `node` -- nothing at its module
// top level touches a browser-only API; those are all behind functions
// this test never calls (getWasmModule, ensureWorkletModule, loadSection).

import assert from 'node:assert/strict';
import { RealtimeEngine } from '../player.js';

function makeEngine() {
  // The constructor only needs a truthy audioContext to skip `new
  // AudioContext()` (browser-only); nothing else on it is ever touched by
  // the methods under test here.
  const engine = new RealtimeEngine({});
  // Bypass loadSection entirely -- rig the post-load state it would have
  // set, exactly as if a 10s slice (source seconds 10..20) had been
  // decoded at 48000 Hz.
  const sentMessages = [];
  engine._node = { port: { postMessage: (msg) => sentMessages.push(msg) } };
  engine._section = { sectionId: 'sec1' };
  engine._sampleRate = 48000;
  engine._sliceStartS = 10;
  engine._sliceEndS = 20;
  engine._qualified = true;
  return { engine, sentMessages };
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

test('seek disqualifies the in-flight lap', () => {
  const { engine } = makeEngine();
  assert.equal(engine._qualified, true);
  engine.seek(15);
  assert.equal(engine._qualified, false);
});

test('a lap that was seeked into never fires pass on its own wrap', () => {
  const { engine } = makeEngine();
  const passes = [];
  engine.addEventListener('pass', (e) => passes.push(e.detail));

  engine.seek(15); // mid-section -- disqualifies the current lap
  engine._onBoundary(); // the worklet reports the (seeked-into) lap's natural wrap

  assert.deepEqual(passes, [], 'the seeked-into lap must not count as a pass');
});

test('the wrap AFTER a seeked lap re-qualifies and fires pass, same as after a pause', () => {
  const { engine } = makeEngine();
  const passes = [];
  engine.addEventListener('pass', (e) => passes.push(e.detail));

  engine.seek(15);
  engine._onBoundary(); // disqualified lap ends -- no pass, but re-arms
  engine._onBoundary(); // a full natural lap ran after that -- this one counts

  assert.equal(passes.length, 1);
  assert.deepEqual(passes[0], { sectionId: 'sec1' });
});

test('seek posts the correct frame offset within the loaded slice', () => {
  const { engine, sentMessages } = makeEngine();
  engine.seek(15); // 5s into a slice starting at source-second 10, at 48000 Hz
  assert.deepEqual(sentMessages, [{ type: 'seek', frame: 5 * 48000 }]);
});

test('seeking before the loaded slice clamps to its start (frame 0), not throw', () => {
  const { engine, sentMessages } = makeEngine();
  engine.seek(3); // below sliceStartS (10)
  assert.deepEqual(sentMessages, [{ type: 'seek', frame: 0 }]);
});

test('seeking past the loaded slice clamps to its end, not throw', () => {
  const { engine, sentMessages } = makeEngine();
  engine.seek(999); // above sliceEndS (20)
  assert.deepEqual(sentMessages, [{ type: 'seek', frame: (20 - 10) * 48000 }]);
});

test('seek before loadSection (no node yet) throws, like every other transport method', () => {
  const engine = new RealtimeEngine({});
  assert.throws(() => engine.seek(1), /call loadSection\(\) first/);
});

test('pause still disqualifies (unchanged by adding seek)', () => {
  const { engine } = makeEngine();
  engine.pause();
  assert.equal(engine._qualified, false);
});
