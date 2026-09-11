// web/tests/test_song_restart.mjs — #2's test contract, run as a plain Node
// script (`node web/tests/test_song_restart.mjs`), same house style as
// test_practice_seek.mjs (see that file's header for why this repo tests
// the pure position math directly rather than driving a real DOM click
// through screens/song.js: there is no DOM library here to do that with).
//
// #2's ask, in two parts, each verified a different way:
//
//   1. "the target is the currently selected section's own start_s, or 0
//      when nothing is selected" -- a pure function, `restartTargetS`,
//      exported for exactly this reason (the same reuse timeline.js's
//      computeSeekPosition earned in P2).
//   2. "without changing whether the transport is playing or paused" --
//      not expressible as a pure-function test (there is no play state to
//      pass in), so this is a lint-style check against the click handler's
//      own extracted source, the same pattern P2's own test uses against
//      seekToClientX. This is exactly the bug this repo's own commit
//      6dc2850 ("Fix restart playing through a pause") already fixed once
//      on the OTHER screen's restart control (practice.js's
//      restart_section) -- worth guarding here before it has the chance
//      to recur on this one.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { restartTargetS } from '../screens/song.js';

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

const SECTIONS = [
  { id: 'a', start_s: 12.5, end_s: 30 },
  { id: 'b', start_s: 60, end_s: 90 },
];

test('restartTargetS resolves the selected section\'s own start_s', () => {
  assert.equal(restartTargetS(SECTIONS, 'b'), 60);
});

test('restartTargetS falls back to 0 when nothing is selected', () => {
  assert.equal(restartTargetS(SECTIONS, null), 0);
});

test('restartTargetS falls back to 0 for a selectedId that names no section '
     + '(stale selection, e.g. the section was just deleted)', () => {
  assert.equal(restartTargetS(SECTIONS, 'does-not-exist'), 0);
});

const songJsPath = fileURLToPath(new URL('../screens/song.js', import.meta.url));
const src = readFileSync(songJsPath, 'utf8');

test('the restart control reuses FOOT_ICONS.restart_section rather than a second copy of its SVG', () => {
  assert.ok(
    /import\s*\{[^}]*\bFOOT_ICONS\b[^}]*\}\s*from\s*['"]\.\/practice\.js['"]/.test(src),
    'song.js should import FOOT_ICONS from practice.js, not hand-roll a duplicate replay glyph',
  );
  assert.ok(src.includes('FOOT_ICONS.restart_section'), 'the restart button markup should use FOOT_ICONS.restart_section');
});

test('the restart button sits to the left of the play/pause button in the transport row', () => {
  const restartAt = src.indexOf('data-transport-restart');
  const playAt = src.indexOf('data-transport-play');
  assert.ok(restartAt >= 0, 'no data-transport-restart element found');
  assert.ok(playAt >= 0, 'no data-transport-play element found');
  assert.ok(restartAt < playAt, 'data-transport-restart must be markup-order before data-transport-play (to its left)');
});

test("restartToSectionStart's own extracted source never starts or stops playback -- "
     + 'only moves position, same invariant practice.js\'s restart_section already enforces '
     + '(commit 6dc2850)', () => {
  const match = src.match(/function restartToSectionStart\(\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'restartToSectionStart not found in song.js -- has it been renamed?');
  const body = match[0];
  assert.ok(!body.includes('transportPlaying ='), 'restartToSectionStart must never assign transportPlaying');
  assert.ok(!/\bengine\.play\(/.test(body), 'restartToSectionStart must never call engine.play()');
  assert.ok(!/\bengine\.pause\(/.test(body), 'restartToSectionStart must never call engine.pause()');
  assert.ok(!body.includes('setTransportIcon('), 'restartToSectionStart must never touch the play/pause icon');
});
