/**
 * screens/library.js — the ways in, and every song's audio state
 * (design/Library.dc.html). Phase 3, M3.
 *
 * mount(el, payload) — app.js's contract 1. `payload` is the body of
 * `GET /api/library` (server.py's `_library`):
 *
 * {
 *   songs: Array<{slug, title, artist, album, duration_s, needs_audio,
 *                 file, spotify_id, section_count}>,
 *   library_paths: Array<{path, exists}>,
 *   spotify: {client_id_configured, connected},
 *   params: {},
 * }
 *
 * The screen exists for one sentence in docs/04-sources.md: "**needs-audio**
 * is a first-class state, not an error... silence about a gap is how a
 * setlist quietly turns out to be half practisable the week before the gig."
 * So the audio column is the point of the table, and a song with no file
 * gets the two real next steps beside it — scan, or capture — rather than a
 * badge and no way forward.
 *
 * Three scope decisions:
 *
 * 1. **Scanning is per row, on demand.** `POST /api/library/scan` walks the
 *    disk; doing that for every needs-audio song on mount would make opening
 *    the screen cost a full library walk per song. A row's "Scan" button
 *    expands its own candidate list underneath it.
 * 2. **Binding is always a click on a named file**, never a "best match"
 *    button. That is the same rule the CLI's `--bind N` follows, and the
 *    reason is in the doc: a fuzzy match that binds itself is invisible
 *    until you practise the wrong recording. Each candidate shows its score
 *    and WHY it matched (tags or filename), because that is what a person
 *    needs to judge it.
 * 3. **Spotify import degrades to instructions.** With no token stored the
 *    paste box stays, and the button says what to run once in a terminal
 *    (`woodshed import --connect`) instead of failing on a click. The OAuth
 *    round trip belongs in the CLI — it needs a browser the human drives and
 *    a paste back, not a callback listener inside a practice tool.
 */
import { get, post } from '../app.js';

const STYLE_ID = 'library-screen-style';

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ws-lib .mono { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace }
    .ws-lib .num { font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1 }
    .ws-lib .lbl { font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;letter-spacing:.2em;
      text-transform:uppercase;color:var(--ink-3,#6A7873) }
    .ws-lib .entry { flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;padding:16px 18px;
      border:1px solid var(--line,#26302E);border-radius:6px;background:var(--raised-dim,#131B19) }
    .ws-lib .irow { display:grid;grid-template-columns:42px 300px 1fr 66px 280px;align-items:center;
      gap:18px;padding:13px 12px;border-bottom:1px solid var(--line-dim,#1C2523) }
    .ws-lib .art { width:42px;height:42px;border-radius:3px;background:var(--raised,#1B2422);
      display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--ink-4,#5B6A64) }
    .ws-lib .badge { font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.12em;
      text-transform:uppercase;padding:3px 8px;border-radius:3px;white-space:nowrap;
      background:var(--raised,#1B2422);color:var(--ink-2,#9CAAA4) }
    .ws-lib .btn { border:1px solid var(--line,#26302E);border-radius:4px;padding:6px 12px;
      font-size:13px;color:var(--ink,#E8EEEB);background:none;cursor:pointer;font-family:inherit }
    .ws-lib .btn:hover { border-color:var(--accent-dim,#8A5C29) }
    .ws-lib .btn[disabled] { opacity:.5;cursor:default }
    .ws-lib .fld { background:var(--sunken,#0F1614);border:1px solid var(--line,#26302E);
      border-radius:4px;padding:8px 11px;font-size:13.5px;color:var(--ink,#E8EEEB);
      font-family:inherit;width:100% }
    .ws-lib .cand { display:flex;align-items:center;gap:12px;padding:7px 12px 7px 72px;
      border-bottom:1px solid var(--line-dim,#1C2523);font-size:13px }
    .ws-lib a { color:var(--accent,#E0913F) }
  `;
  document.head.appendChild(style);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/** "4:29" from seconds — the artboard's own length format. */
export function lengthLabel(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** Two initials for the artwork square, from artist or title. */
export function initials(row) {
  const source = (row.artist || row.title || '?').trim();
  const words = source.split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : source.slice(0, 2);
  return letters.toUpperCase();
}

export function mount(el, payload) {
  ensureStyle();
  render(el, payload);
}

async function reload(el) {
  render(el, await get('/api/library'));
}

function render(el, payload) {
  const songs = payload.songs ?? [];
  const needing = songs.filter((row) => row.needs_audio);
  const paths = payload.library_paths ?? [];
  const spotify = payload.spotify ?? {};

  el.innerHTML = `
    <div class="ws-lib" style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
         font-family:Archivo,'Helvetica Neue',Arial,sans-serif;padding:30px 44px 26px;box-sizing:border-box">

      <div style="display:flex;align-items:baseline;gap:18px;padding-bottom:20px;
                  border-bottom:1px solid var(--line,#26302E)">
        <div style="font-size:26px;font-weight:600;letter-spacing:-.015em">Library</div>
        <div style="font-size:15px;color:var(--ink-3,#6A7873)">
          ${songs.length} song${songs.length === 1 ? '' : 's'}
          &nbsp;&middot;&nbsp; ${needing.length} need${needing.length === 1 ? 's' : ''} audio</div>
        <a href="#/" style="margin-left:auto;font-size:14px">&larr; Dashboard</a>
      </div>

      <div style="display:flex;gap:14px;padding:22px 0 26px">
        <div class="entry" style="border-color:var(--accent-dim,#8A5C29)">
          <div style="font-size:17px;font-weight:500">Capture what's playing</div>
          <div style="font-size:13.5px;color:var(--ink-3,#6A7873);line-height:1.5">
            Records the machine's own output, bit-for-bit. Play a whole playlist once;
            it splits on the gaps.</div>
          <a href="#/capture" class="btn" style="margin-top:auto;text-decoration:none;
             display:inline-block;width:fit-content">Open capture &rarr;</a>
        </div>

        <div class="entry">
          <div style="font-size:17px;font-weight:500">Scan a folder</div>
          <div style="font-size:13.5px;color:var(--ink-3,#6A7873);line-height:1.5">
            Tags first, then filename. Candidates are shown &mdash; nothing binds on a
            fuzzy match.</div>
          <div class="mono" style="font-size:12px;color:var(--ink-4,#5B6A64);margin-top:auto">
            ${paths.length === 0
              ? 'no library_paths in config.yaml'
              : paths.map((p) => `${escapeHtml(p.path)}${p.exists ? '' : ' (missing)'}`).join('<br>')}
          </div>
        </div>

        <div class="entry">
          <div style="font-size:17px;font-weight:500">Search Spotify</div>
          <div style="font-size:13.5px;color:var(--ink-3,#6A7873);line-height:1.5">
            Titles, artists and lengths &mdash; never audio. A pasted playlist becomes a
            whole set of needs-audio songs.</div>
          ${spotify.connected
            ? `<div style="display:flex;gap:8px;margin-top:auto">
                 <input class="fld" data-import-ref placeholder="open.spotify.com/playlist/…">
                 <button class="btn" data-import-go>Import</button>
               </div>
               <div data-import-status style="font-size:12.5px;color:var(--ink-3,#6A7873)"></div>`
            : `<div class="mono" style="font-size:12px;color:var(--ink-4,#5B6A64);margin-top:auto">
                 ${spotify.client_id_configured
                   ? 'not connected yet &mdash; run <span style="color:var(--accent,#E0913F)">woodshed import --connect</span> once'
                   : 'put spotify.client_id in config.yaml, then run <span style="color:var(--accent,#E0913F)">woodshed import --connect</span>'}
               </div>`}
        </div>
      </div>

      <div class="irow" style="border-bottom:1px solid var(--line,#26302E);padding-bottom:9px">
        <div></div><div class="lbl" style="font-size:10px">Track</div>
        <div class="lbl" style="font-size:10px">Album</div>
        <div class="lbl" style="font-size:10px">Length</div>
        <div class="lbl" style="font-size:10px">Audio</div>
      </div>
      <div data-rows>${songs.map(rowHtml).join('')}</div>
      ${songs.length === 0
        ? `<div style="padding:24px 12px;font-size:15px;color:var(--ink-3,#6A7873)">
             Nothing in the library yet &mdash; capture something, or
             <code>woodshed add "path/to/song.wav"</code>.</div>`
        : ''}
    </div>`;

  wire(el, payload);
}

function rowHtml(row) {
  return `
    <div class="irow" data-song="${escapeHtml(row.slug)}">
      <div class="art">${escapeHtml(initials(row))}</div>
      <div>
        <a href="#/song/${encodeURIComponent(row.slug)}"
           style="font-size:15.5px;text-decoration:none">${escapeHtml(row.title)}</a>
        <div style="font-size:13px;color:var(--ink-3,#6A7873);margin-top:2px">
          ${escapeHtml(row.artist || '')}</div>
      </div>
      <div style="font-size:14px;color:var(--ink-2,#9CAAA4)">${escapeHtml(row.album || '')}</div>
      <div class="mono num" style="font-size:13px;color:var(--ink-2,#9CAAA4)">
        ${lengthLabel(row.duration_s)}</div>
      <div style="display:flex;align-items:center;gap:9px">
        ${row.needs_audio
          ? `<div class="badge">needs audio</div>
             <button class="btn" data-scan="${escapeHtml(row.slug)}">Scan</button>
             <a href="#/capture" class="btn" style="text-decoration:none;
                border-color:var(--accent-dim,#8A5C29);color:var(--accent,#E0913F)">Capture</a>`
          : `<span style="color:var(--good,#5FA88F)">&check;</span>
             <span class="mono" style="font-size:12.5px;color:var(--ink-3,#6A7873)">
               ${escapeHtml(row.file || '')}</span>`}
      </div>
    </div>
    <div data-candidates="${escapeHtml(row.slug)}"></div>`;
}

function wire(el, payload) {
  for (const button of el.querySelectorAll('[data-scan]')) {
    button.addEventListener('click', async () => {
      const slug = button.dataset.scan;
      const host = el.querySelector(`[data-candidates="${CSS.escape(slug)}"]`);
      button.disabled = true;
      button.textContent = 'Scanning…';
      try {
        const result = await post('/api/library/scan', { song: slug });
        host.innerHTML = candidatesHtml(result);
        for (const bind of host.querySelectorAll('[data-bind]')) {
          bind.addEventListener('click', async () => {
            bind.disabled = true;
            bind.textContent = 'Binding…';
            try {
              await post('/api/library/bind', { song: slug, path: bind.dataset.bind });
              await reload(el);
            } catch (err) {
              bind.disabled = false;
              bind.textContent = 'Bind';
              host.insertAdjacentHTML(
                'beforeend',
                `<div class="cand" style="color:var(--warn,#C9805E)">${escapeHtml(err.message)}</div>`,
              );
            }
          });
        }
      } catch (err) {
        host.innerHTML = `<div class="cand" style="color:var(--warn,#C9805E)">
          ${escapeHtml(err.message)}</div>`;
      } finally {
        button.disabled = false;
        button.textContent = 'Scan';
      }
    });
  }

  const importGo = el.querySelector('[data-import-go]');
  if (importGo) {
    importGo.addEventListener('click', async () => {
      const field = el.querySelector('[data-import-ref]');
      const status = el.querySelector('[data-import-status]');
      status.textContent = 'importing…';
      try {
        const result = await post('/api/library/import', { ref: field.value });
        status.textContent =
          `${result.name}: ${result.created.length} imported, `
          + `${result.already_present.length} already here`;
        await reload(el);
      } catch (err) {
        status.textContent = err.message;
      }
    });
  }
  void payload;
}

function candidatesHtml(result) {
  if (!result.candidates.length) {
    return `<div class="cand" style="color:var(--ink-3,#6A7873)">
      ${result.library_paths.length
        ? 'no candidates under ' + escapeHtml(result.library_paths.join(', '))
        : 'no library_paths configured — add them to config.yaml'}</div>`;
  }
  return result.candidates.map((c) => `
    <div class="cand">
      <span class="mono num" style="color:var(--ink-3,#6A7873)">${c.score.toFixed(2)}</span>
      <span class="badge">by ${escapeHtml(c.why)}</span>
      <span class="mono" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
            white-space:nowrap">${escapeHtml(c.path)}</span>
      <button class="btn" data-bind="${escapeHtml(c.path)}">Bind</button>
    </div>`).join('');
}
