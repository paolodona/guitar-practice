/**
 * wave.js — the two-path clip-path waveform (design system: "Two
 * mechanics worth lifting exactly"). D2's file.
 *
 * The bar/beat trough grid is CSS `background-image` on the container —
 * repeating-linear-gradient pairs, see design/_css.txt — NOT drawn by this
 * module. This module draws only the waveform ticks: one <svg> containing
 * the SAME `d` path twice (each tick `M{x} {top}V{bottom}`, independent
 * vertical strokes, no curves, no fill — one DOM node for ~300 ticks), once
 * unplayed and once played with `clip-path: inset(0 <remaining>% 0 0)`.
 * JS writes exactly one percentage per frame (see the design system's
 * "Everything on the practice screen is driven by one 0-1 pass fraction"
 * rule) — playedFraction is that fraction, computed by the caller (the
 * ring, the playhead and this all read the SAME number; there is no
 * second source of progress).
 *
 * Per-screen tuning of height/tick-spacing/stroke-width/grid is the
 * CALLER's job, not this module's — practice mode is 104px trough / 5.9
 * tick spacing / 3.32 stroke (design/Main.dc.html), song page is 132px /
 * 2.8 / 1.41 with the played copy in grey (design/SongPage.dc.html: the
 * unplayed stroke there is #3F544E, not the practice screen's #4C635C —
 * the artboard, not the token table's --recessive, is what was built
 * against, per the ground-truth rule for this phase) rather than accent,
 * because the song page's accent is already spent on the loop-region
 * highlight and the sheet forbids two accents in one object.
 * PRACTICE_WAVE_OPTS / SONG_WAVE_OPTS below are those two artboards'
 * exact numbers, exported so screens/practice.js and screens/song.js (D6)
 * do not have to retype magic numbers this module already had to read off
 * the same two files.
 *
 * Peaks payload shape (server.py's GET /api/peaks/<slug>, produced by
 * peaks.py's write_peaks): {"level": number, "peaks": [[min, max], ...]}.
 * peaks.py's `level` is a bucket COUNT over the whole recording (1024 /
 * 4096 / 16384), not a duration, and the endpoint has no start/end query
 * params — so `peaks.peaks` always spans a whole file end to end. This
 * module therefore treats whatever array it is handed as spanning exactly
 * [view.startS, view.endS]: slicing a whole-song peaks array down to one
 * section's window (practice mode) is the CALLER's job, done with
 * information (song.yaml's duration_s) that this module is never given.
 * That is also why the SVG's viewBox width is derived from
 * `peaks.length * tickSpacing` rather than from `view.widthPx` — the
 * viewBox is an intrinsic resolution independent of whatever CSS width
 * the trough happens to render at; `preserveAspectRatio="none"` is what
 * stretches it to fit, same as the artboard's fixed 1776/1180-unit
 * viewBoxes stretch to `width:100%` in a real browser window.
 *
 * The endpoint 404s when peaks are not built yet for a song — the CALLER
 * turns that into `peaks: null` here, and drawWave must degrade to
 * clearing the trough (drawing nothing) rather than throw.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

// A fixed, arbitrary normalization height for the viewBox's Y axis. It
// does NOT need to match the trough's real CSS height (104px, 132px) —
// preserveAspectRatio="none" stretches X and Y independently, so any
// consistent choice here renders identically. 100 just makes the
// amplitude math below read as a percentage.
const VBOX_HEIGHT = 100;
// Keeps a full-scale peak (+-1) a few units short of the trough's edge,
// matching the artboard ticks (which never quite touch top/bottom either).
const VBOX_PAD = 4;

/** Practice mode's exact tuning, read off design/Main.dc.html. */
export const PRACTICE_WAVE_OPTS = Object.freeze({
  tickSpacing: 5.9,
  strokeWidth: 3.32,
  unplayedColor: 'var(--recessive, #4C635C)',
  playedColor: 'var(--accent, #E0913F)',
});

/**
 * Song page's exact tuning, read off design/SongPage.dc.html — note the
 * unplayed colour there is NOT --recessive; the artboard uses a slightly
 * different, denser grey (#3F544E) at this smaller stroke width, and the
 * "played" copy is grey (#7E9089) rather than accent (see module doc).
 */
export const SONG_WAVE_OPTS = Object.freeze({
  tickSpacing: 2.8,
  strokeWidth: 1.41,
  unplayedColor: '#3F544E',
  playedColor: '#7E9089',
});

/**
 * @typedef {Object} WaveOpts
 * @property {number} [tickSpacing] - spacing between ticks, in the SVG
 *   viewBox's own units (the viewBox is stretched to the container via
 *   preserveAspectRatio="none", so this is not a CSS pixel)
 * @property {number} [strokeWidth]
 * @property {string} [playedColor]
 * @property {string} [unplayedColor]
 */

/**
 * Render *peaks* into *svgRoot* (an already-mounted, empty <svg> element)
 * for *view*, with the played portion clipped at *playedFraction* (0 =
 * nothing played yet, 1 = fully played).
 *
 * Degrades to clearing *svgRoot* when *peaks* is null (the caller's signal
 * that GET /api/peaks/<slug> 404'd) — never throws on that path; a song
 * with no cached peaks yet gets an empty trough, not a broken screen.
 *
 * @param {SVGSVGElement} svgRoot
 * @param {{level: number, peaks: [number, number][]} | null} peaks
 * @param {import('./timeline.js').View} view
 * @param {number} playedFraction - 0..1
 * @param {WaveOpts} [opts]
 */
export function drawWave(svgRoot, peaks, view, playedFraction, opts = {}) {
  clearSvg(svgRoot);

  if (!peaks || !Array.isArray(peaks.peaks) || peaks.peaks.length === 0) {
    // No cached peaks (404'd), or an empty array — an empty trough, not a
    // broken screen. Nothing further to draw.
    return;
  }

  const tickSpacing = opts.tickSpacing ?? PRACTICE_WAVE_OPTS.tickSpacing;
  const strokeWidth = opts.strokeWidth ?? PRACTICE_WAVE_OPTS.strokeWidth;
  const unplayedColor = opts.unplayedColor ?? PRACTICE_WAVE_OPTS.unplayedColor;
  const playedColor = opts.playedColor ?? PRACTICE_WAVE_OPTS.playedColor;

  const ticks = peaks.peaks;
  const vbWidth = Math.max(ticks.length * tickSpacing, 1);

  svgRoot.setAttribute('viewBox', `0 0 ${round1(vbWidth)} ${VBOX_HEIGHT}`);
  svgRoot.setAttribute('preserveAspectRatio', 'none');

  const d = buildTickPath(ticks, tickSpacing);

  const unplayedPath = makeTickPath(d, unplayedColor, strokeWidth);
  svgRoot.appendChild(unplayedPath);

  const playedPath = makeTickPath(d, playedColor, strokeWidth);
  const frac = clamp01(playedFraction);
  const remainingPct = round1((1 - frac) * 100);
  // inset(top right bottom left): clip away `remaining%` from the right,
  // leaving the played (accent) copy visible from the left edge up to
  // playedFraction — see the design system's exact mechanic.
  playedPath.setAttribute('clip-path', `inset(0 ${remainingPct}% 0 0)`);
  svgRoot.appendChild(playedPath);
}

/** Build the shared `d` attribute once; both paths (played/unplayed) reuse it. */
function buildTickPath(ticks, tickSpacing) {
  let d = '';
  for (let i = 0; i < ticks.length; i++) {
    const [mn, mx] = ticks[i];
    const x = round1((i + 0.5) * tickSpacing);
    const top = round1(ampToY(mx));
    const bottom = round1(ampToY(mn));
    d += `M${x} ${top}V${bottom}`;
  }
  return d;
}

/**
 * A bucket's min/max amplitude (assumed roughly in [-1, 1], the normalized
 * sample range peaks.py bucketed from) to a Y coordinate in the viewBox's
 * VBOX_HEIGHT-unit space, silence at the vertical centre.
 */
function ampToY(v) {
  const clamped = Math.max(-1, Math.min(1, v));
  const half = VBOX_HEIGHT / 2;
  return half - clamped * (half - VBOX_PAD);
}

function makeTickPath(d, color, strokeWidth) {
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', d);
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', color);
  path.setAttribute('stroke-width', String(strokeWidth));
  return path;
}

function clamp01(x) {
  return Math.max(0, Math.min(1, x));
}

function round1(x) {
  return Math.round(x * 10) / 10;
}

function clearSvg(svgRoot) {
  while (svgRoot.firstChild) {
    svgRoot.removeChild(svgRoot.firstChild);
  }
}
