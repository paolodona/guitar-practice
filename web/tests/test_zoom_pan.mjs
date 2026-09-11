// web/tests/test_zoom_pan.mjs — #1's test contract, run as a plain Node
// script (`node web/tests/test_zoom_pan.mjs`), same house style as
// test_practice_seek.mjs (see that file's header for why this repo tests
// pixel/time math directly rather than driving a real wheel/pointer event
// through screens/song.js: there is no DOM library here to do that with).
//
// #1's ask is Reaper-style zoom/pan lifted from rambass-live's console.html
// (ReviewApp's zoom/left/span/setLeft/zoomBy/panBy/followTo) -- re-derived
// here rather than imported (CLAUDE.md's cross-reference rule: a different
// repo, lift with attribution, never depend on it) and re-expressed as pure
// functions over an explicit `{zoom, left}` pair rather than console.html's
// mutate-this.zoom/this.left methods, so the arithmetic is testable without
// a DOM the same way computeSeekPosition already is. `left`/`zoom` are
// fractions of the WHOLE RECORDING (console.html's own unit is fractions of
// the one section being reviewed; a woodshed section is a span of the whole
// file rather than a separate normalized unit, so the recording plays the
// role console.html's "section" does).

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  FIT_ZOOM_PAN, MAX_ZOOM, zoomBy, panBy, followTo, zoomSpan, zoomPanView, slicePeaksToWindow,
} from '../timeline.js';

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

function approx(a, b, eps = 1e-9) {
  assert.ok(Math.abs(a - b) < eps, `expected ${a} ~= ${b}`);
}

test('FIT_ZOOM_PAN is the whole recording, unzoomed', () => {
  assert.equal(FIT_ZOOM_PAN.zoom, 1);
  assert.equal(FIT_ZOOM_PAN.left, 0);
  assert.equal(zoomSpan(FIT_ZOOM_PAN), 1);
});

test('zoomBy about the centre halves the span and keeps the anchor fixed', () => {
  const next = zoomBy(FIT_ZOOM_PAN, 2, 0.5);
  approx(next.zoom, 2);
  approx(zoomSpan(next), 0.5);
  // The duration-fraction under anchor 0.5 was 0.5 before the zoom (whole
  // recording); it must still be 0.5 after -- left + anchor*span.
  approx(next.left + 0.5 * zoomSpan(next), 0.5);
});

test('zoomBy about a point near the right edge keeps THAT point fixed, not the centre', () => {
  const next = zoomBy(FIT_ZOOM_PAN, 4, 0.9);
  approx(next.left + 0.9 * zoomSpan(next), 0.9);
});

test('zoomBy never zooms out past 1x (the whole recording is the floor)', () => {
  const next = zoomBy(FIT_ZOOM_PAN, 0.5, 0.5);
  assert.equal(next, FIT_ZOOM_PAN, 'already at the floor -- same reference back, nothing moved');
});

test('zoomBy clamps at MAX_ZOOM', () => {
  let z = FIT_ZOOM_PAN;
  for (let i = 0; i < 50; i++) z = zoomBy(z, 4, 0.5); // way past MAX_ZOOM if unclamped
  approx(z.zoom, MAX_ZOOM);
});

test('zooming in then back out by the inverse factor returns to the start', () => {
  const in_ = zoomBy(FIT_ZOOM_PAN, 3, 0.3);
  const back = zoomBy(in_, 1 / 3, 0.3);
  approx(back.zoom, 1);
  approx(back.left, 0);
});

test('panBy shifts left by whole window-widths of the CURRENT span', () => {
  const zoomed = zoomBy(FIT_ZOOM_PAN, 4, 0); // span 0.25, left 0
  const panned = panBy(zoomed, 1); // one whole window to the right
  approx(panned.left, 0.25);
  approx(panned.zoom, zoomed.zoom);
});

test('panBy clamps at the recording\'s own edges, in both directions', () => {
  const zoomed = zoomBy(FIT_ZOOM_PAN, 4, 0.5); // span 0.25, left 0.375
  const left = panBy(zoomed, -10);
  approx(left.left, 0);
  const right = panBy(zoomed, 10);
  approx(right.left, 1 - zoomSpan(zoomed));
});

test('panBy at the edge already returns the same reference -- nothing moved', () => {
  const atEdge = panBy(FIT_ZOOM_PAN, -1); // zoom 1 has nowhere to pan to at all
  assert.equal(atEdge, FIT_ZOOM_PAN);
});

test('followTo does nothing while the fraction is inside the middle 90% of the window', () => {
  const zoomed = zoomBy(FIT_ZOOM_PAN, 4, 0.5); // span 0.25, left 0.375..0.625
  const mid = followTo(zoomed, 0.5);
  assert.equal(mid, zoomed, 'well inside the window -- same reference, no repaint needed');
});

test('followTo pages the window once the fraction nears either edge', () => {
  const zoomed = zoomBy(FIT_ZOOM_PAN, 4, 0.5); // span 0.25, left 0.375
  const paged = followTo(zoomed, zoomed.left + zoomSpan(zoomed) * 0.96); // just past the 95% mark
  assert.notEqual(paged, zoomed);
  // Re-centred with the fraction 15% of a span in from the new left edge --
  // console.html's own numbers, lifted verbatim.
  approx(paged.left, (zoomed.left + zoomSpan(zoomed) * 0.96) - zoomSpan(zoomed) * 0.15);
});

test('followTo never pages a fully zoomed-out view (span >= 1 -- nowhere to page to)', () => {
  const same = followTo(FIT_ZOOM_PAN, 0.99);
  assert.equal(same, FIT_ZOOM_PAN);
});

test('zoomPanView converts a ZoomPan + duration into the concrete {startS, endS, widthPx} View', () => {
  const zoomed = zoomBy(FIT_ZOOM_PAN, 2, 0.5); // span 0.5, left 0.25
  const v = zoomPanView(zoomed, 200, 800);
  approx(v.startS, 50);
  approx(v.endS, 150);
  assert.equal(v.widthPx, 800);
});

test('zoomPanView at FIT_ZOOM_PAN reproduces today\'s whole-recording view exactly', () => {
  const v = zoomPanView(FIT_ZOOM_PAN, 245.6, 900);
  assert.equal(v.startS, 0);
  assert.equal(v.endS, 245.6);
});

// ---- slicePeaksToWindow: moved here from screens/practice.js so song.js's
// new zoomed view can reuse it too (wave.js's own doc: peaks.peaks always
// spans the whole file, and windowing it down to the current view is
// explicitly the CALLER's job, not wave.js's) ----------------------------

const PEAKS = { level: 10, peaks: Array.from({ length: 10 }, (_, i) => [-i / 10, i / 10]) };

test('slicePeaksToWindow slices proportionally to [startS, endS] over durationS', () => {
  const sliced = slicePeaksToWindow(PEAKS, 20, 50, 100); // ticks 2..5
  assert.deepEqual(sliced.peaks, PEAKS.peaks.slice(2, 5));
  assert.equal(sliced.level, PEAKS.level);
});

test('slicePeaksToWindow across the whole recording returns every tick', () => {
  const sliced = slicePeaksToWindow(PEAKS, 0, 100, 100);
  assert.equal(sliced.peaks.length, PEAKS.peaks.length);
});

test('slicePeaksToWindow degrades to null for missing peaks, an empty array, or no duration', () => {
  assert.equal(slicePeaksToWindow(null, 0, 10, 100), null);
  assert.equal(slicePeaksToWindow({ peaks: [] }, 0, 10, 100), null);
  assert.equal(slicePeaksToWindow(PEAKS, 0, 10, 0), null);
});

// ---- lint-style checks against song.js's own source, same pattern as
// test_song_restart.mjs / test_practice_seek.mjs: no DOM here to drive a
// real wheel/drag gesture through, so these check that song.js actually
// WIRES UP the pure functions above rather than re-deriving the arithmetic
// inline (which would drift from timeline.js's own tests silently) --------

const songJsPath = fileURLToPath(new URL('../screens/song.js', import.meta.url));
const songSrc = readFileSync(songJsPath, 'utf8');

test('song.js imports the zoom/pan machinery from timeline.js rather than reimplementing it', () => {
  assert.ok(/import\s*\{[^}]*\bzoomBy\b[^}]*\}\s*from\s*['"]\.\.\/timeline\.js['"]/.test(songSrc));
  assert.ok(songSrc.includes('panBy('), 'song.js should call timeline.js\'s panBy for alt/shift+wheel');
  assert.ok(songSrc.includes('followTo('), 'song.js should call timeline.js\'s followTo to keep a running playhead on screen');
});

test('the wave-host\'s wheel handler prevents the page from scrolling under the gesture', () => {
  const match = songSrc.match(/function onWaveWheel\(event\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'onWaveWheel not found in song.js -- has it been renamed?');
  assert.ok(match[0].includes('event.preventDefault()'));
  assert.ok(/altKey|shiftKey/.test(match[0]), 'alt or shift+wheel must pan rather than zoom');
});

test('the draggable start/end bars reuse sections.js\'s attachDragHandlers and commit through patchSection, rather than a second drag implementation', () => {
  assert.ok(
    /import\s*\{[^}]*\battachDragHandlers\b[^}]*\}\s*from\s*['"]\.\.\/sections\.js['"]/.test(songSrc),
    'song.js should import attachDragHandlers from sections.js',
  );
  const match = songSrc.match(/function renderWaveDrag\(\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'renderWaveDrag not found in song.js -- has it been renamed?');
  assert.ok(match[0].includes('attachDragHandlers('));
  assert.ok(match[0].includes('patchSection('), 'a committed drag must go through the same patchSection path as every other boundary edit');
});

test('the playhead is moved with a transform, not redrawn per frame via a % left (console.html\'s own .playhead convention)', () => {
  assert.ok(
    /playheadEl\.style\.transform\s*=\s*`translateX/.test(songSrc),
    'renderPlayhead should move the playhead with transform: translateX(...)',
  );
  assert.ok(
    !/playheadEl\.style\.left\s*=/.test(songSrc),
    'the old percentage-left positioning should be gone, not left dead alongside the transform',
  );
});

test('renderWave slices peaks to the current view before drawing -- otherwise a zoomed-in view stretches the WHOLE recording\'s peaks across a narrow window', () => {
  const match = songSrc.match(/function renderWave\(\) \{[\s\S]*?\n  \}/);
  assert.ok(match, 'renderWave not found in song.js -- has it been renamed?');
  assert.ok(match[0].includes('slicePeaksToWindow('), 'renderWave must window peaks to the current (possibly zoomed) view');
});
