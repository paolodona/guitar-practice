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
 *    "create your first setlist" form (`POST /api/setlist`) rather than a
 *    blank screen or a fabricated row.
 *
 * 4. (post-Phase-1) Creating a setlist is a real, small inline form --
 *    `POST /api/setlist` -- rather than requiring a terminal.
 *
 * 4b. (Phase 1.5, T1) "Add song" is a real file-binding form, not the old
 *    title-only text field that only ever produced a `needs_audio`
 *    placeholder row: a file picker, title/artist, and the tuning
 *    dropdown (Group O's `AddSong.dc.html`), `POST`ed as `multipart/form-
 *    data` to `/api/song/upload` (the server's own `bind_song_file` --
 *    the same function `cmd_add` calls -- reads the duration and writes
 *    `songs/<slug>/audio/<file>` + `song.yaml` for real). A second call,
 *    `POST /api/setlist/<slug>/songs`, adds the freshly-bound slug to
 *    THIS setlist -- the same second step the old form always made,
 *    kept as two calls rather than folding setlist-membership into the
 *    upload endpoint's own contract.
 *
 * 5. (Phase 1.5) A `#/capture` link is now always present, in BOTH render
 *    paths (a real setlist, and the empty "create your first one" state) —
 *    not conditional on `needs_audio_count`, unlike the existing "Needs
 *    audio" stat card's own link to the same route. Found live: capturing
 *    was only reachable through that one card, so a setlist with nothing
 *    needing audio yet (including a brand-new, empty one) had no door to
 *    it at all — and recording ahead of creating any song entries at all
 *    (capture first, name and bind segments after) is a real workflow this
 *    screen must not block.
 */
import { get, post, postForm, setCurrentSetlist } from '../app.js';

const STYLE_ID = 'dashboard-screen-style';

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
    .ws-dash .row { display:grid;grid-template-columns:18px 380px 1fr 210px 130px 96px 20px;
      align-items:center;gap:24px;padding:17px 12px;border-bottom:1px solid var(--hairline,#1C2523);
      text-decoration:none;color:inherit }
    .ws-dash a.row:hover { background:#111917 }
    /* The drag handle appears on hover only (Paolo's own ask): a running
       order is dragged rarely and read constantly, so the affordance
       should not compete with the row's actual content. */
    .ws-dash .grip { opacity:0;cursor:grab;color:var(--ink-4,#5B6A64);font-size:13px;
      line-height:1;user-select:none;text-align:center;transition:opacity .12s }
    .ws-dash a.row:hover .grip { opacity:1 }
    .ws-dash .row.dragging { opacity:.45 }
    .ws-dash .row.dropping { border-top:2px solid var(--accent,#E0913F) }
    .ws-dash .track { height:6px;border-radius:3px;background:var(--hairline,#1C2523);overflow:hidden }
    .ws-dash .fill { height:6px;border-radius:3px;background:var(--accent,#E0913F) }
    .ws-dash .badge { font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.12em;
      text-transform:uppercase;padding:3px 8px;border-radius:3px;white-space:nowrap }
    .ws-dash .practise-btn { background:var(--accent,#E0913F);color:var(--ground,#0C1211);
      border:none;border-radius:4px;padding:15px 30px;font-size:19px;font-weight:600;
      white-space:nowrap;cursor:pointer;text-decoration:none;display:inline-block }
    .ws-dash .practise-btn:hover { background:var(--accent-hover,#EFA95C) }
    .ws-dash .fld { background:var(--sunken,#0F1614);border:1px solid var(--line,#26302E);
      border-radius:4px;padding:8px 11px;font-size:14px;color:var(--ink,#E8EEEB);font-family:inherit }
    .ws-dash .fld::placeholder { color:var(--ink-4,#5B6A64) }
    .ws-dash .go-btn { background:var(--accent,#E0913F);color:var(--ground,#0C1211);border:none;
      border-radius:4px;padding:8px 16px;font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap }
    .ws-dash .go-btn:hover { background:var(--accent-hover,#EFA95C) }
    .ws-dash .form-error { font-size:12px;color:var(--warn,#C9805E) }
  `;
  document.head.appendChild(style);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
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
    <a class="row" draggable="false" data-song="${escapeHtml(row.slug)}"
       href="#/song/${encodeURIComponent(row.slug)}">
      <div class="grip" draggable="true" data-grip title="Drag to reorder">&#8942;&#8942;</div>
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

/**
 * Drag a row's grip to change the setlist's running order (Paolo's ask,
 * 2026-09-06). Native HTML5 drag-and-drop, no library: this repo has no
 * bundler and no framework (CLAUDE.md, "vanilla ES modules"), and a
 * vertical list of a couple of dozen rows is exactly the case the native
 * API handles without help.
 *
 * Only the grip is draggable; the row anchor has `draggable="false"` so a
 * drag can never turn into "drag this link somewhere". The rows are moved
 * in the DOM as you go, so the list you see IS the order being proposed,
 * and the commit reads it straight back off the DOM.
 *
 * `setlist.reorder` server-side refuses anything that is not a permutation
 * of what is already in the file, so the worst a mis-drag can do is fail —
 * and a failure re-fetches, putting the screen back to whatever the file
 * actually says rather than leaving a lie on screen.
 */
function wireReorder(el, setlists, currentSetlist) {
  const rowsHost = el.querySelector('[data-rows]');
  if (!rowsHost || !currentSetlist) return;
  let dragging = null;
  let orderBeforeDrag = null;

  const currentOrder = () =>
    [...rowsHost.querySelectorAll('[data-song]')].map((row) => row.dataset.song);

  for (const grip of rowsHost.querySelectorAll('[data-grip]')) {
    // The grip lives inside the row's anchor; a click on it must not
    // navigate to the song.
    grip.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); });
    grip.addEventListener('dragstart', (e) => {
      dragging = grip.closest('[data-song]');
      orderBeforeDrag = currentOrder();
      dragging.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      // Firefox needs *something* set or the drag never starts.
      e.dataTransfer.setData('text/plain', dragging.dataset.song);
    });
    grip.addEventListener('dragend', (e) => {
      if (dragging) dragging.classList.remove('dragging');
      for (const row of rowsHost.querySelectorAll('[data-song]')) {
        row.classList.remove('dropping');
      }
      dragging = null;
      // FOUND BY REVIEW 2026-09-07: dragend fires for an ABANDONED drag
      // too (Escape, or a drop outside the list), and the rows have
      // already been reparented by dragover -- so a cancelled reorder
      // saved itself. `dropEffect === 'none'` is the browser saying the
      // drag was not accepted anywhere; put the list back and write
      // nothing.
      const before = orderBeforeDrag;
      orderBeforeDrag = null;
      if (e.dataTransfer && e.dataTransfer.dropEffect === 'none') {
        restoreOrder(rowsHost, before);
        return;
      }
      // Nothing moved: no write. A stray click on the grip is not an edit.
      if (before && before.join('\u0000') === currentOrder().join('\u0000')) return;
      commitOrder(el, rowsHost, setlists, currentSetlist);
    });
  }

  rowsHost.addEventListener('dragover', (e) => {
    if (!dragging) return;
    e.preventDefault();
    const target = e.target.closest?.('[data-song]');
    if (!target || target === dragging) return;
    const box = target.getBoundingClientRect();
    const after = e.clientY > box.top + box.height / 2;
    target.parentNode.insertBefore(dragging, after ? target.nextSibling : target);
  });
}

/** Put the rows back in *order* — the DOM is the only record of where they
 *  were before the drag started, and dragover has already moved them. */
function restoreOrder(rowsHost, order) {
  if (!order) return;
  const byslug = new Map(
    [...rowsHost.querySelectorAll('[data-song]')].map((row) => [row.dataset.song, row]),
  );
  // Appending each row in the old order rearranges all of them: appending
  // an existing child MOVES it. `[data-rows]` holds nothing but rows, so
  // the result is exactly `order`.
  for (const slug of order) {
    const row = byslug.get(slug);
    if (row) rowsHost.appendChild(row);
  }
}

/** Read the order off the DOM and write it to the file. */
async function commitOrder(el, rowsHost, setlists, currentSetlist) {
  const order = [...rowsHost.querySelectorAll('[data-song]')].map((row) => row.dataset.song);
  try {
    await post(`/api/setlist/${encodeURIComponent(currentSetlist)}/order`, { order });
  } catch (err) {
    console.error('dashboard.js: reorder failed', err);
    // Put the screen back to what the file actually says rather than
    // leaving the dragged-to order showing as if it had been saved.
    await switchTo(el, setlists, currentSetlist);
  }
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
      <a class="practise-btn" href="#/practice/${encodeURIComponent(nextUp.song_slug)}/${encodeURIComponent(nextUp.section_id)}">Practice</a>
    </div>`;
}

// tuning.KNOWN_TUNINGS (Python, src/woodshed/tuning.py) mirrored here -- this
// file has no way to import a Python module. A free-text tuning field let a
// typo ("Eb" instead of "Eb standard") through unnoticed at creation time and
// only surfaced later as a 400 the first time something needed the shift,
// blanking the whole dashboard (app.js's router has no error UI) -- a
// fixed-choice dropdown can't produce that typo in the first place.
const KNOWN_TUNINGS = [
  'E standard', 'Eb standard', 'D standard', 'C# standard', 'C standard',
  'B standard', 'Drop D', 'Drop C#',
];

/** `<option>`s for every fixed tuning choice (Phase 1's convention: a
 * dropdown, never free text) -- shared by the setlist form below and the
 * add-song form's "recording tuning" field. */
function tuningOptionsHtml() {
  return KNOWN_TUNINGS.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
}

/** A name + tuning + submit form, shared by the empty state and the
 * pills row's "+ New setlist" toggle. `onCreated(slug)` runs after a
 * successful POST /api/setlist. */
function createSetlistFormHtml() {
  return `
    <form data-form style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <input class="fld" data-name placeholder="Setlist name" required style="width:220px">
      <select class="fld" data-tuning required style="width:200px">
        <option value="" disabled selected>Tuning&hellip;</option>
        ${tuningOptionsHtml()}
      </select>
      <button type="submit" class="go-btn">Create</button>
      <div data-error class="form-error"></div>
    </form>`;
}

function wireCreateSetlistForm(container, onCreated) {
  const form = container.querySelector('[data-form]');
  const errorEl = form.querySelector('[data-error]');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorEl.textContent = '';
    const name = form.querySelector('[data-name]').value.trim();
    const tuning = form.querySelector('[data-tuning]').value;
    if (!name || !tuning) return;
    try {
      const created = await post('/api/setlist', { name, tuning });
      await onCreated(created.slug);
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });
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
                  display:flex;align-items:center;justify-content:center;flex-direction:column;gap:16px">
        <div class="mono" style="font-size:14px;letter-spacing:.32em">WOODSHED</div>
        <div style="font-size:17px;color:var(--ink-2,#9CAAA4)">No setlists yet -- create your first one.</div>
        <div data-create-setlist>${createSetlistFormHtml()}</div>
        <a href="#/capture" class="pill" style="text-decoration:none;border:1px solid var(--line,#26302E)">
          Or capture some audio first &rarr;</a>
      </div>`;
    wireCreateSetlistForm(el.querySelector('[data-create-setlist]'), async (slug) => {
      setCurrentSetlist(slug);
      const setlists = await get('/api/setlists');
      await switchTo(el, setlists, slug);
    });
    return;
  }

  render(el, payload);
}

/** Re-fetches GET /api/setlist/<slug> and re-renders in place -- used both
 * by the initial mount and by a pill click (see decision 1 above: this
 * never goes through app.js's router). */
async function switchTo(el, setlists, slug) {
  setCurrentSetlist(slug);
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
        <button class="pill" data-new-setlist-toggle style="border:1px dashed var(--line,#26302E)">+ New setlist</button>
        <div style="margin-left:auto;display:flex;align-items:center;gap:14px">
          <a href="#/capture" class="pill" style="text-decoration:none;border:1px solid var(--line,#26302E)">Capture</a>
          <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873);letter-spacing:.06em">${payload.song_count} SONGS</div>
          <div style="border:1px solid var(--line,#26302E);border-radius:4px;padding:6px 12px;font-size:14px">${escapeHtml(payload.tuning)}</div>
        </div>
      </div>
      <div data-new-setlist-form hidden style="padding:14px 0;border-bottom:1px solid var(--line,#26302E)">
        ${createSetlistFormHtml()}
      </div>

      <div style="display:flex;gap:14px;padding:22px 0 26px">
        ${nextUpHtml(payload.next_up)}
        <div style="width:250px;display:flex;flex-direction:column;gap:10px">
          <div style="flex:1;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;padding:14px 18px">
            ${weeksBlock}
          </div>
          <a href="#/capture" style="flex:1;display:block;text-decoration:none;color:inherit;
                      background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);
                      border-radius:5px;padding:14px 18px">
            <div class="lbl" style="font-size:10px">Needs audio</div>
            <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px">
              <div class="num" style="font-size:34px;font-weight:600;color:var(--ink-2,#9CAAA4)">${payload.needs_audio_count}</div>
              <div style="font-size:15px;color:var(--ink-2,#9CAAA4)">songs</div>
            </div>
            <div class="mono" style="font-size:12px;color:var(--ink-3,#6A7873);margin-top:4px">BIND A FILE &rarr;</div>
          </a>
        </div>
      </div>

      <div class="row" style="border-bottom:1px solid var(--line,#26302E);padding-top:0;padding-bottom:10px">
        <div></div>
        <div class="lbl" style="font-size:10px">Song</div>
        <div class="lbl" style="font-size:10px">Readiness</div>
        <div class="lbl" style="font-size:10px">Sections</div>
        <div class="lbl" style="font-size:10px">Practiced</div>
        <div class="lbl" style="font-size:10px;text-align:right">Shift</div>
        <div></div>
      </div>
      <div data-rows>${payload.rows.map(rowHtml).join('')}</div>

      <div style="display:flex;gap:8px;align-items:center;padding:14px 12px 0">
        <form data-add-song style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <label class="pill" style="border:1px dashed var(--line,#26302E);cursor:pointer;max-width:220px;overflow:hidden">
            <span data-file-label style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Choose audio file&hellip;</span>
            <input data-file type="file" accept="audio/*" required style="display:none">
          </label>
          <input class="fld" data-title placeholder="Title" required style="width:200px">
          <input class="fld" data-artist placeholder="Artist" style="width:170px">
          <select class="fld" data-tuning required style="width:170px">
            <option value="" disabled selected>Tuning&hellip;</option>
            ${tuningOptionsHtml()}
          </select>
          <button type="submit" class="go-btn" data-submit>Add song</button>
          <div data-importing class="mono" style="display:none;font-size:12px;color:var(--ink-3,#6A7873);letter-spacing:.04em">Importing&hellip; detecting tempo, this can take a few seconds</div>
          <div data-error class="form-error"></div>
        </form>
      </div>

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

  wireReorder(el, setlists, currentSetlist);

  const newSetlistForm = el.querySelector('[data-new-setlist-form]');
  el.querySelector('[data-new-setlist-toggle]').addEventListener('click', () => {
    newSetlistForm.hidden = !newSetlistForm.hidden;
  });
  wireCreateSetlistForm(newSetlistForm, async (slug) => {
    const freshSetlists = await get('/api/setlists');
    await switchTo(el, freshSetlists, slug);
  });

  const addSongForm = el.querySelector('[data-add-song]');
  const addSongError = addSongForm.querySelector('[data-error]');
  const addSongSubmit = addSongForm.querySelector('[data-submit]');
  const addSongImporting = addSongForm.querySelector('[data-importing]');
  const fileInput = addSongForm.querySelector('[data-file]');
  const fileLabel = addSongForm.querySelector('[data-file-label]');
  fileInput.addEventListener('change', () => {
    fileLabel.textContent = fileInput.files[0]?.name ?? 'Choose audio file…';
  });
  addSongForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    addSongError.textContent = '';
    const file = fileInput.files[0];
    const title = addSongForm.querySelector('[data-title]').value.trim();
    const artist = addSongForm.querySelector('[data-artist]').value.trim();
    const tuning = addSongForm.querySelector('[data-tuning]').value;
    if (!file || !title || !tuning) return;
    // Found live 2026-09-06, Paolo: POST /api/song/upload binds the file
    // AND runs cli.analyze_after_bind (tempo detection, librosa) before it
    // returns -- a real file can take several seconds, and with no
    // feedback at all it read as "not working" rather than "still
    // importing". Disable the form and say so for exactly that window;
    // switchTo() below replaces this whole screen on success, so there is
    // nothing to re-enable there -- only the catch path needs to restore
    // it, for a retry.
    for (const field of addSongForm.elements) field.disabled = true;
    addSongSubmit.textContent = 'Importing…';
    addSongImporting.style.display = 'block';
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('title', title);
      formData.append('artist', artist);
      formData.append('tuning', tuning);
      // Two calls, not one -- POST /api/song/upload only binds the file;
      // adding it to THIS setlist is the same second call the old
      // title-only form always made, so a bound song lands here exactly
      // like a needs_audio placeholder used to.
      const bound = await postForm('/api/song/upload', formData);
      await post(`/api/setlist/${encodeURIComponent(currentSetlist)}/songs`, { song: bound.slug });
      await switchTo(el, setlists, currentSetlist);
    } catch (err) {
      addSongError.textContent = err.message;
      addSongImporting.style.display = 'none';
      addSongSubmit.textContent = 'Add song';
      for (const field of addSongForm.elements) field.disabled = false;
    }
  });
}
