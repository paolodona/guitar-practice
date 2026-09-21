// web/tests/test_engine_error_recovery.mjs — the fix for the "impossible
// state" bug (FOUND LIVE 2026-09-12, Paolo, i-want-it-all): a section
// showed "rendered" (solid, not ghost) but pressing play produced no sound
// and no moving playhead, and at another speed the ghost/hollow number
// never resolved even though the status bar's wait had gone quiet.
//
// Two independent defects, one per section below:
//
//   1. `_changeRender`'s catch block latched `_targetSpeedPct`/
//      `_targetSemitones` before the render was known to be able to fail,
//      and never rolled them back on failure -- `pending` (the ghost) could
//      then never become false again for that press.
//   2. `BufferEngine.play()` called `ctx.resume()` without awaiting or
//      catching it. A browser that refuses the resume (a real risk any
//      time a render takes long enough to cost the click its user
//      activation) left `_playing` true and the scheduled source node
//      inaudible forever, with nothing anywhere to say why.
//
// Both now surface as an 'error' event (screens/practice.js's own
// `describeEngineError` turns that into the one line the status bar
// shows), checked here rather than only by reading player.js's source.

import assert from 'node:assert/strict';
import { BufferEngine, renderClock } from '../player.js';
import { describeEngineError, statusBarState } from '../screens/practice.js';

let failures = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(err);
  }
}

// ---- the synthetic AudioContext (test_buffer_engine.mjs's, with a
// resume() the test can fail on demand) ----

class FakeParam {
  constructor(value) { this.value = value; }
  setValueAtTime(v) { this.value = v; }
  setValueCurveAtTime() {}
  cancelScheduledValues() {}
}

class FakeGain {
  constructor() { this.gain = new FakeParam(1); }
  connect(node) { return node; }
  disconnect() { this.disconnected = true; }
}

class FakeSource {
  constructor(ctx) { this.ctx = ctx; this.started = null; this.stopped = null; }
  connect(node) { return node; }
  disconnect() { this.disconnected = true; }
  start(when, offset) { this.started = { when, offset }; }
  stop(when) { this.stopped = when === undefined ? this.ctx.currentTime : when; }
}

class FakeContext {
  constructor({ initialState = 'running' } = {}) {
    this.currentTime = 0;
    this.state = initialState;
    this.sampleRate = 48000;
    this.destination = { name: 'destination' };
    this.sources = [];
    this.resumeBehavior = 'run'; // 'run' | 'stall' | 'reject'
  }
  createBufferSource() { const s = new FakeSource(this); this.sources.push(s); return s; }
  createGain() { return new FakeGain(); }
  async resume() {
    if (this.resumeBehavior === 'reject') throw new Error('NotAllowedError: resume() was blocked');
    if (this.resumeBehavior === 'run') this.state = 'running';
    // 'stall': resolves but leaves state 'suspended', as a browser that
    // silently refused the resume (no user gesture) actually does.
  }
  async close() { this.state = 'closed'; }
  async decodeAudioData(bytes) {
    return { duration: 120, sampleRate: 48000, length: 120 * 48000, numberOfChannels: 2, tag: bytes.tag };
  }
}

function makeSection(extra = {}) {
  return { slug: 'i-want-it-all', sectionId: 'solo', startS: 60, endS: 90, preRollS: 2, crossfadeMs: 10, ...extra };
}

const urlFor = (speed, semitones = 0) =>
  `/api/render/i-want-it-all/solo?speed=${speed}&semitones=${semitones}&source=mix`;

const settle = async (rounds = 6) => {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 2));
};

function gatedFetch() {
  const waiting = new Map();
  const seen = [];
  const fn = (url) => {
    seen.push(url);
    return new Promise((resolve) => waiting.set(url, resolve));
  };
  fn.seen = seen;
  fn.release = async (url, status = 200) => {
    const resolve = waiting.get(url);
    assert.ok(resolve, `no request in flight for ${url}`);
    waiting.delete(url);
    resolve({
      ok: status === 200,
      status,
      statusText: String(status),
      async json() { return {}; },
      async arrayBuffer() { return { tag: url }; },
    });
    await settle();
  };
  return fn;
}

// ---- 1. a render that fails must not leave the ghost stuck forever ------

await test('a failed render rolls the target back to what is actually playing', async () => {
  const ctx = new FakeContext();
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  const gate = gatedFetch();
  globalThis.fetch = gate;
  engine.setSpeedPct(65);
  const loading = engine.loadSection(makeSection());
  await settle(2);
  await gate.release(urlFor(65));
  await loading;
  engine.play();

  const errors = [];
  engine.addEventListener('error', (e) => errors.push(e.detail.error));

  engine.setSpeedPct(40);
  await settle(2);
  assert.equal(engine.targetSpeedPct, 40, 'the press should be latched while the render builds');
  assert.equal(engine.pending, true, 'a ghost is expected while the build is in flight');

  await gate.release(urlFor(40), 500); // the render dies server-side

  assert.equal(errors.length, 1, 'a failed render must be reported');
  // The whole bug: without the fix, these next two stayed at 40 forever —
  // 'pending' could never become false again for a rung nothing will ever
  // build, and the screen showed a hollow speed number with no explanation
  // once the status bar's own wait went quiet (ended by the failure).
  assert.equal(engine.targetSpeedPct, 65, 'the target must roll back to what is actually audible');
  assert.equal(engine.pending, false, 'the ghost must clear rather than latch on an unfulfillable rung');
  assert.equal(engine.speedPct, 65, 'still playing what was loaded before the failed press');
});

// ---- 2. a blocked resume must not leave "playing" showing over silence --

await test('play() rolls itself back when the browser leaves the context stuck suspended', async () => {
  const ctx = new FakeContext({ initialState: 'suspended' });
  ctx.resumeBehavior = 'stall'; // resolves, but never actually reaches 'running'
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  globalThis.fetch = async () => ({
    ok: true, status: 200,
    async arrayBuffer() { return { tag: 'x' }; },
  });
  await engine.loadSection(makeSection());

  const errors = [];
  engine.addEventListener('error', (e) => errors.push(e.detail.error));

  engine.play();
  // Scheduling itself is synchronous and optimistic, same as a real browser
  // mid-resume: a node is scheduled and `playing` is true before the
  // resume's promise has even settled.
  assert.equal(engine.playing, true);
  assert.equal(ctx.sources.length, 1);
  assert.equal(ctx.sources[0].stopped, null);

  await settle(3);

  assert.equal(errors.length, 1, 'a blocked resume must be reported, not silently swallowed');
  assert.equal(engine.playing, false, 'must not keep claiming to play over a suspended context');
  assert.ok(ctx.sources[0].stopped != null, 'the node scheduled against a dead clock must be torn down');
});

await test('play() rolls itself back when resume() rejects outright', async () => {
  const ctx = new FakeContext({ initialState: 'suspended' });
  ctx.resumeBehavior = 'reject';
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  globalThis.fetch = async () => ({
    ok: true, status: 200,
    async arrayBuffer() { return { tag: 'x' }; },
  });
  await engine.loadSection(makeSection());

  const errors = [];
  engine.addEventListener('error', (e) => errors.push(e.detail.error));

  engine.play();
  await settle(3);

  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /NotAllowedError/);
  assert.equal(engine.playing, false);
});

await test('play() succeeds quietly when the resume actually lands', async () => {
  const ctx = new FakeContext({ initialState: 'suspended' });
  ctx.resumeBehavior = 'run';
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  globalThis.fetch = async () => ({
    ok: true, status: 200,
    async arrayBuffer() { return { tag: 'x' }; },
  });
  await engine.loadSection(makeSection());

  const errors = [];
  engine.addEventListener('error', (e) => errors.push(e.detail.error));

  engine.play();
  await settle(3);

  assert.equal(errors.length, 0);
  assert.equal(engine.playing, true);
  assert.equal(ctx.sources[0].stopped, null);
});

// ---- 3. describeEngineError: one line per distinct failure ---------------

await test('describeEngineError tells a blocked resume, a worklet crash and a failed render apart', () => {
  assert.equal(
    describeEngineError(new Error('BufferEngine.play: the browser blocked audio (AudioContext stuck suspended)')),
    'playback was blocked by the browser — press play again',
  );
  assert.equal(
    describeEngineError(new Error('RealtimeEngine: AudioWorkletProcessor crashed')),
    'the audio engine crashed — reload to keep practising',
  );
  assert.equal(
    describeEngineError(new Error('BufferEngine: fetch /api/render/x/y?speed=40: 500 500')),
    'that render failed — still practising the last speed that worked',
  );
  assert.equal(
    describeEngineError(undefined),
    'that render failed — still practising the last speed that worked',
    'no error object at all should still say something rather than throw',
  );
});

await test('an engine error is shown in the status bar, ranked under an active render wait', () => {
  const QUIET = { renderStage: null, detail: '', guitarBusy: false, demucsAvailable: true, engineKind: 'buffer' };
  const withError = statusBarState({ ...QUIET, engineError: 'that render failed — still practising the last speed that worked' });
  assert.equal(withError.text, 'that render failed — still practising the last speed that worked');
  assert.equal(withError.showProgress, false, 'a settled error is not a progress bar');

  const outranked = statusBarState({
    ...QUIET, renderStage: 'rendering', detail: '', engineError: 'stale complaint about a superseded press',
  });
  assert.equal(outranked.text, 'Rendering…', 'a fresh render in flight outranks a stale error');
});

process.exitCode = failures ? 1 : 0;
