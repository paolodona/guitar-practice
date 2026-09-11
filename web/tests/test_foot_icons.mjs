// web/tests/test_foot_icons.mjs — Phase 1.5, Q1's own test contract, run as
// a plain Node script (`node web/tests/test_foot_icons.mjs`), same house
// style as test_seek.mjs/test_ended.mjs/test_capture_review.mjs.
//
// Q1's contract: "a completeness test — every ACTIONS entry that is a foot
// chip has a matching icon — belongs beside actions.js's existing 'one
// action table' discipline: an icon silently missing for a real chip is the
// same class of bug as a missing CC." This imports both real tables
// (actions.js's ACTIONS, practice.js's FOOT_ICONS) and cross-checks them —
// no hand-typed list of action names to drift out of sync with either file.
//
// "Is a foot chip" was simply `cc != null` until restart_section joined the
// strip live 2026-09-10 with no CC of its own (docs/05-foot-control.md's
// six-CC pedal vocabulary is unaffected — see actions.js's own `chip` doc):
// `spec.chip` is now the one predicate, matching screens/practice.js's own
// foot-strip build.

import assert from 'node:assert/strict';
import { ACTIONS } from '../actions.js';
import { FOOT_ICONS } from '../screens/practice.js';

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

test('every ACTIONS entry that is a foot chip has a matching, non-empty FOOT_ICONS entry', () => {
  const footActions = Object.entries(ACTIONS).filter(([, spec]) => spec.chip);
  assert.ok(footActions.length > 0, 'no foot chips found -- ACTIONS itself looks wrong, not this test');
  for (const [name, spec] of footActions) {
    assert.ok(
      typeof FOOT_ICONS[name] === 'string' && FOOT_ICONS[name].length > 0,
      `ACTIONS.${name} (chip: true) has no icon in FOOT_ICONS`
    );
  }
});

test('FOOT_ICONS carries no icon for an action that is not actually a foot chip '
     + '(a stale entry left behind by a renamed/removed action is the same kind of drift, '
     + 'just in the other direction)', () => {
  const footNames = new Set(Object.entries(ACTIONS).filter(([, spec]) => spec.chip).map(([name]) => name));
  for (const name of Object.keys(FOOT_ICONS)) {
    assert.ok(footNames.has(name), `FOOT_ICONS.${name} names an action that is not a foot chip (or does not exist) in ACTIONS`);
  }
});

// ---- #7: a glyph whose own geometry falls outside its own viewBox is ------
// ---- silently clipped, and looks nothing like the intended icon ----------
//
// retract_rep's arc was centred well past the right edge of its `0 0 22 22`
// viewBox (see #7's own writeup: `A7 7 0 1 0` from (15.5,15.5) to (15,6)
// works out to a circle centred near (20.4, 10.5), radius 7 -- spanning x
// 13.4..27.4, more than half of it past x=22). Nothing renders an SVG here
// (no browser, no canvas in this test run), so the only way to catch that
// class of bug unattended is arithmetic: derive the actual circle a `M`+`A`
// pair traces (the same reasoning the bug report itself used) and check its
// extent against the viewBox, the way `renderClock`'s tests check numbers
// against `clock.py` rather than trusting the comment beside them.
//
// Deliberately narrow: this parses exactly the commands retract_rep's own
// path data uses (M, L, absolute; H/h, V/v; and a circular, unrotated A),
// not a general SVG path engine -- FOOT_ICONS' other glyphs (bezier curves,
// `<rect>` elements) are out of scope for what this bug needs.

/** The centre of the circle a `rx ry x-rot large-arc sweep x y` arc traces,
 *  given where it started -- the standard endpoint-to-centre construction,
 *  specialised to a circular (rx === ry), unrotated arc since that covers
 *  every `A` this codebase's icons use. */
function circularArcCenter(x1, y1, x2, y2, r, largeArcFlag, sweepFlag) {
  const d = Math.hypot(x2 - x1, y2 - y1);
  const h = Math.sqrt(Math.max(0, r * r - (d / 2) ** 2));
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const ux = -(y2 - y1) / d;
  const uy = (x2 - x1) / d;
  const sign = largeArcFlag !== sweepFlag ? 1 : -1;
  return [mx + sign * h * ux, my + sign * h * uy];
}

/** The bounding box of an SVG `d` string built only from M/L (absolute),
 *  H/h, V/v and a circular, unrotated A -- see the module doc above for why
 *  that subset is enough. Returns `{minX, maxX, minY, maxY}`. */
function pathBBox(d) {
  const tokens = d.match(/[MLHVAmlhva][^MLHVAmlhva]*/g) ?? [];
  let cx = 0;
  let cy = 0;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  const expand = (x, y) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  };
  for (const tok of tokens) {
    const cmd = tok[0];
    const nums = tok.slice(1).trim().split(/[\s,]+/).filter(Boolean).map(Number);
    if (cmd === 'M' || cmd === 'L') {
      [cx, cy] = nums;
      expand(cx, cy);
    } else if (cmd === 'H') {
      [cx] = nums; expand(cx, cy);
    } else if (cmd === 'h') {
      cx += nums[0]; expand(cx, cy);
    } else if (cmd === 'V') {
      [cy] = nums; expand(cx, cy);
    } else if (cmd === 'v') {
      cy += nums[0]; expand(cx, cy);
    } else if (cmd === 'A' || cmd === 'a') {
      const [rx, , , laf, sf, xArg, yArg] = nums;
      const x2 = cmd === 'a' ? cx + xArg : xArg;
      const y2 = cmd === 'a' ? cy + yArg : yArg;
      const [acx, acy] = circularArcCenter(cx, cy, x2, y2, rx, laf, sf);
      // The arc's own extent, not just its endpoints -- a circle bulges
      // past the chord joining them. Cheap and exact for a circular arc:
      // the full circle's bbox is always a superset of the true arc bbox,
      // and close enough to it here (these are near-full loops) that the
      // slight over-estimate costs nothing against a viewBox check.
      expand(acx - rx, acy - rx);
      expand(acx + rx, acy + rx);
      cx = x2; cy = y2;
      expand(cx, cy);
    }
  }
  return { minX, maxX, minY, maxY };
}

test('circularArcCenter matches a known construction and the bug report\'s own numbers', () => {
  // Ground truth: a circle centred at (11, 11), radius 6.5, arc from -50deg
  // to 220deg (large-arc, sweep=1) -- independently computed from angles,
  // not from this function, in the commit that fixed #7.
  const [cx, cy] = circularArcCenter(15.18, 6.02, 6.02, 6.82, 6.5, 1, 1);
  assert.ok(Math.abs(cx - 11) < 0.01 && Math.abs(cy - 11) < 0.01);

  // The bug itself: `M15.5 15.5A7 7 0 1 0 15 6` -- #7 worked this circle
  // out by hand to be centred near (20.4, 10.5); this function should
  // agree, or the check below is only checking its own arithmetic.
  const [bx, by] = circularArcCenter(15.5, 15.5, 15, 6, 7, 1, 0);
  assert.ok(Math.abs(bx - 20.4) < 0.05 && Math.abs(by - 10.5) < 0.05);
});

test('retract_rep\'s own geometry stays inside its viewBox (#7)', () => {
  const svg = FOOT_ICONS.retract_rep;
  const viewBoxMatch = svg.match(/viewBox="([\d.\s]+)"/);
  assert.ok(viewBoxMatch, 'retract_rep must declare a viewBox to check geometry against');
  const [vx0, vy0, vw, vh] = viewBoxMatch[1].trim().split(/\s+/).map(Number);

  const paths = [...svg.matchAll(/<path\s+d="([^"]+)"[^>]*stroke-width="([\d.]+)"/g)];
  assert.ok(paths.length > 0, 'retract_rep has no stroked path to check');
  for (const [, d, strokeWidthStr] of paths) {
    const strokeWidth = Number(strokeWidthStr);
    const { minX, maxX, minY, maxY } = pathBBox(d);
    // round-cap strokes extend half the stroke width past the geometric
    // path in every direction -- the same margin the icon needs to clear.
    const margin = strokeWidth / 2;
    assert.ok(
      minX - margin >= vx0 && maxX + margin <= vx0 + vw
      && minY - margin >= vy0 && maxY + margin <= vy0 + vh,
      `retract_rep path "${d}" (with ${strokeWidth}px stroke) spans `
      + `x:[${(minX - margin).toFixed(2)}, ${(maxX + margin).toFixed(2)}] `
      + `y:[${(minY - margin).toFixed(2)}, ${(maxY + margin).toFixed(2)}], `
      + `outside viewBox "${viewBoxMatch[1]}"`,
    );
  }
});
