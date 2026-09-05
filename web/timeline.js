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

  ctx.strokeStyle = BEAT_LINE_COLOR;
  for (const t of grid.beats) {
    drawVerticalLine(ctx, viewX(t, view), height, view.widthPx);
  }

  ctx.strokeStyle = BAR_LINE_COLOR;
  for (const t of grid.bars) {
    drawVerticalLine(ctx, viewX(t, view), height, view.widthPx);
  }

  ctx.restore();
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
