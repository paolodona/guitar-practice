/**
 * actions.js — THE ACTION TABLE. CLAUDE.md's "Conventions": one action
 * table shared by mouse, keyboard and MIDI, never two.
 *
 * D0 owns this file in full (not merely stubbed): `ACTIONS` is the literal
 * data keys.js and midi.js key off, and `on`/`dispatch` are the entire
 * pub-sub mechanism — six lines of Set-bookkeeping that every caller must
 * agree on rather than reinvent. keys.js and midi.js may contain ONLY an
 * input-to-action-name map plus a call to dispatch(name, source); no
 * behaviour of their own lives there.
 */

/**
 * @typedef {Object} ActionSpec
 * @property {string} label - human-readable name (foot-strip chip text in
 *   design/Main.dc.html, or a keyboard-shortcut legend)
 * @property {number | null} cc - MIDI CC number for the six-action foot
 *   vocabulary design/Main.dc.html fixes (CC 80-85, verified against the
 *   artboard on disk); null for the other actions. docs/05-foot-control.md's
 *   "resist a seventh" is about THIS vocabulary — the fixed physical pedal
 *   CCs — and stays true: nothing below is ever assigned a CC other than
 *   those six.
 * @property {boolean} [chip] - true if screens/practice.js's foot strip
 *   shows this action as a clickable chip. Was simply `cc != null` until
 *   restart_section joined live 2026-09-10 (Paolo: a chip that returns to
 *   the section's start, mouse/keyboard only — it has no pedal CC, so
 *   `chip` and `cc` are no longer the same question) — this field is now
 *   the one place "is this a foot-strip chip" gets decided, checked by
 *   web/tests/test_foot_icons.mjs the same way `cc` already was.
 */

/** @type {Record<string, ActionSpec>} */
export const ACTIONS = {
  restart_section: { label: 'Restart',         cc: null, chip: true },
  play_pause:      { label: 'Play / pause',    cc: 80,   chip: true },
  prev_section:    { label: 'Previous',        cc: 82,   chip: true },
  next_section:    { label: 'Next section',    cc: 81,   chip: true },
  speed_up:        { label: 'Faster',          cc: 83,   chip: true },
  speed_down:      { label: 'Slower',          cc: 84,   chip: true },
  retract_rep:     { label: 'Retract rep',     cc: 85,   chip: true },
  confirm_clean:   { label: 'Confirm clean',   cc: null },
  nudge_start:     { label: 'Nudge start',     cc: null },
  nudge_end:       { label: 'Nudge end',       cc: null },
  transpose_up:    { label: 'Transpose up',    cc: null },
  transpose_down:  { label: 'Transpose down',  cc: null },
  loop_toggle:     { label: 'Loop',            cc: null },
  metronome:       { label: 'Metronome',       cc: null },
  fullscreen:      { label: 'Fullscreen',      cc: null },
  help:            { label: 'Help',            cc: null },
  cancel_lead_in:  { label: 'Cancel lead-in',  cc: null },
};

/** @typedef {"midi" | "keyboard" | "ui"} ActionSource */

/** action name -> Set<handler>. Module-private; nothing outside on()/dispatch() touches it. */
const listeners = new Map();

/**
 * Subscribe *handler* to action *name*; *handler* is called with the
 * ActionSource every time dispatch(name, source) fires. Returns an
 * unsubscribe function.
 * @param {keyof typeof ACTIONS} name
 * @param {(source: ActionSource) => void} handler
 * @returns {() => void}
 */
export function on(name, handler) {
  if (!(name in ACTIONS)) {
    throw new Error(`actions.on: no such action ${JSON.stringify(name)}`);
  }
  let set = listeners.get(name);
  if (!set) {
    set = new Set();
    listeners.set(name, set);
  }
  set.add(handler);
  return () => {
    set.delete(handler);
  };
}

/**
 * Fire action *name* as triggered from *source*. Calls every handler
 * currently subscribed via on(name, ...), in subscription order, passing
 * *source* through — this is the one place mouse/keyboard/MIDI converge,
 * so nothing downstream needs to know which one fired.
 * @param {keyof typeof ACTIONS} name
 * @param {ActionSource} source
 */
export function dispatch(name, source) {
  if (!(name in ACTIONS)) {
    throw new Error(`actions.dispatch: no such action ${JSON.stringify(name)}`);
  }
  const set = listeners.get(name);
  if (!set) return;
  for (const handler of set) handler(source);
}
