// web/tests/test_status_bar.mjs — #6's test contract, run as a plain Node
// script (`node web/tests/test_status_bar.mjs`), same house style as
// test_song_restart.mjs.
//
// #6 consolidates two previously separate indicators (the render/
// separation-wait popover, commit 3cb403c, and guitarStatusEl's own corner
// caption) into one status bar, with an EXPLICIT precedence the issue itself
// asked to have spelled out rather than left to whichever handler ran last.
// That precedence is real branching logic worth a direct test, so it is
// pulled out as a pure function (statusBarState, same reuse reasoning as
// restartTargetS/computeSeekPosition) rather than only checked by reading
// DOM state nothing here can drive. data-midi-status stays OUT of this bar
// (Paolo's own call, resolving #6's open question) -- confirmed by a
// lint-style check, the same pattern the rest of this suite uses for
// wiring no DOM library can exercise directly.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { statusBarState } from '../screens/practice.js';

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

const QUIET = { renderStage: null, detail: '', guitarBusy: false, demucsAvailable: true, engineKind: 'buffer' };

test('nothing to say: empty text, no progress bar', () => {
  const { text, showProgress } = statusBarState(QUIET);
  assert.equal(text, '');
  assert.equal(showProgress, false);
});

test('a render wait names the stage and the (speed, shift) it is actually for', () => {
  const { text, showProgress } = statusBarState({
    ...QUIET, renderStage: 'rendering', detail: ' at 75% speed, +2 shift',
  });
  assert.equal(text, 'Rendering at 75% speed, +2 shift…');
  assert.equal(showProgress, true);
});

test('a separation wait says so, distinctly from a plain render', () => {
  const { text, showProgress } = statusBarState({ ...QUIET, renderStage: 'separating', detail: '' });
  assert.equal(text, 'Isolating the guitar… (first time only)');
  assert.equal(showProgress, true);
});

test('a render wait outranks every other indicator -- the issue\'s own explicit call', () => {
  const { text } = statusBarState({
    renderStage: 'rendering', detail: '', guitarBusy: true, demucsAvailable: false, engineKind: 'realtime',
  });
  assert.equal(text, 'Rendering…', 'the active render must win over demucs-missing, guitarBusy and the realtime fallback');
});

test('with no render wait, a missing demucs install ranks above guitarBusy and the fallback', () => {
  const { text, showProgress } = statusBarState({
    ...QUIET, demucsAvailable: false, guitarBusy: true, engineKind: 'realtime',
  });
  assert.equal(text, 'install demucs: uv sync --extra separate');
  assert.equal(showProgress, false, 'only an active render/separation wait shows the indeterminate progress bar');
});

test('with demucs available, guitarBusy (a warm-cache swap, no render event fired) ranks above the fallback', () => {
  const { text } = statusBarState({ ...QUIET, guitarBusy: true, engineKind: 'realtime' });
  assert.equal(text, 'separating…');
});

test('the realtime-engine fallback is purely informational -- lowest priority, shown only when nothing else applies', () => {
  const { text } = statusBarState({ ...QUIET, engineKind: 'realtime' });
  assert.equal(text, 'live stretch — no render cache');
});

const practiceJsPath = fileURLToPath(new URL('../screens/practice.js', import.meta.url));
const src = readFileSync(practiceJsPath, 'utf8');

test('the old centred popover and its separate corner caption are gone', () => {
  for (const name of ['data-render-overlay', 'data-render-text', 'data-guitar-status', 'renderRenderOverlay']) {
    assert.ok(!src.includes(name), `${name} should no longer exist -- consolidated into the one status bar`);
  }
  // guitarStatusEl itself may still be named in a historical doc comment
  // explaining what #6 consolidated FROM -- what must be gone is the LIVE
  // declaration that queried it.
  assert.ok(!src.includes("querySelector('[data-guitar-status]')"), 'no live query for the removed corner caption should remain');
});

test('exactly one status bar element exists, moved with a transform under a transition, never display:none', () => {
  const matches = src.match(/data-status-bar/g) ?? [];
  assert.ok(matches.length >= 2, 'expected at least a template declaration and a querySelector for data-status-bar');
  assert.ok(src.includes('.status-bar'), 'the status bar needs its own CSS rule');
  assert.ok(/transform:translateY\(100%\)/.test(src), 'off-screen state should be a transform, not display:none');
  assert.ok(/transition:transform/.test(src), 'the slide must be an animated transform, not a hard swap');
});

test('data-midi-status is untouched and stays separate from the status bar (#6\'s resolved open question)', () => {
  assert.ok(src.includes('data-midi-status'), 'the foot-pedal connectivity caption should still exist, on its own');
  const statusBarBlockMatch = src.match(/function renderStatusBar\(\) \{[\s\S]*?\n  \}/);
  assert.ok(statusBarBlockMatch, 'renderStatusBar not found in practice.js -- has it been renamed?');
  assert.ok(
    !statusBarBlockMatch[0].includes('midiStatus'),
    'renderStatusBar must never touch the MIDI connectivity caption -- it is a different kind of thing (Paolo\'s own call)',
  );
});
