// web/tests/test_loudness_gain.mjs — loudness-matching (plan 004): the
// per-song `loudnessGainDb` a SectionLoad carries (player.js's own typedef
// doc) has to land on the right GainNode at the right value in both
// engines, without breaking BufferEngine's existing crossfade math, which
// already owns the same `gain.gain` this reuses (see player.js's own note
// on why there is no separate master-gain node).
//
// Same house style as test_buffer_engine.mjs: a synthetic AudioContext, no
// framework, no real audio device — the risk here is arithmetic (does the
// right multiplier land on the right node), not DSP, and that is exactly
// what a fake context can drive precisely.

import assert from 'node:assert/strict';
import { BufferEngine, dbToLinear } from '../player.js';

function test(name, fn) {
  try {
    const out = fn();
    if (out instanceof Promise) {
      return out.then(
        () => console.log(`ok - ${name}`),
        (err) => { process.exitCode = 1; console.error(`FAIL - ${name}`); console.error(err); },
      );
    }
    console.log(`ok - ${name}`);
    return Promise.resolve();
  } catch (err) {
    process.exitCode = 1;
    console.error(`FAIL - ${name}`);
    console.error(err);
    return Promise.resolve();
  }
}

// ---- dbToLinear: pure ------------------------------------------------------

test('dbToLinear(0) is unity gain', () => {
  assert.equal(dbToLinear(0), 1);
});

test('dbToLinear(-6) roughly halves amplitude, +6 roughly doubles it', () => {
  assert.ok(Math.abs(dbToLinear(-6) - 0.501) < 0.01);
  assert.ok(Math.abs(dbToLinear(6) - 1.995) < 0.01);
});

test('dbToLinear is the inverse of 20*log10', () => {
  for (const db of [-24, -16, -1, 0, 3, 14]) {
    assert.ok(Math.abs(20 * Math.log10(dbToLinear(db)) - db) < 1e-9);
  }
});

// ---- a synthetic AudioContext, same shape test_buffer_engine.mjs uses -----

class FakeParam {
  constructor(value) { this.value = value; this.calls = []; }
  setValueAtTime(v, t) { this.calls.push(['setValueAtTime', v, t]); this.value = v; }
  setValueCurveAtTime(curve, t, d) { this.calls.push(['curve', Array.from(curve), t, d]); }
  cancelScheduledValues(t) { this.calls.push(['cancel', t]); }
}

class FakeGain {
  constructor() { this.gain = new FakeParam(1); this.outputs = []; }
  connect(node) { this.outputs.push(node); return node; }
  disconnect() { this.disconnected = true; }
}

class FakeSource {
  constructor(ctx) {
    this.ctx = ctx;
    this.buffer = null;
    this.loop = false;
    this.loopStart = 0;
    this.loopEnd = 0;
    this.started = null;
    this.stopped = null;
  }
  connect(node) { this.output = node; return node; }
  disconnect() { this.disconnected = true; }
  start(when, offset) { this.started = { when, offset }; }
  stop(when) { this.stopped = when === undefined ? this.ctx.currentTime : when; }
}

class FakeContext {
  constructor() {
    this.currentTime = 0;
    this.state = 'running';
    this.sampleRate = 48000;
    this.destination = { name: 'destination' };
    this.sources = [];
  }
  createBufferSource() { const s = new FakeSource(this); this.sources.push(s); return s; }
  createGain() { return new FakeGain(); }
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
  async decodeAudioData(bytes) {
    return { duration: 120, sampleRate: 48000, length: 120 * 48000, numberOfChannels: 2, tag: bytes.tag };
  }
}

function makeSection(extra = {}) {
  return {
    slug: 'snow', sectionId: 'solo', startS: 60, endS: 90, preRollS: 2, crossfadeMs: 10, ...extra,
  };
}

function fetchStub() {
  return async (url) => ({
    ok: true, status: 200, statusText: '200',
    async json() { return {}; },
    async arrayBuffer() { return { tag: url }; },
  });
}

async function mounted(opts = {}) {
  const ctx = new FakeContext();
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  globalThis.fetch = fetchStub();
  if (opts.speedPct !== undefined) engine.setSpeedPct(opts.speedPct);
  await engine.loadSection(makeSection(opts.section));
  return { ctx, engine };
}

// ---- BufferEngine._startAt: the steady-state gain -------------------------

await test('a loaded section with no loudnessGainDb plays at unity gain', async () => {
  const { ctx, engine } = await mounted();
  engine.play();
  assert.equal(ctx.sources[0].output.gain.value, 1);
});

await test('_startAt applies loudnessGainDb as a linear multiplier on the steady-state gain', async () => {
  const { ctx, engine } = await mounted({ section: { loudnessGainDb: 6 } });
  engine.play();
  assert.ok(Math.abs(ctx.sources[0].output.gain.value - dbToLinear(6)) < 1e-9);
});

await test('a negative loudnessGainDb (an already-loud song) attenuates below unity', async () => {
  const { ctx, engine } = await mounted({ section: { loudnessGainDb: -8 } });
  engine.play();
  assert.ok(ctx.sources[0].output.gain.value < 1);
  assert.ok(Math.abs(ctx.sources[0].output.gain.value - dbToLinear(-8)) < 1e-9);
});

// ---- BufferEngine._scheduleSwap: the crossfade curve, scaled --------------

await test('a rung swap scales BOTH crossfade curves by the same loudness gain, still equal-power', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50, section: { loudnessGainDb: 6 } });
  engine.play();
  const oldGain = engine._active.gain;
  ctx.currentTime = 10;
  await engine.setSpeedPct(55);
  const newGain = engine._pendingSwap.gain;

  const out = oldGain.gain.calls.find((c) => c[0] === 'curve');
  const inn = newGain.gain.calls.find((c) => c[0] === 'curve');
  assert.ok(out && inn, 'both sides ramp');

  const linear = dbToLinear(6);
  // Still equal-power once un-scaled: (out/g)^2 + (in/g)^2 == 1.
  for (let i = 0; i < out[1].length; i++) {
    const outUnscaled = out[1][i] / linear;
    const innUnscaled = inn[1][i] / linear;
    assert.ok(Math.abs(outUnscaled ** 2 + innUnscaled ** 2 - 1) < 1e-6);
  }
  // And it actually reaches the scaled peak, not unity.
  assert.ok(Math.abs(Math.max(...inn[1]) - linear) < 1e-6);
});

await test('cancelling a swap restores the outgoing node to the LOUDNESS gain, not bare unity', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50, section: { loudnessGainDb: -6 } });
  engine.play();
  const oldGain = engine._active.gain;
  ctx.currentTime = 10;
  await engine.setSpeedPct(55);
  engine._cancelPendingSwap();

  const restore = oldGain.gain.calls.findLast((c) => c[0] === 'setValueAtTime');
  assert.ok(restore, 'a restore call happened');
  assert.ok(Math.abs(restore[1] - dbToLinear(-6)) < 1e-9, 'restored to the loudness gain, not 1');
});

await test('with no loudnessGainDb (0dB), cancelling a swap still restores to exactly 1 (regression guard)', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  engine.play();
  const oldGain = engine._active.gain;
  ctx.currentTime = 10;
  await engine.setSpeedPct(55);
  engine._cancelPendingSwap();

  const restore = oldGain.gain.calls.findLast((c) => c[0] === 'setValueAtTime');
  assert.equal(restore[1], 1);
});
