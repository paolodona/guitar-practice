// web/tests/test_lead_in_removed.mjs — #5's test contract, run as a plain
// Node script (`node web/tests/test_lead_in_removed.mjs`), same house
// style as test_song_restart.mjs: no DOM here to click through the removed
// overlay, so this checks that the machinery is actually gone from
// screens/practice.js's own source, rather than merely unused.
//
// #5's ask, the narrower reading Paolo confirmed: remove the pre-count-in
// overlay and the click sound entirely; KEEP the render's own pre-roll
// audio (pre_roll_beats/pre_roll_every_pass, untouched) and the BPM
// readout (already independent of any of this -- see practice.js's own
// speedSubEl, unaffected). This is a removal, not new logic, so there is
// no pure function to test in isolation the way #1/#2 had -- the
// regression worth guarding is "does it STAY removed".

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const practiceJsPath = fileURLToPath(new URL('../screens/practice.js', import.meta.url));
const src = readFileSync(practiceJsPath, 'utf8');

const actionsJsPath = fileURLToPath(new URL('../actions.js', import.meta.url));
const actionsSrc = readFileSync(actionsJsPath, 'utf8');

const keysJsPath = fileURLToPath(new URL('../keys.js', import.meta.url));
const keysSrc = readFileSync(keysJsPath, 'utf8');

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

test('the pre-count-in overlay and its elements are gone from practice.js', () => {
  for (const name of ['leadInOverlay', 'leadInCountEl', 'leadInPillsEl', 'leadInCaptionEl']) {
    assert.ok(!src.includes(name), `${name} should no longer exist`);
  }
  assert.ok(!src.includes('data-leadin-overlay'), 'the overlay markup should be gone from the template too');
});

test('the click sound (playClick/stopClick/clickCtx) is gone from practice.js', () => {
  for (const name of ['playClick', 'stopClick', 'clickCtx', 'clickSource']) {
    assert.ok(!src.includes(name), `${name} should no longer exist`);
  }
});

test('the cancel_lead_in action is gone -- there is no overlay left to cancel out of', () => {
  assert.ok(!src.includes('cancel_lead_in'), 'cancel_lead_in should no longer be a handler in practice.js');
  assert.ok(!actionsSrc.includes('cancel_lead_in'), 'cancel_lead_in should no longer be an ACTIONS entry');
  assert.ok(!keysSrc.includes('cancel_lead_in'), 'Escape should no longer be bound to cancel_lead_in');
});

test('the metronome stub is gone from actions.js and its keybinding', () => {
  assert.ok(!actionsSrc.includes('metronome'), 'metronome should no longer be an ACTIONS entry');
  assert.ok(!keysSrc.includes('metronome'), '"m" should no longer be bound to metronome');
});

test('the BPM readout survives untouched -- #5 explicitly keeps it', () => {
  assert.ok(src.includes('at full speed'), 'speedSubEl\'s BPM readout should still be there');
});

test('pre-roll itself is untouched -- #5\'s narrower reading keeps the render\'s own lead-in audio', () => {
  assert.ok(src.includes('preRollPlaybackSeconds'), 'the pre-roll cosmetic accounting should still exist');
  assert.ok(src.includes('cosmeticPreRoll'), 'elapsed still tracks the real pre-roll audio playing before the section');
});
