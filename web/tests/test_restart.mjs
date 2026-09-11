// web/tests/test_restart.mjs — restart_section's own test contract, run as
// a plain Node script (`node web/tests/test_restart.mjs`), same fake-worklet
// style as test_seek.mjs (see that file's own doc for why a synthetic node
// stands in for the real AudioWorkletNode here).
//
// FOUND LIVE 2026-09-10: RealtimeEngine.restartSection() used to send
// {type: 'restart'} with no playing state, and the worklet's handler
// hardcoded `this.playing = true` -- so pressing restart while paused made
// the room audibly play anyway, even though this engine keeps no playing
// flag of its own to have gotten that right from. Fixed by having the
// caller (screens/practice.js, the one place that already tracks it) pass
// its own `playing` explicitly. This file pins the message RealtimeEngine
// actually sends; the worklet's own handling of `msg.playing` lives in
// AudioWorkletGlobalScope and is out of reach here for the same reason
// test_seek.mjs's own doc gives for not testing the WASM DSP directly.

import assert from 'node:assert/strict';
import { RealtimeEngine } from '../player.js';

function makeEngine() {
  const engine = new RealtimeEngine({});
  const sentMessages = [];
  engine._node = { port: { postMessage: (msg) => sentMessages.push(msg) } };
  engine._section = { sectionId: 'sec1' };
  engine._sampleRate = 48000;
  engine._sliceStartS = 10;
  engine._sliceEndS = 20;
  engine._qualified = false;
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

test('restartSection(true) tells the worklet to keep playing', () => {
  const { engine, sentMessages } = makeEngine();
  engine.restartSection(true);
  assert.deepEqual(sentMessages, [{ type: 'restart', playing: true }]);
});

test('restartSection(false) tells the worklet to stay paused', () => {
  const { engine, sentMessages } = makeEngine();
  engine.restartSection(false);
  assert.deepEqual(sentMessages, [{ type: 'restart', playing: false }]);
});

test('restartSection() with no argument defaults to playing -- pre-existing callers keep working', () => {
  const { engine, sentMessages } = makeEngine();
  engine.restartSection();
  assert.deepEqual(sentMessages, [{ type: 'restart', playing: true }]);
});

test('restartSection re-qualifies the pass-detection window regardless of playing state', () => {
  const { engine } = makeEngine();
  engine.restartSection(false);
  assert.equal(engine._qualified, true);
});

test('restartSection before loadSection (no node yet) throws, like every other transport method', () => {
  const engine = new RealtimeEngine({});
  assert.throws(() => engine.restartSection(true), /call loadSection\(\) first/);
});
