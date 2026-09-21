// web/tests/test_metronome_control.mjs — what the metronome toggle shows,
// and what the status bar says for it. Run as a plain Node script
// (`node web/tests/test_metronome_control.mjs`), same house style as
// test_status_bar.mjs, and pure for the same reason: this is the branching
// a human would otherwise have to reproduce by hand in a browser.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { metronomeControl, statusBarState } from '../screens/practice.js';

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

const SECTION = { full_song: false };
const WHOLE_SONG = { full_song: true };

test('an ordinary section on the buffer engine offers the toggle', () => {
  const state = metronomeControl({ section: SECTION, engineKind: 'buffer', state: 'off' });
  assert.equal(state.shown, true);
  assert.equal(state.enabled, true);
  assert.equal(state.pressed, false);
});

test('switched on, the toggle reads as pressed', () => {
  const state = metronomeControl({ section: SECTION, engineKind: 'buffer', state: 'on' });
  assert.equal(state.pressed, true);
  assert.match(state.title, /on/i);
});

// ---- the off/1x/2x/4x cycle ----

test('on at the default 1x shows no badge -- the plain click needs no label', () => {
  const state = metronomeControl({
    section: SECTION, engineKind: 'buffer', state: 'on', multiplier: 1,
  });
  assert.equal(state.label, '');
  assert.doesNotMatch(state.title, /\dx|\d×/);
});

test('on at 2x shows the badge and says so in the title', () => {
  const state = metronomeControl({
    section: SECTION, engineKind: 'buffer', state: 'on', multiplier: 2,
  });
  assert.equal(state.label, '2×');
  assert.match(state.title, /2\s*×/);
});

test('on at 4x shows the badge and says so in the title', () => {
  const state = metronomeControl({
    section: SECTION, engineKind: 'buffer', state: 'on', multiplier: 4,
  });
  assert.equal(state.label, '4×');
  assert.match(state.title, /4\s*×/);
});

test('off shows no badge regardless of a stale multiplier', () => {
  const state = metronomeControl({
    section: SECTION, engineKind: 'buffer', state: 'off', multiplier: 4,
  });
  assert.equal(state.label, '');
});

test('the whole-song entry does not offer it at all -- Paolo\'s own call', () => {
  // A five-minute span is exactly where "one tempo" is a fiction, and
  // full_song is a rep counter rather than a practice target.
  const state = metronomeControl({ section: WHOLE_SONG, engineKind: 'buffer', state: 'off' });
  assert.equal(state.shown, false);
  assert.equal(state.enabled, false);
});

test('the real-time fallback engine cannot click, and says so instead of pretending', () => {
  // That engine has no honest position (practice.js, decision 3), so a
  // click would be confidently wrong by a variable margin.
  const state = metronomeControl({ section: SECTION, engineKind: 'realtime', state: 'off' });
  assert.equal(state.shown, true);
  assert.equal(state.enabled, false);
  assert.match(state.title, /render cache/i);
});

test('while the beats are being fitted the toggle cannot be pressed again', () => {
  const state = metronomeControl({ section: SECTION, engineKind: 'buffer', state: 'fitting' });
  assert.equal(state.enabled, false);
});

test('before any engine exists the toggle is still pressable', () => {
  // Pressing it before the first play is how a session usually starts:
  // the fit begins immediately and the click joins when the engine does.
  const state = metronomeControl({ section: SECTION, engineKind: null, state: 'off' });
  assert.equal(state.enabled, true);
});

// ---- the status bar's three metronome lines ----

const QUIET = {
  renderStage: null, detail: '', guitarBusy: false, demucsAvailable: true,
  engineKind: 'buffer', metronome: 'off',
};

test('a metronome that is simply on says nothing', () => {
  const { text } = statusBarState({ ...QUIET, metronome: 'on' });
  assert.equal(text, '');
});

test('fitting the beats shows a wait, with the indeterminate bar', () => {
  const { text, showProgress } = statusBarState({ ...QUIET, metronome: 'fitting' });
  assert.match(text, /beats/i);
  assert.equal(showProgress, true);
});

test('a section with no measurable pulse says so -- the toggle turned itself off', () => {
  const { text, showProgress } = statusBarState({ ...QUIET, metronome: 'none' });
  assert.match(text, /no clear pulse/i);
  assert.equal(showProgress, false);
});

test('the realtime engine explains why the metronome refused', () => {
  const { text } = statusBarState({ ...QUIET, metronome: 'no-cache', engineKind: 'realtime' });
  assert.match(text, /render cache/i);
});

test('an active render still outranks every metronome message', () => {
  const { text } = statusBarState({
    ...QUIET, renderStage: 'rendering', detail: ' at 60% speed, 0 shift', metronome: 'fitting',
  });
  assert.equal(text, 'Rendering at 60% speed, 0 shift…');
});

test('a missing demucs still shows when the metronome has nothing to say', () => {
  const { text } = statusBarState({ ...QUIET, demucsAvailable: false, metronome: 'on' });
  assert.match(text, /guitar isolation/i);
});

test('a FAILED fit stays retryable; an empty one is remembered', () => {
  // Not pure (it lives in mount()'s closure), so this is a lint-style
  // check against the source, the same pattern the rest of this suite
  // uses for wiring no DOM library can exercise. The bug it guards: on a
  // failed request, caching `[]` would make the toggle permanently dead
  // until the page was reloaded -- a server restart mid-session is enough
  // to hit it.
  const src = readFileSync(
    fileURLToPath(new URL('../screens/practice.js', import.meta.url)), 'utf8',
  );
  const start = src.indexOf('async function toggleMetronome()');
  assert.ok(start !== -1, 'no toggleMetronome()');
  const body = src.slice(start, src.indexOf('\n  }', start));
  const failurePath = body.slice(body.indexOf('} catch'));
  assert.ok(
    !failurePath.includes('metronomeBeats = []'),
    'a failed fetch must leave the beats unfetched so a second press retries',
  );
  assert.ok(
    body.includes('Array.isArray(data.beats) ? data.beats : []'),
    'a successful fit is remembered, empty or not',
  );
});

test('the press cycles 1x -> 2x -> 4x -> off without re-fetching', () => {
  // Not pure (mount()'s closure again), same lint-style check as the failed-
  // fit test above: the property worth guarding is that stepping the
  // multiplier never re-enters the fetch-then-fit branch, which is what
  // makes 2x/4x free. `METRONOME_RUNGS` names the cycle in one place so a
  // future rung can't be added to one branch and forgotten in the other.
  const src = readFileSync(
    fileURLToPath(new URL('../screens/practice.js', import.meta.url)), 'utf8',
  );
  assert.match(src, /METRONOME_RUNGS\s*=\s*\[1,\s*2,\s*4\]/);
  const start = src.indexOf('async function toggleMetronome()');
  assert.ok(start !== -1, 'no toggleMetronome()');
  const body = src.slice(start, src.indexOf('\n  }\n', start));
  const onBranch = body.slice(
    body.indexOf("metronomeState === 'on') {"),
    body.indexOf('if (metronomeBeats === null)'),
  );
  assert.ok(
    onBranch.includes('METRONOME_RUNGS.indexOf(metronomeMultiplier)'),
    'stepping the multiplier while already on must not touch the fetch path',
  );
  assert.ok(
    !onBranch.includes('await get('),
    'the on-branch must never fetch -- only the first press does',
  );
});

test('an omitted metronome field changes nothing for the other callers', () => {
  const { text } = statusBarState({
    renderStage: null, detail: '', guitarBusy: true, demucsAvailable: true, engineKind: 'buffer',
  });
  assert.equal(text, 'separating…');
});
