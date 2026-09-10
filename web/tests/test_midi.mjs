// web/tests/test_midi.mjs — Group L1's test contract, run as a plain Node
// script (`node web/tests/test_midi.mjs`), same house style as
// test_buffer_engine.mjs: a fake `navigator.requestMIDIAccess` stands in
// for the real Web MIDI API, and a fake `fetch` stands in for
// `GET /api/config` (app.js's own `get()` calls the global `fetch`, so
// stubbing that is enough — no need to mock app.js itself).
//
// What this actually protects, per docs/05-foot-control.md and the plan's
// L1 bullet: port matching against config.midi.input, hot-plug via
// onstatechange, the >=64 press rule (never the release), the per-ACTION
// debounce, and config.midi.map's override/unknown-action-throws contract.
// None of it needs a real pedal in the room — see prompt 002's own
// reasoning for why L1 does not depend on the eyes-on-the-unit questions.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { ACTIONS, on as onAction } from '../actions.js';
import { attach, CC_MAP, isConnected, onConnectionChange, resolveCcMap } from '../midi.js';

let failures = 0;
function test(name, fn) {
  try {
    const out = fn();
    if (out instanceof Promise) {
      return out.then(
        () => console.log(`ok - ${name}`),
        (err) => { failures += 1; console.error(`FAIL - ${name}`); console.error(err); },
      );
    }
    console.log(`ok - ${name}`);
    return Promise.resolve();
  } catch (err) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(err);
    return Promise.resolve();
  }
}

// ---- fakes: only what midi.js actually touches ----------------------------

class FakeInputPort {
  constructor(name, state = 'connected') {
    this.name = name;
    this.state = state;
    this._onmessage = null;
  }
  addEventListener(type, fn) { if (type === 'midimessage') this._onmessage = fn; }
  removeEventListener(type, fn) { if (type === 'midimessage' && this._onmessage === fn) this._onmessage = null; }
  /** Simulate a Control Change message: status 0xB0 (channel 0), cc, value. */
  fireCC(cc, value) {
    if (this._onmessage) this._onmessage({ data: [0xb0, cc, value] });
  }
}

class FakeMidiAccess {
  constructor(ports) {
    this.inputs = new Map(ports.map((p, i) => [String(i), p]));
    this.onstatechange = null;
  }
}

function withFakes({ ports = [new FakeInputPort('GX-100 MIDI 1')], config = { midi: {} }, clock = { t: 0 } } = {}) {
  globalThis.fetch = async () => ({
    ok: true,
    async json() { return config; },
  });
  const access = new FakeMidiAccess(ports);
  globalThis.navigator = { requestMIDIAccess: async () => access };
  globalThis.performance = { now: () => clock.t };
  return { access, ports, clock };
}

// ---- resolveCcMap / CC_MAP: pure, no fakes needed --------------------------

test('CC_MAP is derived from ACTIONS\' own cc field, not hand-duplicated', () => {
  assert.equal(CC_MAP[80], 'play_pause');
  assert.equal(CC_MAP[85], 'retract_rep');
  assert.equal(Object.keys(CC_MAP).length, Object.values(ACTIONS).filter((a) => a.cc != null).length);
});

test('resolveCcMap: an empty/undefined config.midi.map leaves CC_MAP\'s defaults untouched', () => {
  assert.deepEqual(resolveCcMap(undefined), CC_MAP);
  assert.deepEqual(resolveCcMap({}), CC_MAP);
});

test('resolveCcMap: config.midi.map overrides a CC, leaving every other default alone', () => {
  const resolved = resolveCcMap({ 90: 'speed_up' });
  assert.equal(resolved[90], 'speed_up');
  assert.equal(resolved[80], 'play_pause'); // untouched
  assert.equal(resolved[83], 'speed_up'); // the artboard's own CC for speed_up, also untouched
});

test('resolveCcMap: an unknown action name in config.midi.map throws, loudly', () => {
  assert.throws(() => resolveCcMap({ 90: 'nonexistent_action' }), /unknown action/);
});

// ---- attach(): the full wiring, driven through the fakes -------------------

await test('a press (value >= 64) on a mapped CC dispatches the action, source \'midi\'', async () => {
  const { ports } = withFakes();
  const seen = [];
  const unsub = onAction('play_pause', (source) => seen.push(source));
  const detach = await attach();
  ports[0].fireCC(80, 127);
  assert.deepEqual(seen, ['midi']);
  detach();
  unsub();
});

await test('a release (value < 64) fires nothing', async () => {
  const { ports } = withFakes();
  const seen = [];
  const unsub = onAction('play_pause', () => seen.push(1));
  const detach = await attach();
  ports[0].fireCC(80, 0);
  ports[0].fireCC(80, 63);
  assert.deepEqual(seen, []);
  detach();
  unsub();
});

await test('an unmapped CC is silently ignored', async () => {
  const { ports } = withFakes();
  const detach = await attach();
  assert.doesNotThrow(() => ports[0].fireCC(1, 127)); // mod wheel or similar -- not in CC_MAP
  detach();
});

await test('debounce is per ACTION: two presses inside 150ms only fire once; the window resets after', async () => {
  const { ports, clock } = withFakes();
  const seen = [];
  const unsub = onAction('play_pause', (source) => seen.push(source));
  const detach = await attach();
  ports[0].fireCC(80, 127);
  clock.t += 50;
  ports[0].fireCC(80, 127); // inside the 150ms window -- must not fire again
  assert.equal(seen.length, 1);
  clock.t += 200; // past the window
  ports[0].fireCC(80, 127);
  assert.equal(seen.length, 2);
  detach();
  unsub();
});

await test('debounce keys on the action, not the CC: two CCs mapped to the same action still only fire once', async () => {
  const { ports, clock } = withFakes({ config: { midi: { map: { 91: 'retract_rep' } } } });
  const seen = [];
  const unsub = onAction('retract_rep', () => seen.push(1));
  const detach = await attach();
  ports[0].fireCC(85, 127); // the artboard's own CC for retract_rep
  clock.t += 10;
  ports[0].fireCC(91, 127); // config's override CC, SAME action, inside the window
  assert.equal(seen.length, 1);
  detach();
  unsub();
});

await test('config.midi.input matches a port by case-insensitive substring; a non-matching port is ignored', async () => {
  const matching = new FakeInputPort('GX-100 MIDI 1');
  const other = new FakeInputPort('Some Other Controller');
  withFakes({ ports: [matching, other], config: { midi: { input: 'gx-100' } } });
  const seen = [];
  const unsub = onAction('play_pause', () => seen.push(1));
  const detach = await attach();
  other.fireCC(80, 127);
  assert.deepEqual(seen, [], 'a non-matching port must not be attached at all');
  matching.fireCC(80, 127);
  assert.equal(seen.length, 1);
  detach();
  unsub();
});

await test('an unset config.midi.input matches every port', async () => {
  const a = new FakeInputPort('Whatever Keyboard');
  const b = new FakeInputPort('Some Foot Pedal');
  const { clock } = withFakes({ ports: [a, b], config: { midi: {} } });
  const seen = [];
  const unsub = onAction('play_pause', () => seen.push(1));
  const detach = await attach();
  a.fireCC(80, 127);
  clock.t += 200; // past the debounce window -- this test is about PORT matching, not debounce
  b.fireCC(80, 127);
  assert.equal(seen.length, 2);
  detach();
  unsub();
});

await test('hot-plug: a port that becomes connected AFTER attach() still fires once onstatechange re-scans', async () => {
  const port = new FakeInputPort('GX-100 MIDI 1', 'disconnected');
  const { access } = withFakes({ ports: [port] });
  const seen = [];
  const unsub = onAction('play_pause', () => seen.push(1));
  const detach = await attach();
  port.fireCC(80, 127);
  assert.deepEqual(seen, [], 'not yet connected -- must not be attached');
  port.state = 'connected';
  access.onstatechange();
  port.fireCC(80, 127);
  assert.equal(seen.length, 1);
  detach();
  unsub();
});

await test('detach() removes every listener -- a fire after detach does nothing', async () => {
  const { ports } = withFakes();
  const seen = [];
  const unsub = onAction('play_pause', () => seen.push(1));
  const detach = await attach();
  detach();
  ports[0].fireCC(80, 127);
  assert.deepEqual(seen, []);
  unsub();
});

await test('L2: isConnected()/onConnectionChange() track a real port attaching and detaching', async () => {
  const port = new FakeInputPort('GX-100 MIDI 1');
  withFakes({ ports: [port] });
  const seen = [];
  const unsub = onConnectionChange((c) => seen.push(c));
  assert.deepEqual(seen, [false], 'onConnectionChange fires once immediately with the current value');
  const detach = await attach();
  assert.equal(isConnected(), true);
  assert.deepEqual(seen, [false, true]);
  detach();
  assert.equal(isConnected(), false);
  assert.deepEqual(seen, [false, true, false]);
  unsub();
});

await test('L2: no matching port at all -- never reports connected', async () => {
  const port = new FakeInputPort('Some Other Controller');
  withFakes({ ports: [port], config: { midi: { input: 'gx-100' } } });
  const detach = await attach();
  assert.equal(isConnected(), false);
  detach();
});

await test('no navigator.requestMIDIAccess at all (Safari) -- attach() resolves, never throws', async () => {
  globalThis.fetch = async () => ({ ok: true, async json() { return { midi: {} }; } });
  globalThis.navigator = {}; // no requestMIDIAccess property
  const detach = await attach();
  assert.equal(typeof detach, 'function');
});

await test('requestMIDIAccess refused (Firefox permission denial) -- attach() resolves, never throws', async () => {
  globalThis.fetch = async () => ({ ok: true, async json() { return { midi: {} }; } });
  globalThis.navigator = { requestMIDIAccess: async () => { throw new Error('permission denied'); } };
  const detach = await attach();
  assert.equal(typeof detach, 'function');
});

await test('GET /api/config failing degrades to ACTIONS\' own CC defaults, matching every port', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 500, statusText: 'error', async json() { return {}; } });
  const port = new FakeInputPort('Any Pedal');
  const access = new FakeMidiAccess([port]);
  globalThis.navigator = { requestMIDIAccess: async () => access };
  globalThis.performance = { now: () => 0 };
  const seen = [];
  const unsub = onAction('play_pause', () => seen.push(1));
  const detach = await attach();
  port.fireCC(80, 127); // CC_MAP's own default, since config never loaded
  assert.equal(seen.length, 1);
  detach();
  unsub();
});

// ---- Phase 3 gate's own contract, as a lint-style check: this repo's
// established pattern (test_ended.mjs/test_buffer_engine.mjs) puts a
// source-level check for its own unit's central risk in the SAME test
// file. Modeled on tests/test_web_lint.py's playbackRate scan (comment/
// string stripping) but checked here in JS since the thing being asserted
// -- midi.js's own import list -- is naturally a JS-side fact. ----
test('midi.js imports nothing beyond actions.js (dispatch/ACTIONS) and app.js (get) -- no second place behaviour could live', () => {
  const midiPath = fileURLToPath(new URL('../midi.js', import.meta.url));
  const src = readFileSync(midiPath, 'utf8');
  const imports = [...src.matchAll(/^import\s+.*?\s+from\s+['"](.+?)['"];?$/gm)].map((m) => m[1]);
  assert.deepEqual(imports.sort(), ['./actions.js', './app.js']);
});

if (failures) process.exitCode = 1;
