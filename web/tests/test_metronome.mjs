// web/tests/test_metronome.mjs — the click scheduler's arithmetic, run as a
// plain Node script (`node web/tests/test_metronome.mjs`), same house style
// as test_status_bar.mjs.
//
// There is no DOM or Web Audio here, which is exactly why the scheduling
// maths is a pure function taking (beats, clock, where playback is, what
// time it is) and returning AudioContext times. Everything that could put a
// click in the wrong place lives in that function; the class around it only
// creates nodes.
//
// The one contract worth stating up front, because getting it wrong is
// silent: a click scheduled at an AudioContext time in the PAST plays
// immediately. So a beat that has already gone by this lap must not come
// back as "0.0 seconds from now" -- it must simply not be in the window.

import assert from 'node:assert/strict';
import { beatsToPlayback, clicksInWindow, subdivideBeats } from '../metronome.js';

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

function approx(actual, expected, tolerance = 1e-6, message = '') {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${message} expected ${expected}, got ${actual}`,
  );
}

// A section [60, 90) with a 2s lead-in, played at 50%: the render is
// 2 + 30 = 32 source seconds, stretched to 64 playback seconds, looping
// from 4 (the lead-in, doubled by the stretch) to 64 - 0.01 (the baked
// crossfade). renderClock()'s own arithmetic, spelled out here so this
// file does not depend on player.js to state its own fixture.
const CLOCK = { speed: 0.5, loopStart: 4, loopEnd: 63.99, total: 64, lap: 59.99, preRollS: 2 };
const SECTION = { startS: 60, endS: 90 };

// ---------------------------------------------------------------------------
// beatsToPlayback — source seconds (what the fit and song.yaml are in) to
// playback seconds (where the stretched render actually plays them)
// ---------------------------------------------------------------------------

test('a beat at the section start lands at the end of the lead-in', () => {
  const [p] = beatsToPlayback([60], SECTION, CLOCK);
  approx(p, 4, 1e-9, 'the section starts where the lead-in ends:');
});

test('a beat inside the lead-in lands before loopStart, not at a negative time', () => {
  const [p] = beatsToPlayback([59], SECTION, CLOCK);
  approx(p, 2, 1e-9);
});

test('slowing down pushes beats further apart, in playback seconds', () => {
  const [a, b] = beatsToPlayback([60, 60.5], SECTION, CLOCK);
  approx(b - a, 1.0, 1e-9, 'half a source second at 50% is a whole playback second:');
});

test('a beat before the rendered lead-in is simply negative -- callers drop it', () => {
  const [p] = beatsToPlayback([57], SECTION, CLOCK);
  assert.ok(p < 0, 'nothing was rendered that early; the scheduler must not click it');
});

// ---------------------------------------------------------------------------
// subdivideBeats — the off/1x/2x/4x cycle, without a second fit
// ---------------------------------------------------------------------------

test('a multiplier of 1 leaves the fit untouched', () => {
  assert.deepEqual(subdivideBeats([60, 61, 62], 1), [60, 61, 62]);
});

test('2x inserts exactly one midpoint per gap', () => {
  assert.deepEqual(subdivideBeats([60, 61, 63], 2), [60, 60.5, 61, 62, 63]);
});

test('4x inserts three evenly-spaced points per gap', () => {
  const out = subdivideBeats([60, 64], 4);
  assert.deepEqual(out, [60, 61, 62, 63, 64]);
});

test('a single beat has no gap to subdivide', () => {
  assert.deepEqual(subdivideBeats([60], 2), [60]);
});

test('an empty or missing list stays empty', () => {
  assert.deepEqual(subdivideBeats([], 2), []);
  assert.deepEqual(subdivideBeats(null, 2), []);
});

// ---------------------------------------------------------------------------
// clicksInWindow — which beats fall in the next slice of AudioContext time
// ---------------------------------------------------------------------------

const BEATS = beatsToPlayback(
  // every beat of a 120bpm section from 59s (in the lead-in) to 90s
  Array.from({ length: 63 }, (_, i) => 59 + i * 0.5),
  SECTION,
  CLOCK,
);

test('an empty window produces no clicks', () => {
  const times = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 10, ctxNow: 100, from: 100, to: 100,
  });
  assert.deepEqual(times, []);
});

test('the beats of the next 300ms come back as absolute AudioContext times', () => {
  // At playback position 10, the next beats are at 10 (just gone), 11, 12...
  // (120bpm at 50% speed is one beat per playback second).
  const times = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 10.4, ctxNow: 100, from: 100, to: 100.8,
  });
  assert.equal(times.length, 1, 'exactly one beat falls in the next 800ms');
  approx(times[0], 100.6, 1e-9, 'the beat at playback 11 is 0.6s away:');
});

test('a beat exactly at the window start is not re-scheduled', () => {
  // `from` is the previous window's horizon: a beat AT it was already
  // handed to Web Audio, and scheduling it twice is an audible flam.
  const times = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 11, ctxNow: 100, from: 100, to: 101.5,
  });
  assert.equal(times.length, 1);
  approx(times[0], 101, 1e-9);
});

test('clicks in the lead-in are scheduled on the first pass', () => {
  const times = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 0, ctxNow: 100, from: 100, to: 103,
  });
  // playback 2 and 3 are lead-in beats (source 59 and 59.5); 4 is the
  // section's own first beat.
  assert.deepEqual(times.map((t) => +(t - 100).toFixed(3)), [2, 3]);
});

test('the lead-in is NOT clicked again after the loop wraps', () => {
  // A lap that starts at loopStart never replays the lead-in (unless
  // pre_roll_every_pass says so -- covered below), so neither may the
  // click: clicking a count-in in the middle of practising is exactly the
  // noise #5 removed.
  const times = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: CLOCK.loopEnd - 0.2, ctxNow: 100, from: 100, to: 101.5,
  });
  const offsets = times.map((t) => +(t - 100).toFixed(3));
  // 0.2s to the wrap, then the lap restarts at playback 4 -- the section's
  // own first beat, which is due immediately, and the next at 5.
  assert.ok(!offsets.some((o) => o > 1.3), `nothing from the lead-in: ${offsets}`);
  approx(offsets[0], 0.2, 1e-3, 'the first beat of the new lap:');
});

test('with pre_roll_every_pass the lead-in IS clicked every lap', () => {
  const everyPass = { ...CLOCK, loopStart: 0, lap: CLOCK.loopEnd };
  const times = clicksInWindow({
    beats: BEATS, clock: everyPass, position: everyPass.loopEnd - 0.1, ctxNow: 100,
    from: 100, to: 102.5,
  });
  const offsets = times.map((t) => +(t - 100).toFixed(3));
  assert.ok(offsets.includes(2.1), `the lead-in beat at playback 2 is back: ${offsets}`);
});

test('a window spanning a whole lap does not drift', () => {
  // The seam is arithmetic, not an event: beat N of the next lap must land
  // exactly one lap after beat N of this one, however many laps later the
  // window happens to be.
  const early = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 5, ctxNow: 0, from: 0, to: 1.5,
  });
  const late = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 5, ctxNow: 0, from: CLOCK.lap, to: CLOCK.lap + 1.5,
  });
  assert.equal(early.length, late.length);
  for (let i = 0; i < early.length; i++) {
    approx(late[i] - early[i], CLOCK.lap, 1e-6, `beat ${i} one lap later:`);
  }
});

test('beats outside the loop region are never scheduled', () => {
  // A beat past loopEnd belongs to audio the loop never reaches (the
  // crossfade tail), and one before the rendered lead-in does not exist.
  const beats = [-1, 2, 63.995, 70];
  const times = clicksInWindow({
    beats, clock: CLOCK, position: 0, ctxNow: 0, from: 0, to: CLOCK.total + 10,
  });
  assert.equal(times.length, 1, 'only the lead-in beat at 2 is real');
  approx(times[0], 2, 1e-9);
});

test('a paused-then-resumed position schedules from where it actually is', () => {
  const times = clicksInWindow({
    beats: BEATS, clock: CLOCK, position: 40.25, ctxNow: 500, from: 500, to: 500.9,
  });
  approx(times[0], 500.75, 1e-9, 'the next beat after playback 40.25 is 41:');
});

test('a zero-length lap cannot loop forever', () => {
  // Defensive: a degenerate clock (a section whose loop region collapsed)
  // must return what it can and stop, not spin the tab.
  const broken = { ...CLOCK, loopStart: 10, loopEnd: 10, lap: 0 };
  const times = clicksInWindow({
    beats: BEATS, clock: broken, position: 0, ctxNow: 0, from: 0, to: 30,
  });
  assert.ok(Array.isArray(times));
  assert.ok(times.length < 100);
});

test('the scheduler never returns a time before the window', () => {
  // The silent failure this whole file exists for: Web Audio plays a node
  // scheduled in the past IMMEDIATELY, so an off-by-one that emits a stale
  // beat is heard as a flam, not seen as an exception.
  for (const position of [0, 3.9, 4, 30.5, CLOCK.loopEnd - 0.001]) {
    const times = clicksInWindow({
      beats: BEATS, clock: CLOCK, position, ctxNow: 200, from: 200, to: 200.5,
    });
    for (const t of times) {
      assert.ok(t > 200 - 1e-9, `a click at ${t} is in the past`);
      assert.ok(t <= 200.5 + 1e-9, `a click at ${t} is past the horizon`);
    }
  }
});
