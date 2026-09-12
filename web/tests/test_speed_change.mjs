// web/tests/test_speed_change.mjs — what goes wrong when speed presses
// arrive faster than renders finish.
//
// FOUND LIVE 2026-09-11, Paolo, practising tutti-in-fila/full-solo: "when I
// click faster or slower the speed of the song does not change... Music
// keeps playing at the old speed while a different speed is showing on
// screen... after a number of back and forth with speed adjustments, the
// playhead was off sync with the music". The renders on disk were all
// byte-correct (checked: every full-solo FLAC is exactly (span + pre-roll)
// / speed minus the 10ms baked crossfade), so none of it was the cache.
//
// Four separate defects, one per section below, all of them invisible to
// the existing test_buffer_engine.mjs because every test there changes
// speed exactly once and lets the render resolve before the next one:
//
//   1. `_changeRender` compared against `_pendingSwap ?? this`, and
//      `_pendingSwap` is only assigned AFTER its await. Six rapid presses
//      therefore started six concurrent renders, each calling
//      `_scheduleSwap` (which cancels the previous) whenever ITS rubberband
//      finished. The shortest output renders fastest, so the LAST press
//      resolved FIRST and the FIRST press won — the engine settled on a
//      rung nobody asked for, while the screen showed the one they did.
//   2. Nothing deduplicated concurrent fetches of the same render URL, so
//      an up-down-up press sequence asked the server for the same file
//      twice and ran two poll loops over it.
//   3. The `!this._playing` branch swapped `_buffer` and `_speedPct`
//      without converting `_position` across the clock change — the exact
//      conversion `pause()`, three methods above it, is careful to make.
//      Resuming then started the new (shorter) buffer at an offset from
//      the old (longer) clock, past `loopEnd`, where Web Audio plays to the
//      end of the buffer and stops instead of looping: "the section looping
//      for the first few seconds and getting stuck".
//   4. A `_startAt` offset past `loopEnd` also puts the computed seam in
//      the PAST, so `_tick` fired 'pass' immediately — a phantom rep in an
//      append-only ledger, which is the one file this repo cannot repair.
//
// Same synthetic-AudioContext discipline as test_buffer_engine.mjs, with
// one addition: a fetch stub whose responses are released BY URL, in
// whatever order the test chooses, because the ordering is the bug.

import assert from 'node:assert/strict';
import { BufferEngine, renderClock } from '../player.js';

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

// ---- the synthetic AudioContext (test_buffer_engine.mjs's, unchanged) ----

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

// A 30s section with a 2s lead-in — the same fixture test_buffer_engine.mjs
// uses, so the two files' clock arithmetic is directly comparable.
function makeSection(extra = {}) {
  return { slug: 'cant-stop', sectionId: 'solo', startS: 60, endS: 90, preRollS: 2, crossfadeMs: 10, ...extra };
}

const urlFor = (speed, semitones = 0) =>
  `/api/render/cant-stop/solo?speed=${speed}&semitones=${semitones}&source=mix`;

/** Let every already-queued microtask and 1ms timer run. */
const settle = async (rounds = 6) => {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 2));
};

/**
 * A fetch stub that answers nothing until the test says so, and says so BY
 * URL. `release(url)` completes the request in flight for that URL; the
 * order of those calls is what reproduces defect 1, so it has to be the
 * test's to choose rather than the stub's.
 */
function gatedFetch() {
  const waiting = new Map(); // url -> resolve fn for the request in flight
  const seen = [];
  const fn = (url) => {
    seen.push(url);
    return new Promise((resolve) => waiting.set(url, resolve));
  };
  fn.seen = seen;
  fn.inFlight = () => [...waiting.keys()];
  fn.countFor = (url) => seen.filter((u) => u === url).length;
  fn.release = async (url, status = 200) => {
    const resolve = waiting.get(url);
    assert.ok(resolve, `no request in flight for ${url} (in flight: ${fn.inFlight().join(', ')})`);
    waiting.delete(url);
    resolve({
      ok: status === 200,
      status,
      statusText: String(status),
      async json() { return status === 202 ? { rendering: true, stage: 'rendering' } : {}; },
      async arrayBuffer() { return { tag: url }; },
    });
    await settle();
  };
  return fn;
}

async function mounted({ speedPct = 50, fetch = null } = {}) {
  const ctx = new FakeContext();
  const engine = new BufferEngine(ctx);
  engine.pollMs = 1;
  const gate = fetch ?? gatedFetch();
  globalThis.fetch = gate;
  engine.setSpeedPct(speedPct);
  const loading = engine.loadSection(makeSection());
  await settle(2);
  await gate.release(urlFor(speedPct));
  await loading;
  return { ctx, engine, gate };
}

// ---- 1. rapid presses: the last one asked for is the one that lands -----

await test('a render that resolves after a newer press was made is discarded, not adopted', async () => {
  const { ctx, engine, gate } = await mounted({ speedPct: 50 });
  engine.play();

  // Two presses in rapid succession, 50 -> 55 -> 80, neither render done.
  engine.setSpeedPct(55);
  engine.setSpeedPct(80);
  await settle(2);

  // 80 is what was asked for last, so that is what the engine is heading
  // for, whatever order the two renders come back in.
  assert.equal(engine.targetSpeedPct, 80);

  // The shorter output finishes first in real life; make it so here.
  await gate.release(urlFor(80));
  assert.equal(engine._pendingSwap?.speedPct, 80, 'the 80% swap should be queued');

  // Now the stale 55% render lands. Before the fix this called
  // _scheduleSwap, cancelled the 80% swap and left the engine playing 55%
  // while the screen said 80%.
  await gate.release(urlFor(55));
  assert.equal(engine._pendingSwap?.speedPct, 80, 'the stale 55% render must not take over');

  // And it really becomes 80 once the seam arrives.
  ctx.currentTime = engine._pendingSwap.seamTime;
  engine._tick();
  assert.equal(engine.speedPct, 80);
  assert.equal(engine.pending, false);
});

await test('pressing back to the speed already playing cancels the queued swap', async () => {
  const { engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  engine.setSpeedPct(55);
  await settle(2);
  await gate.release(urlFor(55));
  assert.equal(engine._pendingSwap?.speedPct, 55);

  engine.setSpeedPct(50);
  await settle(2);
  assert.equal(engine._pendingSwap, null, 'never mind — put the outgoing node back');
  assert.equal(engine.speedPct, 50);
  assert.equal(engine.pending, false);
});

// ---- 2. one fetch per render, however many presses ask for it -----------

await test('overlapping requests for the same render share one fetch', async () => {
  const { engine, gate } = await mounted({ speedPct: 50 });
  engine.play();

  engine.setSpeedPct(80);
  await settle(2);
  engine.setSpeedPct(55); // 80 is still in flight
  await settle(2);
  engine.setSpeedPct(80); // ...and now it is wanted again
  await settle(2);

  assert.equal(gate.countFor(urlFor(80)), 1, 'asked the server for the same file twice');
});

await test('a rung already decoded is never refetched', async () => {
  const { ctx, engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  engine.setSpeedPct(55);
  await settle(2);
  await gate.release(urlFor(55));
  ctx.currentTime = engine._pendingSwap.seamTime;
  engine._tick();
  assert.equal(engine.speedPct, 55);

  const before = gate.seen.length;
  engine.setSpeedPct(50); // back to the rung loadSection already decoded
  await settle(2);
  assert.equal(gate.seen.length, before, 'a decoded buffer should be reused');
});

// ---- 3. a speed change while paused keeps its place in the recording ----

await test('changing speed while paused converts the position across the two clocks', async () => {
  const { ctx, engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  ctx.currentTime = 20; // 20 playback seconds into a 50% render
  engine.pause();
  assert.equal(engine._position, 20);

  // The 50% render covers [start_s - pre_roll_s, end_s] = [58s, 90s]
  // stretched to double length, so 20 playback seconds in is 10 source
  // seconds past 58: source second 68, which is 8s into the section itself.
  const sourceBefore = engine.sourcePosition();
  assert.ok(Math.abs(sourceBefore - 68) < 1e-9, `source position was ${sourceBefore}`);

  engine.setSpeedPct(100);
  await settle(2);
  await gate.release(urlFor(100));

  assert.equal(engine.speedPct, 100, 'a stopped engine adopts the rung immediately');
  // The same instant in the RECORDING, not the same raw number of playback
  // seconds read against a different clock.
  const sourceAfter = engine.sourcePosition();
  assert.ok(
    Math.abs(sourceAfter - 68) < 1e-9,
    `paused position moved from source ${sourceBefore}s to ${sourceAfter}s across a speed change`,
  );
  // Source 68 in the 100% render, whose own t=0 is still source 58.
  assert.ok(Math.abs(engine._position - 10) < 1e-9, `_position was ${engine._position}`);
});

await test('resuming after a paused speed change loops instead of running off the end', async () => {
  const { ctx, engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  ctx.currentTime = 60; // near the end of the 64s 50% render
  engine.pause();
  engine.setSpeedPct(100); // the 100% render is only 32s long
  await settle(2);
  await gate.release(urlFor(100));

  engine.play();
  const source = ctx.sources[ctx.sources.length - 1];
  const clock = renderClock(makeSection(), 100);
  assert.ok(
    source.started.offset <= clock.loopEnd,
    `started at ${source.started.offset}s in a render whose loop ends at ${clock.loopEnd}s`,
  );
  assert.equal(source.loop, true);
});

// ---- 4. a stale offset must never manufacture a rep ---------------------

await test('an offset past the loop end never fires a pass on the first tick', async () => {
  const { ctx, engine } = await mounted({ speedPct: 50 });
  const passes = [];
  engine.addEventListener('pass', () => passes.push(1));
  // Whatever put it there (a stale clock, a bad restore), a position past
  // the end of the render must not be readable as "a lap just completed".
  engine._position = 500;
  engine.play();
  engine._tick();
  assert.deepEqual(passes, [], 'phantom pass — this writes a rep to an append-only ledger');
  const source = ctx.sources[ctx.sources.length - 1];
  assert.ok(source.started.offset <= renderClock(makeSection(), 50).loopEnd);
});

// ---- 5. the screen can tell what is playing from what was asked for -----

await test('rung events report the target while it is pending and again when it lands', async () => {
  const { ctx, engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  const events = [];
  engine.addEventListener('rung', (e) => events.push(e.detail));

  engine.setSpeedPct(80);
  await settle(2);
  const asked = events.at(-1);
  assert.equal(asked.speedPct, 50, 'still playing 50');
  assert.equal(asked.targetSpeedPct, 80, 'heading for 80');
  assert.equal(asked.pending, true);

  await gate.release(urlFor(80));
  ctx.currentTime = engine._pendingSwap.seamTime;
  engine._tick();
  const landed = events.at(-1);
  assert.equal(landed.speedPct, 80);
  assert.equal(landed.pending, false);
});

// ---- 6. one status line for however many renders are in flight ----------

await test('the rendering status names the target, and only clears when nothing is left', async () => {
  const { engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  const stages = [];
  engine.addEventListener('rendering', (e) => stages.push(e.detail));

  engine.setSpeedPct(55);
  await settle(2);
  await gate.release(urlFor(55), 202); // still building
  engine.setSpeedPct(80);
  await settle(2);
  await gate.release(urlFor(80), 202); // also still building

  assert.equal(stages.at(-1).stage, 'rendering');
  assert.equal(stages.at(-1).speedPct, 80, 'the bar must name what was actually asked for');

  // 55 finishes first. It is stale, and something else IS still building,
  // so the bar must not blank — that flapping is what made it unreadable.
  await gate.release(urlFor(55));
  assert.notEqual(stages.at(-1).stage, null, 'cleared the status bar while 80% was still rendering');
  assert.equal(stages.at(-1).speedPct, 80);

  await gate.release(urlFor(80));
  assert.equal(stages.at(-1).stage, null, 'nothing left to wait for');
});

// ---- 7. decoded buffers are not kept forever ----------------------------

await test('the decoded-buffer cache is bounded', async () => {
  const { ctx, engine, gate } = await mounted({ speedPct: 50 });
  engine.play();
  // A 90s section at 40% decodes to ~87MB of float32; thirteen rungs of
  // that is most of a gigabyte held live. Walk far enough to prove a cap.
  for (const pct of [55, 60, 65, 70, 75, 80]) {
    engine.setSpeedPct(pct);
    await settle(2);
    await gate.release(urlFor(pct));
    ctx.currentTime = engine._pendingSwap.seamTime;
    engine._tick();
    assert.equal(engine.speedPct, pct);
  }
  assert.ok(
    engine._buffers.size <= 4,
    `held ${engine._buffers.size} decoded renders; a long session would hold every rung`,
  );
  // Whatever was evicted, it was not what is playing.
  assert.ok([...engine._buffers.values()].includes(engine._buffer));
});

process.exit(failures === 0 ? 0 : 1);
