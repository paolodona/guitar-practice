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
 *   artboard on disk); null for the other ten actions. Phase 3 may assign
 *   CCs to some of those — not decided here, and resisting a seventh foot
 *   action is a design rule, not an oversight (plan, "The front end").
 */

/** @type {Record<string, ActionSpec>} */
export const ACTIONS = {
  play_pause:      { label: 'Play / pause',    cc: 80 },
  next_section:    { label: 'Next section',    cc: 81 },
  prev_section:    { label: 'Previous',        cc: 82 },
  speed_up:        { label: 'Faster',          cc: 83 },
  speed_down:      { label: 'Slower',          cc: 84 },
  retract_rep:     { label: 'Retract rep',     cc: 85 },
  confirm_clean:   { label: 'Confirm clean',   cc: null },
  restart_section: { label: 'Restart section', cc: null },
  nudge_start:     { label: 'Nudge start',     cc: null },
  nudge_end:       { label: 'Nudge end',       cc: null },
  transpose_up:    { label: 'Transpose up',    cc: null },
  transpose_down:  { label: 'Transpose down',  cc: null },
  loop_toggle:     { label: 'Loop',            cc: null },
  metronome:       { label: 'Metronome',       cc: null },
  fullscreen:      { label: 'Fullscreen',      cc: null },
  help:            { label: 'Help',            cc: null },
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
