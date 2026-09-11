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
 *   z           retract_rep     \ mirrors CC85; z = "undo" in most editors,
 *   x           retract_rep     /  and 'x' is the key the plan's Group K1
 *               names for it ("c confirms, x retracts"). Both are mapped
 *               rather than one replacing the other: 'z' is what has been
 *               under Paolo's hand since Phase 0, 'x' is what the plan and
 *               any future written-down shortcut list say. Two keys naming
 *               ONE action is not a second decision about what a key
 *               means — that rule is about behaviour living outside
 *               actions.js, and there is still exactly one retract_rep.
 *   c           confirm_clean   — c = "clean"
 *   r           restart_section — r = "restart"
 *   -           transpose_down  \ CLAUDE.md fixes these two literally:
 *   =           transpose_up    /  "shown ... as a number ... '-'/'='"
 *   l           loop_toggle     — l = "loop"
 *   f           fullscreen      — f = "fullscreen", browser-convention key
 *   ?           help            — '?' opens a help overlay, browser convention
 *   [           nudge_start     \ Phase 1, G1. Both widen the selected
 *   ]           nudge_end       /  section outward by a fixed millisecond
 *               step (song.js's NUDGE_S) — '[' pulls the start earlier,
 *               ']' pushes the end later, the same "bracket the range you
 *               want" reading as a video editor's in/out points. No
 *               opposite-direction key: the plan names only these two, and
 *               a boundary that overshoots is one more nudge-then-drag away.
 * @type {Record<string, keyof typeof ACTIONS>}
 */
export const KEY_MAP = {
  ' ': 'play_pause',
  ArrowRight: 'next_section',
  ArrowLeft: 'prev_section',
  ArrowUp: 'speed_up',
  ArrowDown: 'speed_down',
  z: 'retract_rep',
  x: 'retract_rep',
  '[': 'nudge_start',
  ']': 'nudge_end',
  c: 'confirm_clean',
  r: 'restart_section',
  '-': 'transpose_down',
  '=': 'transpose_up',
  l: 'loop_toggle',
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
 * No repeat-suppression, and no *third* place a keypress's meaning gets
 * decided beyond KEY_MAP + ACTIONS -- that part of the original rule holds.
 * Two things were found live 2026-09-06 to be necessary rather than
 * "a second place meaning gets decided", and are narrowly scoped to exactly
 * the keys this file already recognizes:
 *
 * - preventDefault() on a recognized key. Without it, Space also pages the
 *   browser down (its native default action) every time it plays/pauses --
 *   both fire, so the whole page visibly jumps. Several of KEY_MAP's other
 *   keys (arrows) have the same native-scroll conflict. This doesn't add a
 *   second decision about what a key means; it only stops the browser's own
 *   competing default for a key this file has already claimed.
 * - an editable-element guard. With no focus check, typing into ANY text
 *   field (the song page inspector's Name/Notes, say) also dispatches
 *   whatever KEY_MAP entry the typed character happens to match ('r', 'c',
 *   'm', 'f', space, ...) -- indistinguishable from a real shortcut press.
 *   Skipped entirely (not merely un-prevented) when the event's target is a
 *   real text-entry surface, so typing stays just typing.
 * @param {Window | HTMLElement} [target=window]
 * @returns {() => void} detach
 */
export function attach(target = window) {
  function isTextEntry(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }
  function onKeydown(event) {
    if (isTextEntry(event.target)) return;
    const name = KEY_MAP[event.key];
    if (name) {
      event.preventDefault();
      dispatch(name, 'keyboard');
    }
  }
  target.addEventListener('keydown', onKeydown);
  return () => target.removeEventListener('keydown', onKeydown);
}
