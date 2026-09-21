// web/tests/test_truncated_render.mjs — BufferEngine must never LOOP a
// render that arrived short.
//
// FOUND LIVE 2026-09-12, Paolo, practising tutti-in-fila/1431dc68 at 60%:
// "it is now playing a constant 'sine' sound as if a few milliseconds were
// looping". It was. `server._render` answers "ready" when the cache file
// exists, and the render's final ffmpeg used to encode straight onto that
// path, so a poll landing mid-encode is served a prefix of a FLAC with a
// 200. Chrome decodes the frames that did arrive -- a buffer of
// milliseconds -- and this engine then hands it to an
// AudioBufferSourceNode with `loopStart`/`loopEnd` computed for the full
// 22 seconds. Loop points outside the buffer are ignored, so the browser
// loops the whole tiny buffer instead: a few ms, forever, which is a tone.
//
// The server side is fixed at the source (src/woodshed/atomic.py: write
// beside the destination, rename onto it) and tests/test_atomic.py holds
// that end. This is the belt to that brace, and it belongs here because
// this is the layer that knows how long the render is SUPPOSED to be: the
// clock is arithmetic over the section's own span, so "the audio I was
// handed is much shorter than the section I asked for" is checkable
// without asking anyone. A short render is treated exactly like a 202 --
// still building, poll again -- because that is what it almost always is.

import assert from 'node:assert/strict';
import { BufferEngine, renderClock } from '../player.js';

let failures = 0;
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(
      () => console.log(`ok - ${name}`),
      (err) => { failures += 1; console.error(`FAIL - ${name}`); console.error(err); },
    );
}

// ---- the smallest AudioContext this engine will run against ----

class FakeParam {
  constructor(v) { this.value = v; }
  setValueAtTime() {}
  setValueCurveAtTime() {}
  cancelScheduledValues() {}
}
class FakeGain {
  constructor() { this.gain = new FakeParam(1); }
  connect(n) { return n; }
  disconnect() {}
}
class FakeSource {
  constructor(ctx) { this.ctx = ctx; this.buffer = null; this.loop = false; }
  connect(n) { return n; }
  disconnect() {}
  start(when, offset) { this.started = { when, offset }; }
  stop() {}
}

/** `durations` is consumed one per decode; the last one repeats. */
class FakeContext {
  constructor(durations) {
    this.currentTime = 0;
    this.state = 'running';
    this.sampleRate = 48000;
    this.destination = {};
    this.sources = [];
    this.decoded = [];
    this._durations = durations;
  }
  createBufferSource() { const s = new FakeSource(this); this.sources.push(s); return s; }
  createGain() { return new FakeGain(); }
  async resume() {}
  async close() {}
  async decodeAudioData() {
    const d = this._durations[Math.min(this.decoded.length, this._durations.length - 1)];
    const buffer = { duration: d, sampleRate: 48000, length: d * 48000, numberOfChannels: 2 };
    this.decoded.push(buffer);
    return buffer;
  }
}

// tutti-in-fila/1431dc68, the section that caused this: 11.006s long, a
// 4-beat lead-in at 116.04bpm (2.068s), practised at 60%.
const SECTION = {
  slug: 'tutti-in-fila',
  sectionId: '1431dc68',
  startS: 156.762,
  endS: 167.768,
  preRollS: 2.0685,
  crossfadeMs: 10,
};
const SPEED = 60;
const FULL = renderClock(SECTION, SPEED).total; // 21.79s

function engineWith(durations, { responses = [{ status: 200 }] } = {}) {
  const ctx = new FakeContext(durations);
  const fetches = [];
  globalThis.fetch = async (url) => {
    fetches.push(url);
    const r = responses[Math.min(fetches.length - 1, responses.length - 1)];
    return {
      ok: r.status === 200,
      status: r.status,
      statusText: String(r.status),
      async json() { return r.body ?? {}; },
      async arrayBuffer() { return new ArrayBuffer(8); },
    };
  };
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  return { ctx, engine, fetches };
}

await test('a render that decodes far short of the section is not adopted', async () => {
  // 0.05s where 21.79s was asked for: the shape of the bug, to the order.
  const { ctx, engine, fetches } = engineWith([0.05, FULL]);
  engine.setSpeedPct(SPEED);
  await engine.loadSection(SECTION);

  assert.equal(fetches.length, 2, 'the short render should have been re-fetched, not kept');
  assert.equal(engine._buffer.duration, FULL);
});

await test('the short buffer never reaches a source node', async () => {
  const { ctx, engine } = engineWith([0.05, FULL]);
  engine.setSpeedPct(SPEED);
  await engine.loadSection(SECTION);
  engine.play();

  assert.ok(ctx.sources.length > 0, 'nothing started playing at all');
  for (const s of ctx.sources) {
    assert.equal(s.buffer.duration, FULL, 'a truncated buffer was handed to the audio graph');
  }
});

await test('a short render says "still rendering" rather than failing', async () => {
  const { engine } = engineWith([0.05, FULL]);
  const stages = [];
  engine.addEventListener('rendering', (e) => stages.push(e.detail.stage));
  engine.setSpeedPct(SPEED);
  await engine.loadSection(SECTION);

  assert.ok(stages.includes('rendering'), `expected a rendering wait, saw ${JSON.stringify(stages)}`);
  assert.equal(stages[stages.length - 1], null, 'the status bar never went quiet again');
});

await test('the truncated decode is not remembered under its URL', async () => {
  // `_buffers` is keyed by URL, so caching the bad decode would keep the
  // drone for the whole session however many times the rung is revisited.
  const { engine, fetches } = engineWith([0.05, FULL]);
  engine.setSpeedPct(SPEED);
  await engine.loadSection(SECTION);
  const after = fetches.length;

  await engine.setSpeedPct(SPEED); // same rung: served from `_buffers`
  assert.equal(fetches.length, after, 'the rung was re-fetched; something cached the wrong thing');
  assert.equal(engine._buffer.duration, FULL);
});

await test('a whole render a hair under the clock is still accepted', async () => {
  // The tolerance is not "exactly `total`": ffmpeg's cut and rubberband's
  // output land a few ms either side of the arithmetic. Measured on the
  // real file that caused this bug: clock 21.790s, FLAC 21.780s.
  const { engine, fetches } = engineWith([FULL - 0.01]);
  engine.setSpeedPct(SPEED);
  await engine.loadSection(SECTION);

  assert.equal(fetches.length, 1, 'a complete render was rejected as truncated');
  assert.equal(engine._buffer.duration, FULL - 0.01);
});

await test('a render that never comes up to length eventually gives up', async () => {
  // Never silently loops the fragment, and never polls forever either.
  const { engine } = engineWith([0.05]);
  engine.setSpeedPct(SPEED);
  engine.maxPolls = 3;
  await assert.rejects(
    () => engine.loadSection(SECTION),
    /gave up waiting/,
  );
});

process.exit(failures === 0 ? 0 : 1);
