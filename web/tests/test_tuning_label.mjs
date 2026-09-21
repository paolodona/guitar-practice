// web/tests/test_tuning_label.mjs — the song page's "plays in …" caption.
//
// FOUND LIVE 2026-09-12: on #/song/<slug> the header read "RECORD IN E
// STANDARD · 0 · plays in E standard", and pressing "−" moved the stepper to
// −1 while the caption still said "plays in E standard". It was never wired
// to anything: the caption was interpolated once into the header's innerHTML
// from `payload.recording.tuning` and `renderShift()` only ever touched the
// number. Exactly the class of bug this screen's own module doc already names
// twice ("bumpShift still only updated the number, silent until the next
// press") — a control that moves and a readout that does not.
//
// Two halves, verified the two ways this repo's front-end tests verify things
// (see test_song_restart.mjs's header for why): the maths is a pure exported
// function called directly, and "the caption is actually re-rendered" is a
// lint-style check against screens/song.js's own source, since there is no
// DOM library here to press the button with.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { KNOWN_TUNINGS, playsInLabel, shiftedTuning } from '../tuning.js';

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

// ---- the reported bug, stated as its own case ----

test('a −1 press renames the caption (the bug: it stayed on the recording tuning)', () => {
  assert.equal(playsInLabel('E standard', 0), 'plays in E standard');
  assert.equal(playsInLabel('E standard', -1), 'plays in E♭ standard');
});

// ---- naming the tuning you end up in ----

test('the shift walks the standard-tuning ladder in both directions', () => {
  assert.equal(playsInLabel('E standard', -2), 'plays in D standard');
  assert.equal(playsInLabel('E standard', -3), 'plays in C# standard');
  assert.equal(playsInLabel('E standard', -5), 'plays in B standard');
  assert.equal(playsInLabel('Eb standard', 1), 'plays in E standard');
  assert.equal(playsInLabel('D standard', 2), 'plays in E standard');
  assert.equal(playsInLabel('B standard', 2), 'plays in C# standard');
  assert.equal(playsInLabel('B standard', 5), 'plays in E standard');
});

test('the flat marker is the unicode flat, matching tuning.tuning_label', () => {
  assert.equal(playsInLabel('Eb standard', 0), 'plays in E♭ standard');
  // Only a flat NOTE name is remapped -- "#" already reads correctly, and a
  // stray "b" mid-word is not a flat.
  assert.equal(playsInLabel('C# standard', 0), 'plays in C# standard');
  assert.equal(playsInLabel('Drop D', 0), 'plays in Drop D');
});

test('a drop tuning stays in its own family -- shifting Drop D down one is '
     + 'Drop C#, not Eb standard (tuning.py\'s _DROP_PARENTS reasoning: the '
     + 'low string is still a tone below the rest)', () => {
  assert.equal(shiftedTuning('Drop D', -1), 'Drop C#');
  assert.equal(playsInLabel('Drop D', -1), 'plays in Drop C#');
  assert.equal(playsInLabel('Drop C#', 1), 'plays in Drop D');
});

// ---- and refusing to name one that has no name ----

test('a shift that lands on no known tuning says the shift instead of '
     + 'inventing a name', () => {
  // E standard is the top of the ladder; there is no "F standard" anywhere in
  // this repo, and making one up here would be a name nothing else recognises.
  assert.equal(playsInLabel('E standard', 1), 'shifted +1');
  assert.equal(playsInLabel('B standard', -1), 'shifted -1');
  // Drop D's family is two names deep and that is all it is.
  assert.equal(playsInLabel('Drop D', -2), 'shifted -2');
  assert.equal(shiftedTuning('Drop D', -2), null);
});

test('an unrecognised recording tuning degrades rather than throwing -- '
     + 'song.yaml files predate the fixed-choice dropdown', () => {
  assert.equal(playsInLabel('Open G', 0), 'plays in Open G');
  assert.equal(playsInLabel('Open G', -1), 'shifted -1');
  assert.equal(shiftedTuning('Open G', 0), null);
});

// ---- the mirror stays a mirror ----

const pyPath = fileURLToPath(new URL('../../src/woodshed/tuning.py', import.meta.url));
const py = readFileSync(pyPath, 'utf8');

test('KNOWN_TUNINGS names exactly what tuning.py names (this file is a mirror '
     + 'of a Python module and drift is silent)', () => {
  const names = [...py.matchAll(/^\s{4}"([^"]+)":/gm)].map((m) => m[1]);
  assert.ok(names.length >= 8, `parsed too few tuning names out of tuning.py: ${names}`);
  assert.deepEqual([...KNOWN_TUNINGS].sort(), [...new Set(names)].sort());
});

// ---- the caption is re-rendered, not baked into innerHTML ----

const songJsPath = fileURLToPath(new URL('../screens/song.js', import.meta.url));
const src = readFileSync(songJsPath, 'utf8');

test('song.js renders the caption through playsInLabel rather than '
     + 'interpolating recording.tuning into the header once', () => {
  assert.ok(
    /import\s*\{[^}]*\bplaysInLabel\b[^}]*\}\s*from\s*['"]\.\.\/tuning\.js['"]/.test(src),
    'song.js should import playsInLabel from ../tuning.js',
  );
  assert.ok(
    !/plays in \$\{/.test(src),
    'the "plays in …" caption must not be interpolated into the header template -- '
    + 'that is the bug: it renders once and never updates',
  );
});

test("renderShift updates the caption element, so every path that moves the "
     + 'shift (click, key, MIDI) moves the caption too', () => {
  const match = src.match(/function renderShift\(\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'renderShift not found in song.js -- has it been renamed?');
  assert.ok(
    /playsInLabel\(/.test(match[0]),
    'renderShift must set the caption from playsInLabel(...) -- it is the one '
    + 'function every shift change already calls',
  );
});
