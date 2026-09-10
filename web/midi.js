/**
 * midi.js — Web MIDI -> actions.js. Phase 3, Group L1.
 *
 * Same rule as keys.js: only an input-to-action-name map, plus the
 * listener that reads it and calls dispatch(name, 'midi') — no other
 * behaviour, no device-selection UI, no state beyond the map and whatever
 * requestMIDIAccess itself needs to stay attached. keys.js's own two
 * documented exceptions (preventDefault, the text-entry guard) are the
 * worked example of what "no other behaviour" actually forbids: a SECOND
 * place a control's MEANING gets decided. Reading config to know which
 * port and which CC numbers to listen for is not that — it is wiring, the
 * same way keys.js's own KEY_MAP is a literal, hand-written wiring table.
 *
 * ---- CC_MAP: config overrides the artboard's defaults, per CC ----
 * Two sources, one precedence. `ACTIONS`' own `cc` field (the artboard's
 * fixed 80-85, design/Main.dc.html) supplies the DEFAULT map, exported
 * below as CC_MAP. `config.midi.map` (config.yaml, served at
 * `GET /api/config`) OVERRIDES it per CC — which physical CC each
 * footswitch actually sends is docs/05-foot-control.md's question (1),
 * answered at the pedal and must be settable without editing this file.
 * `ACTIONS` stays the only place an action NAME is defined: a
 * `config.midi.map` entry naming an unknown action is refused loudly
 * (resolveCcMap throws, attach() catches it and degrades rather than
 * silently ignoring the bad entry) — the same "a wrong value would be
 * silently harmful" instinct manifest.py's Pydantic models already apply
 * to spans and the shift range.
 *
 * ---- The two rules docs/05-foot-control.md fixes ----
 * A CC value >= 64 counts as a press; below 64 (the release half, for a
 * switch that sends both) fires nothing. The 150ms debounce is keyed by
 * ACTION, not by CC — two CCs mapped to the same action must still only
 * fire once inside that window, the same "one action, however many
 * inputs name it" reasoning keys.js's own two `retract_rep` keys already
 * establish.
 */

import { ACTIONS, dispatch } from './actions.js';
import { get } from './app.js';

const PRESS_THRESHOLD = 64;
const DEBOUNCE_MS = 150;
const CONTROL_CHANGE = 0xb0;

// ---- connection state, for L2's "quiet indicator" -------------------------
// Not a second decision about what a control MEANS (see the module doc) --
// only whether one is currently attached, which is a fact this file already
// knows and nowhere else can honestly know (this process cannot enumerate a
// pedal itself; see doctor.py's own "matched in the browser, never from
// here"). One shared state: attach() is called once, globally, from
// app.js's start(); a screen that wants to show it (screens/practice.js's
// foot strip) reads isConnected() on mount and subscribes via
// onConnectionChange for as long as it needs to react live (a pedal
// unplugged mid-practice).
let connected = false;
const connectionTarget = new EventTarget();

function setConnected(value) {
  if (value === connected) return;
  connected = value;
  connectionTarget.dispatchEvent(new CustomEvent('change', { detail: { connected } }));
}

/** Whether a port matching config.midi.input is currently attached. */
export function isConnected() {
  return connected;
}

/**
 * Subscribe to connection-state changes; `handler(connected)` fires once
 * immediately with the current value, then again on every change.
 * @param {(connected: boolean) => void} handler
 * @returns {() => void} unsubscribe
 */
export function onConnectionChange(handler) {
  const listener = (e) => handler(e.detail.connected);
  connectionTarget.addEventListener('change', listener);
  handler(connected);
  return () => connectionTarget.removeEventListener('change', listener);
}

/**
 * ACTIONS' own `cc` field, inverted — the default cc -> action map, before
 * any config.midi.map override. Derived, never hand-duplicated a second
 * time (see the module doc).
 * @type {Record<number, keyof typeof ACTIONS>}
 */
export const CC_MAP = Object.fromEntries(
  Object.entries(ACTIONS)
    .filter(([, spec]) => spec.cc != null)
    .map(([name, spec]) => [spec.cc, name]),
);

/**
 * CC_MAP with `configMap`'s overrides applied on top — exported so the
 * override/precedence/unknown-action rule can be pinned by a test without
 * a fake requestMIDIAccess.
 * @param {Record<string, string> | Record<number, string> | undefined} configMap - config.midi.map
 * @returns {Record<number, keyof typeof ACTIONS>}
 */
export function resolveCcMap(configMap) {
  const resolved = { ...CC_MAP };
  for (const [ccStr, name] of Object.entries(configMap || {})) {
    if (!(name in ACTIONS)) {
      throw new Error(`midi.js: config.midi.map names an unknown action ${JSON.stringify(name)}`);
    }
    resolved[Number(ccStr)] = name;
  }
  return resolved;
}

/**
 * Request MIDI access, match `config.midi.input` (a case-insensitive
 * substring against each input port's own `name`; an empty/unset
 * `config.midi.input` matches every port) and attach a `midimessage`
 * listener that looks the CC number up in the resolved CC_MAP (defaults +
 * `config.midi.map`'s overrides) and dispatch()es the matching action with
 * source 'midi' — only on a press (value >= PRESS_THRESHOLD), debounced
 * per action. Hot-plug: `onstatechange` re-scans every input port and
 * (re)attaches or detaches as ports matching `config.midi.input` come and
 * go, so a pedal plugged in after the page loaded still works.
 *
 * Never throws and never blocks startup: a browser with no Web MIDI at
 * all (Safari), one that refuses the permission (Firefox), a
 * `config.midi.input` that matches no port, or a `GET /api/config` that
 * fails all degrade to "no foot control this session" — logged once, not
 * surfaced as an error, the same "controls update local state only"
 * degrade screens/practice.js's own engine-unavailable path already uses.
 * @returns {Promise<() => void>} detach
 */
export async function attach() {
  let ccMap = CC_MAP;
  let inputSubstring = '';
  try {
    const config = await get('/api/config');
    ccMap = resolveCcMap(config.midi && config.midi.map);
    inputSubstring = ((config.midi && config.midi.input) || '').toLowerCase();
  } catch (err) {
    console.warn(`midi.js: could not read config.midi (${err && err.message}) — using ACTIONS' own CC defaults, matching every input port`);
  }

  if (!navigator.requestMIDIAccess) {
    console.warn('midi.js: navigator.requestMIDIAccess is not available in this browser — no foot control this session');
    setConnected(false);
    return () => {};
  }

  let midiAccess;
  try {
    midiAccess = await navigator.requestMIDIAccess({ sysex: false });
  } catch (err) {
    console.warn(`midi.js: requestMIDIAccess was refused (${err && err.message}) — no foot control this session`);
    setConnected(false);
    return () => {};
  }

  const lastFiredAt = new Map(); // action name -> performance.now() of its last dispatch
  const attachedPorts = new Set(); // input ports this listener currently holds

  function matches(port) {
    if (!inputSubstring) return true; // no config.midi.input set -- every input port qualifies
    return (port.name || '').toLowerCase().includes(inputSubstring);
  }

  function onMessage(event) {
    const [status, cc, value] = event.data;
    if ((status & 0xf0) !== CONTROL_CHANGE) return;
    if (value < PRESS_THRESHOLD) return; // the release half of a switch that sends both
    const name = ccMap[cc];
    if (!name) return;
    const now = performance.now();
    const last = lastFiredAt.get(name) ?? -Infinity;
    if (now - last < DEBOUNCE_MS) return;
    lastFiredAt.set(name, now);
    dispatch(name, 'midi');
  }

  function refresh() {
    for (const port of midiAccess.inputs.values()) {
      const shouldBeAttached = port.state === 'connected' && matches(port);
      if (shouldBeAttached && !attachedPorts.has(port)) {
        port.addEventListener('midimessage', onMessage);
        attachedPorts.add(port);
      } else if (!shouldBeAttached && attachedPorts.has(port)) {
        port.removeEventListener('midimessage', onMessage);
        attachedPorts.delete(port);
      }
    }
    setConnected(attachedPorts.size > 0);
  }

  midiAccess.onstatechange = refresh;
  refresh();

  return () => {
    midiAccess.onstatechange = null;
    for (const port of attachedPorts) {
      port.removeEventListener('midimessage', onMessage);
    }
    attachedPorts.clear();
    setConnected(false);
  };
}
