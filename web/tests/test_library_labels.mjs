// web/tests/test_library_labels.mjs — M3's two pure helpers. The rest of
// screens/library.js is DOM (see test_seek.mjs's note on why this repo's
// front-end tests stop at the pure edge), but these two are exactly the
// kind of formatting that goes wrong quietly: a length that reads 4:9
// instead of 4:09, or initials that throw on a song with no artist.

import assert from 'node:assert/strict';
import { initials, lengthLabel } from '../screens/library.js';

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`ok - ${name}`); }
  catch (err) { failures += 1; console.error(`FAIL - ${name}`); console.error(err); }
}

test('lengthLabel is m:ss, zero-padded', () => {
  assert.equal(lengthLabel(269), '4:29');
  assert.equal(lengthLabel(249), '4:09');
  assert.equal(lengthLabel(0), '0:00');
  assert.equal(lengthLabel(undefined), '0:00');
});

test('initials prefer the artist, and survive a song with neither', () => {
  // First letters of the first two words -- the artboard's own "RH" for
  // Red Hot Chili Peppers, "DS" for Dire Straits.
  assert.equal(initials({ artist: 'Red Hot Chili Peppers', title: "Can't Stop" }), 'RH');
  assert.equal(initials({ artist: 'Dire Straits', title: 'Sultans of Swing' }), 'DS');
  assert.equal(initials({ artist: 'Prince', title: 'Kiss' }), 'PR');
  assert.equal(initials({ artist: '', title: 'Untitled' }), 'UN');
  assert.equal(initials({}), '?');
});

if (failures) process.exitCode = 1;
