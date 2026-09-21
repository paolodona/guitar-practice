// web/tests/test_practice_engine_wiring.mjs — Phase 1.5's half of the
// instant-playback-while-cold-cache change that lives inside mount()'s
// closures, where there is no DOM to drive it through -- the same narrow,
// deliberate trade test_practice_speed.mjs/test_practice_seek.mjs already
// make for the same reason: check the source.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const src = readFileSync(
  fileURLToPath(new URL('../screens/practice.js', import.meta.url)), 'utf8',
);

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

function bodyOf(name, startMarker) {
  const start = src.indexOf(startMarker);
  assert.ok(start !== -1, `no ${name}`);
  let depth = 0;
  let i = src.indexOf('{', start);
  const bodyStart = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) break; }
  }
  return src.slice(bodyStart, i + 1);
}

test('ensureEngine requests the hybrid engine, not a manual buffer/realtime fallback', () => {
  const body = bodyOf('ensureEngine', 'function ensureEngine()');
  assert.ok(body.includes("startEngine('hybrid')"), 'should call startEngine(\'hybrid\')');
  assert.ok(!body.includes("startEngine('buffer')"), 'the old two-branch fallback should be gone');
  assert.ok(!body.includes("startEngine('realtime')"), 'the old two-branch fallback should be gone');
  assert.ok(body.includes('engine.kind'), 'engineKind should be read off the engine, not hardcoded');
});

test("startEngine listens for the engine's own 'kind' changes", () => {
  const body = bodyOf('startEngine', 'async function startEngine(kind)');
  assert.ok(body.includes("addEventListener('kind'"), 'no kind listener');
  const listener = body.slice(body.indexOf("addEventListener('kind'"));
  assert.ok(listener.includes('engineKind = event.detail.kind'), 'should update the local engineKind');
  assert.ok(listener.includes('applyMetronome()'), 'a kind change must re-check the metronome');
});

test('applyMetronome stops the metronome when it can no longer honour it', () => {
  const body = bodyOf('applyMetronome', 'function applyMetronome()');
  const realtimeBranch = body.slice(body.indexOf("engineKind === 'realtime'"));
  assert.ok(
    realtimeBranch.includes('metronome.stop()'),
    'FOUND LIVE 2026-09-21: dropping to realtime mid-session must stop an already-running metronome, not just relabel the toggle',
  );
});

test('applyMetronome transparently resumes once the sample-exact engine is back', () => {
  const body = bodyOf('applyMetronome', 'function applyMetronome()');
  assert.ok(
    /no-cache['"]?\s*&&\s*engineKind === ['"]buffer['"]/.test(body) || body.includes("metronomeState === 'no-cache' && engineKind === 'buffer'"),
    'no auto-recovery out of the no-cache state once back on buffer',
  );
});

if (failures) process.exitCode = 1;
