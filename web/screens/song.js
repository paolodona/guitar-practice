/**
 * screens/song.js — the workbench: bar ruler, waveform, section lanes,
 * transport, inspector (design/SongPage.dc.html). D6's file.
 *
 * mount(el, payload) — app.js's contract 1. *payload* is GET
 * /api/song/<slug>'s JSON body (see server.py's `_song`) plus
 * `params: {slug}` merged in by the router. Full shape is documented on
 * screens/practice.js's mount() (this unit's other file, sharing the same
 * endpoint) — see that comment for the field-by-field breakdown.
 *
 * Three scope decisions, made explicit here rather than silently assumed:
 *
 * 1. The transpose cluster is interactive as of Phase 1's F3, on the same
 *    terms as practice.js's (see that file's decision 2): `-`/`+` clicks
 *    and the `-`/`=` keys (via actions.js's `on('transpose_up'/'down', …)`
 *    -- keys.js's listener is global, wired once in app.js's `start()`, so
 *    it reaches this screen with no key handling of its own) step
 *    `payload.shift`, and `POST /api/shift` persists the change ONLY when
 *    app.js's `currentSetlist()` names one -- with none, there is still no
 *    setlist entry to hold an override, so the number updates locally and
 *    nothing is written, exactly the prior behaviour. This screen has no
 *    audio engine (see decision 2 below) to re-pitch live; the number is
 *    the whole of what changes here.
 *
 * 2. The transport bar is visual scaffolding, not a second audio engine.
 *    D6's brief assigns instantiating `player.RealtimeEngine` to
 *    practice.js only ("the hero" — where pass-counting lives). Building
 *    a second, independent playback consumer here — for a plain unlooped
 *    scrub preview with no rep semantics — is not required by the D0/D6
 *    contract and risks a second, divergent notion of "playing" before
 *    the engine itself is even built (player.js/D4 is still throw-stubbed
 *    at the time of writing). The transport's play/rung controls update
 *    local UI state only (documented per-control below); auditioning a
 *    section for real happens by pressing "Practise this" and reaching
 *    practice.js, which owns the one RealtimeEngine this phase.
 *
 * 3. The inspector's rep/readiness footer (31 REPS / BEST 75% / YESTERDAY
 *    in the artboard) is omitted. Reading historical reps needs a ledger
 *    aggregation endpoint that does not exist yet — server.py's GET
 *    routes are exactly /api/song, /api/peaks, /api/audio (see its module
 *    docstring) — so there is nothing on disk this screen could honestly
 *    show there. Do not fabricate a placeholder count.
 *
 * Waveform semantics on THIS screen differ from practice.js's: there is no
 * "played so far" concept (nothing loops here), so the design's
 * played-in-grey overlay instead highlights the CURRENTLY SELECTED
 * SECTION's bounds — drawn as a plain absolutely-positioned overlay div
 * computed from timeline.viewX, not through wave.js's playedFraction
 * (which is strictly "fraction played from the view's start", the wrong
 * shape for an arbitrary mid-view highlight). wave.js itself is called
 * with playedFraction 0 here — see decision 2 above for why there is no
 * real transport position to feed it.
 *
 * Section selection/highlighting is sections.js's own internal state (see
 * its module doc: "Re-running renderSections... always starts unselected
 * ... a caller that wants selection to survive a redraw has to re-select
 * the tile itself"). This screen's own `selectedId` (used to drive the
 * inspector panel and the waveform highlight box, both owned here) is
 * therefore a SEPARATE, parallel piece of state from whatever tile
 * sections.js currently paints as visually "selected" — after any
 * server-round-trip redraw (a commit, a create), the lane tile's own
 * selected look resets even though this screen's inspector keeps showing
 * the same section. This is sections.js's own documented, not-silently-
 * patched-around gap, not something introduced here; flagged again in
 * this unit's report.
 */
import { currentSetlist, get, post } from '../app.js';
import { drawWave, SONG_WAVE_OPTS } from '../wave.js';
import { renderSections, attachCreateHandler } from '../sections.js';
import { viewX } from '../timeline.js';
import { on } from '../actions.js';

const STYLE_ID = 'song-screen-style';

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ws-song .mono { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace }
    .ws-song .flbl { font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.16em;
      text-transform:uppercase;color:var(--ink-3,#6A7873);margin-bottom:6px }
    .ws-song .fld { background:var(--sunken,#0F1614);border:1px solid var(--line,#26302E);
      border-radius:4px;padding:9px 11px;font-size:14px;color:var(--ink,#E8EEEB);width:100%;
      box-sizing:border-box;font-family:inherit }
    .ws-song textarea.fld { resize:none;line-height:1.5;font-size:13.5px;color:var(--ink-2,#9CAAA4) }
    .ws-song .fld:focus { outline:1px solid var(--accent-dim,#8A5C29) }
    .ws-song .rung { font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:var(--ink-3,#6A7873);
      padding:6px 9px;border-radius:3px;border:none;background:none;cursor:pointer }
    .ws-song .rung.sel { background:var(--accent,#E0913F);color:var(--ground,#0C1211);font-weight:600 }
    .ws-song .practise-btn { background:var(--accent,#E0913F);color:var(--ground,#0C1211);
      border:none;border-radius:4px;padding:13px;text-align:center;font-size:16px;font-weight:600;
      cursor:pointer;width:100% }
    .ws-song .practise-btn:hover { background:var(--accent-hover,#EFA95C) }
  `;
  document.head.appendChild(style);
}

function secPerBeat(tempo) { return 60 / tempo.bpm; }
function beatsPerBar(tempo) {
  const n = parseInt(String(tempo.time_signature).split('/')[0], 10);
  return Number.isFinite(n) && n > 0 ? n : 4;
}
function barOf(t, tempo) {
  const totalBeats = (t - tempo.grid_offset_s) / secPerBeat(tempo);
  return Math.floor(totalBeats / beatsPerBar(tempo)) + 1;
}
function beatOf(t, tempo) {
  const totalBeats = (t - tempo.grid_offset_s) / secPerBeat(tempo);
  const bpb = beatsPerBar(tempo);
  return (((Math.floor(totalBeats) % bpb) + bpb) % bpb) + 1;
}
function barBeatLabel(t, tempo) { return `${barOf(t, tempo)}.${beatOf(t, tempo)}`; }
function fmtClock(s) {
  const m = Math.floor(s / 60);
  const rem = Math.max(0, s - m * 60);
  return `${m}:${rem.toFixed(1).padStart(4, '0')}`;
}

/** order() mirrored client-side: (start_s, -duration), display order only —
 *  sections.py/server.py own containment (`lane`/`ancestors`), never
 *  recomputed here; this only decides which order lane tiles are handed
 *  to renderSections in (cosmetically stable across a redraw). */
function orderSections(sections) {
  return [...sections].sort((a, b) => a.start_s - b.start_s || (b.end_s - b.start_s) - (a.end_s - a.start_s));
}

function clampShift(v) { return Math.min(6, Math.max(-6, Math.round(v))); }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const RUNGS = [50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100];

/**
 * @param {HTMLElement} el
 * @param {any} payload - GET /api/song/<slug> body + {params:{slug}}
 * @returns {() => void} unmount
 */
export function mount(el, payload) {
  ensureStyle();
  const slug = payload.params.slug;
  let sections = orderSections(payload.sections);
  let selectedId = sections.length ? sections[0].id : null;
  let peaks = null;

  function closestRung(target) {
    return RUNGS.reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a), RUNGS[0]);
  }
  let previewSpeed = closestRung(payload.practice.start_speed);
  let transportPlaying = false;

  let shift = clampShift(payload.shift ?? 0); // interactive — see module doc, decision 1.
  const durationS = payload.recording.duration_s;
  let shiftPersistTimer = null;
  const unsubs = [];

  function persistShift() {
    const setlist = currentSetlist();
    if (!setlist) return; // no setlist entry to hold an override -- session-only
    clearTimeout(shiftPersistTimer);
    shiftPersistTimer = setTimeout(() => {
      post('/api/shift', { setlist, song: slug, shift }).catch(() => {
        // best-effort; the displayed number is already right locally
      });
    }, 400);
  }

  const root = document.createElement('div');
  root.className = 'ws-song';
  root.style.cssText = 'width:100%;min-height:900px;background:var(--ground,#0C1211);' +
    'display:flex;flex-direction:column;padding:30px 44px 28px;color:var(--ink,#E8EEEB);' +
    "font-family:Archivo,'Helvetica Neue',Arial,sans-serif;box-sizing:border-box";
  root.innerHTML = `
    <div style="display:flex;align-items:center;gap:16px;padding-bottom:20px;border-bottom:1px solid var(--line,#26302E)">
      <div data-back title="Back to dashboard" style="cursor:pointer;color:var(--ink-3,#6A7873);font-size:20px;line-height:1;padding:2px 6px">&#8249;</div>
      <div style="font-size:26px;font-weight:600;letter-spacing:-.015em">${escapeHtml(payload.title)}</div>
      <div style="font-size:16px;color:var(--ink-3,#6A7873)">${escapeHtml(payload.artist)}${payload.album ? ' &middot; ' + escapeHtml(payload.album) : ''}</div>
      <div style="margin-left:auto;display:flex;align-items:center;gap:12px">
        <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873);letter-spacing:.04em">RECORD IN ${escapeHtml(payload.recording.tuning).toUpperCase()}</div>
        <div style="display:flex;align-items:center;gap:6px;border:1px solid var(--line,#26302E);border-radius:5px;padding:4px">
          <button data-shift-minus style="width:28px;height:28px;border-radius:3px;border:none;background:var(--raised,#1B2422);display:flex;align-items:center;justify-content:center;font-size:17px;color:var(--ink-2,#9CAAA4);cursor:pointer">&minus;</button>
          <div class="mono num" data-shift-val style="font-size:17px;width:34px;text-align:center;font-weight:600;font-variant-numeric:tabular-nums"></div>
          <button data-shift-plus style="width:28px;height:28px;border-radius:3px;border:none;background:var(--raised,#1B2422);display:flex;align-items:center;justify-content:center;font-size:17px;color:var(--ink-2,#9CAAA4);cursor:pointer">+</button>
        </div>
        <div style="font-size:14px;color:var(--ink-2,#9CAAA4)">plays in ${escapeHtml(payload.recording.tuning)}</div>
      </div>
    </div>

    <div style="display:flex;gap:22px;flex-grow:1;padding-top:22px;min-height:0">
      <div style="flex-grow:1;display:flex;flex-direction:column;gap:10px;min-width:0">
        <div data-bar-ruler style="display:flex;justify-content:space-between"></div>
        <div data-wave-host style="position:relative;height:132px;background:var(--surface,#131B19);border-radius:3px;overflow:hidden;
                    background-image:repeating-linear-gradient(to right,var(--line,#26302E) 0 1px,transparent 1px 150px),
                                     repeating-linear-gradient(to right,var(--hairline,#1C2523) 0 1px,transparent 1px 37.5px)">
          <svg data-wave-svg preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:132px"></svg>
          <div data-selection-box style="position:absolute;top:0;bottom:0;display:none;
                      background:rgba(224,145,63,.10);border-left:1px solid var(--accent,#E0913F);border-right:1px solid var(--accent,#E0913F)"></div>
        </div>

        <div data-lane-root style="position:relative;height:88px"></div>

        <div style="margin-top:14px;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;
                    padding:14px 18px;display:flex;align-items:center;gap:20px">
          <div data-transport-play style="width:42px;height:42px;border-radius:21px;background:var(--accent,#E0913F);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0">
            <svg width="14" height="16" viewBox="0 0 14 16"><path d="M1 1l12 7-12 7z" fill="var(--ground,#0C1211)"/></svg>
          </div>
          <div style="width:1px;height:26px;background:var(--line,#26302E)"></div>
          <div data-rungs style="display:flex;align-items:center;gap:2px"></div>
          <div style="width:1px;height:26px;background:var(--line,#26302E)"></div>
          <div class="mono" style="font-size:13px;color:var(--ink-2,#9CAAA4)">PREVIEW ONLY &middot; PRACTISE A SECTION TO LOOP IT</div>
          <div data-transport-clock class="mono" style="margin-left:auto;font-size:14px;color:var(--ink-2,#9CAAA4);font-variant-numeric:tabular-nums">0:00.0 / ${fmtClock(durationS)}</div>
        </div>
      </div>

      <div data-inspector style="width:326px;flex-shrink:0;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;
                  padding:20px;display:flex;flex-direction:column;gap:16px"></div>
    </div>
  `;
  el.appendChild(root);

  const waveHost = root.querySelector('[data-wave-host]');
  const waveSvg = root.querySelector('[data-wave-svg]');
  const selectionBox = root.querySelector('[data-selection-box]');
  const laneRoot = root.querySelector('[data-lane-root]');
  const inspector = root.querySelector('[data-inspector]');
  const barRuler = root.querySelector('[data-bar-ruler]');
  const rungsHost = root.querySelector('[data-rungs]');
  const transportPlayBtn = root.querySelector('[data-transport-play]');

  root.querySelector('[data-back]').addEventListener('click', () => { location.hash = '#/'; });

  const shiftValEl = root.querySelector('[data-shift-val]');
  function renderShift() {
    shiftValEl.textContent = `${shift > 0 ? '+' : ''}${shift}`;
    shiftValEl.style.color = shift === 0 ? 'var(--good,#5FA88F)' : 'var(--accent,#E0913F)';
  }
  renderShift();
  function bumpShift(delta) {
    shift = clampShift(shift + delta);
    renderShift();
    persistShift();
  }
  root.querySelector('[data-shift-minus]').addEventListener('click', () => bumpShift(-1));
  root.querySelector('[data-shift-plus]').addEventListener('click', () => bumpShift(1));
  unsubs.push(on('transpose_down', () => bumpShift(-1)));
  unsubs.push(on('transpose_up', () => bumpShift(1)));

  transportPlayBtn.addEventListener('click', () => {
    // Visual-only toggle — see module doc, decision 2: no engine lives here.
    transportPlaying = !transportPlaying;
    transportPlayBtn.innerHTML = transportPlaying
      ? '<svg width="14" height="16" viewBox="0 0 14 16"><rect x="1" y="1" width="4" height="14" fill="var(--ground,#0C1211)"/><rect x="9" y="1" width="4" height="14" fill="var(--ground,#0C1211)"/></svg>'
      : '<svg width="14" height="16" viewBox="0 0 14 16"><path d="M1 1l12 7-12 7z" fill="var(--ground,#0C1211)"/></svg>';
  });

  rungsHost.innerHTML = RUNGS.map((r) => `<button class="rung${r === previewSpeed ? ' sel' : ''}" data-rung="${r}">${r}</button>`).join('');
  rungsHost.querySelectorAll('[data-rung]').forEach((btn) => {
    btn.addEventListener('click', () => {
      previewSpeed = Number(btn.dataset.rung);
      rungsHost.querySelectorAll('.rung').forEach((b) => b.classList.toggle('sel', Number(b.dataset.rung) === previewSpeed));
    });
  });

  function view() {
    return { startS: 0, endS: durationS, widthPx: waveHost.clientWidth || 1 };
  }

  function renderBarRuler() {
    const marks = 7;
    barRuler.innerHTML = '';
    for (let i = 0; i < marks; i++) {
      const t = (durationS * i) / (marks - 1);
      const d = document.createElement('div');
      d.className = 'mono';
      d.style.cssText = 'font-size:11px;color:var(--ink-4,#5B6A64)';
      d.textContent = String(Math.max(1, barOf(t, payload.tempo)));
      barRuler.appendChild(d);
    }
  }

  function renderWave() {
    drawWave(waveSvg, peaks, view(), 0, SONG_WAVE_OPTS);
  }

  function renderSelectionHighlight() {
    const v = view();
    const sec = sections.find((s) => s.id === selectedId);
    if (!sec || !v.widthPx) { selectionBox.style.display = 'none'; return; }
    const leftPx = viewX(sec.start_s, v);
    const rightPx = viewX(sec.end_s, v);
    selectionBox.style.display = 'block';
    selectionBox.style.left = `${(leftPx / v.widthPx) * 100}%`;
    selectionBox.style.width = `${((rightPx - leftPx) / v.widthPx) * 100}%`;
  }

  function renderLanes() {
    renderSections(laneRoot, sections, view(), {
      onSelect: (id) => { selectedId = id; renderSelectionHighlight(); renderInspector(); },
      onDragCommit: (patch) => { patchSection(patch.id, patch); },
    });
    attachCreateHandler(laneRoot, view(), {
      onCreateCommit: (body) => {
        post('/api/section', {
          song: slug, action: 'upsert', target_speed: 100,
          counts_toward_readiness: true, ...body,
        }).then((res) => {
          sections = orderSections(res.sections);
          const created = res.sections.find((s) => s.start_s === body.start_s && s.end_s === body.end_s);
          if (created) selectedId = created.id;
          redrawAll();
        });
      },
    });
  }

  async function patchSection(id, partial) {
    const current = sections.find((s) => s.id === id);
    if (!current) return;
    const body = {
      song: slug, id: current.id, name: current.name, start_s: current.start_s,
      end_s: current.end_s, snapped: current.snapped, target_speed: current.target_speed,
      ladder_step: current.ladder_step, reps_to_advance: current.reps_to_advance,
      notes: current.notes, patch: current.patch,
      counts_toward_readiness: current.counts_toward_readiness,
      ...partial,
    };
    const res = await post('/api/section', body);
    sections = orderSections(res.sections);
    redrawAll();
  }

  function renderInspector() {
    const sec = sections.find((s) => s.id === selectedId);
    if (!sec) {
      inspector.innerHTML = '<div class="mono" style="color:var(--ink-3,#6A7873);font-size:13px">No section selected. Drag on empty lane space to create one.</div>';
      return;
    }
    const parent = sec.ancestors && sec.ancestors.length
      ? sections.find((s) => s.id === sec.ancestors[sec.ancestors.length - 1])
      : null;
    const ancestorNote = parent ? `INSIDE ${escapeHtml(parent.name).toUpperCase()}` : '';
    inspector.innerHTML = `
      <div>
        <div class="flbl">Section</div>
        <input class="fld" data-f="name" value="${escapeHtml(sec.name)}" style="font-size:16px;font-weight:500">
        <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64);margin-top:6px">${ancestorNote}</div>
      </div>
      <div style="display:flex;gap:10px">
        <div style="flex:1"><div class="flbl">Start</div>
          <input class="fld mono" data-f="start_s" value="${sec.start_s.toFixed(3)} s" style="font-size:13px">
          <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64);margin-top:5px">BAR ${barBeatLabel(sec.start_s, payload.tempo)} &middot; ${sec.snapped.toUpperCase()}</div></div>
        <div style="flex:1"><div class="flbl">End</div>
          <input class="fld mono" data-f="end_s" value="${sec.end_s.toFixed(3)} s" style="font-size:13px">
          <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64);margin-top:5px">BAR ${barBeatLabel(sec.end_s, payload.tempo)}</div></div>
      </div>
      <div style="display:flex;gap:10px">
        <div style="flex:1"><div class="flbl">Target</div><input class="fld mono" data-f="target_speed" value="${sec.target_speed}%" style="font-size:13px"></div>
        <div style="flex:1"><div class="flbl">Step</div><input class="fld mono" data-f="ladder_step" value="${sec.ladder_step ?? payload.practice.ladder_step}%" style="font-size:13px"></div>
        <div style="flex:1"><div class="flbl">Reps to adv.</div><input class="fld mono" data-f="reps_to_advance" value="${sec.reps_to_advance ?? payload.practice.reps_to_advance}" style="font-size:13px"></div>
      </div>
      <div>
        <div class="flbl">Notes</div>
        <textarea class="fld" data-f="notes" style="height:94px">${escapeHtml(sec.notes ?? '')}</textarea>
      </div>
      <div>
        <div class="flbl">GX-100 patch</div>
        <input class="fld" data-f="patch" value="${escapeHtml(sec.patch ?? '')}" style="font-size:14px">
      </div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:12px">
        <button class="practise-btn" data-practise>Practise this</button>
        <button class="practise-btn" data-delete style="background:var(--warn-tint,#2A1D17);color:var(--warn,#C9805E)">Delete section</button>
      </div>
    `;
    const numField = (name, parse, extract = (v) => v) => {
      inspector.querySelector(`[data-f="${name}"]`).addEventListener('change', (e) => {
        const v = parse(extract(e.target.value));
        if (Number.isFinite(v)) patchSection(sec.id, { [name]: v });
      });
    };
    inspector.querySelector('[data-f="name"]').addEventListener('change', (e) => patchSection(sec.id, { name: e.target.value }));
    numField('start_s', parseFloat);
    numField('end_s', parseFloat);
    numField('target_speed', parseFloat);
    numField('ladder_step', parseFloat);
    numField('reps_to_advance', (v) => parseInt(v, 10));
    inspector.querySelector('[data-f="notes"]').addEventListener('change', (e) => patchSection(sec.id, { notes: e.target.value }));
    inspector.querySelector('[data-f="patch"]').addEventListener('change', (e) => patchSection(sec.id, { patch: e.target.value || null }));
    inspector.querySelector('[data-practise]').addEventListener('click', () => {
      location.hash = `#/practice/${encodeURIComponent(slug)}/${encodeURIComponent(sec.id)}`;
    });
    inspector.querySelector('[data-delete]').addEventListener('click', () => deleteSection(sec.id));
  }

  // Found live 2026-09-06: the inspector had no way to remove a section at
  // all -- creation exists (drag on empty lane space, attachCreateHandler
  // above), deletion never got a unit. window.confirm is synchronous and
  // blocks the tab, same trade renderSections' own rename prompt makes.
  async function deleteSection(id) {
    if (!window.confirm('Delete this section? This cannot be undone.')) return;
    const res = await post('/api/section', { song: slug, action: 'delete', id });
    sections = orderSections(res.sections);
    if (selectedId === id) selectedId = null;
    redrawAll();
  }

  function redrawAll() {
    renderBarRuler();
    renderWave();
    renderLanes();
    renderSelectionHighlight();
    renderInspector();
  }

  redrawAll();

  get(payload.peaks_url)
    .then((data) => { peaks = data; renderWave(); })
    .catch(() => { peaks = null; renderWave(); }); // 404 == not built yet, per wave.js's contract.

  // Only the waveform (canvas-shaped: an SVG whose viewBox is stretched via
  // preserveAspectRatio) needs a redraw on resize. The lane tiles and the
  // selection box are already percentage left/width, so they track a
  // container resize on their own — re-running renderSections here would
  // additionally reset its internally-tracked selection (see its own
  // module doc), which a mere window resize has no business doing.
  const resizeObserver = new ResizeObserver(() => { renderWave(); });
  resizeObserver.observe(waveHost);

  return function unmount() {
    resizeObserver.disconnect();
    clearTimeout(shiftPersistTimer);
    for (const unsub of unsubs) unsub();
  };
}
