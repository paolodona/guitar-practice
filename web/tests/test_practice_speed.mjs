// web/tests/test_practice_speed.mjs — screens/practice.js's half of the
// 2026-09-11 speed-change repair. The engine's half is
// test_speed_change.mjs, which drives a real BufferEngine against a
// synthetic AudioContext; this half lives inside mount()'s closures, where
// there is no DOM to drive it through, so it is checked against the
// source — the same narrow, deliberate trade test_lead_in_removed.mjs and
// test_practice_seek.mjs already make for the same reason.
//
// What Paolo hit, practising tutti-in-fila/full-solo: "when I click faster
// or slower the speed of the song does not change... Music keeps playing at
// the old speed while a different speed is showing on screen... after a
// number of back and forth with speed adjustments, the playhead was off
// sync with the music (clicking in one place made it jump to another,
// unrelated section of the waveform)."
//
// The screen's three contributions to that, each guarded below:
//
//   1. Every press called `engine.setSpeedPct` directly, and each distinct
//      speed is a distinct cache key, so a burst of presses commissioned a
//      render per press -- "I do not need all the intermediate versions to
//      be created, as my target is 80%".
//   2. The cosmetic clocks (`preRollPlaybackSeconds`, `loopDurationPlayback`)
//      were derived from `speedPct`, the speed last PRESSED, while `tick()`
//      read `engine.position()`, measured in the speed actually PLAYING.
//      Two clocks, mixed, exactly as CLAUDE.md warns -- and silently.
//   3. `POST /api/rep` filed each rep against `speedPct` too, so a lap
//      played at 50% while the 80% render built was recorded as an 80%
//      rep. The ledger is append-only and is the only irreplaceable file
//      in the repo, which is what makes that one more than cosmetic.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const src = readFileSync(
  fileURLToPath(new URL('../screens/practice.js', import.meta.url)), 'utf8',
);
const playerSrc = readFileSync(
  fileURLToPath(new URL('../player.js', import.meta.url)), 'utf8',
);

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(err);
  }
}

/** The body of a `name() { ... }` action handler, brace-matched. */
function handlerBody(name) {
  const start = src.indexOf(`\n    ${name}() {`);
  assert.ok(start !== -1, `no ${name}() action handler in practice.js`);
  let i = src.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(i + 1, j);
  }
  throw new Error(`unbalanced braces in ${name}()`);
}

// ---- 1. a burst of presses is one render, not one per press -------------

test('speed_up and speed_down do not call the engine directly', () => {
  for (const name of ['speed_up', 'speed_down']) {
    const body = handlerBody(name);
    assert.ok(
      !body.includes('engine.setSpeedPct'),
      `${name}() calls engine.setSpeedPct directly — every press then commissions its own render`,
    );
    assert.ok(body.includes('commitSpeed()'), `${name}() should go through commitSpeed()`);
  }
});

test('transpose_up and transpose_down do not call the engine directly', () => {
  // A shift is a different rendered FILE, not a live parameter (player.js's
  // setSemitones doc), so holding the + key has exactly the speed problem.
  for (const name of ['transpose_up', 'transpose_down']) {
    const body = handlerBody(name);
    assert.ok(
      !body.includes('engine.setSemitones'),
      `${name}() calls engine.setSemitones directly`,
    );
    assert.ok(body.includes('commitShift()'), `${name}() should go through commitShift()`);
  }
});

test('commitSpeed debounces for the buffer engine and is immediate for the realtime one', () => {
  const start = src.indexOf('function commitSpeed()');
  assert.ok(start !== -1, 'no commitSpeed()');
  const body = src.slice(start, src.indexOf('\n  }', start));
  assert.ok(body.includes('setTimeout'), 'commitSpeed should defer the commit');
  assert.ok(body.includes('SPEED_COMMIT_MS'), 'the delay should be the named constant');
  assert.ok(
    body.includes("engineKind === 'realtime'"),
    'a live ratio change costs nothing to apply at once — do not add lag to the fallback engine',
  );
});

test('the debounce window is stated as a number, not left to a literal at the call site', () => {
  const m = src.match(/const SPEED_COMMIT_MS = (\d+);/);
  assert.ok(m, 'SPEED_COMMIT_MS should be declared');
  const ms = Number(m[1]);
  assert.ok(ms >= 150 && ms <= 600, `${ms}ms is outside "longer than a click burst, shorter than a deliberate second press"`);
});

// ---- 2. one clock, and it is the engine's ------------------------------

test('the cosmetic playback clocks are derived from the audible speed', () => {
  for (const fn of ['preRollPlaybackSeconds', 'loopDurationPlayback']) {
    const start = src.indexOf(`function ${fn}()`);
    assert.ok(start !== -1, `no ${fn}()`);
    const body = src.slice(start, src.indexOf('\n  }', start));
    assert.ok(
      body.includes('audibleSpeedPct()'),
      `${fn}() divides by the PRESSED speed while tick() reads the engine's own clock`,
    );
    assert.ok(!/\bspeedPct \/ 100\b/.test(body), `${fn}() still reads the raw speedPct`);
  }
});

test('audibleSpeedPct reads the engine, falling back to the pressed value', () => {
  const start = src.indexOf('function audibleSpeedPct()');
  assert.ok(start !== -1, 'no audibleSpeedPct()');
  const body = src.slice(start, src.indexOf('\n  }', start));
  assert.ok(body.includes('engine.speedPct'), 'should read the engine');
  assert.ok(body.includes('return speedPct'), 'should degrade to the pressed value with no engine');
});

test('both engines expose speedPct, so the screen never branches on which it holds', () => {
  // RealtimeEngine gained the accessor BufferEngine already had; without it
  // audibleSpeedPct() silently falls back to the pressed value on the
  // fallback engine and the guard above proves nothing there.
  const getters = playerSrc.match(/get speedPct\(\)/g) ?? [];
  assert.equal(getters.length, 2, 'both RealtimeEngine and BufferEngine should expose speedPct');
  assert.equal((playerSrc.match(/get pending\(\)/g) ?? []).length, 2);
});

// ---- 3. the ledger records what was played ----------------------------

test('a rep is filed against the speed actually played', () => {
  const start = src.indexOf("post('/api/rep'");
  assert.ok(start !== -1, 'no rep POST');
  const body = src.slice(start, src.indexOf('})', start));
  assert.ok(
    body.includes('speed: audibleSpeedPct()'),
    'the rep records the pressed speed, not the one the recording was making',
  );
  assert.ok(!/speed: speedPct\b/.test(body), 'still filing reps against the pressed speed');
});

test('the ladder advances on the engine rung event, not on the press', () => {
  for (const name of ['speed_up', 'speed_down']) {
    assert.ok(
      !handlerBody(name).includes('ladder.setSpeed'),
      `${name}() moves the ladder before the speed is audible — reps land on the wrong rung`,
    );
  }
  const start = src.indexOf('function onRung(');
  assert.ok(start !== -1, 'no onRung() handler');
  const body = src.slice(start, src.indexOf('\n  }', start));
  assert.ok(body.includes('ladder.setSpeed'), 'onRung() should own the ladder speed');
  assert.ok(body.includes('detail.pending'), 'a pending rung must not drag the ladder backwards');
  assert.ok(body.includes('beginLap()'), 'a swap lands at a seam — re-freeze the lap estimate there');
  assert.ok(src.includes("addEventListener('rung'"), 'nothing subscribes to the rung event');
});

// ---- the display tells the truth while a render builds -----------------

test('the speed readout marks a pressed-but-not-yet-audible rung', () => {
  assert.ok(src.includes('const speedPending = audiblePct !== speedPct'),
    'nothing distinguishes the pressed speed from the playing one');
  assert.ok(src.includes('speedNumStyle'), 'the big number should be drawn differently while pending');
  assert.ok(/playing \$\{audiblePct\}%/.test(src),
    'the audible rung should be named somewhere the eye can find it');
  // Both branches of renderDiscrete (advancing and ordinary) must use it.
  assert.equal((src.match(/speedNumStyle/g) ?? []).length >= 3, true);
});

test('the lap caption names the same speed its duration was computed from', () => {
  assert.ok(
    src.includes('s AT ${audiblePct}%'),
    'the caption pairs an audible duration with the pressed percent — two numbers that disagree',
  );
});

process.exit(failures === 0 ? 0 : 1);
