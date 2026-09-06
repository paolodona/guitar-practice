/**
 * screens/progress.js — range chips, readiness area chart, the section
 * table with sparklines, the cold list and this week's totals
 * (design/Progress.dc.html). Phase 2, K2.
 *
 * mount(el, payload) — app.js's contract 1. `payload` is the body of
 * `GET /api/progress/<slug>?weeks=` (server.py's `_progress`) with the
 * router's `params: {slug}` merged in:
 *
 * {
 *   slug, title, artist, weeks,
 *   readiness_series: Array<[isoDate, 0..1]>,
 *   totals: {passes, cleans, minutes},      // all time, this song
 *   week:   {passes, cleans, minutes},      // the last seven days
 *   rungs_gained: number,                   // this week, compared not counted
 *   sections: Array<{id, name, target_speed, reps, cleans, minutes,
 *     best_sustained, reached, last_speed, last_practised, days_since,
 *     cold, series: Array<[isoDate, speedPct]>}>,   // furthest from target first
 *   params: {slug},
 * }
 *
 * Three scope decisions:
 *
 * 1. **This screen is per SONG, and the artboard's header is per set.**
 *    The plan's module map fixes the endpoint as
 *    `GET /api/progress/<slug> -> per-section series from the ledger`, and
 *    `<slug>` there is a song — so the artboard's "Ramba S.S. — the set"
 *    becomes this song's title and artist, and the section table lists
 *    this song's sections rather than a whole setlist's. A set-wide
 *    version would be a second endpoint over `practice.next_up`'s own
 *    per-setlist walk; it is not invented here.
 * 2. **The range chips re-fetch.** 12 / 26 weeks / All is a server-side
 *    window (it changes what the readiness replay covers), so a chip click
 *    re-requests `?weeks=` and re-renders in place — the same
 *    bypass-the-router move dashboard.js's setlist pills make, and for the
 *    same reason: the window is a view preference, not a location.
 * 3. **Nothing here is a stored number.** Every figure is a fresh read of
 *    practice/reps.jsonl (CLAUDE.md invariant 6), including "rungs gained",
 *    which is a comparison of best-sustained speed before and after the
 *    window rather than a counter anyone increments. So a retraction moves
 *    the chart, which is the point of an append-only ledger.
 */
import { get } from '../app.js';

const STYLE_ID = 'progress-screen-style';

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ws-prog .mono { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace }
    .ws-prog .num { font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;
      letter-spacing:-.035em;line-height:.84;font-weight:700 }
    .ws-prog .lbl { font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;letter-spacing:.2em;
      text-transform:uppercase;color:var(--ink-3,#6A7873) }
    .ws-prog .prow { display:grid;grid-template-columns:290px 156px 66px 74px 74px 96px;
      align-items:center;gap:20px;padding:13px 10px;border-bottom:1px solid var(--line-dim,#1C2523) }
    .ws-prog .chip { font-size:13.5px;padding:6px 13px;border-radius:4px;color:var(--ink-2,#9CAAA4);
      background:none;border:none;cursor:pointer;font-family:inherit }
    .ws-prog .chip[aria-pressed="true"] { background:var(--raised,#1B2422);color:var(--ink,#E8EEEB) }
    .ws-prog .card { background:var(--raised-dim,#131B19);border:1px solid var(--line-dim,#1C2523);
      border-radius:5px;padding:18px 20px }
    .ws-prog a { color:var(--accent,#E0913F) }
  `;
  document.head.appendChild(style);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/** "today" / "yesterday" / "N days" / "N weeks" — the artboard's own
 *  vocabulary, and dashboard.js's, kept identical rather than shared: the
 *  two screens are separate units and a shared helper module for one
 *  format string would be the wrong kind of coupling. */
export function relativeLabel(days) {
  if (days === null || days === undefined) return 'never';
  const d = Math.floor(days);
  if (d <= 0) return 'today';
  if (d === 1) return 'yesterday';
  if (d < 14) return `${d} days`;
  return `${Math.floor(d / 7)} weeks`;
}

/** Minutes as "4:12" — hours and minutes, the artboard's "This week" figure. */
export function hoursLabel(minutes) {
  const total = Math.max(0, Math.round(minutes));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * Map a series of values onto an SVG polyline inside `width` x `height`,
 * with `pad` at every edge. Pure, and exported because it is the one bit
 * of this screen with arithmetic worth asserting directly: an off-by-one
 * on the domain silently flattens or clips a chart rather than failing.
 *
 * `min`/`max` fix the domain (0..1 for readiness, 0..110 for a speed
 * sparkline) so two rows are comparable by eye — auto-scaling each row to
 * its own extremes would make a section that crawled from 50 to 55 look
 * exactly like one that went 50 to 100, which is the opposite of what the
 * chart is for.
 * @param {number[]} values
 * @returns {{points: Array<[number, number]>, d: string, area: string}}
 */
export function plot(values, { width, height, pad = 2, min = 0, max = 1 }) {
  const n = values.length;
  if (n === 0) return { points: [], d: '', area: '' };
  const span = max - min || 1;
  const x = (i) => (n === 1 ? pad : pad + (i * (width - 2 * pad)) / (n - 1));
  const y = (v) => {
    const clamped = Math.min(max, Math.max(min, v));
    return pad + (1 - (clamped - min) / span) * (height - 2 * pad);
  };
  const points = values.map((v, i) => [x(i), y(v)]);
  const d = points.map(([px, py], i) => `${i === 0 ? 'M' : 'L'}${px.toFixed(1)} ${py.toFixed(1)}`).join(' ');
  const area = n < 2 ? '' : `${d} L${x(n - 1).toFixed(1)} ${(height - pad).toFixed(1)} `
    + `L${x(0).toFixed(1)} ${(height - pad).toFixed(1)} Z`;
  return { points, d, area };
}

const RANGES = [
  { label: '12 weeks', weeks: 12 },
  { label: '26 weeks', weeks: 26 },
  { label: 'All', weeks: 0 },
];

/**
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {void | (() => void)}
 */
export function mount(el, payload) {
  ensureStyle();
  render(el, payload);
}

async function reload(el, slug, weeks) {
  const query = weeks > 0 ? `?weeks=${weeks}` : '?weeks=all';
  const data = await get(`/api/progress/${encodeURIComponent(slug)}${query}`);
  render(el, { ...data, params: { slug }, selectedWeeks: weeks });
}

function render(el, payload) {
  const slug = payload.params?.slug ?? payload.slug;
  const selected = payload.selectedWeeks ?? payload.weeks;
  const readiness = payload.readiness_series ?? [];
  const values = readiness.map(([, v]) => v);
  const chart = plot(values, { width: 1180, height: 150, pad: 6, min: 0, max: 1 });
  const latest = values.length ? values[values.length - 1] : 0;

  el.innerHTML = `
    <div class="ws-prog" style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
         font-family:Archivo,'Helvetica Neue',Arial,sans-serif;padding:30px 44px 26px;box-sizing:border-box">

      <div style="display:flex;align-items:center;gap:18px;padding-bottom:20px;
                  border-bottom:1px solid var(--line,#26302E)">
        <div style="font-size:26px;font-weight:600;letter-spacing:-.015em">Progress</div>
        <div style="font-size:15px;color:var(--ink-3,#6A7873)">
          ${escapeHtml(payload.title ?? slug)}${payload.artist ? ` &mdash; ${escapeHtml(payload.artist)}` : ''}</div>
        <a href="#/song/${encodeURIComponent(slug)}" style="font-size:14px">&larr; Song</a>
        <div style="margin-left:auto;display:flex;gap:4px">
          ${RANGES.map((r) => `<button class="chip" data-weeks="${r.weeks}"
              aria-pressed="${String(r.weeks === selected || (r.weeks === 0 && selected !== 12 && selected !== 26))}"
              >${r.label}</button>`).join('')}
        </div>
      </div>

      <div style="padding:22px 0 10px">
        <div style="display:flex;align-items:baseline;gap:14px;margin-bottom:12px">
          <div style="font-size:17px;font-weight:500">Readiness</div>
          <div class="mono" style="font-size:12.5px;color:var(--ink-3,#6A7873);letter-spacing:.05em">
            LENGTH-WEIGHTED OVER COVERED SONG TIME &middot; ${payload.weeks} WEEKS</div>
        </div>
        <div style="position:relative;height:150px">
          <svg viewBox="0 0 1180 150" preserveAspectRatio="none" style="width:100%;height:150px;display:block">
            <line x1="6" y1="6" x2="1174" y2="6" stroke="var(--line-dim,#1C2523)" stroke-width="1"></line>
            <line x1="6" y1="75" x2="1174" y2="75" stroke="var(--line-dim,#1C2523)" stroke-width="1"></line>
            <line x1="6" y1="144" x2="1174" y2="144" stroke="var(--line,#26302E)" stroke-width="1"></line>
            ${chart.area ? `<path d="${chart.area}" fill="rgba(224,145,63,.13)" stroke="none"></path>` : ''}
            ${chart.d ? `<path d="${chart.d}" fill="none" stroke="var(--accent,#E0913F)" stroke-width="2"></path>` : ''}
            ${chart.points.length ? `<circle cx="${chart.points[chart.points.length - 1][0].toFixed(1)}"
              cy="${chart.points[chart.points.length - 1][1].toFixed(1)}" r="4.5"
              fill="var(--accent,#E0913F)" stroke="var(--ground,#0C1211)" stroke-width="2"></circle>` : ''}
          </svg>
          <div class="mono" style="position:absolute;left:0;top:-2px;font-size:11px;color:var(--ink-4,#5B6A64)">100%</div>
          <div class="mono" style="position:absolute;left:0;top:67px;font-size:11px;color:var(--ink-4,#5B6A64)">50%</div>
          <div class="mono num" style="position:absolute;right:0;top:16px;font-size:15px;
               color:var(--accent,#E0913F);font-weight:600">${Math.round(latest * 100)}%</div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:6px">
          <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64)">
            ${readiness.length ? escapeHtml(readiness[0][0]) : ''}</div>
          <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64)">NOW</div>
        </div>
      </div>

      <div style="display:flex;gap:18px;padding-top:14px">
        <div style="flex-grow:1;min-width:0">
          <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:6px">
            <div style="font-size:17px;font-weight:500">Furthest from target first</div>
            <div class="mono" style="font-size:12px;color:var(--ink-3,#6A7873)">SPEED PER PRACTISED DAY</div>
          </div>
          <div class="prow" style="border-bottom:1px solid var(--line,#26302E);padding-bottom:8px">
            <div class="lbl" style="font-size:10px">Section</div><div class="lbl" style="font-size:10px">Speed</div>
            <div class="lbl" style="font-size:10px">Reps</div><div class="lbl" style="font-size:10px">Best</div>
            <div class="lbl" style="font-size:10px">Target</div><div class="lbl" style="font-size:10px">Last</div>
          </div>
          ${(payload.sections ?? []).map((row) => sectionRow(slug, row)).join('')}
        </div>

        <div style="width:308px;flex-shrink:0;display:flex;flex-direction:column;gap:12px">
          ${coldCard(payload.sections ?? [])}
          <div class="card" style="flex-grow:1">
            <div class="lbl" style="font-size:10px">This week</div>
            <div style="display:flex;align-items:baseline;gap:8px;margin-top:8px">
              <div class="num" style="font-size:38px;font-weight:600">${hoursLabel(payload.week?.minutes ?? 0)}</div>
              <div style="font-size:15px;color:var(--ink-2,#9CAAA4)">practised</div></div>
            <div style="display:flex;justify-content:space-between;margin-top:16px;padding-top:14px;
                        border-top:1px solid var(--line-dim,#1C2523)">
              ${statCell(payload.week?.passes ?? 0, 'REPS')}
              ${statCell(payload.week?.cleans ?? 0, 'CLEAN')}
              ${statCell(payload.rungs_gained ?? 0, 'RUNGS GAINED', 'var(--accent,#E0913F)')}
            </div>
            <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64);margin-top:16px">
              ALL TIME &middot; ${payload.totals?.passes ?? 0} REPS &middot;
              ${payload.totals?.cleans ?? 0} CLEAN &middot; ${hoursLabel(payload.totals?.minutes ?? 0)}</div>
          </div>
        </div>
      </div>
    </div>`;

  for (const chip of el.querySelectorAll('[data-weeks]')) {
    chip.addEventListener('click', () => {
      reload(el, slug, Number(chip.dataset.weeks)).catch((err) => {
        console.error('progress.js: reload failed', err);
      });
    });
  }
}

function statCell(value, label, color) {
  return `<div><div class="num" style="font-size:22px;font-weight:600${color ? `;color:${color}` : ''}">${value}</div>
    <div class="mono" style="font-size:11px;color:var(--ink-3,#6A7873);margin-top:3px">${label}</div></div>`;
}

function sectionRow(slug, row) {
  const atTarget = row.best_sustained >= row.target_speed && row.target_speed > 0;
  const stroke = atTarget ? 'var(--good,#5FA88F)' : 'var(--accent,#E0913F)';
  const speeds = (row.series ?? []).map(([, speed]) => speed);
  const spark = plot(speeds, { width: 152, height: 40, pad: 3, min: 40, max: 110 });
  const last = spark.points.length ? spark.points[spark.points.length - 1] : null;
  return `
    <div class="prow">
      <div>
        <a href="#/practice/${encodeURIComponent(slug)}/${encodeURIComponent(row.id)}"
           style="font-size:15px;text-decoration:none">${escapeHtml(row.name)}</a>
        <div style="font-size:13px;color:var(--ink-3,#6A7873);margin-top:2px">
          ${row.cold ? 'going cold' : `${Math.round(row.reached * 100)}% of target`}</div>
      </div>
      <svg width="152" height="40" viewBox="0 0 152 40" aria-label="speed per practised day">
        ${spark.d ? `<path d="${spark.d}" fill="none" stroke="${stroke}" stroke-width="2"
             stroke-linejoin="round"/>` : ''}
        ${last ? `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3.5" fill="${stroke}"
             stroke="var(--ground,#0C1211)" stroke-width="2"/>` : ''}
      </svg>
      <div class="mono num" style="font-size:14px;color:var(--ink-2,#9CAAA4)">${row.reps}</div>
      <div class="mono num" style="font-size:14px${atTarget ? ';color:var(--good,#5FA88F)' : ''}">${row.best_sustained}%</div>
      <div class="mono num" style="font-size:14px;color:var(--ink-3,#6A7873)">${row.target_speed}%</div>
      <div style="font-size:13.5px;color:var(--ink-2,#9CAAA4)">${relativeLabel(row.days_since)}</div>
    </div>`;
}

function coldCard(sections) {
  const cold = sections.filter((row) => row.cold);
  return `
    <div class="card">
      <div class="lbl" style="font-size:10px;color:var(--warn,#C9805E)">Going cold</div>
      <div style="font-size:13px;color:var(--ink-3,#6A7873);margin:6px 0 12px;line-height:1.5">
        Above 80% of target once, untouched for a fortnight.</div>
      ${cold.length === 0
        ? '<div style="font-size:14px;color:var(--ink-3,#6A7873)">Nothing has gone cold.</div>'
        : cold.map((row) => `
        <div style="display:flex;justify-content:space-between;padding:7px 0;
                    border-top:1px solid var(--line-dim,#1C2523)">
          <div style="font-size:14px">${escapeHtml(row.name)}</div>
          <div class="mono num" style="font-size:13px;color:var(--warn,#C9805E)">
            ${Math.round(row.days_since)} d</div></div>`).join('')}
    </div>`;
}
