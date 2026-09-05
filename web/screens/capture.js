/**
 * screens/capture.js — live capture: stat tiles, level meters, pass
 * timeline, segment queue (design/Capture.dc.html). Not built this phase —
 * no GET /api/capture endpoint exists server-side yet, and capture.py
 * (the audio-device recorder CLAUDE.md's "Don't" section requires — an
 * audio device, never a Spotify/DRM path) is not wired to the server in
 * this pass either.
 *
 * mount(el, payload) / optional unmount() — app.js's contract 1. *payload*
 * is loosely typed (`{}` this phase, plus `params` — none, for `#/capture`)
 * since nothing implements the real shape yet.
 *
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void | (() => void)}
 */
export function mount(el, payload) {
  throw new Error('not implemented — a later phase\'s unit (Capture is not scheduled to D1-D7)');
}
