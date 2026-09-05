/**
 * screens/progress.js — range chips, area chart, section table with
 * sparklines (design/Progress.dc.html). Not built this phase — no GET
 * /api/progress/<slug> endpoint exists server-side yet, and the readiness
 * numbers it would chart come from woodshed.sections.coverage_readiness
 * over practice/reps.jsonl (CLAUDE.md: "readiness is measured over covered
 * song time"), which practice.py has not assembled yet either.
 *
 * mount(el, payload) / optional unmount() — app.js's contract 1. *payload*
 * is loosely typed (`{}` this phase, plus `params: {slug}` the router
 * merges in for `#/progress/<slug>`) since nothing implements the real
 * shape yet.
 *
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void | (() => void)}
 */
export function mount(el, payload) {
  throw new Error('not implemented — a later phase\'s unit (Progress is not scheduled to D1-D7)');
}
