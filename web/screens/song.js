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
 *    nothing is written, exactly the prior behaviour. Written when this
 *    screen had no audio engine at all, true no longer (see decision 2)
 *    -- **found live 2026-09-06**: `bumpShift` still only updated the
 *    number, silent until the next press, the same class of bug the
 *    rung/speed fix below already names. `engine.setSemitones(shift)` now
 *    fires immediately when a press lands mid-playback, matching
 *    practice.js's own transpose handlers exactly.
 *
 * 2. **Phase 1.5, R1**: the transport bar now plays real audio. Through
 *    Phase 1 it was visual scaffolding only — D6's brief assigned
 *    instantiating `player.RealtimeEngine` to practice.js alone, and
 *    player.js/D4 was still throw-stubbed when this file was written. Both
 *    are no longer true, and P1 (Phase 1.5) added exactly the piece this
 *    screen needed: `RealtimeEngine.loadSection`'s `loop: false` option,
 *    which plays a section once and fires `'ended'` instead of wrapping —
 *    never `'boundary'`, so this screen's engine **cannot** produce a
 *    `'pass'` event by construction (see player.js's own class doc and
 *    worklet.js's module doc). Pressing play loads whatever is currently
 *    selected (or the whole recording, with nothing selected) fresh, at
 *    `previewSpeed`/`shift`, and plays it through once; pressing pause (or
 *    reaching the end) stops it. Selection can change between presses —
 *    unlike practice.js, which owns one section for its whole mount — so
 *    the engine reloads the section on every press rather than once at
 *    mount, mirroring practice.js's own lazy-engine-creation reasoning
 *    (`ensureEngine`, called only from inside a user-gesture handler) for
 *    the SAME reason: a fresh `AudioContext` is suspended until resumed
 *    from a user gesture's own call stack. This still owns no rep
 *    semantics and writes nothing to the ledger — practice.js remains the
 *    one screen that does that, and "Practice this" is still how you get
 *    there for the looped, counted version.
 *
 *    **Found live 2026-09-06**: with no playhead at all, there was no way
 *    to see where playback actually was in order to decide "shorten the
 *    section here". `RealtimeEngine` still has no real position accessor
 *    (D6's own report on practice.js flagged this as a future D4 gap;
 *    still open) — so the playhead below is a COSMETIC wall-clock
 *    estimate, the same kind practice.js's own ring/waveform already uses
 *    for its "decision 3", not a read from the audio graph. Unlike that
 *    one, this is a plain per-frame INTEGRATION (`playheadTick`) rather
 *    than a single elapsed-time multiplication, so a mid-playback speed
 *    change (the rungs below, now itself live — see their own "Found
 *    live" note) reshapes only time from that point forward, never
 *    retroactively distorting time that already played at the old speed.
 *    The transport clock itself is still the static total-duration
 *    display; only the playhead line moves.
 *
 * 3. The inspector's rep/readiness footer (31 REPS / BEST 75% / YESTERDAY
 *    in the artboard) is omitted. Reading historical reps needs a ledger
 *    aggregation endpoint that does not exist yet — server.py's GET
 *    routes are exactly /api/song, /api/peaks, /api/audio (see its module
 *    docstring) — so there is nothing on disk this screen could honestly
 *    show there. Do not fabricate a placeholder count.
 *
 * 4. Phase 1's G1: the bar ruler and the wave-host's grid lines are real
 *    now, from `timeline.computeGrid(payload.tempo, durationS)` — replacing
 *    Phase 0's two placeholders (an evenly-spaced-in-TIME approximation
 *    for the ruler; a fixed-pixel `repeating-linear-gradient` for the
 *    grid, copied verbatim from the artboard's own static mockup, which
 *    has no tempo to be accurate to). Both degrade to nothing when
 *    `grid.bars`/`beats` are empty (`tempo.bpm` 0 or absent), per
 *    CLAUDE.md — no fabricated ruler numbers. The inspector's new Snap
 *    row (free/beat/bar) is this screen's only way to ever produce a
 *    section with `snapped !== 'free'` — nothing else in this file wrote
 *    one before this unit — and `[`/`]` (keys.js) nudge the selected
 *    section's boundary by a fixed 10ms (`NUDGE_S`), debounced the same
 *    way the transpose stepper's persistence is.
 *
 * 5. The inspector's Full song toggle (`manifest.Section.full_song`) is
 *    this screen's only way to create one: a section spanning the whole
 *    recording so its reps count, but excluded from practice.js's next/
 *    prev cycling and from the readiness bar (both exclusions live where
 *    they take effect, not here — this toggle only ever writes the one
 *    field).
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
 * Section selection is THIS screen's state and nothing else's: `selectedId`
 * drives the inspector panel, the waveform highlight box, and — since
 * 2026-09-06 — the lane tile's own selected look, by being passed into
 * `renderSections(..., {selectedId, ...})`. Before that, sections.js kept
 * its own closure-local copy with no way in, so every server-round-trip
 * redraw (committing an inspector field, creating a section) visually
 * deselected the tile mid-edit while the inspector carried on showing it.
 * One piece of state, one owner.
 */
import { currentSetlist, get, post } from '../app.js';
import { drawWave, SONG_WAVE_OPTS } from '../wave.js';
import { renderSections, attachCreateHandler, attachDragHandlers } from '../sections.js';
import {
  computeGrid, drawGrid, sizeCanvas, viewX, computeSeekPosition, slicePeaksToWindow,
  FIT_ZOOM_PAN, zoomBy, panBy, followTo, zoomPanView, ZOOM_STEP, PAN_STEP,
} from '../timeline.js';
import { on } from '../actions.js';
import { createEngine } from '../player.js';
// Reused rather than redrawn (#2): the same Material "replay" glyph
// practice.js's foot strip already uses for the identical concept there
// ("back to the top of THIS section, without touching play/pause").
import { FOOT_ICONS } from './practice.js';
import { DEFAULT_MEMORY, resolvePatchAt, sendProgramChange } from '../gx100.js';

/**
 * #2's target: the currently selected section's own start, or 0 when
 * nothing is selected (or the whole recording is previewing). Exported,
 * pure, and free of `mount()`'s closures for the same reason
 * timeline.js's `computeSeekPosition` is (P2) -- this repo has no DOM
 * library to drive a real click through `mount()`'s own markup, so the
 * one piece of real logic here is tested directly instead.
 * @param {{id: string, start_s: number}[]} sections
 * @param {string | null} selectedId
 */
export function restartTargetS(sections, selectedId) {
  const sec = sections.find((s) => s.id === selectedId);
  return sec ? sec.start_s : 0;
}

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
// 3 decimals -- matches the inspector's own Start/End fields
// (`sec.start_s.toFixed(3)`) exactly, so a number read off this live
// counter can be typed straight into either without rounding it first.
// Found live 2026-09-06, Paolo: "I need to see the precise moment a
// section starts... so I can key into the start or end input boxes."
function fmtPreciseS(s) { return `${s.toFixed(3)}s`; }

/** order() mirrored client-side: (start_s, -duration), display order only —
 *  sections.py/server.py own containment (`lane`/`ancestors`), never
 *  recomputed here; this only decides which order lane tiles are handed
 *  to renderSections in (cosmetically stable across a redraw). */
function orderSections(sections) {
  return [...sections].sort((a, b) => a.start_s - b.start_s || (b.end_s - b.start_s) - (a.end_s - a.start_s));
}

function clampShift(v) { return Math.min(6, Math.max(-6, Math.round(v))); }

/** "A millisecond nudge" (docs/00-spec.md) — 10ms per '['/']' press. */
const NUDGE_S = 0.01;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const RUNGS = [50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100];

// Inspector dropdown option lists (Paolo, live 2026-09-06: "change the
// start, target, step, reps to be dropdown (option) rather than free text
// so I can select more quickly"). SPEED_OPTIONS backs both Start and Target
// -- same percent domain, a wider floor than RUNGS above (which only backs
// the preview transport's OWN speed dial, a separate control) since a
// genuinely hard passage may start well under 50%. STEP_OPTIONS keeps every
// value docs/02-data-model.md's own example already uses (2.5, 5) alongside
// the coarser/finer steps a shorter or longer ladder might want.
const SPEED_OPTIONS = Array.from({ length: 19 }, (_, i) => 10 + i * 5); // 10..100 by 5
const STEP_OPTIONS = [1, 2, 2.5, 5, 10, 15, 20];
const REPS_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

/** Builds `<option>`s for a fixed preset list, always including `current`
 *  even when it falls outside the preset (an older or hand-typed value) --
 *  a plain `<select>` silently shows its first option as "selected" when
 *  the bound value matches none of them, which would misreport what
 *  `song.yaml` actually holds rather than just failing to offer it as a
 *  future choice. */
function selectOptions(presets, current, fmt = (v) => String(v)) {
  const values = presets.includes(current) ? presets : [...presets, current].sort((a, b) => a - b);
  return values.map((v) => `<option value="${v}"${v === current ? ' selected' : ''}>${fmt(v)}</option>`).join('');
}

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
  // #3: the song-level GX-100 patch-change timeline. Sorted by at_s, same
  // ordering server.py's own POST /api/patch-change response already
  // guarantees -- kept that way here too so resolvePatchAt (which does
  // NOT assume a sorted list, but every other reader in this file may as
  // well see one) and the lane's own dot order agree.
  let patchChanges = [...(payload.patch_changes || [])].sort((a, b) => a.at_s - b.at_s);
  // The memory last actually sent, so playheadTick (which re-resolves every
  // frame while playing) only calls sendProgramChange again when the
  // playhead has actually crossed into a NEW entry -- not on every frame it
  // happens to still be inside the same one, and not a second time for the
  // same memory two adjacent entries both happen to name. Reset to null on
  // stop/pause so the next playPreview always sends fresh (matching the
  // pre-existing "always apply on load" behaviour), even if it resolves to
  // the same memory that was already showing.
  let activePatchMemory = null;
  function applyPatchAt(atS) {
    const memory = resolvePatchAt(patchChanges, atS);
    if (memory === activePatchMemory) return;
    activePatchMemory = memory;
    sendProgramChange(memory);
  }

  function closestRung(target) {
    return RUNGS.reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a), RUNGS[0]);
  }
  // Found live 2026-09-06, Paolo: "not all songs or sections will be
  // practiced from 50%" -- default the preview rung to the INITIALLY
  // selected section's own `starting_speed_pct` (server.py's
  // `_section_starting_speed`, GET /api/song's per-section field: the
  // earned ladder rung for an ordinary section, the last speed actually
  // practiced for a `full_song` one -- and a `full_song` section, being
  // the longest span starting at 0, is `sections[0]` -- i.e. the initial
  // selection -- almost always in practice), falling back to the song's
  // flat `start_speed` default only when nothing has been practised yet
  // or the field is missing (an older cached payload).
  const initialSection = sections.find((s) => s.id === selectedId);
  const initialStartSpeed = initialSection?.starting_speed_pct ?? payload.practice.start_speed;
  let previewSpeed = closestRung(initialStartSpeed);
  let transportPlaying = false;

  // ---- the preview engine (Phase 1.5, R1) ----
  // One RealtimeEngine, lazily created on the first press (never at mount
  // -- see module doc, decision 2, for why: a fresh AudioContext stays
  // suspended until resumed inside a user gesture's own call stack).
  // loadSection() is NOT hoisted into engine creation, unlike practice.js's
  // ensureEngine: the section this screen plays can change between presses
  // (a different lane tile gets selected), so every press loads whatever
  // is currently selected fresh.
  let engine = null;
  let engineReady = false;
  let engineInitPromise = null;
  function ensureEngine() {
    if (!engineInitPromise) {
      engineInitPromise = (async () => {
        const e = createEngine();
        e.addEventListener('ended', onPreviewEnded);
        e.addEventListener('error', (err) => console.error('song.js: engine error', err.detail?.error));
        engine = e;
        engineReady = true;
      })().catch((err) => {
        // Same degrade practice.js's ensureEngine makes: player.js/D4's
        // engine may be unavailable in this browser -- the transport still
        // toggles its own icon, it just plays nothing.
        console.warn(`song.js: RealtimeEngine unavailable (${err && err.message}) — transport updates local state only, no audio.`);
      });
    }
    return engineInitPromise;
  }

  function setTransportIcon(playing) {
    transportPlayBtn.innerHTML = playing
      ? '<svg width="14" height="16" viewBox="0 0 14 16"><rect x="1" y="1" width="4" height="14" fill="var(--ground,#0C1211)"/><rect x="9" y="1" width="4" height="14" fill="var(--ground,#0C1211)"/></svg>'
      : '<svg width="14" height="16" viewBox="0 0 14 16"><path d="M1 1l12 7-12 7z" fill="var(--ground,#0C1211)"/></svg>';
  }

  function onPreviewEnded() {
    transportPlaying = false;
    setTransportIcon(false);
    stopPlayhead();
    // A real end, not stopPlayhead's own internal use from startPlayhead
    // (which must NOT clear this -- playPreview already sent the fresh
    // patch for the section it just loaded, immediately before calling
    // startPlayhead) -- so the NEXT playPreview always sends fresh again,
    // same as this screen's own pre-existing "always apply on load" rule.
    activePatchMemory = null;
  }

  // References `durationS`/`transportPlayBtn`, both declared further down
  // in this function body -- safe: playPreview/setTransportIcon are only
  // ever CALLED from the click handler wired near the end of mount(), long
  // after both are assigned. Kept here, beside the rest of the engine
  // lifecycle, rather than moved past its own declaration site.
  //
  // Found live 2026-09-06, Paolo: previewing a section to fine-tune its
  // boundaries meant re-pressing play after every edit, and the loop
  // shape (R1's `loop: false`, one-shot) didn't match "keep listening
  // while I nudge this" at all. Two changes:
  //
  // - A SELECTED section now loops continuously (`loop: !!sec`) instead
  //   of playing once -- 'ended' (only fired for a non-looping load, see
  //   player.js's own doc) simply never arrives for one, so the only way
  //   to stop it is the transport button itself, same as any other loop
  //   in this app. Nothing selected (previewing the whole recording)
  //   keeps the original one-shot behaviour -- auto-looping a whole
  //   multi-minute recording by default would be a surprise, not a
  //   convenience. This still fires no 'pass' and posts no rep (this
  //   screen never listens for the former or calls the latter -- R1's own
  //   lint test already asserts that, unchanged by adding `loop: true`).
  // - Resumes from `playheadSourceS` when it's still inside the (possibly
  //   just-edited) section, rather than always restarting at `start_s`:
  //   the same one code path now serves a waveform click mid-playback
  //   (seekToClientX, below, only moves the cosmetic position -- the next
  //   reload picks it up from here) AND `patchSection`'s own "reload if
  //   the section playing right now is the one that just changed" call,
  //   so dragging a boundary mid-preview keeps roughly where you were
  //   instead of jumping back to the new start every time.
  async function playPreview() {
    await ensureEngine();
    if (!engineReady || !transportPlaying) return; // unavailable, or paused again before this resolved
    const sec = sections.find((s) => s.id === selectedId);
    const startS = sec ? sec.start_s : 0;
    const endS = sec ? sec.end_s : durationS;
    const startFrom = (playheadSourceS !== null && playheadSourceS >= startS && playheadSourceS < endS)
      ? playheadSourceS : startS;
    await engine.loadSection({
      sectionId: sec ? sec.id : 'full-song',
      audioUrl: `/api/audio/${encodeURIComponent(slug)}`,
      startS, endS,
      preRollS: 0,
      loop: !!sec,
    });
    // #3: auto-apply the patch that covers wherever playback is ABOUT to
    // actually start. For a SELECTED section that is always its own
    // start_s, never startFrom (a possible mid-section scrub resume
    // position) -- "which patch is this section" is a fact about start_s,
    // not about wherever playback happens to pick back up. For the
    // whole-recording preview (no section, sec is null) there is no
    // equivalent fixed "this preview's own patch" -- startS is always 0
    // there, so using it unconditionally would (re-)apply the very first
    // patch on every press even after scrubbing deep into the song;
    // startFrom (the actual resume position, playheadTick's own frame-by-
    // frame applyPatchAt calls would otherwise have to correct a beat
    // later) is the fact that matters there. applyPatchAt/
    // sendProgramChange's own doc covers the toggle/degrade -- fire-and-
    // forget, never blocks audio on a MIDI round trip. activePatchMemory
    // was reset to null on the last stop/pause (or this is the first press
    // this mount), so this always sends even when it resolves to the same
    // memory that was already showing.
    applyPatchAt(sec ? startS : startFrom);
    if (!transportPlaying) return; // paused again while loadSection was in flight
    engine.setSpeedPct(previewSpeed);
    engine.setSemitones(shift);
    if (startFrom !== startS) {
      // Best-effort: a stale scrub position just outside the freshly
      // loaded slice clamps rather than throws (RealtimeEngine.seek's own
      // contract) -- nothing here needs a second fallback for that.
      try { engine.seek(startFrom); } catch { /* no node -- loadSection above would have thrown first */ }
    }
    engine.play();
    startPlayhead(startFrom);
  }

  let shift = clampShift(payload.shift ?? 0); // interactive — see module doc, decision 1.
  const durationS = payload.recording.duration_s;
  // Computed once -- payload.tempo doesn't change during this mount (a
  // re-detect is a full page reload). {bars:[], beats:[]} when bpm is 0 or
  // absent, per CLAUDE.md's degrade rule -- every consumer below already
  // treats that as "nothing to draw / nothing to snap to".
  const grid = computeGrid(payload.tempo, durationS);
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
      <a href="#/progress/${encodeURIComponent(payload.slug)}" title="Practice history for this song"
         style="font-size:14px;color:var(--accent,#E0913F);text-decoration:none">Progress &rarr;</a>
      <div style="margin-left:auto;display:flex;align-items:center;gap:12px">
        <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873);letter-spacing:.04em">RECORD IN ${escapeHtml(payload.recording.tuning).toUpperCase()}</div>
        <div style="display:flex;align-items:center;gap:6px;border:1px solid var(--line,#26302E);border-radius:5px;padding:4px">
          <button data-shift-minus style="width:28px;height:28px;border-radius:3px;border:none;background:var(--raised,#1B2422);display:flex;align-items:center;justify-content:center;font-size:17px;color:var(--ink-2,#9CAAA4);cursor:pointer">&minus;</button>
          <div class="mono num" data-shift-val style="font-size:17px;width:34px;text-align:center;font-weight:600;font-variant-numeric:tabular-nums"></div>
          <button data-shift-plus style="width:28px;height:28px;border-radius:3px;border:none;background:var(--raised,#1B2422);display:flex;align-items:center;justify-content:center;font-size:17px;color:var(--ink-2,#9CAAA4);cursor:pointer">+</button>
        </div>
        <div style="font-size:14px;color:var(--ink-2,#9CAAA4)">plays in ${escapeHtml(payload.recording.tuning)}</div>
        <div data-delete-song title="Delete this song" style="cursor:pointer;color:var(--ink-3,#6A7873);
                    font-size:13px;letter-spacing:.03em;margin-left:8px;padding-left:12px;border-left:1px solid var(--line,#26302E)">Delete</div>
      </div>
    </div>

    <div style="display:flex;gap:22px;flex-grow:1;padding-top:22px;min-height:0">
      <div style="flex-grow:1;display:flex;flex-direction:column;gap:10px;min-width:0">
        <div data-bar-ruler style="position:relative;height:16px"></div>
        <div data-wave-host style="position:relative;height:132px;background:var(--surface,#131B19);border-radius:3px;overflow:hidden">
          <canvas data-grid-canvas style="position:absolute;inset:0;width:100%;height:132px"></canvas>
          <svg data-wave-svg preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:132px"></svg>
          <div data-selection-box style="position:absolute;top:0;bottom:0;display:none;
                      background:rgba(224,145,63,.10);border-left:1px solid var(--accent,#E0913F);border-right:1px solid var(--accent,#E0913F)"></div>
          <div data-playhead style="position:absolute;top:0;bottom:0;left:0;width:2px;display:none;
                      background:var(--good,#5FA88F);pointer-events:none"></div>
        </div>

        <div data-patch-lane title="Click to add a GX-100 patch change; click a dot to edit or delete it"
             style="position:relative;height:18px;cursor:pointer;background:var(--surface,#131B19);border-radius:3px"></div>

        <div data-lane-root style="position:relative;height:88px;overflow:hidden"></div>

        <div style="margin-top:14px;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;
                    padding:14px 18px;display:flex;align-items:center;gap:20px">
          <div data-transport-restart title="Back to this section's start" style="width:42px;height:42px;border-radius:21px;background:var(--raised,#1B2422);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0">
            ${FOOT_ICONS.restart_section}
          </div>
          <div data-transport-play style="width:42px;height:42px;border-radius:21px;background:var(--accent,#E0913F);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0">
            <svg width="14" height="16" viewBox="0 0 14 16"><path d="M1 1l12 7-12 7z" fill="var(--ground,#0C1211)"/></svg>
          </div>
          <div style="width:1px;height:26px;background:var(--line,#26302E)"></div>
          <div data-rungs style="display:flex;align-items:center;gap:2px"></div>
          <div style="width:1px;height:26px;background:var(--line,#26302E)"></div>
          <div class="mono" style="font-size:13px;color:var(--ink-2,#9CAAA4)">PREVIEW ONLY &middot; PRACTICE A SECTION TO LOOP IT</div>
          <div data-transport-clock class="mono" style="margin-left:auto;font-size:14px;color:var(--ink-2,#9CAAA4);font-variant-numeric:tabular-nums">${fmtPreciseS(0)} / ${fmtPreciseS(durationS)}</div>
        </div>
      </div>

      <div data-inspector style="width:326px;flex-shrink:0;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;
                  padding:20px;display:flex;flex-direction:column;gap:16px"></div>
    </div>
  `;
  el.appendChild(root);

  const waveHost = root.querySelector('[data-wave-host]');
  const waveSvg = root.querySelector('[data-wave-svg]');
  const gridCanvas = root.querySelector('[data-grid-canvas]');
  const selectionBox = root.querySelector('[data-selection-box]');
  const playheadEl = root.querySelector('[data-playhead]');
  const laneRoot = root.querySelector('[data-lane-root]');
  const inspector = root.querySelector('[data-inspector]');
  const barRuler = root.querySelector('[data-bar-ruler]');
  const rungsHost = root.querySelector('[data-rungs]');
  const transportPlayBtn = root.querySelector('[data-transport-play]');
  const transportRestartBtn = root.querySelector('[data-transport-restart]');
  const transportClockEl = root.querySelector('[data-transport-clock]');

  root.querySelector('[data-back]').addEventListener('click', () => { location.hash = '#/'; });

  // Deliberately irreversible: this deletes the whole songs/<slug>/ tree on
  // disk, real audio included (server.py's `_post_song_delete`), and drops
  // the slug from every setlist that names it. It does NOT touch
  // practice/reps.jsonl (CLAUDE.md invariant 5 -- the ledger never loses a
  // line, even for a song that no longer exists). window.confirm() is the
  // only guard against a misclick; there is no undo past this point.
  root.querySelector('[data-delete-song]').addEventListener('click', async () => {
    const ok = window.confirm(
      `Delete "${payload.title}" for good?\n\nThis removes its audio file and cache from disk `
      + 'and takes it out of every setlist. Its past practice history stays in the ledger, '
      + 'but nothing can undo this.'
    );
    if (!ok) return;
    try {
      await post('/api/song/delete', { song: slug });
      location.hash = '#/';
    } catch (err) {
      window.alert(`Could not delete "${payload.title}": ${err && err.message}`);
    }
  });

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
    // Found live 2026-09-06, same class of bug as the rung/speed fix
    // above: this only ever updated the LOCAL `shift` a future
    // playPreview() would read -- a mid-playback +/- press was silent
    // until the next play. practice.js's own transpose handlers already
    // call engine.setSemitones(shift) live on every press; this screen
    // just never did.
    if (engineReady) engine.setSemitones(shift);
  }
  root.querySelector('[data-shift-minus]').addEventListener('click', () => bumpShift(-1));
  root.querySelector('[data-shift-plus]').addEventListener('click', () => bumpShift(1));
  unsubs.push(on('transpose_down', () => bumpShift(-1)));
  unsubs.push(on('transpose_up', () => bumpShift(1)));

  // Millisecond nudge ('['/']', see keys.js) -- widens the SELECTED
  // section's boundary by NUDGE_S. Mutates the local `sections` entry and
  // redraws immediately (so repeated presses feel instant), then debounces
  // the commit (300ms) so holding the key doesn't send one POST per
  // keystroke -- the same pattern persistShift uses for the same reason.
  let nudgeTimer = null;
  function nudgeBoundary(which, deltaS) {
    const sec = sections.find((s) => s.id === selectedId);
    if (!sec) return;
    if (which === 'start') sec.start_s = Math.min(sec.start_s + deltaS, sec.end_s - 0.01);
    else sec.end_s = Math.max(sec.end_s + deltaS, sec.start_s + 0.01);
    redrawAll();
    clearTimeout(nudgeTimer);
    nudgeTimer = setTimeout(() => patchSection(sec.id, {}), 300);
  }
  unsubs.push(on('nudge_start', () => nudgeBoundary('start', -NUDGE_S)));
  unsubs.push(on('nudge_end', () => nudgeBoundary('end', NUDGE_S)));

  /** Toggle the preview transport's own play/pause state -- shared by the
   * button's click and Space (ACTIONS.play_pause, keys.js's KEY_MAP entry
   * for it; found live 2026-09-06, this screen never actually subscribed
   * to the action despite the map already naming Space "the universal
   * transport key"), so the two never drift into two copies of the same
   * toggle. Phase 1.5, R1: a real, non-looping preview -- see module doc,
   * decision 2. Never counts a rep; practice.js's engine is the only one
   * that does. */
  function togglePreviewPlaying() {
    transportPlaying = !transportPlaying;
    setTransportIcon(transportPlaying);
    if (transportPlaying) {
      playPreview().catch((err) => console.error('song.js: playPreview failed', err));
    } else if (engineReady) {
      // playPreview() reloads the section on every press (module doc,
      // decision 2), so there is a window while that load is in flight
      // where engine._node has been torn down and not yet replaced --
      // pause() would throw there. playPreview's own `if (!transportPlaying)
      // return` checks (before AND after its await) already make this a
      // no-op-and-abort rather than a real desync; only the throw itself
      // needs swallowing here.
      try { engine.pause(); } catch { /* no node between loads -- see above */ }
      pausePlayhead();
      // Same reasoning as onPreviewEnded: the next resume goes back through
      // playPreview (module doc, decision 2: it reloads on every press), so
      // this makes that resend fresh instead of skipping it because the
      // resolved memory happens to be unchanged.
      activePatchMemory = null;
    }
  }
  transportPlayBtn.addEventListener('click', togglePreviewPlaying);
  unsubs.push(on('play_pause', togglePreviewPlaying));

  rungsHost.innerHTML = RUNGS.map((r) => `<button class="rung${r === previewSpeed ? ' sel' : ''}" data-rung="${r}">${r}</button>`).join('');
  rungsHost.querySelectorAll('[data-rung]').forEach((btn) => {
    btn.addEventListener('click', () => {
      previewSpeed = Number(btn.dataset.rung);
      rungsHost.querySelectorAll('.rung').forEach((b) => b.classList.toggle('sel', Number(b.dataset.rung) === previewSpeed));
      // Found live 2026-09-06: this only ever updated the LOCAL variable
      // a future playPreview() would read -- a mid-playback rung click did
      // nothing audible until the next press. practice.js's own speed
      // slider already established that engine.setSpeedPct() takes effect
      // immediately (see player.js's module doc / practice.js's "FOUND
      // LIVE 2026-09-05" note); this screen just never called it here.
      if (engineReady) engine.setSpeedPct(previewSpeed);
    });
  });

  // #1: Reaper-style zoom/pan, session-only view state -- never persisted,
  // recomputed (well, consulted) on every render exactly like `view()`
  // itself always was. See timeline.js's own module doc for the full
  // reasoning and attribution (lifted from rambass-live's console.html).
  let viewWindow = FIT_ZOOM_PAN;

  function view() {
    return zoomPanView(viewWindow, durationS, waveHost.clientWidth || 1);
  }

  // Wheel to zoom about the pointer; alt+wheel OR shift+wheel to pan --
  // Reaper's own gestures (console.html's onWheel, lifted). preventDefault
  // because the alternative is the page scrolling out from under the
  // pointer while the wave-host zooms.
  waveHost.addEventListener('wheel', onWaveWheel, { passive: false });
  function onWaveWheel(event) {
    event.preventDefault();
    const rect = waveHost.getBoundingClientRect();
    // Both sides of this ratio are viewport pixels (clientX, rect.left,
    // rect.width all come from the same getBoundingClientRect()), so unlike
    // computeSeekPosition this needs no separate rescale for the practice/
    // song screens' own CSS `transform: scale(...)` stage wrapper -- the
    // scale cancels out of a ratio of two viewport measurements.
    const anchorFrac = rect.width ? (event.clientX - rect.left) / rect.width : 0.5;
    const notches = event.deltaY > 0 ? 1 : -1;
    const next = (event.altKey || event.shiftKey)
      ? panBy(viewWindow, notches * PAN_STEP)
      : zoomBy(viewWindow, notches > 0 ? 1 / ZOOM_STEP : ZOOM_STEP, anchorFrac);
    if (next === viewWindow) return; // already at a limit -- nothing moved
    viewWindow = next;
    repaintView();
  }

  // Roughly this many bar-number labels across the ruler, matching
  // design/SongPage.dc.html's own 7 (1 · 18 · 35 · 52 · 69 · 86 · 103 —
  // evenly spaced in BAR NUMBER, which is the same as evenly spaced in
  // time at a constant bpm). Real marks from `grid.bars`, positioned with
  // the real `viewX` -- not the flex-evenly-distributed approximation this
  // replaced, which had no way to account for `grid_offset_s`.
  const RULER_MARK_COUNT = 7;

  function renderBarRuler() {
    barRuler.innerHTML = '';
    if (!grid.bars.length) return; // no tempo yet -- no ruler, per CLAUDE.md's degrade rule
    const v = view();
    // #1: picked from bars actually IN the current (possibly zoomed) view,
    // not a fixed whole-song stride -- otherwise a zoomed-in view, exactly
    // the case #1 exists to make legible, would show zero bar numbers most
    // of the time (a stride computed for ~7 marks across the WHOLE song
    // lands almost none of them inside a narrow window). Falls back to the
    // whole-song set when the view happens to contain none (an edge case
    // at very low bar density), matching the pre-#1 behaviour there.
    const visible = grid.bars.filter((t) => t >= v.startS && t <= v.endS);
    const source = visible.length ? visible : grid.bars;
    const step = Math.max(1, Math.round(source.length / RULER_MARK_COUNT));
    for (let i = 0; i < source.length; i += step) {
      const t = source[i];
      const x = viewX(t, v);
      if (x < 0 || x > v.widthPx) continue;
      const d = document.createElement('div');
      d.className = 'mono';
      d.style.cssText = `position:absolute;left:${x}px;top:0;font-size:11px;color:var(--ink-4,#5B6A64)`;
      d.textContent = String(barOf(t, payload.tempo));
      barRuler.appendChild(d);
    }
  }

  function renderWave() {
    const v = view();
    if (gridCanvas.clientWidth) {
      const { ctx } = sizeCanvas(gridCanvas);
      ctx.clearRect(0, 0, gridCanvas.clientWidth, gridCanvas.clientHeight);
      drawGrid(ctx, v, grid);
    }
    // #1: peaks.peaks always spans the WHOLE recording (wave.js's own
    // module doc) -- a zoomed view must window it down first, or drawWave
    // stretches the entire song's peaks across whatever narrow slice of
    // pixels the zoom left it, which looks like a waveform but is the
    // wrong one.
    const windowed = slicePeaksToWindow(peaks, v.startS, v.endS, durationS);
    drawWave(waveSvg, windowed, v, 0, SONG_WAVE_OPTS);
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

  // #1: the current section's start/end as two draggable vertical bars
  // drawn directly on the waveform, against the (possibly zoomed) pixel
  // space -- reusing sections.js's own attachDragHandlers on
  // `selectionBox` itself rather than a second drag implementation.
  // selectionBox is already exactly the shape attachDragHandlers expects
  // (an absolutely-positioned child of the "track" it drags against,
  // percentage left/width -- `pct()`'s own math matches
  // renderSelectionHighlight's above exactly), so this gets the same
  // bronze `sect__handle` grips the lane tiles use for free. Committed
  // through the same patchSection path as every other boundary edit
  // (inspector fields, snap, nudge, the lane's own drag handles) --
  // patchSection's own reload-if-playing note applies here unchanged.
  let detachWaveDrag = null;
  function renderWaveDrag() {
    detachWaveDrag?.();
    detachWaveDrag = null;
    const sec = sections.find((s) => s.id === selectedId);
    if (!sec) return; // selectionBox is already hidden -- nothing to attach handles to
    detachWaveDrag = attachDragHandlers(selectionBox, sec, view(), grid, {
      onDragCommit: (patch) => { patchSection(patch.id, patch); },
      // selectionBox owns its own permanent, translucent look (set once in
      // this screen's own HTML template so the waveform stays visible
      // underneath) — never the lane-tile opaque "selected" fill
      // attachDragHandlers otherwise reapplies on drag-end. See
      // attachDragHandlers' own doc, "Found live 2026-09-11".
      restyleOnDrag: false,
    });
  }

  // ---- the playhead (Phase 1.5, found live 2026-09-06) ----
  // player.js's RealtimeEngine still has no real position accessor (module
  // doc, decision 2, names this an open D4 gap) -- so, same as
  // practice.js's own ring/waveform estimate (that file's own "decision 3"),
  // this is a COSMETIC wall-clock estimate, not a read from the audio
  // graph. Unlike practice.js's looping estimate, this one is a plain
  // INTEGRATION (add each frame's own elapsed-real-time * the speed THAT
  // WAS ACTIVE during that frame) rather than a single elapsed*speed
  // multiplication -- the same "FOUND LIVE 2026-09-05" note above already
  // explains why recomputing from a CHANGED speed retroactively distorts
  // time that already played at the OLD one; integrating avoids that
  // without needing this screen's own non-looping playback to reason about
  // wrap-around at all.
  let playheadSourceS = null; // null: hidden, nothing playing
  let playheadRafId = null;
  let playheadLastTs = null;

  // transportClockEl now doubles as the live, precise seconds counter this
  // unit's own module doc line 3 wanted, driven by the exact same estimate
  // as the playhead line -- both read `playheadSourceS`, so they can never
  // show two different numbers. 3 decimals, matching the inspector's own
  // Start/End fields exactly (see fmtPreciseS): read this while listening,
  // type it straight into Start or End.
  // Re-entrancy guard for the nested repaintView() call below -- console.
  // html's own updatePlayhead has the identical guard (`this._following`),
  // for the identical reason: repaintView() calls renderPlayhead() again,
  // which must find the playhead already on screen and stop, not follow a
  // second time.
  let followingRepaint = false;

  function renderPlayhead() {
    // #1: page the zoomed window along while ACTUALLY PLAYING, before
    // drawing anything at the (about to be stale) view -- console.html's
    // own "Keep the playhead on screen while it is running". Never while
    // paused: a scrub or a nudge (seekToClientX, restartToSectionStart) is
    // a deliberate look somewhere, and paging the view out from under a
    // click is how a zoomed screen becomes unusable.
    if (transportPlaying && !followingRepaint && playheadSourceS !== null && durationS > 0) {
      const next = followTo(viewWindow, playheadSourceS / durationS);
      if (next !== viewWindow) {
        viewWindow = next;
        followingRepaint = true;
        repaintView();
        followingRepaint = false;
        return; // repaintView() already re-rendered the playhead against the new window
      }
    }
    const v = view();
    transportClockEl.textContent = `${fmtPreciseS(playheadSourceS ?? 0)} / ${fmtPreciseS(durationS)}`;
    if (playheadSourceS === null || !v.widthPx) { playheadEl.style.display = 'none'; return; }
    playheadEl.style.display = 'block';
    // transform, not a redrawn %-left -- console.html's own `.playhead` CSS
    // convention (position:absolute, moved via transform), lifted because
    // this now runs every animation frame AND potentially pages the whole
    // view along with it; a transform is a compositor-only move, no layout.
    // waveHost's own `overflow:hidden` clips it for free once it strays
    // outside [0, widthPx] -- no separate visibility toggle needed for that.
    playheadEl.style.transform = `translateX(${viewX(playheadSourceS, v).toFixed(2)}px)`;
  }

  function playheadTick(ts) {
    if (playheadLastTs !== null) {
      const dtS = (ts - playheadLastTs) / 1000;
      playheadSourceS += dtS * (previewSpeed / 100);
    }
    playheadLastTs = ts;
    // Found live 2026-09-11, Paolo: "the playhead crossing a program change
    // dot does not change the selected patch" -- playPreview only ever
    // applied the patch that covers a section's/preview's own START, once,
    // at load. This is the other half: re-resolve every frame against the
    // (cosmetic-estimate) playhead position itself, so a dot the playhead
    // actually plays PAST while a section or the whole recording is looping
    // or playing through it fires too -- applyPatchAt's own de-dupe against
    // activePatchMemory means this is a no-op on every frame that hasn't
    // crossed into a new entry yet, not one MIDI send per frame.
    applyPatchAt(playheadSourceS);
    const sec = sections.find((s) => s.id === selectedId);
    const startS = sec ? sec.start_s : 0;
    const endS = sec ? sec.end_s : durationS;
    if (playheadSourceS >= endS) {
      if (sec) {
        // A selected section now loops continuously (playPreview's own
        // `loop: !!sec`, found live 2026-09-06) -- the real audio wraps
        // natively in the audio graph, so this cosmetic estimate wraps to
        // match rather than clamping-and-stopping (that was only ever
        // correct for the one-shot case below). Modulo, not a hard reset
        // to startS, so an overshoot of a few ms -- this is still only an
        // estimate, module doc decision 2 -- carries into the next lap
        // instead of being silently dropped.
        const span = endS - startS;
        playheadSourceS = span > 0 ? startS + ((playheadSourceS - startS) % span) : startS;
      } else {
        // Whole-recording preview: still one-shot (playPreview's own
        // doc). 'ended' will stop the engine and hide the playhead on its
        // own (onPreviewEnded); clamp here just so the line doesn't
        // visibly overshoot the end in the last frame or two before that
        // event actually arrives.
        playheadSourceS = endS;
        renderPlayhead();
        return;
      }
    }
    renderPlayhead();
    playheadRafId = requestAnimationFrame(playheadTick);
  }

  function startPlayhead(startS) {
    stopPlayhead();
    playheadSourceS = startS;
    playheadLastTs = null;
    playheadRafId = requestAnimationFrame(playheadTick);
  }

  function stopPlayhead() {
    if (playheadRafId !== null) cancelAnimationFrame(playheadRafId);
    playheadRafId = null;
    playheadSourceS = null;
    renderPlayhead();
  }

  /** Cancels the rAF loop WITHOUT clearing `playheadSourceS` -- unlike
   *  stopPlayhead (a real end: nothing to show a position for any more),
   *  a pause is the whole point of this workflow: Paolo, live 2026-09-06,
   *  hunting for a section's exact start by playing at 50%, hitting pause
   *  the instant it sounds right, then reading the timestamp off the
   *  frozen counter -- "upon pausing the playhead disappears and timer
   *  goes back to 0.000s, which prevents me from finding exactly where
   *  the start of the solo is." The marker stays put (and stays click-
   *  seekable, per seekToClientX's own doc above) until the next
   *  startPlayhead (a fresh press elsewhere) or stopPlayhead (a real end)
   *  moves or clears it. */
  function pausePlayhead() {
    if (playheadRafId !== null) cancelAnimationFrame(playheadRafId);
    playheadRafId = null;
    playheadLastTs = null;
    renderPlayhead();
  }

  // ---- waveform click-to-seek (found live 2026-09-06, Paolo: "I need the
  // ability to click on the waveform and move the playhead... to seek
  // section starts much more quickly") ----
  // Reuses timeline.js's computeSeekPosition (moved there from
  // practice.js this same session for exactly this reuse) against THIS
  // screen's own whole-recording view() (unlike practice.js, which windows
  // to one section) -- a click anywhere in the waveform seeks to that
  // absolute source position. Works whether or not anything is currently
  // playing: paused, it just moves the reference marker/counter (and
  // playPreview, above, will resume from there on the next press, same as
  // a boundary edit does); playing, it ALSO seeks the live engine, and the
  // already-running playheadTick rAF loop picks up the new position on its
  // very next frame (playheadLastTs reset so it doesn't integrate a huge
  // dt against a stale timestamp).
  waveHost.addEventListener('pointerdown', (e) => seekToClientX(e.clientX));
  function seekToClientX(clientX) {
    const rect = waveHost.getBoundingClientRect();
    const { sourceS } = computeSeekPosition(clientX, rect, view());
    seekPlayheadTo(sourceS);
  }

  /** The move-position half of a seek, factored out of seekToClientX so
   *  #2's restart control (any other fixed-target seek, not just a click)
   *  can share it rather than duplicate the two-state dance: paused, only
   *  the cosmetic marker/counter moves; playing, the live engine seeks too
   *  and the running playheadTick rAF loop picks the new position up on
   *  its very next frame. */
  function seekPlayheadTo(sourceS) {
    playheadSourceS = sourceS;
    playheadLastTs = null;
    renderPlayhead();
    if (engineReady && transportPlaying) {
      try { engine.seek(sourceS); } catch { /* no node yet -- nothing to seek */ }
    }
  }

  // #2: back to the top of the SELECTED section without touching whether
  // the transport is playing or paused -- seekPlayheadTo already makes
  // exactly that distinction (see its own doc), so this is only ever the
  // target resolution (restartTargetS) plus the same seek. Must never
  // assign `transportPlaying`, call `engine.play()`/`engine.pause()`, or
  // touch the play/pause icon -- practice.js's own restart_section chip
  // hit precisely that bug once already (commit 6dc2850, "Fix restart
  // playing through a pause"), and web/tests/test_song_restart.mjs checks
  // this function's own source for it.
  function restartToSectionStart() {
    seekPlayheadTo(restartTargetS(sections, selectedId));
  }
  transportRestartBtn.addEventListener('click', restartToSectionStart);

  let detachCreateHandler = null;
  function renderLanes() {
    renderSections(laneRoot, sections, view(), grid, {
      // This screen owns the selection; sections.js only draws it. Passing
      // it back in is what keeps the tile you are editing selected across
      // the redraw a committed inspector field triggers.
      selectedId,
      onSelect: (id) => { selectedId = id; renderSelectionHighlight(); renderWaveDrag(); renderInspector(); },
      onDragCommit: (patch) => { patchSection(patch.id, patch); },
    });
    // Detach the PREVIOUS call's listeners before attaching fresh ones --
    // renderLanes runs on every redrawAll, and attachCreateHandler's own
    // hover-cursor listeners (found live 2026-09-06) made a stale,
    // never-detached copy from an earlier redraw newly visible as a bug
    // (duplicate pointerenter/leave handlers, each harmlessly setting the
    // same value -- but "harmless today" is not a reason to leave a
    // known-growing listener list attached to a long-lived element).
    detachCreateHandler?.();
    detachCreateHandler = attachCreateHandler(laneRoot, view(), {
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
      start_speed: current.start_speed, ladder_step: current.ladder_step, reps_to_advance: current.reps_to_advance,
      notes: current.notes,
      counts_toward_readiness: current.counts_toward_readiness,
      lead_in_beats: current.lead_in_beats, full_song: current.full_song,
      ...partial,
    };
    const res = await post('/api/section', body);
    sections = orderSections(res.sections);
    redrawAll();
    // Found live 2026-09-06, Paolo: dragging a boundary (or typing into the
    // inspector's Start/End fields) of the section CURRENTLY PLAYING kept
    // looping the OLD bounds until the transport was stopped and restarted
    // by hand. Every boundary-commit path (inspector fields, snap, nudge,
    // the lane's own drag handles) funnels through here, so reloading once,
    // right here, covers all of them without each caller remembering to.
    // Only when the edited section IS the one playing right now -- editing
    // some OTHER section while a different one plays must not interrupt it.
    if (transportPlaying && selectedId === id) {
      playPreview().catch((err) => console.error('song.js: playPreview failed', err));
    }
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
          <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64);margin-top:5px">BAR ${barBeatLabel(sec.start_s, payload.tempo)}</div></div>
        <div style="flex:1"><div class="flbl">End</div>
          <input class="fld mono" data-f="end_s" value="${sec.end_s.toFixed(3)} s" style="font-size:13px">
          <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64);margin-top:5px">BAR ${barBeatLabel(sec.end_s, payload.tempo)}</div></div>
      </div>
      <div>
        <div class="flbl">Snap</div>
        <div style="display:flex;gap:6px" data-snap-group>
          ${['free', 'beat', 'bar'].map((mode) => `
            <button data-snap="${mode}" class="mono" style="flex:1;padding:6px 0;border-radius:3px;border:1px solid var(--line,#26302E);
                        cursor:pointer;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
                        background:${sec.snapped === mode ? 'var(--accent,#E0913F)' : 'var(--sunken,#0F1614)'};
                        color:${sec.snapped === mode ? 'var(--ground,#0C1211)' : 'var(--ink-2,#9CAAA4)'}">${mode}</button>
          `).join('')}
        </div>
      </div>
      <div>
        <button data-full-song class="mono" style="width:100%;text-align:left;padding:8px 11px;border-radius:4px;
                    border:1px solid var(--line,#26302E);cursor:pointer;font-size:12px;letter-spacing:.04em;
                    background:${sec.full_song ? 'var(--accent-tint,#2A2118)' : 'var(--sunken,#0F1614)'};
                    color:${sec.full_song ? 'var(--on-tint,#F0C48A)' : 'var(--ink-2,#9CAAA4)'}">
          ${sec.full_song ? '&#9745;' : '&#9744;'} FULL SONG &mdash; reps count, excluded from next/prev and readiness
        </button>
      </div>
      <div style="display:flex;gap:6px">
        <div style="flex:1;min-width:0"><div class="flbl">Start</div>
          <select class="fld mono" data-f="start_speed" style="font-size:12px;padding:8px 4px">${
            selectOptions(SPEED_OPTIONS, sec.start_speed ?? payload.practice.start_speed, (v) => `${v}%`)
          }</select></div>
        <div style="flex:1;min-width:0"><div class="flbl">Target</div>
          <select class="fld mono" data-f="target_speed" style="font-size:12px;padding:8px 4px">${
            selectOptions(SPEED_OPTIONS, sec.target_speed, (v) => `${v}%`)
          }</select></div>
        <div style="flex:1;min-width:0"><div class="flbl">Step</div>
          <select class="fld mono" data-f="ladder_step" style="font-size:12px;padding:8px 4px">${
            selectOptions(STEP_OPTIONS, sec.ladder_step ?? payload.practice.ladder_step, (v) => `${v}%`)
          }</select></div>
        <div style="flex:1;min-width:0"><div class="flbl">Reps</div>
          <select class="fld mono" data-f="reps_to_advance" style="font-size:12px;padding:8px 4px">${
            selectOptions(REPS_OPTIONS, sec.reps_to_advance ?? payload.practice.reps_to_advance)
          }</select></div>
      </div>
      <div>
        <div class="flbl">Notes</div>
        <textarea class="fld" data-f="notes" style="height:94px">${escapeHtml(sec.notes ?? '')}</textarea>
      </div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:12px">
        <button class="practise-btn" data-practise>Practice this</button>
        ${sec.full_song
          ? '<div class="mono" style="font-size:11px;color:var(--ink-3,#6A7873);text-align:center;line-height:1.5">The whole-song entry can&rsquo;t be deleted &mdash; trim Start/End for a long intro or outro instead.</div>'
          : '<button class="practise-btn" data-delete style="background:var(--warn-tint,#2A1D17);color:var(--warn,#C9805E)">Delete section</button>'}
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
    numField('start_speed', parseFloat);
    numField('target_speed', parseFloat);
    numField('ladder_step', parseFloat);
    numField('reps_to_advance', (v) => parseInt(v, 10));
    inspector.querySelector('[data-f="notes"]').addEventListener('change', (e) => patchSection(sec.id, { notes: e.target.value }));
    inspector.querySelector('[data-practise]').addEventListener('click', () => {
      location.hash = `#/practice/${encodeURIComponent(slug)}/${encodeURIComponent(sec.id)}`;
    });
    // Absent entirely for the full_song entry (the block above replaces it
    // with an explanatory note) -- optional chaining, not a `?` in the
    // selector, since deleteSection itself also refuses (server.py's
    // ensure_deletable) and this is only the UI-side half of that.
    inspector.querySelector('[data-delete]')?.addEventListener('click', () => deleteSection(sec.id));
    inspector.querySelectorAll('[data-snap]').forEach((btn) => {
      btn.addEventListener('click', () => patchSection(sec.id, { snapped: btn.dataset.snap }));
    });
    inspector.querySelector('[data-full-song]').addEventListener('click', () => {
      patchSection(sec.id, { full_song: !sec.full_song });
    });
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

  // ---- #3: the GX-100 patch-change lane ------------------------------
  // A per-SONG timeline (unlike sections, which are per-target spans):
  // clicking an empty spot drops a program change at that source second;
  // clicking an existing dot opens the same popup pre-filled, with a
  // delete option. Dots are positioned through the same view() every
  // other wave-aligned element uses, so they stay correct under zoom/pan
  // (#1) with no separate coordinate system.
  const patchLaneEl = root.querySelector('[data-patch-lane]');
  patchLaneEl.style.position = 'relative';
  // Found live 2026-09-11, Paolo: nothing about the lane read as clickable
  // beyond its title tooltip (invisible until hovered long enough to show)
  // -- same hover-tint convention sections.js's own attachCreateHandler
  // already uses for its "drag here to create a section" affordance, lifted
  // rather than reinvented. The resting background (template's own
  // `var(--surface)`) is what makes the strip legible as its own clickable
  // element even before any hover; this only brightens it further on top.
  patchLaneEl.addEventListener('pointerenter', () => { patchLaneEl.style.background = 'rgba(224,145,63,.10)'; });
  patchLaneEl.addEventListener('pointerleave', () => { patchLaneEl.style.background = 'var(--surface,#131B19)'; });

  /** The local patch-name list (config/gx100.yaml), fetched once and
   *  cached -- "refresh" (below) re-fetches it explicitly rather than a
   *  network round trip happening on every popup open. */
  let patchNames = null;
  async function ensurePatchNames(forceRefresh = false) {
    if (patchNames && !forceRefresh) return patchNames;
    try {
      patchNames = await get('/api/gx100/patches');
    } catch {
      patchNames = { channel: 1, patches: [] };
    }
    return patchNames;
  }

  function patchOptionsHtml(names, selected) {
    const known = names.patches ?? [];
    // The currently selected memory is always an option, even if it is
    // not (or no longer) in the local list -- editing an existing dot
    // must never silently show a DIFFERENT memory than the one actually
    // stored just because config/gx100.yaml doesn't mention it.
    const memories = known.some((p) => p.memory === selected)
      ? known
      : [{ memory: selected, name: '' }, ...known];
    return memories.map((p) => {
      const label = p.name ? `${p.memory} — ${p.name}` : p.memory;
      return `<option value="${escapeHtml(p.memory)}"${p.memory === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
  }

  /**
   * The popup's own body -- factored out of openPatchPopup so a refresh can
   * re-render it in place (same popupEl, same position) both BEFORE the
   * fetch (a "Loading…" shell) and after it resolves, rather than the two
   * states being different code paths that can drift.
   * *state* is `{loading: true}` or `{loading: false, names}`.
   */
  function patchPopupBodyHtml(existing, state) {
    const controls = state.loading
      ? `<span class="mono" data-patch-loading style="font-size:12px;color:var(--ink-3,#6A7873);min-width:140px">Loading patches…</span>
         <button data-patch-refresh title="Re-read config/gx100.yaml" class="stepper-btn" disabled
                 style="width:28px;height:28px;font-size:14px;opacity:.45">&#8635;</button>`
      : `<select class="fld mono" data-patch-select style="font-size:13px;min-width:140px">
           ${patchOptionsHtml(state.names, existing?.patch ?? DEFAULT_MEMORY)}
         </select>
         <button data-patch-refresh title="Re-read config/gx100.yaml" class="stepper-btn" style="width:28px;height:28px;font-size:14px">&#8635;</button>`;
    const deleteBtn = existing
      ? '<button data-patch-delete title="Delete this program change" class="stepper-btn" style="width:28px;height:28px;font-size:14px;color:var(--warn,#C9805E)">&times;</button>'
      : '';
    // Found live 2026-09-11, Paolo: an empty config/gx100.yaml (or one that
    // doesn't exist yet -- gx100.load_patch_names's own degrade) left the
    // select showing only the current/default memory with nothing to
    // explain why, indistinguishable from "the list failed to load". This
    // names the actual reason once the fetch has genuinely come back empty.
    // `woodshed gx100 sync` (added the same day) reads it straight off the
    // pedal -- this popup's own refresh icon only re-reads the file, since
    // a sync is a real, minutes-long hardware operation and stays a
    // deliberate terminal command (docs/05-foot-control.md).
    const hint = (!state.loading && (state.names.patches ?? []).length === 0)
      ? `<div class="mono" style="font-size:10px;color:var(--ink-3,#6A7873);margin-top:6px;max-width:230px;line-height:1.4">
           No patches configured yet &mdash; run <code>woodshed gx100 sync</code> to read
           them off the pedal, or add them by hand to <code>config/gx100.yaml</code>.
         </div>`
      : '';
    return `<div style="display:flex;gap:6px;align-items:center">${controls}${deleteBtn}</div>${hint}`;
  }

  /** Wires up whatever the current popup body actually contains -- called
   *  after every (re)render, loading shell included, so a click during a
   *  refresh (delete, say) still works. */
  function attachPatchPopupHandlers(popup, atS, existing) {
    popup.querySelector('[data-patch-select]')?.addEventListener('change', async (e) => {
      const { patch_changes: updated } = await post('/api/patch-change', {
        song: slug, action: 'upsert', at_s: atS, patch: e.target.value,
      });
      patchChanges = updated;
      closePatchPopup();
      renderPatchLane();
    });
    const refreshBtn = popup.querySelector('[data-patch-refresh]');
    if (!refreshBtn.disabled) {
      refreshBtn.addEventListener('click', () => {
        // Re-render the SAME popupEl as a loading shell first -- so the
        // click has a visible effect immediately, even when the fetch
        // that follows is fast enough (or, on a still-empty
        // config/gx100.yaml, unchanged enough) to look like nothing
        // happened otherwise -- then fetch and fill it back in.
        popup.innerHTML = patchPopupBodyHtml(existing, { loading: true });
        attachPatchPopupHandlers(popup, atS, existing);
        fillPatchPopup(popup, atS, existing, /* forceRefresh */ true);
      });
    }
    popup.querySelector('[data-patch-delete]')?.addEventListener('click', async () => {
      const { patch_changes: updated } = await post('/api/patch-change', {
        song: slug, action: 'delete', at_s: atS,
      });
      patchChanges = updated;
      closePatchPopup();
      renderPatchLane();
    });
  }

  /** Fetch the patch-name list and fill *popup* in once it resolves.
   *  Guards against the popup having been closed (click elsewhere) or
   *  reopened at a different position while the fetch was in flight -- a
   *  stale response must never overwrite whatever is showing now. */
  async function fillPatchPopup(popup, atS, existing, forceRefresh) {
    const names = await ensurePatchNames(forceRefresh);
    if (popupEl !== popup) return;
    popup.innerHTML = patchPopupBodyHtml(existing, { loading: false, names });
    attachPatchPopupHandlers(popup, atS, existing);
  }

  let popupEl = null;
  function closePatchPopup() {
    popupEl?.remove();
    popupEl = null;
  }

  /**
   * Open the create/edit popup at *atS* (source seconds). *existing* is
   * the patch_changes entry a dot click landed on, or null for a click on
   * empty lane space (in which case a memory is not committed until one
   * is actually chosen from the select -- there is nothing to create yet
   * from an empty click alone).
   */
  async function openPatchPopup(atS, existing) {
    closePatchPopup();
    const x = viewX(atS, view());

    popupEl = document.createElement('div');
    popupEl.style.cssText = 'position:absolute;top:100%;margin-top:4px;z-index:20;'
      + 'background:var(--surface,#131B19);border:1px solid var(--line,#26302E);border-radius:6px;'
      + 'padding:8px;box-shadow:0 8px 24px rgba(0,0,0,.4)';
    popupEl.style.left = `${Math.max(0, x - 60)}px`;
    // Found live 2026-09-11, Paolo: this used to `await ensurePatchNames()`
    // BEFORE creating popupEl at all -- on a cold cache (the very first
    // popup of the session) that meant one full network round trip with
    // NOTHING on screen: no popup, no spinner, indistinguishable from the
    // click not having registered. The loading shell below appears
    // immediately; fillPatchPopup swaps in the real content once the fetch
    // (cached, so usually instant after the first open) actually resolves.
    popupEl.innerHTML = patchPopupBodyHtml(existing, { loading: true });
    patchLaneEl.appendChild(popupEl);
    attachPatchPopupHandlers(popupEl, atS, existing);
    // Stop a click inside the popup from bubbling to the document-level
    // "click elsewhere closes it" listener wired once, below.
    popupEl.addEventListener('pointerdown', (e) => e.stopPropagation());
    await fillPatchPopup(popupEl, atS, existing, /* forceRefresh */ false);
  }

  patchLaneEl.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    const dotAtS = e.target.dataset.atS;
    if (dotAtS !== undefined) {
      openPatchPopup(Number(dotAtS), patchChanges.find((c) => c.at_s === Number(dotAtS)));
      return;
    }
    const rect = patchLaneEl.getBoundingClientRect();
    const { sourceS } = computeSeekPosition(e.clientX, rect, view());
    openPatchPopup(sourceS, null);
  });
  // A click anywhere else on the page closes an open popup -- the same
  // "click elsewhere dismisses it" convention a native <select> itself
  // already uses for its own dropdown.
  document.addEventListener('pointerdown', closePatchPopup);

  // Shown centered in the lane only while it is EMPTY -- once a first patch
  // change exists, the dots plus the lane's own title tooltip already carry
  // the "click a dot to edit or delete it" half of the affordance, and a
  // permanent label would compete with real dots for the same 18px strip.
  // pointer-events:none so it never intercepts the click meant for the lane
  // (or a dot) underneath it.
  const PATCH_HINT_HTML = `<div data-patch-hint style="position:absolute;inset:0;display:flex;
    align-items:center;justify-content:center;pointer-events:none;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;
    font-size:10px;letter-spacing:.04em;color:var(--ink-2,#9CAAA4);opacity:.45">
    Click to add a GX-100 patch change</div>`;

  function renderPatchLane() {
    const v = view();
    const dots = patchChanges.map((c) => {
      const x = viewX(c.at_s, v);
      if (x < -6 || x > v.widthPx + 6) return ''; // off-screen at this zoom -- skip, don't clamp (timeline.js's own viewX trap)
      return `<div data-at-s="${c.at_s}" title="${escapeHtml(c.patch)} at ${c.at_s.toFixed(2)}s" style="position:absolute;left:${x}px;top:2px;
        width:10px;height:10px;border-radius:50%;background:var(--accent,#E0913F);cursor:pointer;transform:translateX(-50%)"></div>`;
    }).join('');
    patchLaneEl.innerHTML = dots + (patchChanges.length === 0 ? PATCH_HINT_HTML : '');
    if (popupEl) patchLaneEl.appendChild(popupEl); // survives the innerHTML rebuild above
  }

  // Everything that depends on `view()` (so, everything a zoom/pan change
  // or a section-boundary commit needs redrawn) except the inspector,
  // which shows a section's FIELD values and depends on none of it --
  // re-running it on every wheel notch would be wasted work and would blow
  // away in-progress focus/typing in one of its inputs for no reason.
  function repaintView() {
    renderBarRuler();
    renderWave();
    renderLanes();
    renderSelectionHighlight();
    renderWaveDrag();
    renderPlayhead();
    renderPatchLane();
  }

  function redrawAll() {
    repaintView();
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
    clearTimeout(nudgeTimer);
    if (playheadRafId !== null) cancelAnimationFrame(playheadRafId);
    for (const unsub of unsubs) unsub();
    detachCreateHandler?.();
    document.removeEventListener('pointerdown', closePatchPopup);
    if (engine) {
      try { engine.destroy(); } catch { /* already torn down, or never finished loading */ }
    }
  };
}
