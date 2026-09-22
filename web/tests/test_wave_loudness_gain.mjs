// web/tests/test_wave_loudness_gain.mjs — loudness-matching follow-up
// (plan 004): the waveform's visual height is scaled by the same per-song
// gain player.js applies to what you hear, purely cosmetic (peaks.json on
// disk is never touched -- see wave.js's own `loudnessGainDb` doc on
// drawWave's WaveOpts). `ampToY`/`dbToLinear` are pure and exported for
// exactly this DOM-free testing, same as player.js's `computeSliceFrames`.

import assert from 'node:assert/strict';
import { ampToY, dbToLinear } from '../wave.js';

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

test('0dB gain leaves a tick exactly where it already was', () => {
  const v = 0.2;
  assert.equal(ampToY(v * dbToLinear(0)), ampToY(v));
});

test('a positive gain draws a tick further from the centre (taller)', () => {
  const centre = ampToY(0);
  const v = 0.2;
  const boosted = ampToY(v * dbToLinear(6));
  const original = ampToY(v);
  assert.ok(Math.abs(boosted - centre) > Math.abs(original - centre));
});

test('a quiet recording boosted by its own real gain_db draws noticeably taller, by the same ratio', () => {
  // "snow"'s own real numbers (2026-09-22): -21.6 dBFS peak (linear
  // 0.0835), +16.7dB of gain computed by woodshed.loudness.gain_db toward
  // the -16 LUFS target -- target-limited here, not peak-limited (the
  // -1 dBTP ceiling would have allowed +20.6dB), so the boosted peak lands
  // well short of full scale, not at it.
  const snowPeak = 0.0835;
  const centre = ampToY(0);
  const original = Math.abs(ampToY(snowPeak) - centre);
  const boosted = Math.abs(ampToY(snowPeak * dbToLinear(16.7)) - centre);
  assert.ok(Math.abs(boosted / original - dbToLinear(16.7)) < 0.05, 'scales by the linear gain ratio');
  assert.ok(boosted > original * 5, 'draws substantially taller, not a cosmetic nudge');
});

test('the boosted amplitude still clamps at the trough edge, never drawing outside it', () => {
  // A large gain could in principle push v*gain well past 1 -- ampToY must
  // still clamp, the same ceiling the real playback gain_db respects via
  // PEAK_CEILING_DBFS.
  const huge = ampToY(0.5 * dbToLinear(24));
  const exactlyFullScale = ampToY(1.0);
  assert.equal(huge, exactlyFullScale);
});

test('a negative gain (an already-loud song) draws ticks shorter, not taller', () => {
  const v = 0.5;
  const centre = ampToY(0);
  const attenuated = ampToY(v * dbToLinear(-8));
  const original = ampToY(v);
  assert.ok(Math.abs(attenuated - centre) < Math.abs(original - centre));
});
