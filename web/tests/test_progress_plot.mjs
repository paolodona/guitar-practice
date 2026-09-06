// web/tests/test_progress_plot.mjs — K2's arithmetic, tested where it can
// be: screens/progress.js's `plot`, plus the two label formatters. A plot
// that is off by one on its domain does not fail, it silently flattens or
// clips a chart, which is exactly the kind of wrong that looks fine.
//
// mount()/render() need a DOM and are not exercised here (this repo has no
// DOM library; see test_seek.mjs's note on the trade).

import assert from 'node:assert/strict';
import { plot, relativeLabel, hoursLabel } from '../screens/progress.js';

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`ok - ${name}`); }
  catch (err) { failures += 1; console.error(`FAIL - ${name}`); console.error(err); }
}

test('plot spans the full width and inverts y (0 at the bottom)', () => {
  const { points } = plot([0, 1], { width: 100, height: 50, pad: 5, min: 0, max: 1 });
  assert.deepEqual(points[0], [5, 45]);
  assert.deepEqual(points[1], [95, 5]);
});

test('plot holds a fixed domain so two rows are comparable by eye', () => {
  // A section that crawled 50 -> 55 must NOT look like one that went
  // 50 -> 100. Auto-scaling per row would make them identical.
  const crawl = plot([50, 55], { width: 100, height: 50, pad: 0, min: 40, max: 110 });
  const leap = plot([50, 100], { width: 100, height: 50, pad: 0, min: 40, max: 110 });
  assert.ok(crawl.points[1][1] > leap.points[1][1]);
});

test('plot clamps out-of-domain values instead of drawing off the chart', () => {
  const { points } = plot([-1, 500], { width: 100, height: 50, pad: 0, min: 0, max: 1 });
  assert.equal(points[0][1], 50);
  assert.equal(points[1][1], 0);
});

test('plot of one point sits at the padding, and has no area', () => {
  const one = plot([0.5], { width: 100, height: 50, pad: 4, min: 0, max: 1 });
  assert.equal(one.points.length, 1);
  assert.equal(one.points[0][0], 4);
  assert.equal(one.area, '');
});

test('plot of nothing draws nothing rather than NaN', () => {
  const none = plot([], { width: 100, height: 50 });
  assert.deepEqual(none.points, []);
  assert.equal(none.d, '');
});

test('relativeLabel says never, today, yesterday, days, weeks', () => {
  assert.equal(relativeLabel(null), 'never');
  assert.equal(relativeLabel(0.4), 'today');
  assert.equal(relativeLabel(1.2), 'yesterday');
  assert.equal(relativeLabel(6), '6 days');
  assert.equal(relativeLabel(21), '3 weeks');
});

test('hoursLabel is h:mm, zero-padded', () => {
  assert.equal(hoursLabel(252), '4:12');
  assert.equal(hoursLabel(5), '0:05');
  assert.equal(hoursLabel(0), '0:00');
  assert.equal(hoursLabel(-3), '0:00');
});

if (failures) process.exitCode = 1;
