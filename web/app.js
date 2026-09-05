/**
 * app.js — router, fetch helper, screen mounting.
 *
 * D0 owns this file's cross-unit contract in full (the route table and the
 * fetch helper below are real, not stubs — they are exactly the "small
 * table" and "fetch helper" the plan's Group D brief asks D0 to fix). D1
 * ("the shell") wires `start()` into web/index.html and may adjust its
 * body, but ROUTES / get / post are ground truth: every screen and every
 * other web/*.js module reads or calls these, never `fetch` directly.
 *
 * ---- Contract 1: the screen shape ----
 * Every web/screens/*.js exports `mount(el, payload)`, called with the
 * screen's root element and the already-parsed JSON payload for its route
 * (see each route's `loadPayload` below). `mount` returns either nothing,
 * or an `unmount` function (`() => void`) the router calls before mounting
 * the NEXT screen, so a screen can tear down timers/listeners/audio nodes
 * it started. `payload` additionally carries a `params` key: the route's
 * named regex capture groups (e.g. `{slug: 'cant-stop'}` for `#/song/…`,
 * `{slug, sectionId}` for `#/practice/…/…`) — the endpoint JSON alone has
 * no way to carry the sectionId a practice route names, so the router
 * merges params in rather than changing mount()'s two-argument shape.
 *
 * ---- Contract 2: routes ----
 * Hash-based, matched against `location.hash` with the leading '#' and
 * leading '/' stripped down to nothing (`'#/song/x'` -> pattern tested
 * against `'/song/x'`). `loadModule` is a dynamic import() so the initial
 * page load does not pull in every screen; `loadPayload` resolves to
 * exactly what `mount`'s payload becomes (before params are merged in).
 * capture/library/progress have no GET endpoint yet in this phase (only
 * /api/song, /api/peaks, /api/audio, /api/setlists and /api/setlist/<slug>
 * exist server-side, per server.py's module docstring) — their loadPayload
 * is `async () => ({})` until a later phase's unit adds the endpoint AND
 * updates the route here. `#/`'s loadPayload (Phase 1, F2) resolves the
 * "current setlist" — a per-viewer preference, not itself a route, see
 * dashboard.js's decision 1 — from localStorage, falling back to the first
 * setlist GET /api/setlists returns; `{setlists: []}` when there are none
 * yet, which dashboard.js renders as an empty state rather than fetching a
 * dashboard payload for a setlist that doesn't exist.
 */

import { attach } from './keys.js';

const SCREEN_ROOT_ID = 'screen-root';

/**
 * @typedef {Object} Route
 * @property {RegExp} pattern - tested against location.hash with the '#'
 *   stripped; named groups (`(?<slug>[^/]+)`) become `params`.
 * @property {() => Promise<{mount: (el: HTMLElement, payload: any) => (void | (() => void))}>} loadModule
 * @property {(params: Record<string, string>) => Promise<any>} loadPayload
 */

/** @type {Route[]} */
export const ROUTES = [
  {
    pattern: /^\/$/,
    loadModule: () => import('./screens/dashboard.js'),
    loadPayload: async () => {
      const setlists = await get('/api/setlists');
      if (setlists.length === 0) return { setlists: [] };
      let slug = null;
      try {
        slug = localStorage.getItem('woodshed:setlist');
      } catch {
        // private window / storage disabled -- fall through to the default below
      }
      if (!slug || !setlists.some((s) => s.slug === slug)) slug = setlists[0].slug;
      const dashboard = await get(`/api/setlist/${encodeURIComponent(slug)}`);
      return { setlists, currentSetlist: slug, ...dashboard };
    },
  },
  {
    pattern: /^\/song\/(?<slug>[^/]+)$/,
    loadModule: () => import('./screens/song.js'),
    loadPayload: async (params) => get(`/api/song/${encodeURIComponent(params.slug)}`),
  },
  {
    pattern: /^\/practice\/(?<slug>[^/]+)\/(?<sectionId>[^/]+)$/,
    loadModule: () => import('./screens/practice.js'),
    // Same endpoint as the song screen — there is no separate practice
    // payload; practice.js finds its section by params.sectionId within
    // payload.sections.
    loadPayload: async (params) => get(`/api/song/${encodeURIComponent(params.slug)}`),
  },
  {
    pattern: /^\/capture$/,
    loadModule: () => import('./screens/capture.js'),
    loadPayload: async () => ({}),
  },
  {
    pattern: /^\/library$/,
    loadModule: () => import('./screens/library.js'),
    loadPayload: async () => ({}),
  },
  {
    pattern: /^\/progress\/(?<slug>[^/]+)$/,
    loadModule: () => import('./screens/progress.js'),
    loadPayload: async () => ({}),
  },
];

/**
 * GET *path*, parse the JSON body, throw on a non-2xx response. The
 * thrown Error's `.message` is the server's `{error}` body when present
 * (every WoodshedError/ValidationError the handler catches becomes exactly
 * that shape — see server.py's `_error`), else `"<status> <statusText>"`.
 * @param {string} path
 * @returns {Promise<any>}
 */
export async function get(path) {
  const res = await fetch(path);
  if (!res.ok) throw await responseError(res);
  return res.json();
}

/**
 * POST *body* (JSON-encoded) to *path*; same error contract as get().
 * @param {string} path
 * @param {any} [body]
 * @returns {Promise<any>}
 */
export async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw await responseError(res);
  return res.json();
}

/** @param {Response} res */
async function responseError(res) {
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body && typeof body.error === 'string') message = body.error;
  } catch {
    // body wasn't JSON (or was empty) -- keep the status-line message.
  }
  return new Error(message);
}

/**
 * Match the current location.hash against ROUTES.
 * @returns {{route: Route, params: Record<string, string>} | null}
 */
function matchRoute() {
  const hash = location.hash.replace(/^#/, '') || '/';
  for (const route of ROUTES) {
    const m = hash.match(route.pattern);
    if (m) return { route, params: m.groups ?? {} };
  }
  return null;
}

/** The previous screen's unmount, or null if it returned none. Module-private. */
let currentUnmount = null;

/**
 * Run one navigation: unmount the current screen (if it returned an
 * unmount function), resolve the new route's module + payload, mount it
 * into `#${SCREEN_ROOT_ID}`.
 *
 * Deliberately does not catch a screen's mount() throwing, nor a failed
 * loadPayload() fetch — both propagate as a rejected promise. This is
 * "fail loud, not silent" per the Group D brief: a stub screen's
 * `not implemented` Error, or a 404 from a bad slug, belongs in the
 * console where it is impossible to miss, not swallowed into a blank
 * screen that looks merely idle.
 * @returns {Promise<void>}
 */
export async function route() {
  const el = document.getElementById(SCREEN_ROOT_ID);
  if (!el) {
    throw new Error(`app.js: index.html has no #${SCREEN_ROOT_ID} to mount into`);
  }
  if (currentUnmount) {
    currentUnmount();
    currentUnmount = null;
  }
  el.textContent = '';

  const matched = matchRoute();
  if (!matched) {
    el.textContent = `No such page: ${location.hash || '#/'}`;
    return;
  }
  const { route: matchedRoute, params } = matched;
  const [payload, mod] = await Promise.all([
    matchedRoute.loadPayload(params),
    matchedRoute.loadModule(),
  ]);
  const result = mod.mount(el, { ...payload, params });
  currentUnmount = typeof result === 'function' ? result : null;
}

/**
 * Wire `route()` to hashchange + initial load. Called once from
 * index.html.
 * @returns {void}
 */
export function start() {
  attach(window);
  window.addEventListener('hashchange', () => {
    route();
  });
  route();
}

export { SCREEN_ROOT_ID };
