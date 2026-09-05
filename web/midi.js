/**
 * midi.js — Web MIDI -> actions.js. D5's file to finish (per the plan's
 * module map; this run folds it into the same unit as keys.js).
 *
 * Same rule as keys.js: only an input-to-action-name map, plus a call to
 * dispatch(name, 'midi'). No sysex, no device selection UI, no state
 * beyond the map and whatever WebMidi.js/navigator.requestMIDIAccess
 * itself needs to stay attached.
 */

import { ACTIONS, dispatch } from './actions.js';

/**
 * MIDI CC number -> action name, derived from ACTIONS' own `cc` field
 * (the six foot actions design/Main.dc.html fixes: CC 80-85) rather than
 * hand-duplicating the numbers a second time:
 *
 *   Object.fromEntries(
 *     Object.entries(ACTIONS)
 *       .filter(([, spec]) => spec.cc != null)
 *       .map(([name, spec]) => [spec.cc, name])
 *   )
 *
 * Left unbuilt here — D0 fixes the CONTRACT (cc numbers live on ACTIONS,
 * this map is derived not duplicated), not this module's body.
 * @type {Record<number, keyof typeof ACTIONS>}
 */
export const CC_MAP = {};

/**
 * Request MIDI access (navigator.requestMIDIAccess) and attach a
 * controlchange listener on every input port that looks the CC number up
 * in CC_MAP and dispatch()es the matching action with source 'midi'.
 * Returns a detach function.
 * @returns {Promise<() => void>} detach
 */
export async function attach() {
  throw new Error('not implemented — see unit D5');
}
