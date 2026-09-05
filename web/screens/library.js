/**
 * screens/library.js — "ways in" cards, import table (design/Library.dc.html).
 * Not built this phase — no GET /api/library endpoint exists server-side
 * yet.
 *
 * mount(el, payload) / optional unmount() — app.js's contract 1. *payload*
 * is loosely typed (`{}` this phase, plus `params` — none, for `#/library`)
 * since nothing implements the real shape yet.
 *
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void | (() => void)}
 */
export function mount(el, payload) {
  throw new Error('not implemented — a later phase\'s unit (Library is not scheduled to D1-D7)');
}
