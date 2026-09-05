/**
 * screens/dashboard.js — "Next up" band, stat cards, the song table
 * (design/Dashboard.dc.html). Phase 1, F2 — the endpoints this screen
 * needs (GET /api/setlists, GET /api/setlist/<slug>) are F1's, built
 * immediately ahead of this unit in the same group.
 *
 * mount(el, payload) — app.js's contract 1. `payload` is assembled by
 * app.js's `#/` route (not the raw body of either endpoint alone):
 *
 * {
 *   setlists: Array<{slug, name, tuning, date, song_count}>,  // GET /api/setlists
 *   currentSetlist: string | null,       // the slug app.js resolved to load below
 *   ...                                  // GET /api/setlist/<currentSetlist>'s body,
 *                                        // spread in directly (slug, name, tuning,
 *                                        // date, venue, weeks_to_gig, song_count,
 *                                        // songs_at_target, needs_audio_count,
 *                                        // next_up, rows) — see server.py's _setlist.
 *   params: {},
 * }
 *
 * Three scope decisions:
 *
 * 1. Switching setlists (the pills in the top bar) does NOT change
 *    location.hash — there is only one dashboard route (`#/`) and the
 *    "current setlist" is a per-viewer preference, not a navigable page.
 *    A click remembers the choice in localStorage (read back by app.js's
 *    loadPayload on the next visit) and this module re-fetches
 *    GET /api/setlist/<slug> + re-renders itself directly, bypassing the
 *    router entirely — the same reason practice.js keeps its own local
 *    ladder state rather than reaching into app.js for it.
 * 2. The footer's two counts are honest about what this payload actually
 *    knows: "N SONGS IN THIS SETLIST" (this setlist's own row count) and
 *    "M SETLISTS" (the length of the /api/setlists list already fetched).
 *    The artboard's "34 SONGS" library-wide total and "17 MORE IN THIS
 *    SET" pagination both need data no endpoint in this phase provides
 *    (a repo-wide song scan; a capped/paginated row list) — inventing
 *    either would be a number nothing on disk backs. Every row renders
 *    instead of a fixed page size, which is the honest behaviour for a
 *    setlist this phase has no reason to believe is unbounded.
 * 3. No setlist on disk yet: `setlists` is `[]` and there is no
 *    `currentSetlist` to have fetched a dashboard payload for. Renders a
 *    plain empty state ("create one with `woodshed setlist add`") rather
 *    than a blank screen or a fabricated row.
 */
import { get } from '../app.js';

const STYLE_ID = 'dashboard-screen-style';
const SETLIST_STORAGE_KEY = 'woodshed:setlist';

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ws-dash { font-family:Archivo,'Helvetica Neue',Arial,sans-serif }
    .ws-dash .mono { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace }
    .ws-dash .lbl { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;
      letter-spacing:.2em;text-transform:uppercase;color:var(--ink-3,#6A7873) }
    .ws-dash .num { font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;
      letter-spacing:-.035em;line-height:.84;font-weight:700 }
    .ws-dash .pill { font-size:15px;padding:7px 14px;border-radius:4px;color:var(--ink-2,#9CAAA4);
      background:none;border:none;cursor:pointer;font-family:inherit }
    .ws-dash .pill.sel { background:var(--raised,#1B2422);color:var(--ink,#E8EEEB) }
    .ws-dash .row { display:grid;grid-template-columns:380px 1fr 210px 130px 96px 20px;
      align-items:center;gap:24px;padding:17px 12px;border-bottom:1px solid var(--hairline,#1C2523);
      text-decoration:none;color:inherit }
    .ws-dash a.row:hover { background:#111917 }
    .ws-dash .track { height:6px;border-radius:3px;background:var(--hairline,#1C2523);overflow:hidden }
    .ws-dash .fill { height:6px;border-radius:3px;background:var(--accent,#E0913F) }
    .ws-dash .badge { font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.12em;
      text-transform:uppercase;padding:3px 8px;border-radius:3px;white-space:nowrap }
    .ws-dash .practise-btn { background:var(--accent,#E0913F);color:var(--ground,#0C1211);
      border:none;border-radius:4px;padding:15px 30px;font-size:19px;font-weight:600;
      white-space:nowrap;cursor:pointer;text-decoration:none;display:inline-block }
    .ws-dash .practise-btn:hover { background:var(--accent-hover,#EFA95C) }
  `;
  document.head.appendChild(style);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function rememberSetlist(slug) {
  try {
    localStorage.setItem(SETLIST_STORAGE_KEY, slug);
  } catch {
    // private window / storage disabled -- the pill click still works for
    // this visit, only the memory of it across visits is lost.
  }
}

/** Days since *iso* (a last_practised timestamp), or null if never. */
function daysSince(iso) {
  if (!iso) return null;
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86400000));
}

/** "today" / "yesterday" / "N days" / "N weeks" -- matches the artboard's
 * examples (design/Dashboard.dc.html: "yesterday", "16 days", "4 days"). */
function relativeLabel(iso) {
  const days = daysSince(iso);
  if (days === null) return null;
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 14) return `${days} days`;
  const weeks = Math.floor(days / 7);
  return `${weeks} week${weeks === 1 ? '' : 's'}`;
}

function rowHtml(row) {
  if (row.needs_audio && row.readiness === null) {
    // No song.yaml at all yet -- nothing to link to, nothing measured.
    return `
      <div class="row" style="background:none">
        <div><div style="font-size:17px;font-weight:500;color:var(--ink-3,#6A7873)">${escapeHtml(row.title)}</div></div>
        <div style="display:flex;align-items:center;gap:9px">
          <div class="badge" style="background:var(--raised,#1B2422);color:var(--ink-2,#9CAAA4)">needs audio</div>
          <div style="font-size:14px;color:var(--ink-3,#6A7873)">drop a file, or scan the library</div>
        </div>
        <div style="font-size:14px;color:var(--ink-4,#5B6A64)">&mdash;</div>
        <div style="font-size:14px;color:var(--ink-4,#5B6A64)">&mdash;</div>
        <div class="mono" style="font-size:13px;color:var(--ink-4,#5B6A64);text-align:right">?</div>
        <div></div>
      </div>`;
  }

  const pct = Math.round(row.readiness * 100);
  const atTarget = row.section_count > 0 && row.sections_under_target === 0;
  const sectionsText = row.section_count === 0
    ? '&mdash;'
    : `${row.section_count} &middot; <span style="color:${atTarget ? 'var(--good,#5FA88F)' : 'var(--ink-3,#6A7873)'}">${
        atTarget ? 'all at target' : `${row.sections_under_target} under target`
      }</span>`;
  const practisedLabel = relativeLabel(row.last_practised);
  const shiftColor = row.shift === 0 ? 'var(--good,#5FA88F)' : 'var(--accent,#E0913F)';
  const shiftText = row.shift > 0 ? `+${row.shift}` : String(row.shift);

  return `
    <a class="row" href="#/song/${encodeURIComponent(row.slug)}">
      <div>
        <div style="font-size:17px;font-weight:500">${escapeHtml(row.title)}</div>
        <div style="font-size:14px;color:var(--ink-3,#6A7873);margin-top:2px">${escapeHtml(row.artist ?? '')}</div>
      </div>
      <div style="display:flex;align-items:center;gap:12px">
        <div class="track" style="flex-grow:1"><div class="fill" style="width:${pct}%;${
          pct >= 100 ? 'background:var(--good,#5FA88F)' : ''
        }"></div></div>
        <div class="mono num" style="font-size:14px;width:38px;text-align:right">${pct}%</div>
      </div>
      <div style="font-size:14px;color:var(--ink-2,#9CAAA4)">${sectionsText}</div>
      <div style="display:flex;align-items:center;gap:8px">
        <div style="font-size:14px;color:var(--ink-2,#9CAAA4)">${practisedLabel ?? '&mdash;'}</div>
        ${row.is_cold ? '<div class="badge" style="background:var(--warn-tint,#2A1D17);color:var(--warn,#C9805E)">cold</div>' : ''}
      </div>
      <div class="mono num" style="font-size:13px;color:${shiftColor};text-align:right">${shiftText}</div>
      <div><svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="#3E4A46" stroke-width="1.5" stroke-linecap="round"/></svg></div>
    </a>`;
}

function nextUpHtml(nextUp) {
  if (!nextUp) {
    return `
      <div style="flex:1;background:var(--surface,#131B19);border:1px solid var(--line,#26302E);
                  border-radius:5px;padding:22px 26px;color:var(--ink-3,#6A7873);font-size:15px">
        Nothing to rank yet -- practise a section, or bind a song's audio, and Next up will
        have an opinion.
      </div>`;
  }
  const speedNote = `at ${Math.round(nextUp.target_speed * nextUp.reached)}% &middot; ${
    Math.round((1 - nextUp.reached) * 100)
  }% below target`;
  return `
    <div style="flex:1;background:var(--surface,#131B19);border:1px solid var(--accent-dim,#8A5C29);
                border-radius:5px;padding:22px 26px;display:flex;align-items:center;gap:28px">
      <div style="flex-grow:1;display:flex;flex-direction:column;gap:5px">
        <div class="lbl" style="font-size:11px;color:var(--accent,#E0913F)">Next up</div>
        <div style="font-size:29px;font-weight:600;letter-spacing:-.015em">${escapeHtml(nextUp.song_title)} &mdash; ${escapeHtml(nextUp.section_name)}</div>
        <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873);letter-spacing:.04em">${speedNote}</div>
      </div>
      <a class="practise-btn" href="#/practice/${encodeURIComponent(nextUp.song_slug)}/${encodeURIComponent(nextUp.section_id)}">Practise</a>
    </div>`;
}

/**
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void}
 */
export function mount(el, payload) {
  ensureStyle();

  if (!payload.setlists || payload.setlists.length === 0) {
    el.innerHTML = `
      <div class="ws-dash" style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
                  display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px">
        <div class="mono" style="font-size:14px;letter-spacing:.32em">WOODSHED</div>
        <div style="font-size:17px;color:var(--ink-2,#9CAAA4)">No setlists yet.</div>
        <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873)">woodshed setlist add &lt;name&gt;</div>
      </div>`;
    return;
  }

  render(el, payload);
}

/** Re-fetches GET /api/setlist/<slug> and re-renders in place -- used both
 * by the initial mount and by a pill click (see decision 1 above: this
 * never goes through app.js's router). */
async function switchTo(el, setlists, slug) {
  rememberSetlist(slug);
  const dashboard = await get(`/api/setlist/${encodeURIComponent(slug)}`);
  render(el, { setlists, currentSetlist: slug, ...dashboard });
}

function render(el, payload) {
  const { setlists, currentSetlist } = payload;

  const pills = setlists.map((s) => `
    <button class="pill${s.slug === currentSetlist ? ' sel' : ''}" data-setlist="${escapeHtml(s.slug)}">${escapeHtml(s.name)}</button>
  `).join('');

  const weeksBlock = payload.weeks_to_gig === null
    ? `<div class="lbl" style="font-size:10px">No gig date</div>
       <div style="font-size:14px;color:var(--ink-3,#6A7873);margin-top:8px">set one with woodshed setlist</div>`
    : `<div class="lbl" style="font-size:10px">To the gig</div>
       <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px">
         <div class="num" style="font-size:34px;font-weight:600">${payload.weeks_to_gig}</div>
         <div style="font-size:15px;color:var(--ink-2,#9CAAA4)">weeks</div>
       </div>
       <div class="mono" style="font-size:12px;color:var(--ink-3,#6A7873);margin-top:4px">
         ${payload.songs_at_target} OF ${payload.song_count} AT TARGET</div>`;

  el.innerHTML = `
    <div class="ws-dash" style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
                display:flex;flex-direction:column;padding:34px 48px 30px">
      <div style="display:flex;align-items:center;gap:36px;padding-bottom:22px;border-bottom:1px solid var(--line,#26302E)">
        <div class="mono" style="font-size:14px;letter-spacing:.32em;font-weight:600">WOODSHED</div>
        <div style="display:flex;gap:6px;align-items:center" data-pills>${pills}</div>
        <div style="margin-left:auto;display:flex;align-items:center;gap:14px">
          <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873);letter-spacing:.06em">${payload.song_count} SONGS</div>
          <div style="border:1px solid var(--line,#26302E);border-radius:4px;padding:6px 12px;font-size:14px">${escapeHtml(payload.tuning)}</div>
        </div>
      </div>

      <div style="display:flex;gap:14px;padding:22px 0 26px">
        ${nextUpHtml(payload.next_up)}
        <div style="width:250px;display:flex;flex-direction:column;gap:10px">
          <div style="flex:1;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;padding:14px 18px">
            ${weeksBlock}
          </div>
          <div style="flex:1;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;padding:14px 18px">
            <div class="lbl" style="font-size:10px">Needs audio</div>
            <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px">
              <div class="num" style="font-size:34px;font-weight:600;color:var(--ink-2,#9CAAA4)">${payload.needs_audio_count}</div>
              <div style="font-size:15px;color:var(--ink-2,#9CAAA4)">songs</div>
            </div>
            <div class="mono" style="font-size:12px;color:var(--ink-3,#6A7873);margin-top:4px">BIND A FILE &rarr;</div>
          </div>
        </div>
      </div>

      <div class="row" style="border-bottom:1px solid var(--line,#26302E);padding-top:0;padding-bottom:10px">
        <div class="lbl" style="font-size:10px">Song</div>
        <div class="lbl" style="font-size:10px">Readiness</div>
        <div class="lbl" style="font-size:10px">Sections</div>
        <div class="lbl" style="font-size:10px">Practised</div>
        <div class="lbl" style="font-size:10px;text-align:right">Shift</div>
        <div></div>
      </div>
      <div data-rows>${payload.rows.map(rowHtml).join('')}</div>

      <div style="margin-top:auto;padding-top:18px;display:flex;justify-content:space-between">
        <div class="mono" style="font-size:12px;color:var(--ink-4,#5B6A64);letter-spacing:.08em">${payload.rows.length} SONGS IN THIS SETLIST</div>
        <div class="mono" style="font-size:12px;color:var(--ink-4,#5B6A64);letter-spacing:.08em">${setlists.length} SETLIST${setlists.length === 1 ? '' : 'S'}</div>
      </div>
    </div>`;

  for (const button of el.querySelectorAll('[data-setlist]')) {
    button.addEventListener('click', () => {
      switchTo(el, setlists, button.dataset.setlist);
    });
  }
}
