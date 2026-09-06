/**
 * timeline.js — the source-seconds <-> pixel mapping shared by the
 * waveform, the section lanes and the bar/beat grid. D2's file.
 *
 * Lifted concepts, named in the plan's "Traps this phase must avoid" and
 * pointing at rambass-live/src/rambass/console.html:25-29 / :128-131 (two
 * alignment bugs already paid for once, in a repo this one cannot import
 * from — the fix is re-derived here, not copied):
 *
 *   - ONE gutter constant, shared by every caller that lays canvases (or
 *     an svg beside a canvas) side by side, rather than each screen
 *     inventing its own border/measurement convention and drifting a
 *     device pixel apart from its neighbour. `CANVAS_BORDER_PX` below
 *     *is* that constant: a canvas that reserves a 1px transparent border
 *     from the moment it is first sized never resizes its drawable area
 *     later when a real (hover/selection) border is applied on top of it.
 *   - `border: 1px solid transparent` on every canvas, so a later hover/
 *     selection border does not shift the drawable area by a device
 *     pixel. sizeCanvas applies this unconditionally, every call, rather
 *     than trusting each caller's stylesheet to remember it.
 *   - device pixel ratio is FIXED at 2, never `window.devicePixelRatio` —
 *     the point is one render that looks identical on every monitor, not
 *     one tuned to whichever laptop it was built on. See sizeCanvas.
 *
 * Two more traps, restated so a future reader does not have to dig for
 * them in the plan:
 *   - viewX must NOT clamp a mark outside the view to the nearest edge. A
 *     clamped bar line draws a bar where there isn't one. Return the true
 *     (possibly negative, possibly > widthPx) pixel value; the caller
 *     (drawGrid, wave.js, sections.js) decides whether to skip it.
 *   - zoom changes the View only. It must never change what a loop plays —
 *     that comes from the server's Render (clock.py), untouched by this
 *     file.
 */

/**
 * @typedef {Object} View
 * @property {number} startS - source seconds at the canvas's left edge
 * @property {number} endS - source seconds at the canvas's right edge
 * @property {number} widthPx - CSS pixel width of the canvas (not device px)
 */

/**
 * @typedef {Object} Grid
 * @property {number[]} bars - source-second positions of bar lines
 * @property {number[]} beats - source-second positions of beat lines
 *
 * Phase 1 populates bars/beats from tempo.bpm + grid_offset_s (CLAUDE.md:
 * "Bars are displayed, derived from tempo.bpm + grid_offset_s"). Phase 0
 * callers may pass {bars: [], beats: []} — an empty grid, not an error.
 */

/**
 * The one gutter constant every caller aligns by (see the module doc's
 * first trap). It is also literally the border width sizeCanvas applies,
 * so "the gutter" and "the reserved border" are the same number by
 * construction — there is nowhere for the two to drift apart.
 */
export const CANVAS_BORDER_PX = 1;

/**
 * Source seconds -> CSS pixel x, linear over [view.startS, view.endS] ->
 * [0, view.widthPx]. UNCLAMPED: a sourceS outside the view range returns a
 * pixelX outside [0, view.widthPx]. Do not clamp — see the module doc's
 * trap.
 * @param {number} sourceS
 * @param {View} view
 * @returns {number} pixelX
 */
export function viewX(sourceS, view) {
  const span = view.endS - view.startS;
  // A zero-width (or inverted) view has no meaningful mapping. Rather than
  // divide by zero and hand every caller a NaN to check for, collapse to
  // the left edge — a degenerate view is a caller bug upstream, and this
  // keeps the failure visible (everything piles up at x=0) instead of
  // poisoning arithmetic downstream with NaN.
  if (span === 0) return 0;
  return ((sourceS - view.startS) / span) * view.widthPx;
}

/**
 * Inverse of viewX: a CSS pixel x -> source seconds.
 * @param {number} pixelX
 * @param {View} view
 * @returns {number} sourceS
 */
export function positionAt(pixelX, view) {
  // Same degenerate-view guard as viewX, mirrored: a zero-width canvas
  // maps every pixel back to the view's start rather than dividing by
  // zero.
  if (view.widthPx === 0) return view.startS;
  return view.startS + (pixelX / view.widthPx) * (view.endS - view.startS);
}

/**
 * Size *canvas* for crisp rendering at a FIXED device pixel ratio (default
 * 2, NOT `window.devicePixelRatio` — see the module doc's trap). Sets
 * canvas.width/height to cssWidth*dpr/cssHeight*dpr, canvas.style.width/
 * height to the CSS size, and scales the returned context so every other
 * function in this file and in wave.js draws in CSS-pixel coordinates.
 *
 * Applies CANVAS_BORDER_PX as a transparent border unconditionally,
 * before measuring, so cssWidth/cssHeight already include it and every
 * canvas this module ever sizes reserves the same gutter whether or not
 * its caller's stylesheet remembered to.
 * @param {HTMLCanvasElement} canvas
 * @param {number} [dpr=2]
 * @returns {{ctx: CanvasRenderingContext2D, cssWidth: number, cssHeight: number}}
 */
export function sizeCanvas(canvas, dpr = 2) {
  canvas.style.border = `${CANVAS_BORDER_PX}px solid transparent`;

  // getBoundingClientRect (border-box, per the design system's global
  // `*{box-sizing:border-box}`) so cssWidth/cssHeight are the box the
  // border was just reserved on, not the pre-border content box.
  const rect = canvas.getBoundingClientRect();
  const cssWidth = rect.width;
  const cssHeight = rect.height;

  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;

  const ctx = canvas.getContext('2d');
  // setTransform rather than scale(): idempotent across repeated calls
  // (e.g. a window resize re-running sizeCanvas) — scale() would compound
  // with whatever transform a previous call left behind.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  return { ctx, cssWidth, cssHeight };
}

//: --line — the bar grid line (design system tokens table).
const BAR_LINE_COLOR = '#26302E';
//: --hairline — dividers and the beat grid line, one step dimmer than a bar.
const BEAT_LINE_COLOR = '#1C2523';

/**
 * Draw bar/beat grid lines onto *ctx* for *view*. A mark whose viewX()
 * falls outside [0, view.widthPx] must be skipped, never clamped into
 * view (same trap as viewX itself — drawGrid is one of its callers).
 *
 * Bars are drawn last (on top of beats) so a position that is both a bar
 * and a beat line reads as a bar, matching the CSS trough backdrop's own
 * layering (design/_css.txt's bar gradient is listed before the beat
 * gradient, i.e. painted over it).
 *
 * **Found live 2026-09-06**: a song viewed at whole-song zoom (the only
 * zoom level this phase has — `song.js`'s own `view()` always spans
 * `[0, durationS]`) with no real tempo analysed yet still has SOME
 * `tempo.bpm` (`cli.py`'s placeholder default, 120) — `computeGrid` has no
 * way to know that's a guess, so it dutifully returns one mark per beat
 * across the whole file. At whole-song width that is often several
 * hundred beat lines landing closer together than a device pixel, which
 * does not read as "a beat grid" — it reads as solid vertical stripes
 * covering the whole waveform, indistinguishable from a rendering bug.
 * `MIN_BEAT_PX`/`MIN_BAR_PX` skip a whole TIER of marks (never a decimated
 * subset — a beat line every 3rd beat is not a beat grid, it is a wrong
 * one) once consecutive marks would land closer than that many CSS
 * pixels apart; both empty or single-entry `grid.beats`/`grid.bars` draw
 * nothing, since spacing needs at least two marks to measure.
 * @param {CanvasRenderingContext2D} ctx
 * @param {View} view
 * @param {Grid} grid
 */
export function drawGrid(ctx, view, grid) {
  const canvas = ctx.canvas;
  // CSS-pixel height to draw full-bleed vertical lines in, matching the
  // coordinate space sizeCanvas's setTransform already put ctx into.
  const height = canvas.clientHeight || canvas.height;

  ctx.save();
  ctx.lineWidth = 1;

  if (marksAreLegibleAt(grid.beats, view, MIN_BEAT_PX)) {
    ctx.strokeStyle = BEAT_LINE_COLOR;
    for (const t of grid.beats) {
      drawVerticalLine(ctx, viewX(t, view), height, view.widthPx);
    }
  }

  if (marksAreLegibleAt(grid.bars, view, MIN_BAR_PX)) {
    ctx.strokeStyle = BAR_LINE_COLOR;
    for (const t of grid.bars) {
      drawVerticalLine(ctx, viewX(t, view), height, view.widthPx);
    }
  }

  ctx.restore();
}

/** Beat lines closer together than this many CSS px read as solid
 * stripes, not a grid -- see drawGrid's own "Found live" note. */
const MIN_BEAT_PX = 4;
/** Bars are the coarser, more load-bearing mark -- allowed to sit closer
 * together than beats before the same "solid stripe" problem applies. */
const MIN_BAR_PX = 2;

/** Whether consecutive entries in *marks* (assumed evenly spaced, as
 * `computeGrid`'s own output always is) land at least *minPx* CSS pixels
 * apart at *view*'s current scale. Fewer than two marks has no spacing to
 * measure and is treated as legible (there is nothing to crowd). */
function marksAreLegibleAt(marks, view, minPx) {
  if (marks.length < 2) return true;
  const pxPerSecond = view.widthPx / Math.max(1e-9, view.endS - view.startS);
  const spacingPx = (marks[1] - marks[0]) * pxPerSecond;
  return spacingPx >= minPx;
}

/** One crisp 1px vertical line at *x*, skipped entirely when outside [0, widthPx]. */
function drawVerticalLine(ctx, x, height, widthPx) {
  if (x < 0 || x > widthPx) return; // the viewX trap: skip, never clamp
  // +0.5 lands a 1px stroke exactly on a device pixel boundary instead of
  // straddling two and rendering as a 2px blur.
  const px = Math.round(x) + 0.5;
  ctx.beginPath();
  ctx.moveTo(px, 0);
  ctx.lineTo(px, height);
  ctx.stroke();
}

/**
 * Build a {@link Grid} from a song's tempo -- bar/beat positions in SOURCE
 * seconds, over [0, durationS]. Phase 1, G1: the function nothing built
 * yet (Phase 0's `drawGrid` above was written ahead of it, expecting one).
 *
 * Degrades to an empty grid — never throws, never fabricates a fake evenly-
 * spaced ruler — exactly when CLAUDE.md's invariant says to: `bpm <= 0` or
 * absent, or a non-positive duration (a needs-audio song). Every caller
 * (drawGrid, the bar ruler, sections.js's snapping) already treats an empty
 * grid as "nothing to draw / nothing to snap to", so this is the one place
 * that decision needs to live.
 *
 * `tempo.grid_offset_s` is where bar 1 beat 1 lands in the file — it can be
 * negative-relative in the sense that the FIRST on-grid beat at or after
 * 0 is not necessarily beat index 0; `firstIndex` below is the smallest
 * (possibly negative) beat index landing at or after source second 0, so a
 * grid_offset_s of, say, 1.3s with a 0.5s beat still starts counting from
 * the right index instead of silently skipping into the second bar.
 * @param {{bpm: number, grid_offset_s: number, time_signature: string}} tempo
 * @param {number} durationS
 * @returns {Grid}
 */
export function computeGrid(tempo, durationS) {
  if (!tempo || !(tempo.bpm > 0) || !(durationS > 0)) return { bars: [], beats: [] };

  const secPerBeat = 60 / tempo.bpm;
  const offset = tempo.grid_offset_s ?? 0;
  const beatsPerBarRaw = parseInt(String(tempo.time_signature ?? '4/4').split('/')[0], 10);
  const beatsPerBar = Number.isFinite(beatsPerBarRaw) && beatsPerBarRaw > 0 ? beatsPerBarRaw : 4;

  const bars = [];
  const beats = [];
  const firstIndex = Math.ceil((0 - offset) / secPerBeat);
  for (let i = firstIndex, t = offset + i * secPerBeat; t <= durationS; i++, t += secPerBeat) {
    if (t < 0) continue; // floating-point slop at the boundary -- skip, don't clamp
    beats.push(t);
    if (((i % beatsPerBar) + beatsPerBar) % beatsPerBar === 0) bars.push(t);
  }
  return { bars, beats };
}

/**
 * Mirrors `woodshed.sections.snap` (the same algorithm, re-derived client-
 * side for a live drag — a network round trip per pointermove is not an
 * option). Snaps *t* to the nearest value in *marks* within *toleranceS*;
 * returns *t* unchanged when *mode* is `'free'` or *marks* is empty
 * (identical degrade to the Python original, including the "don't even
 * validate mode" shortcut it documents for a free caller).
 * @param {number} t
 * @param {number[]} marks
 * @param {string} mode
 * @param {number} [toleranceS=0.12]
 * @returns {number}
 */
export function snapToGrid(t, marks, mode, toleranceS = 0.12) {
  if (mode === 'free' || !marks.length) return t;
  let nearest = marks[0];
  let nearestDist = Math.abs(nearest - t);
  for (const mark of marks) {
    const dist = Math.abs(mark - t);
    if (dist < nearestDist) {
      nearest = mark;
      nearestDist = dist;
    }
  }
  return nearestDist <= toleranceS ? nearest : t;
}
