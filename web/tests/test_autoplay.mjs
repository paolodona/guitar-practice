// web/tests/test_autoplay.mjs — #8's test contract, run as a plain Node
// script (`node web/tests/test_autoplay.mjs`), same house style as
// test_song_restart.mjs: no DOM/AudioContext here to actually verify a
// resume succeeds or is blocked, so this checks practice.js's own source
// for the two things that matter -- that entering the screen attempts the
// SAME play_pause() path a real press takes (module doc's "one action
// table" rule, not a parallel autoplay-only code path), and that the
// attempt is VERIFIED rather than assumed, per ensureEngine()'s own
// documented hazard: a freshly-created AudioContext refuses to resume
// outside a real user gesture, and navigating into this screen (async
// route(), reached via a hashchange event) is not one by the time mount()
// runs. Left unchecked, that reproduces the exact "shows playing, plays
// nothing" bug ensureEngine()'s own "FOUND LIVE 2026-09-06" comment
// already names once for the eager-load case.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const practiceJsPath = fileURLToPath(new URL('../screens/practice.js', import.meta.url));
const src = readFileSync(practiceJsPath, 'utf8');

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

test('mount() attempts to start playing via the same play_pause() path a real press takes', () => {
  const renderAllAt = src.indexOf('renderAll();');
  const playPauseCallAt = src.indexOf('handlers.play_pause()');
  assert.ok(renderAllAt >= 0, 'renderAll() call not found -- has mount() been restructured?');
  assert.ok(playPauseCallAt >= 0, 'mount() should call handlers.play_pause() to start playing automatically (#8)');
  assert.ok(playPauseCallAt > renderAllAt, 'the autoplay attempt should happen after the initial render, not before it');
});

test('a blocked autoplay (AudioContext stuck suspended) is verified and rolled back, not left showing "playing" with no sound', () => {
  const autoplayBlockStart = src.indexOf('handlers.play_pause()');
  assert.ok(autoplayBlockStart >= 0);
  // The surrounding block (the .then() this triggers) must check the
  // engine's real AudioContext state, not just assume the gesture-free
  // resume succeeded.
  const nearby = src.slice(autoplayBlockStart, autoplayBlockStart + 700);
  assert.ok(/engine\.ctx\.state/.test(nearby), 'the autoplay attempt must check engine.ctx.state before trusting `playing`');
  assert.ok(/playing = false/.test(nearby), 'a still-suspended context must roll `playing` back to false');
});
