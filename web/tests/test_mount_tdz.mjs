// web/tests/test_mount_tdz.mjs — the screens' mount() functions are long,
// and they interleave two kinds of line: `function foo() {...}` helpers,
// which JS hoists, and `let x = foo()` bindings, which it does not. A
// helper declared anywhere may be CALLED anywhere; a `let` may only be
// READ after its own line has run. Mixing the two is a blank screen.
//
// FOUND LIVE 2026-09-12, Paolo: the practice screen went blank with one
// console line, `Uncaught (in promise) ReferenceError: Cannot access
// 'engineReady' before initialization`, thrown from audibleSpeedPct()
// (added the day before, to fix the two-clock speed bug) by way of
// mount()'s own `let cosmeticPreRoll = preRollPlaybackSeconds()`. The
// three engine bindings lived 600 lines further down, beside
// ensureEngine(), which was the right place to READ them and the wrong
// place to DECLARE them. The throw happens inside route()'s await, so it
// surfaces as an unhandled rejection and an empty <div> -- no error
// screen, nothing in the server log.
//
// This checks the whole class rather than that one binding: for each
// screen, every statement mount() executes DIRECTLY (not from inside a
// callback, which runs later and is allowed to see everything) is
// expanded through the local helpers it calls, and any read of a
// let/const declared further down is a failure.
//
// Like test_practice_speed.mjs and test_lead_in_removed.mjs this reads
// the source rather than running it: mount() wants a real DOM, and the
// deliberate trade there is the same one here.

import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

const screensDir = fileURLToPath(new URL('../screens/', import.meta.url));

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(err);
  }
}

/** Blank comments and string/template bodies, preserving offsets and lines. */
function blankNoise(src) {
  const out = src.split('');
  const keep = (i) => { if (out[i] !== '\n') out[i] = ' '; };
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    const next = src[i + 1];
    if (c === '/' && next === '/') {
      while (i < src.length && src[i] !== '\n') keep(i++);
    } else if (c === '/' && next === '*') {
      const end = src.indexOf('*/', i + 2);
      const stop = end === -1 ? src.length : end + 2;
      while (i < stop) keep(i++);
    } else if (c === '"' || c === "'" || c === '`') {
      const quote = c;
      i++; // keep the opening quote so the token still reads as a literal
      while (i < src.length) {
        if (src[i] === '\\') { keep(i); keep(i + 1); i += 2; continue; }
        if (src[i] === quote) { i++; break; }
        keep(i++);
      }
    } else {
      i++;
    }
  }
  return out.join('');
}

/** Offset just past the `{` opening `signature`'s body, and the body's end. */
function blockAfter(code, from) {
  const open = code.indexOf('{', from);
  assert.ok(open !== -1, 'no block found');
  let depth = 0;
  for (let i = open; i < code.length; i++) {
    if (code[i] === '{') depth++;
    else if (code[i] === '}') {
      depth--;
      if (depth === 0) return { open: open + 1, close: i };
    }
  }
  throw new Error('unbalanced braces');
}

/**
 * Blank out every nested function/arrow BODY inside `text`, leaving the
 * rest. A callback's contents do not run while mount() is still
 * executing, so a `let` read from inside one is not a dead-zone read --
 * counting them would fail every screen for no reason.
 */
function blankCallbackBodies(text) {
  const out = text.split('');
  const blank = (from, to) => {
    for (let j = from; j < to; j++) if (out[j] !== '\n') out[j] = ' ';
  };
  const endOfBrace = (at) => {
    let depth = 0;
    for (let i = at; i < text.length; i++) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}' && --depth === 0) return i;
    }
    return text.length;
  };
  const isWord = (i) => /[\w$]/.test(text[i] ?? '');

  for (let i = 0; i < text.length; i++) {
    if (text[i] === '=' && text[i + 1] === '>') {
      let k = i + 2;
      while (k < text.length && /\s/.test(text[k])) k++;
      if (text[k] === '{') {
        const end = endOfBrace(k);
        blank(k + 1, end);
        i = end;
      } else {
        // A concise arrow body -- `() => nudgeBoundary('start', -NUDGE_S)`
        // is still a callback, and blanking only braced ones would fault
        // every screen that writes its handlers the short way.
        let depth = 0;
        let j = k;
        for (; j < text.length; j++) {
          const c = text[j];
          if ('([{'.includes(c)) depth++;
          else if (')]}'.includes(c)) { if (depth === 0) break; depth--; }
          else if ((c === ',' || c === ';') && depth === 0) break;
        }
        blank(k, j);
        i = j;
      }
    } else if (text.startsWith('function', i) && !isWord(i - 1) && !isWord(i + 8)) {
      const brace = text.indexOf('{', i);
      if (brace === -1) break;
      const end = endOfBrace(brace);
      blank(brace + 1, end);
      i = end;
    }
  }
  return out.join('');
}

const KEYWORDS = new Set([
  'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'break', 'continue',
  'return', 'function', 'let', 'const', 'var', 'new', 'typeof', 'instanceof',
  'in', 'of', 'this', 'null', 'true', 'false', 'undefined', 'await', 'async',
  'try', 'catch', 'finally', 'throw', 'delete', 'void', 'class', 'extends',
  'super', 'yield', 'default',
]);

/** Bare identifier reads: no `.prop`, no `{ prop:` keys, no keywords. */
function identifiers(text) {
  const stripped = text
    .replace(/\.\s*[A-Za-z_$][\w$]*/g, ' ')          // member access
    .replace(/([A-Za-z_$][\w$]*)\s*:/g, ' ')         // object-literal keys
    .replace(/\?\./g, ' ');
  const names = new Set();
  for (const m of stripped.matchAll(/[A-Za-z_$][\w$]*/g)) {
    if (!KEYWORDS.has(m[0])) names.add(m[0]);
  }
  return names;
}

/**
 * @returns {{binding: string, usedBy: string, line: number, declLine: number}[]}
 *   one entry per mount-time read of a not-yet-initialised binding.
 */
export function deadZoneReads(source) {
  const code = blankNoise(source);
  const mountAt = code.search(/\bexport\s+(?:async\s+)?function\s+mount\s*\(/);
  if (mountAt === -1) return [];
  const { open, close } = blockAfter(code, mountAt);
  const body = code.slice(open, close);
  const lineOf = (offset) => code.slice(0, open + offset).split('\n').length;

  // Top-level of mount() only: depth 0 relative to the body.
  const topLevel = (() => {
    const spans = [];
    let depth = 0;
    let start = 0;
    for (let i = 0; i < body.length; i++) {
      const c = body[i];
      if (c === '{' || c === '(' || c === '[') depth++;
      else if (c === '}' || c === ')' || c === ']') depth--;
      else if (depth === 0 && (c === ';' || c === '\n')) {
        if (body.slice(start, i).trim()) spans.push({ start, end: i });
        start = i + 1;
      }
      if (depth === 0 && c === '}') { // a top-level block just closed
        if (body.slice(start, i + 1).trim()) spans.push({ start, end: i + 1 });
        start = i + 1;
      }
    }
    if (body.slice(start).trim()) spans.push({ start, end: body.length });
    return spans;
  })();

  // Local helpers (hoisted -- callable from anywhere) and their bodies.
  const helpers = new Map();
  const fnDecl = /\bfunction\s+([A-Za-z_$][\w$]*)\s*\(/g;
  let f;
  while ((f = fnDecl.exec(body)) !== null) {
    const { open: bOpen, close: bClose } = blockAfter(body, f.index);
    if (!helpers.has(f[1])) helpers.set(f[1], body.slice(bOpen, bClose));
  }

  // let/const bindings of mount() ITSELF: name -> offset of the
  // declaration. Depth 0 only -- a `const rect` inside a helper, or an
  // arrow's own parameter, is a fresh binding of its own scope and says
  // nothing about what mount() may read.
  const depth = new Array(body.length).fill(0);
  {
    let d0 = 0;
    for (let i = 0; i < body.length; i++) {
      const c = body[i];
      if (c === '{' || c === '(' || c === '[') d0++;
      depth[i] = d0;
      if (c === '}' || c === ')' || c === ']') d0--;
      if (c === '}' || c === ')' || c === ']') depth[i] = d0;
    }
  }
  const bindings = new Map();
  const declRe = /\b(?:let|const)\s+([A-Za-z_$][\w$]*)/g;
  let d;
  while ((d = declRe.exec(body)) !== null) {
    if (depth[d.index] !== 0) continue;
    if (!bindings.has(d[1])) bindings.set(d[1], d.index);
  }

  const found = [];
  for (const span of topLevel) {
    const text = body.slice(span.start, span.end);
    if (/^\s*(?:export\s+)?(?:async\s+)?function\s/.test(text)) continue; // a declaration, not a call
    // Expand through the helpers this statement calls, transitively.
    const seen = new Set();
    const queue = [{ text: blankCallbackBodies(text), via: null }];
    while (queue.length) {
      const { text: chunk, via } = queue.pop();
      // CALLED helpers only. `on('play_pause', togglePreviewPlaying)`
      // hands the function over for later; it does not run it now, and
      // treating a mention as a call faults every screen that subscribes
      // a named handler.
      for (const m of chunk.matchAll(/\b([A-Za-z_$][\w$]*)\s*\(/g)) {
        const name = m[1];
        if (!helpers.has(name) || seen.has(name)) continue;
        seen.add(name);
        queue.push({ text: blankCallbackBodies(helpers.get(name)), via: via ?? name });
      }
      for (const name of identifiers(chunk)) {
        if (helpers.has(name)) continue;
        const declAt = bindings.get(name);
        // `>= span.end`, not `> span.start`: a declaration statement
        // mentions its own name, and `const body = el.querySelector(...)`
        // is not a dead-zone read of `body`.
        if (declAt !== undefined && declAt >= span.end) {
          found.push({
            binding: name,
            usedBy: via ?? text.trim().split('\n')[0].trim(),
            line: lineOf(span.start),
            declLine: lineOf(declAt),
          });
        }
      }
    }
  }
  return found;
}

const screens = readdirSync(screensDir).filter((n) => n.endsWith('.js'));
assert.ok(screens.length > 0, 'no screen modules found');

for (const name of screens) {
  test(`${name}: mount() reads no let/const before its declaration`, () => {
    const src = readFileSync(screensDir + name, 'utf8');
    const bad = deadZoneReads(src);
    assert.deepEqual(
      bad, [],
      bad.map((b) => (
        `${name}:${b.line} runs at mount and reaches '${b.binding}'`
        + ` (via ${b.usedBy}), declared at line ${b.declLine}`
        + ' -- a let is not hoisted; move the declaration above this line'
      )).join('\n'),
    );
  });
}

// A guard on the guard: a detector that quietly matched nothing (a
// changed mount() signature, an over-eager blanker) would pass forever.
// This is the shape of the real bug, reduced.
test('the detector catches the 2026-09-12 shape', () => {
  const bad = deadZoneReads(`
    export function mount(el, payload) {
      function audibleSpeedPct() {
        if (engineReady && engine) return engine.speedPct;
        return speedPct;
      }
      function preRollPlaybackSeconds() { return 4 / audibleSpeedPct(); }
      let cosmeticPreRoll = preRollPlaybackSeconds();
      el.addEventListener('x', () => { engine.play(); });
      let engine = null;
      let engineReady = false;
    }
  `);
  assert.deepEqual(bad.map((b) => b.binding).sort(), ['engine', 'engineReady']);
  assert.equal(bad[0].usedBy, 'preRollPlaybackSeconds');
});

test('the detector does not fault a callback that reads a later binding', () => {
  const bad = deadZoneReads(`
    export function mount(el) {
      el.addEventListener('click', () => { engine.play(); });
      function onKey() { engine.pause(); }
      let engine = null;
    }
  `);
  assert.deepEqual(bad, []);
});

// `deadZoneReads` is exported so it can be pointed at an arbitrary source
// (an old revision, say) from a scratch script, which is how the detector
// was checked against the real pre-fix practice.js. Exiting only when this
// file is the entry point is what keeps that possible.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(failures === 0 ? 0 : 1);
}
