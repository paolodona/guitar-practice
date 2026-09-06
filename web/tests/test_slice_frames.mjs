// web/tests/test_slice_frames.mjs — Phase 1.5, S3's test contract, run as a
// plain Node script (`node web/tests/test_slice_frames.mjs`), same house
// style as test_seek.mjs/test_ended.mjs/test_practice_seek.mjs.
//
// computeSliceFrames is the one function that knows how a SectionLoad's
// absolute source-second bounds become frame indices into a buffer that may
// itself start `clipOffsetS` seconds into the original recording (S3's
// "Guitar only" toggle: the isolated-guitar clip GET /api/stem/<slug>/
// <section> serves is NOT the whole recording, unlike the mix's own GET
// /api/audio/<slug>). This is pure and exported, so it is tested directly
// -- no fetch, no decodeAudioData, no AudioWorkletNode.
//
// The load-bearing assertion: clipOffsetS=0 reproduces loadSection's
// pre-S3 behaviour EXACTLY (same numbers a hand check of the old inline
// math would give), and a non-zero clipOffsetS still returns
// sliceStartS/sliceEndS in ABSOLUTE source seconds -- seek()'s own
// contract (player.js's module doc) -- even though rawStartFrame/endFrame
// themselves are relative to the fetched (offset) buffer.

import assert from 'node:assert/strict';
import { computeSliceFrames } from '../player.js';

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

const SR = 48000;

test('clipOffsetS=0 (the mix, unchanged from before S3): plain start/end/pre-roll', () => {
  const section = { startS: 100, endS: 130, preRollS: 4, preRollEveryPass: false };
  const { rawStartFrame, loopStartFrame, endFrame, sliceStartS, sliceEndS } =
    computeSliceFrames(section, SR, 1e9);
  assert.equal(rawStartFrame, Math.round(96 * SR)); // 100 - 4
  assert.equal(loopStartFrame, Math.round(4 * SR)); // preRollS, unclamped
  assert.equal(endFrame, Math.round(130 * SR));
  assert.equal(sliceStartS, 96);
  assert.equal(sliceEndS, 130);
});

test('clipOffsetS=0, pre-roll would run off the start of the file: clamped, not negative', () => {
  const section = { startS: 1, endS: 10, preRollS: 5, preRollEveryPass: false };
  const { rawStartFrame, loopStartFrame } = computeSliceFrames(section, SR, 1e9);
  assert.equal(rawStartFrame, 0);
  assert.equal(loopStartFrame, Math.round(1 * SR)); // 5 - (1 - 5) shrunk by the 4s clamp -> 1s left
});

test('clipOffsetS=0, preRollEveryPass forces loopStartFrame to 0', () => {
  const section = { startS: 100, endS: 130, preRollS: 4, preRollEveryPass: true };
  const { loopStartFrame } = computeSliceFrames(section, SR, 1e9);
  assert.equal(loopStartFrame, 0);
});

test('endFrame is clamped to the decoded buffer length, same as before S3', () => {
  const section = { startS: 0, endS: 1000, preRollS: 0, preRollEveryPass: false };
  const decodedLength = Math.round(10 * SR);
  const { endFrame } = computeSliceFrames(section, SR, decodedLength);
  assert.equal(endFrame, decodedLength);
});

test('a non-zero clipOffsetS re-bases raw/end frame onto the fetched clip, not the recording', () => {
  // isolate_guitar's own clamp: clip covers [start_s - pre_roll_s, end_s]
  // -- so a caller passes clipOffsetS = max(0, startS - preRollS), and the
  // fetched buffer's own sample 0 IS that source position.
  const section = {
    startS: 100, endS: 130, preRollS: 4, preRollEveryPass: false, clipOffsetS: 96,
  };
  const { rawStartFrame, endFrame } = computeSliceFrames(section, SR, 1e9);
  assert.equal(rawStartFrame, 0); // 100 - 4 - 96 == 0 -- the clip's own very first sample
  assert.equal(endFrame, Math.round((130 - 96) * SR)); // 34s into the clip
});

test('a non-zero clipOffsetS still returns sliceStartS/sliceEndS in ABSOLUTE source seconds', () => {
  // seek()'s own contract (player.js module doc): sourceSeconds is
  // absolute, unaffected by which physical file backs playback.
  const mixSection = { startS: 100, endS: 130, preRollS: 4, preRollEveryPass: false };
  const guitarSection = { ...mixSection, clipOffsetS: 96 };

  const mix = computeSliceFrames(mixSection, SR, 1e9);
  const guitar = computeSliceFrames(guitarSection, SR, 1e9);

  assert.equal(guitar.sliceStartS, mix.sliceStartS);
  assert.equal(guitar.sliceEndS, mix.sliceEndS);
  // ...even though the underlying frame indices are NOT the same numbers
  // (one is relative to the recording, the other to the isolated clip).
  assert.notEqual(guitar.rawStartFrame, mix.rawStartFrame);
});

test('a non-zero clipOffsetS still clamps a pre-roll that would run off the CLIP\'s own start', () => {
  // isolate_guitar itself clamps clip_start_s at 0 -- so a section whose
  // pre-roll reaches before start_s=1 (clipOffsetS=0, same as the earlier
  // recording-clamp case) shrinks loopStartFrame identically whether the
  // clamp happened against the recording or against an isolated clip.
  const section = {
    startS: 1, endS: 10, preRollS: 5, preRollEveryPass: false, clipOffsetS: 0,
  };
  const { rawStartFrame, loopStartFrame } = computeSliceFrames(section, SR, 1e9);
  assert.equal(rawStartFrame, 0);
  assert.equal(loopStartFrame, Math.round(1 * SR));
});
