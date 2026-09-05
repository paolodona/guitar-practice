/**
 * keys.js — keyboard -> actions.js. D5's file to finish.
 *
 * Per CLAUDE.md's "one action table" rule this file may contain ONLY a map
 * from a keyboard event to an action name, plus the listener that reads
 * the map and calls dispatch(name, 'keyboard') — no other behaviour, no
 * DOM beyond add/removeEventListener. See actions.js for ACTIONS and
 * dispatch()'s signature; do not duplicate the CC-vs-key decision there.
 */

import { ACTIONS, dispatch } from './actions.js';

/**
 * KeyboardEvent.key -> action name. D5's call (D0 fixed only that this file
 * may hold nothing but this map and the attach() listener below). Chosen to
 * sit under the left hand while the right hand holds a pick, one key per
 * action, no chords:
 *
 *   ' '         play_pause      — the universal transport key
 *   ArrowRight  next_section    \ mirrors the foot strip's CC81/82 pair and
 *   ArrowLeft   prev_section    /  the physical left-to-right song order
 *   ArrowUp     speed_up        \ mirrors CC83/84; up/down reads naturally
 *   ArrowDown   speed_down      /  as "more/less" speed
 *   z           retract_rep     — mirrors CC85; z = "undo" in most editors
 *   c           confirm_clean   — c = "clean"
 *   r           restart_section — r = "restart"
 *   -           transpose_down  \ CLAUDE.md fixes these two literally:
 *   =           transpose_up    /  "shown ... as a number ... '-'/'='"
 *   l           loop_toggle     — l = "loop"
 *   m           metronome       — m = "metronome"
 *   f           fullscreen      — f = "fullscreen", browser-convention key
 *   ?           help            — '?' opens a help overlay, browser convention
 *
 * nudge_start/nudge_end are deliberately NOT bound here — the plan reserves
 * '['/']' for Phase 1's G1, out of scope for this unit. Their ACTIONS
 * entries exist (actions.js) but no key reaches them yet.
 * @type {Record<string, keyof typeof ACTIONS>}
 */
export const KEY_MAP = {
  ' ': 'play_pause',
  ArrowRight: 'next_section',
  ArrowLeft: 'prev_section',
  ArrowUp: 'speed_up',
  ArrowDown: 'speed_down',
  z: 'retract_rep',
  c: 'confirm_clean',
  r: 'restart_section',
  '-': 'transpose_down',
  '=': 'transpose_up',
  l: 'loop_toggle',
  m: 'metronome',
  f: 'fullscreen',
  '?': 'help',
};

/**
 * Attach a keydown listener on *target* that looks *event.key* up in
 * KEY_MAP and dispatch()es the matching action with source 'keyboard'.
 * Returns a detach function (mirrors the screens/*.js mount/unmount shape
 * from app.js's contract 1, so a screen that wants scoped key handling can
 * call this itself and undo it on unmount).
 *
 * Nothing beyond the lookup-and-dispatch lives here — no preventDefault, no
 * focus/input guard, no repeat-suppression. That is a named rule (see this
 * file's module docstring and CLAUDE.md's "one action table"), not an
 * oversight: any of those would be a second place a keypress's meaning gets
 * decided.
 * @param {Window | HTMLElement} [target=window]
 * @returns {() => void} detach
 */
export function attach(target = window) {
  function onKeydown(event) {
    const name = KEY_MAP[event.key];
    if (name) dispatch(name, 'keyboard');
  }
  target.addEventListener('keydown', onKeydown);
  return () => target.removeEventListener('keydown', onKeydown);
}
