/**
 * screens/dashboard.js — "Next up" band, stat cards, the song table
 * (design/Dashboard.dc.html). Not built this phase — no GET /api/dashboard
 * exists yet server-side (server.py's routes are song/peaks/audio/rep/
 * section only). A later phase's unit both adds that endpoint AND updates
 * app.js's `#/` route to fetch it; until then app.js's loadPayload for
 * this route resolves to `{}`.
 *
 * mount(el, payload) / optional unmount() — app.js's contract 1. *payload*
 * is loosely typed (`{}` this phase, plus whatever `params` app.js merges
 * in — none, for the `#/` route) since nothing implements the real shape
 * yet; do not invent a schema here.
 *
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void | (() => void)}
 */
export function mount(el, payload) {
  throw new Error('not implemented — a later phase\'s unit (Dashboard is not scheduled to D1-D7)');
}
