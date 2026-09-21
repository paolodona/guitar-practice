// web/tests/test_hybrid_engine.mjs — Phase 1.5's test contract for
// HybridEngine (instant playback while the practice cache is cold; see
// docs/03-audio-engine.md and player.js's own HybridEngine class doc), run
// as a plain Node script (`node web/tests/test_hybrid_engine.mjs`), not
// under a framework — the same narrow, deliberate exception
// test_seek.mjs/test_buffer_engine.mjs already document at length: the risk
// here lives in orchestration bookkeeping (which engine is audible, when
// the swap fires, whether position carries across correctly), not in DSP.
//
// Two synthetic layers, reused/adapted from the existing suite rather than
// invented fresh:
//
//  - BufferEngine's side: the same FakeContext/FakeSource/FakeGain/
//    FakeParam trio test_buffer_engine.mjs uses, plus the queued/gated
//    fetch stubs test_engine_error_recovery.mjs already established for
//    pausing mid-render and inspecting state before "the server" answers.
//  - RealtimeEngine's side: `loadSection()` needs a real fetch/decode/
//    AudioWorklet/WASM pipeline this Node test cannot run (same reason
//    test_seek.mjs/test_ended.mjs bypass it entirely and rig the post-load
//    state directly). HybridEngine calls it for real, so it is stubbed
//    once, at the PROTOTYPE level, for this whole file — every other
//    RealtimeEngine method (setSpeedPct/setSemitones/seek/play/pause/
//    restartSection/_onBoundary/_onWorkletMessage) stays the REAL
//    implementation, exercised through a synthetic worklet node exactly as
//    test_seek.mjs/test_ended.mjs already do. This means the pass-detection
//    bookkeeping under test is the real thing, not a re-description of it.

import assert from 'node:assert/strict';
import {
  HybridEngine, BufferEngine, RealtimeEngine, createEngine, renderClock, renderUrl,
} from '../player.js';

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

// ---- stub RealtimeEngine.loadSection() for the whole file ---------------

RealtimeEngine.prototype.loadSection = async function loadSection(section) {
  this._section = section;
  this._sampleRate = 48000;
  this._sliceStartS = Math.max(0, section.startS - section.preRollS);
  this._sliceEndS = section.endS;
  this._sentMessages = [];
  this._node = {
    port: { postMessage: (msg) => this._sentMessages.push(msg), onmessage: null },
    onprocessorerror: null,
    disconnect() {},
  };
  this._qualified = true;
};

// Every seek() call across the whole file, for the one test that needs to
// see the exact source-seconds value HybridEngine hands off — patched at
// the prototype level for the same reason loadSection is (no clean seam to
// intercept a lazily-constructed instance before its own first call).
const seekCalls = [];
const realSeek = RealtimeEngine.prototype.seek;
RealtimeEngine.prototype.seek = function seek(s) {
  seekCalls.push(s);
  return realSeek.call(this, s);
};

// ---- a synthetic AudioContext: only what BufferEngine actually touches --
// (test_buffer_engine.mjs's trio, verbatim)

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

// A 30s section with a 2s lead-in: at 50% that is a 4s lead-in and a 60s
// section in playback seconds -- same fixture test_buffer_engine.mjs uses.
function makeSection(extra = {}) {
  return {
    slug: 'cant-stop', sectionId: 'solo', startS: 60, endS: 90, preRollS: 2, crossfadeMs: 10, ...extra,
  };
}
const url = (speedPct, semitones = 0) => renderUrl(makeSection(), speedPct, semitones);

function fetchStub(responses) {
  const seen = [];
  const fn = async (u) => {
    seen.push(u);
    const r = responses[Math.min(seen.length - 1, responses.length - 1)];
    return {
      ok: r.status === 200,
      status: r.status,
      statusText: String(r.status),
      async json() { return r.body ?? {}; },
      async arrayBuffer() { return { tag: u }; },
    };
  };
  fn.seen = seen;
  return fn;
}

const settle = async (rounds = 8) => {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 2));
};

function gatedFetch() {
  const waiting = new Map();
  const seen = [];
  const fn = (u) => {
    seen.push(u);
    return new Promise((resolve) => waiting.set(u, resolve));
  };
  fn.seen = seen;
  fn.release = async (u, status = 200) => {
    const resolve = waiting.get(u);
    assert.ok(resolve, `no request in flight for ${u}`);
    waiting.delete(u);
    resolve({
      ok: status === 200,
      status,
      statusText: String(status),
      async json() { return {}; },
      async arrayBuffer() { return { tag: u }; },
    });
    await settle();
  };
  return fn;
}

function makeHybrid({ fetch, speedPct } = {}) {
  const ctx = new FakeContext();
  globalThis.fetch = fetch;
  const engine = new HybridEngine(ctx);
  engine._buffer.pollMs = 1;
  if (speedPct !== undefined) engine.setSpeedPct(speedPct);
  return { ctx, engine };
}

// ---- 1. the common case: a cache hit costs nothing extra ----------------

await test('a cache hit stays on buffer and never creates a realtime engine', async () => {
  const fetch = fetchStub([{ status: 200 }]);
  const { engine } = makeHybrid({ fetch, speedPct: 50 });
  await engine.loadSection(makeSection());
  assert.equal(engine.kind, 'buffer');
  assert.equal(engine._realtime, null);
  assert.equal(typeof engine.position, 'function', 'an honest position is available');
});

// ---- 2/3. a cold cache: instant realtime, then a hard cut at the seam ---

await test('a cold cache plays instantly on realtime, then hard-cuts to buffer at the next pass', async () => {
  const fetch = gatedFetch();
  const { ctx, engine } = makeHybrid({ fetch, speedPct: 50 });
  const section = makeSection();
  const u = url(50);

  const loadPromise = engine.loadSection(section);
  await settle();
  assert.deepEqual(fetch.seen, [u]);

  await fetch.release(u, 202); // confirms a cache MISS -- before any poll delay
  assert.equal(engine.kind, 'realtime', 'dropped to realtime immediately, before the render is ready');
  assert.ok(engine._realtime, 'a realtime engine was created');
  assert.equal(typeof engine.position, 'undefined', 'no honest position while on realtime');

  await loadPromise; // loadSection() resolves once realtime is actually loaded

  const passes = [];
  engine.addEventListener('pass', (e) => passes.push(e.detail));
  engine.play();
  engine._realtime._onBoundary(); // realtime's own natural wrap
  assert.deepEqual(passes, [{ sectionId: 'solo' }]);
  assert.equal(engine.kind, 'realtime', 'still realtime -- the render has not landed yet');

  await fetch.release(u, 200); // the render lands
  assert.equal(engine._pendingBufferReady, true);
  assert.equal(engine.kind, 'realtime', 'not adopted mid-lap -- waits for the next natural pass');

  const clock = renderClock(section, 50);
  engine._realtime._onBoundary(); // the next natural wrap
  assert.equal(passes.length, 2, 'the lap that just finished on realtime still counts -- no missed rep');
  assert.equal(engine.kind, 'buffer', 'hard-cut to the sample-exact loop');
  assert.equal(engine._buffer._position, clock.loopStart, 'the new lap starts at loopStart, not sample 0');
});

// ---- 4. regression guard: an already-cached rung is unchanged -----------

await test('a press landing on an already-cached rung never touches realtime', async () => {
  const fetch = fetchStub([{ status: 200 }]); // every render request is a hit
  const { ctx, engine } = makeHybrid({ fetch, speedPct: 50 });
  await engine.loadSection(makeSection());
  engine.play();
  ctx.currentTime = 10;

  await engine.setSpeedPct(55);
  assert.equal(engine._realtime, null, 'never created -- the render was already cached');
  assert.ok(engine._buffer._pendingSwap, 'the familiar crossfaded swap is scheduled, unchanged');
  assert.equal(engine.kind, 'buffer');
});

// ---- 5. a mid-session press landing on an uncached rung ------------------

await test('a mid-session press on an uncached rung drops to realtime immediately, then swaps back at the NEW target', async () => {
  seekCalls.length = 0;
  const fetch = gatedFetch();
  const { ctx, engine } = makeHybrid({ fetch, speedPct: 50 });
  const section = makeSection();

  const loadPromise = engine.loadSection(section);
  await settle();
  await fetch.release(url(50), 200); // the initial load is a cache hit
  await loadPromise;
  assert.equal(engine.kind, 'buffer');

  engine.play();
  ctx.currentTime = 10; // 10 playback seconds in, at 50%

  const setPromise = engine.setSpeedPct(55);
  await settle();
  assert.deepEqual(fetch.seen.slice(-1), [url(55)]);

  await fetch.release(url(55), 202); // confirms a cache MISS for the NEW target
  await settle();
  assert.equal(engine.kind, 'realtime', 'dropped immediately -- mid-phrase, not deferred to the old rung\'s loop end');

  const oldClock = renderClock(section, 50);
  const expectedSourceSeconds = 10 * oldClock.speed + (section.startS - oldClock.preRollS);
  assert.equal(seekCalls[seekCalls.length - 1], expectedSourceSeconds, 'position carried across through source seconds');

  await fetch.release(url(55), 200); // the new render lands
  assert.equal(engine._pendingBufferReady, true);
  engine._realtime._onBoundary(); // next natural wrap on realtime
  assert.equal(engine.kind, 'buffer');
  assert.equal(engine._buffer.speedPct, 55, 'swapped back at the NEW target, not the old 50%');
  await setPromise;
});

// ---- 6. total failure: permanent realtime fallback for the section ------

await test('a total render failure (no rubberband at all) stays on realtime for the whole section', async () => {
  const fetch = fetchStub([{ status: 500 }]);
  const { engine } = makeHybrid({ fetch, speedPct: 50 });
  await engine.loadSection(makeSection());
  assert.equal(engine.kind, 'realtime');
  assert.equal(engine._pendingBufferReady, false);
  assert.equal(engine._bufferUsable, false);
});

// ---- 7. destroy() tears down both sub-engines, double-call safe ---------

await test('destroy tears down both sub-engines and is safe to call twice', async () => {
  const fetch = gatedFetch();
  const { engine } = makeHybrid({ fetch, speedPct: 50 });
  const section = makeSection();
  const loadPromise = engine.loadSection(section);
  await settle();
  await fetch.release(url(50), 202);
  await loadPromise;
  assert.equal(engine.kind, 'realtime');
  assert.ok(engine._realtime);

  engine.destroy();
  assert.equal(engine._buffer._destroyed, true);
  assert.equal(engine._realtime._destroyed, true);
  assert.doesNotThrow(() => engine.destroy());
});

// ---- createEngine picks HybridEngine for kind: 'hybrid' ------------------

await test('createEngine picks HybridEngine for kind: "hybrid"', () => {
  assert.ok(createEngine({}, { kind: 'hybrid' }) instanceof HybridEngine);
  assert.ok(createEngine({}, { kind: 'buffer' }) instanceof BufferEngine);
  assert.ok(createEngine({}) instanceof RealtimeEngine);
});

if (failures) process.exitCode = 1;
