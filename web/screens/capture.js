/**
 * screens/capture.js — Phase 1, H2. Real, but deliberately narrower than
 * design/Capture.dc.html: that artboard depicts a LIVE session (running
 * level meters, an elapsed timer, a tracklist matched by duration as
 * segments land) with no server endpoint behind it in this phase, and no
 * server endpoint COULD exist honestly yet -- capture is a real-time,
 * `woodshed capture` CLI process (server.py's endpoint table has no
 * `/api/capture` at all; see capture.py's own module doc), and matching a
 * whole tracklist needs an imported playlist's metadata (Phase 3's M1,
 * Spotify import) this repo has no way to produce yet. Building the live
 * meters/queue against nothing would be exactly the failure mode
 * CLAUDE.md spends a page on: inventing a measurement the tool cannot
 * make.
 *
 * What IS real here: this screen shows, for the viewer's current setlist
 * (app.js's `currentSetlist()` — the same preference dashboard.js uses),
 * every song still flagged `needs_audio` by `GET /api/setlist/<slug>`
 * (F1) — genuine data, not fabricated — and the exact CLI command to
 * capture each one.
 *
 * mount(el, payload) — app.js's contract 1. `payload` is `{}` this phase
 * (no GET endpoint feeds this route; see app.js's own docstring) plus
 * `params: {}`. This screen fetches `/api/setlists` and
 * `/api/setlist/<current>` itself, the same two calls app.js's `#/`
 * loadPayload makes for the dashboard.
 *
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void}
 */
import { currentSetlist, get } from '../app.js';

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function captureCommand(row) {
  const title = row.title.replace(/"/g, '\\"');
  return `woodshed capture "${title}"`;
}

export function mount(el, payload) {
  el.innerHTML = `
    <div style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
                font-family:Archivo,'Helvetica Neue',Arial,sans-serif;padding:34px 48px">
      <div style="font-size:26px;font-weight:600;letter-spacing:-.015em">Capture</div>
      <div style="font-size:15px;color:var(--ink-2,#9CAAA4);margin-top:6px;max-width:640px;line-height:1.5">
        Capture is a terminal command this phase, not a live page — arm the loopback device,
        play exactly one track, and it stops itself after the silence at the end.
        Everything the output device plays is recorded, notifications included — mute them.
      </div>
      <div data-body style="margin-top:28px;color:var(--ink-3,#6A7873);font-size:14px"></div>
    </div>`;
  const body = el.querySelector('[data-body]');

  (async () => {
    let setlists;
    try {
      setlists = await get('/api/setlists');
    } catch (err) {
      body.textContent = `Couldn't reach the server: ${err.message}`;
      return;
    }
    if (setlists.length === 0) {
      body.textContent = 'No setlists yet — nothing to check for missing audio against.';
      return;
    }
    let slug = currentSetlist();
    if (!slug || !setlists.some((s) => s.slug === slug)) slug = setlists[0].slug;

    const dashboard = await get(`/api/setlist/${encodeURIComponent(slug)}`);
    const needsAudio = dashboard.rows.filter((r) => r.needs_audio);

    if (needsAudio.length === 0) {
      body.innerHTML = `Every song in <strong>${escapeHtml(dashboard.name)}</strong> already has audio bound.`;
      return;
    }

    body.innerHTML = `
      <div style="font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3,#6A7873)">
        ${needsAudio.length} of ${dashboard.song_count} in ${escapeHtml(dashboard.name)} need audio</div>
      <div data-rows style="margin-top:14px;display:flex;flex-direction:column;gap:10px"></div>`;
    const rowsEl = body.querySelector('[data-rows]');
    for (const row of needsAudio) {
      const command = captureCommand(row);
      const div = document.createElement('div');
      div.style.cssText =
        'background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);' +
        'border-radius:5px;padding:14px 18px;display:flex;align-items:center;gap:18px';
      div.innerHTML = `
        <div style="flex:1;min-width:0">
          <div style="font-size:15px;color:var(--ink,#E8EEEB)">${escapeHtml(row.title)}</div>
        </div>
        <code data-cmd style="font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;
                    font-size:12.5px;color:var(--ink-2,#9CAAA4);background:var(--sunken,#0F1614);
                    padding:6px 10px;border-radius:4px;white-space:nowrap;overflow:auto;max-width:60%"
              >${escapeHtml(command)}</code>
        <button data-copy style="background:var(--raised,#1B2422);color:var(--ink-2,#9CAAA4);
                    border:none;border-radius:4px;padding:8px 14px;font-size:13px;cursor:pointer">Copy</button>`;
      div.querySelector('[data-copy]').addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(command);
        } catch {
          // clipboard permission denied or unavailable -- the command is
          // already visible in the row, just not copied for you.
        }
      });
      rowsEl.appendChild(div);
    }
  })().catch((err) => {
    body.textContent = `Couldn't load: ${err.message}`;
  });
}
