/**
 * gx100.js — the GX-100 patch-change lane's client half (#3, Group N2).
 *
 * Mirrors src/woodshed/gx100.py's own memory<->index arithmetic (this file
 * cannot import a Python module, and the arithmetic is small enough that a
 * round trip would only make it later, not safer — the same reasoning
 * timeline.js mirrors clock.py and ladder.js mirrors ladder.py). Both sides
 * assert the same hardware-verified table (tests/test_gx100.py,
 * web/tests/test_gx100.mjs), so a drift shows up as a test failure, not a
 * silent disagreement only discovered at the pedal.
 *
 * `resolvePatchAt` has no Python counterpart — nothing server-side ever
 * needs "which patch applies at source-second T", only this side does,
 * exactly where the decision of what to actually SEND is also made.
 *
 * **The hardware-verified protocol this file assumes throughout**
 * (docs/05-foot-control.md): a bare Program Change (`0xC0`, no Bank
 * Select) selects a memory directly. `CC#0`/`CC#32` left the physical unit
 * unresponsive until its power was pulled, 2026-09-06 — **this file must
 * never construct or send that pair.** There is no bank arithmetic
 * anywhere below, on purpose.
 *
 * The pedal is attached to the same machine the browser runs on, so Web
 * MIDI reaches it directly and no server-side MIDI library is needed —
 * unlike midi.js (input, `{sysex: false}`, one narrow job: read a CC and
 * dispatch an action), this file owns the OUTPUT direction entirely,
 * deliberately kept out of midi.js so that file's own "no other
 * behaviour" doc stays true.
 */

import { get } from './app.js';

/** A bare Program Change is 7-bit, and this pedal never receives Bank
 *  Select (see the module doc) — 0-127 is the whole reachable range. */
export const MAX_PC = 127;

/** What a section plays through when its song has no patch_changes entry
 *  covering it yet — U01-1, the pedal's own bank-0/slot-0 default. */
export const DEFAULT_MEMORY = 'U01-1';

const PROGRAM_CHANGE = 0xc0;

/**
 * `"U01-1"` -> 0 ... `"U32-4"` -> 127 — the Program Change number that
 * selects *memory* directly (no PROGRAM MAP indirection, no Bank Select).
 * Case- and whitespace-tolerant, matching how the pedal itself prints
 * these labels.
 *
 * Throws for a P-bank preset or a U-bank memory past U32-4: a bare
 * Program Change cannot reach either — reaching them needs SysEx (out of
 * scope here) or Bank Select (unsafe on this unit, never sent).
 * @param {string} memory
 * @returns {number}
 */
export function memoryToIndex(memory) {
  const text = String(memory).trim().toUpperCase();
  const match = /^U(\d+)-(\d+)$/.exec(text);
  if (!match) {
    throw new Error(`gx100.js: not a reachable GX-100 memory: ${JSON.stringify(memory)} -- expected U01-1..U32-4`);
  }
  const bank = Number(match[1]);
  const slot = Number(match[2]);
  if (bank < 1 || bank > 32 || slot < 1 || slot > 4) {
    throw new Error(`gx100.js: ${JSON.stringify(memory)} is outside the range a bare Program Change can reach (U01-1..U32-4)`);
  }
  return (bank - 1) * 4 + (slot - 1);
}

/** Inverse of {@link memoryToIndex}.
 * @param {number} index
 * @returns {string}
 */
export function indexToMemory(index) {
  if (!Number.isInteger(index) || index < 0 || index > MAX_PC) {
    throw new Error(`gx100.js: program change ${index} outside 0-${MAX_PC}`);
  }
  return `U${String(Math.floor(index / 4) + 1).padStart(2, '0')}-${(index % 4) + 1}`;
}

/**
 * The last `patch_changes` entry with `at_s <= atS`, or {@link DEFAULT_MEMORY}
 * when the list is empty or nothing qualifies — the same "most-recent-
 * before, not a range lookup" shape as this codebase's own renderClock/
 * ladder-state resolution. Does not assume the list is pre-sorted.
 * @param {{at_s: number, patch: string}[]} patchChanges
 * @param {number} atS
 * @returns {string}
 */
export function resolvePatchAt(patchChanges, atS) {
  const applicable = (patchChanges || []).filter((c) => c.at_s <= atS);
  if (!applicable.length) return DEFAULT_MEMORY;
  return applicable.reduce((latest, c) => (c.at_s > latest.at_s ? c : latest)).patch;
}

// ---- sending ---------------------------------------------------------------

/** Port names that look like the pedal itself — the same hints
 *  `gx100_sync.guess_gx100_ports` matches on the Python side, for the same
 *  job (find the GX-100 among whatever else is plugged in). */
const GX_HINTS = ['gx-100', 'gx100', 'boss gx'];

/**
 * Which output port to send Program Changes to, out of *outputs*, given
 * `config.gx100.midi_output`.
 *
 * A non-empty *substring* is an explicit instruction: case-insensitive
 * match on a port's own `name` (same convention as `config.midi.input`),
 * and **null when it matches nothing** — an explicit ask that cannot be
 * honoured must send nowhere, never to some other device instead.
 *
 * An empty one used to mean "the first available output", which was wrong
 * on this machine and silently so (found live 2026-09-12, see
 * web/tests/test_gx100.mjs): Windows enumerates `Microsoft GS Wavetable
 * Synth` first, ahead of `GX-100`, so every Program Change went to the
 * software synth and the pedal never moved. It now means "the pedal, if
 * one is attached" — a GX-hinted port, preferring its playing port over
 * its `DAW CTRL` companion (the pedal exposes both; only the first one
 * selects memories) — and falls back to the first output only when nothing
 * on the machine looks like a GX-100 at all, which is the one case the old
 * behaviour was ever right about.
 * @param {{name?: string}[]} outputs
 * @param {string} substring
 * @returns {{name?: string}|null}
 */
export function pickOutput(outputs, substring) {
  const ports = [...(outputs || [])];
  const nameOf = (port) => (port.name || '').toLowerCase();
  const wanted = String(substring || '').trim().toLowerCase();
  if (wanted) return ports.find((port) => nameOf(port).includes(wanted)) ?? null;
  const pedals = ports.filter((port) => GX_HINTS.some((hint) => nameOf(port).includes(hint)));
  return pedals.find((port) => !nameOf(port).includes('daw')) ?? pedals[0] ?? ports[0] ?? null;
}

let outputPromise = null;

/** Lazily request MIDI access and resolve the output port through
 *  {@link pickOutput}. Cached: every call after the first reuses the same
 *  port rather than re-requesting access and re-scanning ports on every
 *  section load — so the one line it logs names, once per page, the device
 *  every Program Change this session will actually reach. That line is not
 *  noise: a Program Change sent to the wrong output makes no sound, raises
 *  no error and leaves the pedal sitting on the previous patch, which is
 *  exactly how this went unnoticed until 2026-09-12. */
function findOutput() {
  if (!outputPromise) {
    outputPromise = (async () => {
      if (!navigator.requestMIDIAccess) return null;
      let outputSubstring = '';
      try {
        const config = await get('/api/config');
        outputSubstring = (config.gx100 && config.gx100.midi_output) || '';
      } catch {
        // No config reachable -- fall through to the pedal-hint match, the
        // same degrade midi.js's own attach() makes for its input side.
      }
      const access = await navigator.requestMIDIAccess({ sysex: false });
      const output = pickOutput([...access.outputs.values()], outputSubstring);
      if (!output) {
        console.warn(`gx100.js: no MIDI output matched ${JSON.stringify(outputSubstring)} — no patch changes this session`);
        return null;
      }
      console.info(`gx100.js: sending Program Changes to MIDI output "${output.name}"`);
      return output;
    })().catch((err) => {
      console.warn(`gx100.js: MIDI output unavailable (${err && err.message})`);
      return null;
    });
  }
  return outputPromise;
}

/**
 * Send a bare Program Change selecting *memory* — and ONLY a Program
 * Change; never a Bank Select pair (see the module doc: that exact pair
 * wedged the physical unit until its power was pulled). Gated on
 * `config.gx100.send_program_changes` (CLAUDE.md: "Don't send Program
 * Changes to the GX-100 without an explicit toggle") — checked here, the
 * one place that actually calls `output.send()`, so no future call site
 * can bypass it by forgetting to check first. The channel comes from
 * `config/gx100.yaml`'s own `channel` (via `GET /api/gx100/patches`) --
 * that file, not `config.midi.channel`, is the pedal's own RX CHANNEL
 * setting (docs/05-foot-control.md), a separate concern from the foot
 * pedal's INPUT mapping even though they happen to be the same physical
 * unit today.
 *
 * Silent no-op, never throws, short of a clean send: no Web MIDI, no
 * matching output port, the toggle off, or an unreachable memory — a
 * practice tool degrading is expected (this repo's own "controls update
 * local state only" pattern); a practice tool THROWING because a pedal is
 * unplugged is not.
 * @param {string} memory - e.g. "U01-1"
 * @returns {Promise<void>}
 */
export async function sendProgramChange(memory) {
  let config;
  try {
    config = await get('/api/config');
  } catch {
    return; // no config reachable -- degrade silently
  }
  if (!config.gx100 || !config.gx100.send_program_changes) return;

  let index;
  try {
    index = memoryToIndex(memory);
  } catch (err) {
    console.warn(`gx100.js: ${err.message}`);
    return;
  }

  const output = await findOutput();
  if (!output) return;

  let channel = 1;
  try {
    const names = await get('/api/gx100/patches');
    channel = names.channel ?? 1;
  } catch {
    // no local patch-name file yet -- channel 1, the pedal's own default
  }

  const ch = Math.min(16, Math.max(1, channel)) - 1;
  output.send([PROGRAM_CHANGE | (ch & 0x0f), index]);
}
