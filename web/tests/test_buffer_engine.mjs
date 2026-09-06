// web/tests/test_buffer_engine.mjs — Group J's test contract (J1 the buffer
// engine, J2 the boundary swap, J3 pass detection re-based onto buffer
// arithmetic), run as a plain Node script (`node web/tests/test_buffer_engine.mjs`),
// not under a framework — the same narrow, deliberate exception
// test_seek.mjs already documents at length for the same reason: the risk
// in Group J lives in bookkeeping (which seam, at which AudioContext time,
// with which buffer) rather than in the DSP, and that bookkeeping can be
// driven exactly, without a browser, by a synthetic AudioContext.
//
// This matters more here than anywhere else in web/: this repo's Phase 2
// gate is a LISTENING test ("loop a real solo at 55% and listen for a tick
// at the seam") that no unattended run can perform. Everything below is
// the arithmetic that gate depends on, checked where it can be checked.

import assert from 'node:assert/strict';
import {
  BufferEngine,
  createEngine,
  RealtimeEngine,
  equalPowerCurve,
  renderClock,
  renderUrl,
  seamTimeAt,
} from '../player.js';

let failures = 0;
function test(name, fn) {
  try {
    const out = fn();
    if (out instanceof Promise) {
      return out.then(
        () => console.log(`ok - ${name}`),
        (err) => { failures += 1; console.error(`FAIL - ${name}`); console.error(err); },
      );
    }
    console.log(`ok - ${name}`);
    return Promise.resolve();
  } catch (err) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(err);
    return Promise.resolve();
  }
}

// ---- a synthetic AudioContext: only what BufferEngine actually touches ----

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
// section in playback seconds.
function makeSection(extra = {}) {
  return {
    slug: 'cant-stop',
    sectionId: 'solo',
    startS: 60,
    endS: 90,
    preRollS: 2,
    crossfadeMs: 10,
    ...extra,
  };
}

function fetchStub(responses) {
  // `responses` is a list of {status} consumed in order; the last one repeats.
  const seen = [];
  const fn = async (url) => {
    seen.push(url);
    const r = responses[Math.min(seen.length - 1, responses.length - 1)];
    return {
      ok: r.status === 200,
      status: r.status,
      statusText: String(r.status),
      async json() { return r.body ?? {}; },
      async arrayBuffer() { return { tag: url }; },
    };
  };
  fn.seen = seen;
  return fn;
}

async function mounted(opts = {}) {
  const ctx = new FakeContext();
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  globalThis.fetch = opts.fetch ?? fetchStub([{ status: 200 }]);
  if (opts.speedPct !== undefined) engine.setSpeedPct(opts.speedPct);
  await engine.loadSection(makeSection(opts.section));
  return { ctx, engine };
}

// ---- renderClock: the client-side mirror of clock.Render ----------------

await test('renderClock mirrors clock.Render at 50%', () => {
  const c = renderClock(makeSection(), 50);
  assert.equal(c.speed, 0.5);
  assert.equal(c.loopStart, 4); // pre_roll_s / speed
  assert.equal(c.total, 64); // (2 + 30) / 0.5
  assert.ok(Math.abs(c.loopEnd - (64 - 0.01)) < 1e-9);
  assert.ok(Math.abs(c.lap - 59.99) < 1e-9);
});

await test('renderClock puts loopStart at 0 but keeps loopEnd at the section end when the lead-in replays', () => {
  // The exact contract tests/test_clock.py names: where a lap BEGINS says
  // nothing about where the section ENDS. A loopEnd derived from loopStart
  // would silently truncate the last 4s of every pass here.
  const c = renderClock(makeSection({ preRollEveryPass: true }), 50);
  const once = renderClock(makeSection(), 50);
  assert.equal(c.loopStart, 0);
  assert.ok(Math.abs(c.loopEnd - once.loopEnd) < 1e-9);
  assert.ok(Math.abs(c.lap - (once.lap + 4)) < 1e-9);
});

await test('renderUrl names the render endpoint with speed, semitones and source', () => {
  const url = renderUrl(makeSection(), 55, -1);
  assert.equal(url, '/api/render/cant-stop/solo?speed=55&semitones=-1&source=mix');
  assert.equal(
    renderUrl(makeSection({ source: 'guitar' }), 100, 0),
    '/api/render/cant-stop/solo?speed=100&semitones=0&source=guitar',
  );
});

// ---- J1: the buffer engine ---------------------------------------------

await test('loadSection fetches the render for the current speed and decodes it', async () => {
  const fetch = fetchStub([{ status: 200 }]);
  const { engine } = await mounted({ fetch, speedPct: 55 });
  assert.deepEqual(fetch.seen, ['/api/render/cant-stop/solo?speed=55&semitones=0&source=mix']);
  assert.equal(engine._buffer.tag, fetch.seen[0]);
});

await test('a 202 means the render is still being built, so keep asking', async () => {
  const fetch = fetchStub([{ status: 202 }, { status: 202 }, { status: 200 }]);
  const { engine } = await mounted({ fetch });
  assert.equal(fetch.seen.length, 3);
  assert.ok(engine._buffer);
});

await test('play loops natively from the end of the pre-roll to the section end', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  engine.play();
  const source = ctx.sources[0];
  assert.equal(source.loop, true);
  assert.equal(source.loopStart, 4);
  assert.ok(Math.abs(source.loopEnd - 63.99) < 1e-9);
  // The first pass plays from sample 0 and includes the lead-in.
  assert.equal(source.started.offset, 0);
  assert.equal(source.started.when, ctx.currentTime);
  assert.equal(source.playbackRate, undefined); // never the wrong knob
});

// ---- J3: pass detection from buffer-boundary arithmetic ----------------

await test('the nth pass fires at startTime + loopEnd + n*lap, computed not sampled', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const passes = [];
  engine.addEventListener('pass', (e) => passes.push(e.detail.sectionId));
  engine.play();

  const clock = renderClock(makeSection(), 50);
  // The first lap starts at sample 0 (lead-in included), so its seam is a
  // whole loopEnd away, not a lap away.
  ctx.currentTime = clock.loopEnd - 0.001;
  engine._tick();
  assert.deepEqual(passes, []);

  ctx.currentTime = clock.loopEnd;
  engine._tick();
  assert.deepEqual(passes, ['solo']);

  // Two more laps' worth of time in one tick counts two more passes: the
  // seam is arithmetic, so a late tick can never lose one.
  ctx.currentTime = clock.loopEnd + 2 * clock.lap;
  engine._tick();
  assert.equal(passes.length, 3);
});

await test('seamTimeAt is the same arithmetic, exposed for the caller', () => {
  const clock = renderClock(makeSection(), 50);
  const anchor = { startTime: 100, offset: 0, clock };
  assert.ok(Math.abs(seamTimeAt(anchor, 0) - (100 + clock.loopEnd)) < 1e-9);
  assert.ok(Math.abs(seamTimeAt(anchor, 2) - (100 + clock.loopEnd + 2 * clock.lap)) < 1e-9);
  // Resuming mid-lap: the first seam is only the REMAINDER away.
  const resumed = { startTime: 100, offset: clock.loopEnd - 5, clock };
  assert.ok(Math.abs(seamTimeAt(resumed, 0) - 105) < 1e-9);
});

await test('pause disqualifies the lap in flight and a fresh wrap re-qualifies the next', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const passes = [];
  engine.addEventListener('pass', () => passes.push(1));
  engine.play();
  const clock = renderClock(makeSection(), 50);

  ctx.currentTime = 10;
  engine.pause();
  ctx.currentTime = 11;
  engine.play();
  // The lap that resumes here started mid-section, so its seam is not a pass.
  ctx.currentTime = 11 + (clock.loopEnd - 10);
  engine._tick();
  assert.deepEqual(passes, []);
  // The next wrap begins at loopStart by construction, so it counts.
  ctx.currentTime += clock.lap;
  engine._tick();
  assert.equal(passes.length, 1);
});

await test('seek disqualifies the lap in flight, like pause', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const passes = [];
  engine.addEventListener('pass', () => passes.push(1));
  engine.play();
  engine.seek(70); // source seconds -> playback (70 - 58) / 0.5 = 24
  const source = ctx.sources[ctx.sources.length - 1];
  assert.ok(Math.abs(source.started.offset - 24) < 1e-9);
  const clock = renderClock(makeSection(), 50);
  ctx.currentTime = clock.loopEnd - 24;
  engine._tick();
  assert.deepEqual(passes, []);
});

await test('restartSection re-arms the pass window from the top', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const passes = [];
  engine.addEventListener('pass', () => passes.push(1));
  engine.play();
  engine.seek(70);
  ctx.currentTime = 5;
  engine.restartSection();
  const clock = renderClock(makeSection(), 50);
  assert.equal(ctx.sources[ctx.sources.length - 1].started.offset, 0);
  ctx.currentTime = 5 + clock.loopEnd;
  engine._tick();
  assert.deepEqual(passes, [1]);
});

// ---- J2: the boundary swap ---------------------------------------------

await test('a speed change while playing starts the new buffer at the exact seam', async () => {
  const fetch = fetchStub([{ status: 200 }]);
  const { ctx, engine } = await mounted({ fetch, speedPct: 50 });
  engine.play();
  const oldSource = ctx.sources[0];
  const clock = renderClock(makeSection(), 50);

  ctx.currentTime = 10;
  await engine.setSpeedPct(55);

  const newSource = ctx.sources[ctx.sources.length - 1];
  assert.notEqual(newSource, oldSource);
  assert.equal(fetch.seen[1], '/api/render/cant-stop/solo?speed=55&semitones=0&source=mix');
  const seam = clock.loopEnd; // the first seam, startTime 0 + loopEnd
  assert.ok(Math.abs(newSource.started.when - seam) < 1e-9);
  // The new buffer joins at ITS loop start -- the lead-in is not replayed
  // for a rung change mid-practice.
  assert.ok(Math.abs(newSource.started.offset - renderClock(makeSection(), 55).loopStart) < 1e-9);
  // ... and the old one is stopped one crossfade later: two nodes overlap
  // for exactly the crossfade, never longer.
  assert.ok(Math.abs(oldSource.stopped - (seam + 0.01)) < 1e-9);
});

await test('the pass at the swap seam still fires exactly once, then the new lap length applies', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const passes = [];
  engine.addEventListener('pass', () => passes.push(1));
  engine.play();
  const oldClock = renderClock(makeSection(), 50);
  ctx.currentTime = 10;
  await engine.setSpeedPct(55);

  ctx.currentTime = oldClock.loopEnd;
  engine._tick();
  assert.equal(passes.length, 1);

  const newClock = renderClock(makeSection(), 55);
  ctx.currentTime = oldClock.loopEnd + newClock.lap - 0.001;
  engine._tick();
  assert.equal(passes.length, 1, 'the new rung has its own, shorter lap');
  ctx.currentTime = oldClock.loopEnd + newClock.lap;
  engine._tick();
  assert.equal(passes.length, 2);
  assert.equal(engine.speedPct, 55);
});

await test('the swap crossfades with equal power, both directions over the same window', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  engine.play();
  const oldGain = engine._active.gain;
  ctx.currentTime = 10;
  await engine.setSpeedPct(55);
  const newGain = engine._pendingSwap.gain;

  const out = oldGain.gain.calls.find((c) => c[0] === 'curve');
  const inn = newGain.gain.calls.find((c) => c[0] === 'curve');
  assert.ok(out && inn, 'both sides ramp');
  assert.equal(out[2], inn[2]); // same start time -- the seam
  assert.equal(out[3], inn[3]); // same duration -- one crossfade
  for (let i = 0; i < out[1].length; i++) {
    assert.ok(Math.abs(out[1][i] ** 2 + inn[1][i] ** 2 - 1) < 1e-6);
  }
});

await test('a speed change while stopped just reloads, with nothing to swap at', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  await engine.setSpeedPct(55);
  assert.equal(ctx.sources.length, 0);
  assert.equal(engine.speedPct, 55);
  engine.play();
  assert.ok(Math.abs(ctx.sources[0].loopStart - renderClock(makeSection(), 55).loopStart) < 1e-9);
});

await test('a semitone change swaps at the boundary too, not instantly', async () => {
  const fetch = fetchStub([{ status: 200 }]);
  const { ctx, engine } = await mounted({ fetch, speedPct: 50 });
  engine.play();
  await engine.setSemitones(-1);
  assert.equal(fetch.seen[1], '/api/render/cant-stop/solo?speed=50&semitones=-1&source=mix');
  const seam = renderClock(makeSection(), 50).loopEnd;
  assert.ok(Math.abs(ctx.sources[ctx.sources.length - 1].started.when - seam) < 1e-9);
});

await test('equalPowerCurve is sin/cos of one angle, so the pair sums to unit power', () => {
  const up = equalPowerCurve(64, 'in');
  const down = equalPowerCurve(64, 'out');
  assert.equal(up.length, 64);
  assert.ok(Math.abs(up[0]) < 1e-6);
  assert.ok(Math.abs(up[63] - 1) < 1e-6);
  assert.ok(Math.abs(down[0] - 1) < 1e-6);
  assert.ok(Math.abs(down[63]) < 1e-6);
  for (let i = 0; i < 64; i++) assert.ok(Math.abs(up[i] ** 2 + down[i] ** 2 - 1) < 1e-6);
});

await test('position() is exact, and wraps at the loop points', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const clock = renderClock(makeSection(), 50);
  assert.equal(engine.position(), 0);
  engine.play();
  ctx.currentTime = 10;
  assert.ok(Math.abs(engine.position() - 10) < 1e-9);
  // One lap and a bit later: back round to just past the loop start.
  ctx.currentTime = clock.loopEnd + 3;
  assert.ok(Math.abs(engine.position() - (clock.loopStart + 3)) < 1e-9);
  // ... and in the recording's own clock: 60s section start, 2s lead-in,
  // so playback 4s (the loop start) is source second 60.
  ctx.currentTime = clock.loopEnd;
  assert.ok(Math.abs(engine.sourcePosition() - 60) < 1e-9);
});

await test('position() holds still while paused', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  engine.play();
  ctx.currentTime = 7;
  engine.pause();
  ctx.currentTime = 99;
  assert.ok(Math.abs(engine.position() - 7) < 1e-9);
});

await test('createEngine picks the engine, and still defaults to the real-time one', () => {
  // docs/03-audio-engine.md's table: exploring gets the stretcher,
  // practising gets the buffer. Every caller that has not asked for the
  // buffer engine (song.js's preview, capture.js's audition) keeps exactly
  // what it had.
  assert.ok(createEngine({}) instanceof RealtimeEngine);
  assert.ok(createEngine({}, { kind: 'buffer' }) instanceof BufferEngine);
  assert.ok(createEngine({}, { kind: 'realtime' }) instanceof RealtimeEngine);
});

if (failures) process.exitCode = 1;
