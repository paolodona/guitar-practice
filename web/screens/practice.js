/**
 * screens/practice.js — the hero: speed, rep count, ring, section
 * waveform, foot strip (design/Main.dc.html, plus PracticeLeadIn.dc.html /
 * PracticeAdvance.dc.html for states B/C). D6's file.
 *
 * mount(el, payload) — app.js's contract 1. Route is
 * `#/practice/<slug>/<sectionId>`, backed by the SAME endpoint as
 * screens/song.js (GET /api/song/<slug> — there is no separate practice
 * payload; app.js's router merges `params: {slug, sectionId}` in). Full
 * shape, read from server.py's `_song` and manifest.py's models:
 *
 * {
 *   slug, title, artist, album,
 *   recording: { file, sha256, duration_s, tuning, spotify_id, source },
 *   tempo: { bpm, source, grid_offset_s, time_signature, confidence },
 *   practice: { start_speed, ladder_step, reps_to_advance, pre_roll_beats,
 *               pre_roll_every_pass, click, loop_crossfade_ms },
 *   shift: 0,                 // always 0 this phase — no setlist context
 *                             // reaches /api/song yet (CLAUDE.md's
 *                             // "transpose is per song" invariant). The
 *                             // stepper below still WORKS — it just keeps
 *                             // its result in memory only; see decision 2.
 *   peaks_url,                // GET this; a 404 means "not built yet"
 *   sections: Array<{ id, name, start_s, end_s, snapped, target_speed,
 *     ladder_step, reps_to_advance, notes, patch, counts_toward_readiness,
 *     lane, ancestors }>,
 *   params: { slug, sectionId },
 * }
 *
 * Four scope decisions, made explicit rather than silently assumed:
 *
 * 1. Ladder state (which rung, how many clean reps at it) starts FRESH
 *    every mount. There is no ledger-read endpoint in this phase —
 *    server.py's GET routes are exactly /api/song, /api/peaks, /api/audio
 *    (POST /api/rep only appends) — so there is no way to ask "how many
 *    clean reps already happened at this speed". Starting from
 *    `payload.practice.start_speed` with zero clean reps mirrors exactly
 *    what `woodshed.ladder.starting_speed` returns when handed an empty
 *    `clean_by_speed` history — the honest behaviour for "no data yet",
 *    not an invented one. `rungs()`/`nextRung()` below are a small,
 *    deliberate client-side port of ladder.py's pure percent-domain maths
 *    (mirrored, not imported — ladder.py is Python and this file cannot
 *    reach it), needed only because that read endpoint does not exist.
 *
 * 2. The transpose stepper is fully interactive and drives the engine live
 *    (`engine.setSemitones`), but the result is NOT persisted anywhere —
 *    there is no setlist-scoped endpoint to save it to this phase (see the
 *    payload note on `shift` above). This is the CLAUDE.md-mandated
 *    control (`−`/`+`, range ±6) working exactly as specified for the
 *    current session; only cross-session persistence is deferred, and
 *    that is a server-surface gap, not a shortcut taken here.
 *
 * 3. Playback PROGRESS (the one 0-1 fraction driving the ring, the
 *    waveform's clip-path and the playhead) has no authoritative source in
 *    player.js's current contract: RealtimeEngine exposes no position
 *    getter and no per-frame progress event, only the discrete 'pass'
 *    event at loop boundaries (confirmed against web/player.js as
 *    written — see the `tick`/`currentP` functions below). This
 *    screen therefore keeps its own local wall-clock estimate — reset to
 *    an exact 0 at every 'pass' (the one authoritative sync point) and
 *    interpolated between passes from `performance.now()` deltas. This is
 *    NOT re-deriving "was this a pass" (player.js/D4 still owns that
 *    exclusively; this screen never counts a rep from its own timer) — it
 *    is only smoothing the COSMETIC progress indicator between two
 *    'pass' events the engine already fired. Flagged as a real gap in
 *    this unit's report: a `getPosition()` or per-frame 'progress' event
 *    on RealtimeEngine would let a later pass replace this estimate with
 *    an exact one.
 *
 * 4. Eleven of the sixteen actions are wired here (play_pause, next_section,
 *    prev_section, speed_up, speed_down, retract_rep, confirm_clean,
 *    restart_section, transpose_up, transpose_down, help) — the ten the
 *    brief names as "at least", plus help (added live 2026-09-06: a
 *    shortcut reference overlay, built from ACTIONS + keys.js's KEY_MAP
 *    directly so it can't drift from what actually fires). It's the one
 *    deliberate exception to "six elements, nothing else" — hidden until
 *    invoked, so it doesn't compete with the hero for attention while
 *    playing. loop_toggle/metronome/fullscreen/nudge_start/nudge_end still
 *    have no represented control on this artboard and are left unsubscribed
 *    rather than given invented behaviour.
 */
import { get, post } from '../app.js';
import { drawWave, PRACTICE_WAVE_OPTS } from '../wave.js';
import { createEngine } from '../player.js';
import { ACTIONS, on, dispatch } from '../actions.js';
import { KEY_MAP } from '../keys.js';

// Human-readable form of the KeyboardEvent.key values KEY_MAP uses — for
// the help overlay only; keys.js itself never needs a display label.
const KEY_LABELS = {
  ' ': 'Space', ArrowRight: '→', ArrowLeft: '←', ArrowUp: '↑', ArrowDown: '↓',
};
function keyLabel(k) { return KEY_LABELS[k] ?? k.toUpperCase(); }

const STYLE_ID = 'practice-screen-style';

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .ws-practice .mono { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace }
    .ws-practice .num { font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;
      letter-spacing:-.035em;line-height:.84;font-weight:700 }
    .ws-practice .lbl { font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;
      letter-spacing:.2em;text-transform:uppercase }
    .ws-practice .stepper-btn { width:38px;height:38px;border-radius:3px;background:var(--raised,#1B2422);
      display:flex;align-items:center;justify-content:center;font-size:22px;color:var(--ink-2,#9CAAA4);
      border:none;cursor:pointer }
    .ws-practice .stepper-btn:hover { background:var(--accent-tint,#2A2118);color:var(--on-tint,#F0C48A) }
    .ws-practice .chip { flex:1;background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);
      border-radius:4px;padding:12px 16px;display:flex;flex-direction:column;gap:3px;cursor:pointer;
      text-align:left;font-family:inherit;color:inherit }
    .ws-practice .chip:hover { border-color:var(--accent-dim,#8A5C29) }
    .ws-practice .pill { width:64px;height:6px;border-radius:3px }
    .ws-practice .ws-song-link:hover { color:var(--ink,#E8EEEB);text-decoration:underline }
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

/** order() mirrored client-side (start_s, -duration) — display/navigation
 *  order only, matching sections.py's rule; never used to derive
 *  containment (that arrives pre-computed as lane/ancestors). */
function orderSections(sections) {
  return [...sections].sort((a, b) => a.start_s - b.start_s || (b.end_s - b.start_s) - (a.end_s - a.start_s));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** Slice a whole-song peaks payload down to [startS, endS] — wave.js's own
 *  doc is explicit that this is the caller's job, not its. */
function slicePeaksToWindow(peaksPayload, startS, endS, durationS) {
  if (!peaksPayload || !Array.isArray(peaksPayload.peaks) || !peaksPayload.peaks.length || !durationS) return null;
  const n = peaksPayload.peaks.length;
  const i0 = Math.max(0, Math.min(n, Math.floor((startS / durationS) * n)));
  const i1 = Math.max(i0, Math.min(n, Math.ceil((endS / durationS) * n)));
  return { level: peaksPayload.level, peaks: peaksPayload.peaks.slice(i0, i1) };
}

/**
 * A small, deliberate client-side mirror of woodshed.ladder's pure
 * percent-domain maths (rungs()/starting_speed()'s "next rung" logic) —
 * see module doc, decision 1, for why this isn't fetched from the server
 * instead. Kept intentionally tiny: nothing here holds a HISTORICAL count
 * (that would be ledger.clean_by_speed's job and this file never claims
 * to represent it), only "what is the next rung above `speed`".
 */
function rungs(cfg) {
  if (cfg.ladderStep <= 0) return [cfg.startSpeed];
  const result = [];
  let rung = cfg.startSpeed;
  while (rung < cfg.targetSpeed) {
    result.push(rung);
    rung += cfg.ladderStep;
  }
  result.push(cfg.targetSpeed);
  return result;
}
function nextRung(speed, cfg) {
  const all = rungs(cfg);
  const found = all.find((r) => r > speed + 1e-9);
  return found === undefined ? null : found;
}
// 110, not 100 — Paolo asked to be able to push a section faster than the
// recording on purpose (found live 2026-09-06). Ladder auto-advance is
// unaffected: nextRung()/rungs() below only ever return values up to
// cfg.targetSpeed, so pressing speed_up past 100% never counts toward or
// changes what "at target" means, and clean reps above 100% still just log
// at whatever speedPct actually was.
function clampSpeed(v) { return Math.min(110, Math.max(40, v)); }
function clampShift(v) { return Math.min(6, Math.max(-6, Math.round(v))); }

const RING_R = 136;
const RING_CIRC = 854; // 2*pi*136 rounded, per the design system's own arithmetic

/**
 * @param {HTMLElement} el
 * @param {any} payload - GET /api/song/<slug> body + {params:{slug,sectionId}}
 * @returns {() => void} unmount
 */
export function mount(el, payload) {
  ensureStyle();

  const section = payload.sections.find((s) => s.id === payload.params.sectionId);

  // Desktop-only (CLAUDE.md: "no mobile layout") but the artboard is a
  // fixed 1920x1080 design and the real viewport rarely matches that
  // exactly once browser chrome/taskbar are subtracted, so a naive
  // min-height:1080px overflowed and scrolled on any shorter window --
  // found live 2026-09-06. `stage` fills the actual viewport and clips;
  // `root` stays a fixed 1920x1080 box, scaled and centered inside it, so
  // every pixel value elsewhere in this file (the 236px hero numbers, the
  // ring, the gaps) stays exactly proportional to the artboard rather than
  // needing a rewrite into viewport units.
  const stage = document.createElement('div');
  stage.className = 'ws-practice-stage';
  stage.style.cssText = 'position:relative;width:100vw;height:100vh;overflow:hidden;background:var(--ground,#0C1211)';
  el.appendChild(stage);

  const root = document.createElement('div');
  root.className = 'ws-practice';
  root.style.cssText = 'width:1920px;height:1080px;background:var(--ground,#0C1211);position:absolute;top:50%;left:50%;' +
    "overflow:hidden;color:var(--ink,#E8EEEB);font-family:Archivo,'Helvetica Neue',Arial,sans-serif;box-sizing:border-box";
  stage.appendChild(root);

  function applyScale() {
    const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    root.style.transform = `translate(-50%,-50%) scale(${s})`;
  }
  applyScale();
  window.addEventListener('resize', applyScale);

  if (!section) {
    root.innerHTML = `<div style="padding:80px;font-size:24px;color:var(--warn,#C9805E)">
      No section ${escapeHtml(payload.params.sectionId ?? '')} on ${escapeHtml(payload.slug)}.</div>`;
    return function unmount() { window.removeEventListener('resize', applyScale); };
  }

  const cfg = {
    startSpeed: payload.practice.start_speed,
    ladderStep: section.ladder_step ?? payload.practice.ladder_step,
    repsToAdvance: section.reps_to_advance ?? payload.practice.reps_to_advance,
    targetSpeed: section.target_speed,
  };

  // ---- mutable session state (see module doc, decisions 1 and 2) ----
  let speedPct = clampSpeed(Math.min(cfg.startSpeed, cfg.targetSpeed));
  let shift = clampShift(payload.shift ?? 0);
  let repCount = 0;
  let cleanAtSpeed = 0;
  let pendingClean = false;
  let lastRepId = null;
  let lastRepWasClean = false;
  let advancing = false; // true only during the brief "ladder advanced" flourish window
  let advanceInfo = null; // {oldSpeed, earnedAt}
  let advanceTimer = null;
  let playing = false;
  let peaks = null;

  function preRollPlaybackSeconds() {
    return (payload.practice.pre_roll_beats * secPerBeat(payload.tempo)) / (speedPct / 100);
  }
  function loopDurationPlayback() {
    return (section.end_s - section.start_s) / (speedPct / 100);
  }

  // Cosmetic-only durations for the ring/waveform/playhead estimate (module
  // doc, decision 3), FROZEN at the start of each lap rather than
  // recomputed live from speedPct on every frame.
  //
  // FOUND LIVE 2026-09-05: engine.setSpeedPct() takes effect immediately
  // (correct -- this is the real-time "exploring" engine, ratio changes are
  // meant to be instant), but currentP()'s dur used to call
  // loopDurationPlayback() straight off the live speedPct while `elapsed`
  // keeps counting real wall-clock time since the last genuine 'pass'. A
  // mid-lap speed_up shrinks that recomputed dur immediately, so
  // `elapsed % dur` wraps -- the ring/waveform/playhead visually snap back
  // to 0 -- well before the real engine actually reaches the section end.
  // That reads as "it looped and didn't record a rep, then looped again a
  // few seconds later", but no pass was missed: onPass()/POST /api/rep are
  // driven only by the engine's genuine 'pass' event (worklet.js's
  // input-position-driven boundary, unaffected by ratio), which is why the
  // ledger stayed correct throughout. Freezing dur per-lap means a speed
  // change only reshapes the NEXT lap's cosmetic estimate, matching what
  // the engine itself does (the current lap keeps whatever timing its
  // already-fed audio implies).
  let cosmeticPreRoll = preRollPlaybackSeconds();
  let cosmeticLoopDur = loopDurationPlayback();
  function beginLap() {
    cosmeticPreRoll = preRollPlaybackSeconds();
    cosmeticLoopDur = loopDurationPlayback();
  }

  let elapsed = -cosmeticPreRoll;

  root.innerHTML = `
    <div data-main style="width:100%;height:100%;display:flex;flex-direction:column;padding:68px 80px 60px;box-sizing:border-box">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:48px">
        <div style="display:flex;flex-direction:column;gap:8px">
          <div class="lbl" data-eyebrow style="font-size:13px"></div>
          <a class="ws-song-link" href="#/song/${encodeURIComponent(payload.slug)}" style="font-size:23px;color:var(--ink-2,#9CAAA4);letter-spacing:.01em;text-decoration:none">${escapeHtml(payload.title)} &middot; ${escapeHtml(payload.artist)}</a>
          <div style="font-size:68px;font-weight:600;letter-spacing:-.025em;line-height:1.04;margin-top:2px">${escapeHtml(section.name)}</div>
          <div class="mono" style="font-size:17px;color:var(--ink-3,#6A7873);letter-spacing:.05em;margin-top:4px" data-breadcrumb></div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:12px;padding-top:6px">
          <div style="display:flex;gap:12px;align-items:center">
            <div class="lbl" style="font-size:12px">Shift</div>
            <div style="display:flex;align-items:center;gap:8px;border:1px solid var(--line,#26302E);border-radius:5px;padding:5px">
              <button class="stepper-btn" data-shift-minus>&minus;</button>
              <div class="mono num" data-shift-val style="font-size:28px;color:var(--accent,#E0913F);width:56px;text-align:center;font-weight:600"></div>
              <button class="stepper-btn" data-shift-plus>+</button>
            </div>
          </div>
          <div class="mono" style="font-size:14px;color:var(--ink-3,#6A7873);letter-spacing:.03em" data-tuning-caption></div>
        </div>
      </div>

      <div style="display:flex;align-items:flex-end;gap:110px;flex-grow:1;padding-top:44px">
        <div style="display:flex;flex-direction:column;gap:14px">
          <div class="lbl" style="font-size:13px">Speed</div>
          <div data-speed-cell></div>
          <div class="mono" data-speed-sub style="font-size:19px;color:var(--ink-2,#9CAAA4);letter-spacing:.02em"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:14px">
          <div class="lbl" style="font-size:13px">Reps</div>
          <div class="num" data-reps-val style="font-size:236px;color:var(--ink,#E8EEEB);font-weight:600"></div>
          <div class="mono" data-reps-sub style="font-size:19px;color:var(--ink-2,#9CAAA4);letter-spacing:.02em"></div>
        </div>
        <div style="margin-left:auto;display:flex;flex-direction:column;align-items:center;gap:16px">
          <svg width="300" height="300" viewBox="0 0 300 300" aria-label="Loop progress">
            <circle cx="150" cy="150" r="${RING_R}" fill="none" stroke="var(--hairline,#1C2523)" stroke-width="9"></circle>
            <circle data-ring-arc cx="150" cy="150" r="${RING_R}" fill="none" stroke="var(--accent,#E0913F)" stroke-width="9"
                    stroke-linecap="round" stroke-dasharray="${RING_CIRC}" transform="rotate(-90 150 150)"></circle>
            <text x="150" y="143" text-anchor="middle" fill="var(--ink-3,#6A7873)"
                  font-family="'IBM Plex Mono',monospace" font-size="15" letter-spacing="3.5">PASS</text>
            <text data-ring-pct x="150" y="186" text-anchor="middle" fill="var(--ink,#E8EEEB)"
                  font-family="Archivo,sans-serif" font-size="46" font-weight="600"></text>
          </svg>
        </div>
      </div>

      <div data-next-rung style="display:flex;align-items:center;gap:16px;padding:36px 0 30px"></div>

      <div style="display:flex;flex-direction:column;gap:9px">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <div class="mono" data-cap-start style="font-size:14px;color:var(--ink-3,#6A7873);letter-spacing:.06em"></div>
          <div class="mono" data-cap-mid style="font-size:14px;color:var(--ink-3,#6A7873);letter-spacing:.06em"></div>
          <div class="mono" data-cap-end style="font-size:14px;color:var(--ink-3,#6A7873);letter-spacing:.06em"></div>
        </div>
        <div data-wave-host style="position:relative;height:104px;background:var(--surface,#131B19);border-radius:3px;overflow:hidden;
                    background-image:repeating-linear-gradient(to right,var(--line,#26302E) 0 1px,transparent 1px 220px),
                                     repeating-linear-gradient(to right,var(--hairline,#1C2523) 0 1px,transparent 1px 55px)">
          <svg data-wave-svg preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:104px"></svg>
          <div data-playhead style="position:absolute;top:0;bottom:0;width:2px;background:var(--accent-hi,#F6C98A)"></div>
          <div data-playhead-cap style="position:absolute;top:0;width:0;height:0;margin-left:-6px;
                      border-left:6px solid transparent;border-right:6px solid transparent;border-top:8px solid var(--accent-hi,#F6C98A)"></div>
        </div>
      </div>

      <div data-foot style="display:flex;gap:12px;padding-top:30px"></div>
    </div>

    <div data-leadin-overlay style="position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px">
      <div class="lbl" style="font-size:18px;color:var(--accent-dim,#8A5C29);letter-spacing:.34em">Lead-in</div>
      <div class="num" data-leadin-count style="font-size:520px;color:var(--accent,#E0913F);line-height:.9"></div>
      <div data-leadin-pills style="display:flex;gap:14px;align-items:center;margin-top:14px"></div>
      <div class="mono" data-leadin-caption style="font-size:20px;color:var(--ink-3,#6A7873);letter-spacing:.06em;margin-top:22px"></div>
    </div>

    <div data-help-overlay style="position:absolute;inset:0;display:none;align-items:center;justify-content:center;
                background:rgba(12,18,17,.82)">
      <div style="background:var(--surface,#131B19);border:1px solid var(--line,#26302E);border-radius:8px;
                  padding:32px 40px;min-width:420px;max-width:560px;max-height:80vh;overflow:auto">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:18px">
          <div style="font-size:22px;font-weight:600">Shortcuts</div>
          <div class="mono lbl" style="font-size:11px;color:var(--ink-3,#6A7873)">? to close</div>
        </div>
        <div data-help-body style="display:flex;flex-direction:column;gap:2px"></div>
      </div>
    </div>
  `;

  const mainEl = root.querySelector('[data-main]');
  const eyebrowEl = root.querySelector('[data-eyebrow]');
  const breadcrumbEl = root.querySelector('[data-breadcrumb]');
  const shiftValEl = root.querySelector('[data-shift-val]');
  const tuningCaptionEl = root.querySelector('[data-tuning-caption]');
  const speedCellEl = root.querySelector('[data-speed-cell]');
  const speedSubEl = root.querySelector('[data-speed-sub]');
  const repsValEl = root.querySelector('[data-reps-val]');
  const repsSubEl = root.querySelector('[data-reps-sub]');
  const ringArcEl = root.querySelector('[data-ring-arc]');
  const ringPctEl = root.querySelector('[data-ring-pct]');
  const nextRungEl = root.querySelector('[data-next-rung]');
  const capStartEl = root.querySelector('[data-cap-start]');
  const capMidEl = root.querySelector('[data-cap-mid]');
  const capEndEl = root.querySelector('[data-cap-end]');
  const waveHost = root.querySelector('[data-wave-host]');
  const waveSvg = root.querySelector('[data-wave-svg]');
  const playheadEl = root.querySelector('[data-playhead]');
  const playheadCapEl = root.querySelector('[data-playhead-cap]');
  const footEl = root.querySelector('[data-foot]');
  const leadInOverlay = root.querySelector('[data-leadin-overlay]');
  const leadInCountEl = root.querySelector('[data-leadin-count]');
  const leadInPillsEl = root.querySelector('[data-leadin-pills]');
  const leadInCaptionEl = root.querySelector('[data-leadin-caption]');
  const helpOverlay = root.querySelector('[data-help-overlay]');
  const helpBodyEl = root.querySelector('[data-help-body]');

  // Reverse KEY_MAP once: action name -> every key that reaches it (usually
  // one, but the table doesn't promise that). Built from KEY_MAP/ACTIONS
  // directly so this can never drift from what actually fires -- no second
  // hand-typed shortcut list to fall out of sync with keys.js/actions.js.
  const keysForAction = {};
  for (const [key, name] of Object.entries(KEY_MAP)) {
    (keysForAction[name] ??= []).push(key);
  }
  function renderHelp() {
    helpBodyEl.innerHTML = Object.entries(ACTIONS).map(([name, spec]) => {
      const keys = (keysForAction[name] ?? []).map(keyLabel).join(' / ');
      const cc = spec.cc != null ? `CC ${spec.cc}` : '';
      return `
        <div style="display:flex;justify-content:space-between;gap:24px;padding:7px 0;border-bottom:1px solid var(--hairline,#1C2523)">
          <div style="font-size:14px;color:var(--ink,#E8EEEB)">${escapeHtml(spec.label ?? name)}</div>
          <div class="mono" style="font-size:13px;color:var(--ink-3,#6A7873);white-space:nowrap">${escapeHtml([keys, cc].filter(Boolean).join('  ·  ') || '—')}</div>
        </div>`;
    }).join('');
  }
  function toggleHelp() {
    const opening = helpOverlay.style.display !== 'flex';
    if (opening) renderHelp();
    helpOverlay.style.display = opening ? 'flex' : 'none';
  }
  helpOverlay.addEventListener('click', (e) => { if (e.target === helpOverlay) toggleHelp(); });

  // ---- foot strip: exactly the six CC actions, in ACTIONS' own table
  // order (never hand-reordered — see CLAUDE.md's "one action table"). ----
  const footActions = Object.entries(ACTIONS).filter(([, spec]) => spec.cc != null);
  footEl.innerHTML = footActions.map(([name, spec]) => `
    <button class="chip" data-chip="${name}">
      <div class="mono" style="font-size:12px;color:var(--accent-dim,#8A5C29);letter-spacing:.14em">CC ${spec.cc}</div>
      <div style="font-size:17px;font-weight:500">${escapeHtml(spec.label)}</div>
    </button>`).join('');
  footEl.querySelectorAll('[data-chip]').forEach((btn) => {
    btn.addEventListener('click', () => dispatch(btn.dataset.chip, 'ui'));
  });

  // ---- transpose stepper ----
  root.querySelector('[data-shift-minus]').addEventListener('click', () => dispatch('transpose_down', 'ui'));
  root.querySelector('[data-shift-plus]').addEventListener('click', () => dispatch('transpose_up', 'ui'));

  function tuningNote() {
    const rec = payload.recording.tuning;
    return shift === 0
      ? `RECORD IN ${rec.toUpperCase()} · PLAYING IN ${rec.toUpperCase()}`
      : `RECORD IN ${rec.toUpperCase()} · SHIFTED ${shift > 0 ? '+' : ''}${shift}`;
  }

  // ---- one canonical playback-progress fraction, per the design system's
  // "everything is driven by one 0-1 fraction" rule. See module doc,
  // decision 3, for why this is a local wall-clock estimate rather than
  // read from the engine. ----
  function currentP() {
    if (elapsed < 0) return 0;
    const dur = cosmeticLoopDur;
    if (dur <= 0) return 0;
    return Math.min(1, (elapsed % dur) / dur);
  }

  const durationS = payload.recording.duration_s;
  function view() {
    return { startS: section.start_s, endS: section.end_s, widthPx: waveHost.clientWidth || 1 };
  }

  function renderWave() {
    const windowed = slicePeaksToWindow(peaks, section.start_s, section.end_s, durationS);
    drawWave(waveSvg, windowed, view(), currentP(), PRACTICE_WAVE_OPTS);
  }

  /** Cheap, every-frame updates: ring, playhead, lead-in overlay. No DOM
   *  rebuild — direct attribute/style writes only. */
  function renderCheap() {
    const p = currentP();
    ringArcEl.setAttribute('stroke-dashoffset', String(RING_CIRC * (1 - p)));
    ringPctEl.textContent = `${Math.round(p * 100)}%`;
    playheadEl.style.left = `${p * 100}%`;
    playheadCapEl.style.left = `${p * 100}%`;

    // Eyebrow text tracks `elapsed`'s sign continuously (it flips the
    // instant lead-in ends, mid-frame) so it belongs here, not in the
    // discrete-event render below, even though it is otherwise a "static
    // until something happens" label.
    eyebrowEl.textContent = advancing ? 'Ladder advanced' : (elapsed < 0 ? 'Lead-in' : 'Practising');
    eyebrowEl.style.color = advancing ? 'var(--good,#5FA88F)' : 'var(--accent,#E0913F)';

    const inLeadIn = !advancing && elapsed < 0;
    mainEl.style.opacity = inLeadIn ? '.2' : '1';
    leadInOverlay.style.display = inLeadIn ? 'flex' : 'none';
    if (inLeadIn) {
      const totalSteps = Math.max(1, Math.round(payload.practice.pre_roll_beats));
      const perStep = cosmeticPreRoll / totalSteps;
      const remaining = Math.max(1, Math.min(totalSteps, Math.ceil(-elapsed / Math.max(perStep, 1e-6))));
      leadInCountEl.textContent = String(remaining);
      const doneSteps = totalSteps - remaining;
      leadInPillsEl.innerHTML = '';
      for (let i = 0; i < totalSteps; i++) {
        const pill = document.createElement('div');
        pill.className = 'pill';
        pill.style.background = i < doneSteps ? 'var(--accent-dim,#8A5C29)' : i === doneSteps ? 'var(--accent,#E0913F)' : 'var(--hairline,#1C2523)';
        leadInPillsEl.appendChild(pill);
      }
      leadInCaptionEl.textContent = `${totalSteps} beat${totalSteps === 1 ? '' : 's'}, then bar ${barBeatLabel(section.start_s, payload.tempo)}`;
    }
  }

  /** Everything that changes only on a discrete event (speed/shift/rep/
   *  phase change) — full innerHTML rebuild of the small handful of
   *  elements that carry it, cheap because it never runs per-frame. */
  function renderDiscrete() {
    const ancestorNote = section.ancestors && section.ancestors.length
      ? `INSIDE ${escapeHtml(payload.sections.find((s) => s.id === section.ancestors[section.ancestors.length - 1])?.name ?? '').toUpperCase()} · `
      : '';
    breadcrumbEl.textContent = `${ancestorNote}BARS ${barBeatLabel(section.start_s, payload.tempo)}–${barBeatLabel(section.end_s, payload.tempo)}`;

    shiftValEl.textContent = `${shift > 0 ? '+' : ''}${shift}`;
    shiftValEl.style.color = shift === 0 ? 'var(--good,#5FA88F)' : 'var(--accent,#E0913F)';
    tuningCaptionEl.textContent = tuningNote();

    const currentBpm = Math.round(payload.tempo.bpm * (speedPct / 100));
    speedSubEl.textContent = `${currentBpm} bpm · ${payload.tempo.bpm.toFixed(1)} at full speed`;

    if (advancing && advanceInfo) {
      speedCellEl.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:14px">
          <div class="mono num" style="font-size:15px;color:var(--ink-3,#6A7873);text-decoration:line-through;font-weight:400">${advanceInfo.oldSpeed}%</div>
          <div class="mono" style="font-size:15px;color:var(--good,#5FA88F);letter-spacing:.02em">+${(advanceInfo.newSpeed - advanceInfo.oldSpeed).toFixed(advanceInfo.newSpeed % 1 === 0 && advanceInfo.oldSpeed % 1 === 0 ? 0 : 1)}</div>
        </div>
        <div class="num" style="font-size:236px;color:var(--accent,#E0913F);text-shadow:0 0 90px rgba(224,145,63,.34)">${speedPct}%</div>`;
      repsValEl.textContent = '0';
      repsValEl.style.color = 'var(--recessive,#4C635C)';
      repsSubEl.textContent = 'counter reset at the new rung';
    } else {
      speedCellEl.innerHTML = `<div class="num" style="font-size:236px;color:var(--accent,#E0913F)">${speedPct}%</div>`;
      repsValEl.textContent = String(repCount);
      repsValEl.style.color = 'var(--ink,#E8EEEB)';
      const remaining = Math.max(0, cfg.repsToAdvance - cleanAtSpeed);
      repsSubEl.textContent = speedPct >= cfg.targetSpeed
        ? 'at target speed'
        : `${cleanAtSpeed} of ${cfg.repsToAdvance} clean to advance`;
    }

    const nxt = nextRung(speedPct, cfg);
    if (advancing && advanceInfo) {
      const t = advanceInfo.earnedAt;
      const hh = String(t.getHours()).padStart(2, '0');
      const mm = String(t.getMinutes()).padStart(2, '0');
      nextRungEl.innerHTML = `
        <div style="width:34px;height:1px;background:var(--good,#5FA88F)"></div>
        <div style="font-size:27px;color:var(--ink-2,#9CAAA4)">Three clean reps at
          <span class="mono" style="color:var(--ink,#E8EEEB)">${advanceInfo.oldSpeed}%</span> &middot; earned at ${hh}:${mm}
          ${nxt != null ? `&middot; <span>next rung</span> <span style="color:var(--accent,#E0913F);font-weight:600">${nxt}%</span>` : ''}
        </div>`;
    } else {
      const remaining = Math.max(0, cfg.repsToAdvance - cleanAtSpeed);
      const text = nxt != null
        ? `Next rung <span style="color:var(--accent,#E0913F);font-weight:600">${nxt}%</span> after ${remaining} more clean rep${remaining === 1 ? '' : 's'}`
        : 'Already at target speed';
      nextRungEl.innerHTML = `<div style="width:34px;height:1px;background:var(--accent-dim,#8A5C29)"></div>
        <div style="font-size:27px;color:var(--ink-2,#9CAAA4)">${text}</div>`;
    }

    const loopS = loopDurationPlayback();
    const barsInSection = Math.max(1, Math.round((section.end_s - section.start_s) / (secPerBeat(payload.tempo) * beatsPerBar(payload.tempo))));
    const preRollBeats = Math.round(payload.practice.pre_roll_beats);
    capStartEl.textContent = `BAR ${barBeatLabel(section.start_s, payload.tempo)}`;
    capMidEl.textContent = `${preRollBeats} BEAT${preRollBeats === 1 ? '' : 'S'} LEAD-IN · ${barsInSection} BARS · ${loopS.toFixed(1)} s AT ${speedPct}%`;
    capEndEl.textContent = `BAR ${barBeatLabel(section.end_s, payload.tempo)}`;
  }

  function renderAll() {
    renderDiscrete();
    renderCheap();
    renderWave();
  }

  // ---- the engine (this unit's half of D7) ----
  let engine = null;
  let engineReady = false;
  let engineInitPromise = null;

  /**
   * Create the RealtimeEngine and load this section -- but only once, and
   * only ever called from inside a user-gesture handler (play_pause,
   * below), never from mount() itself.
   *
   * FOUND LIVE 2026-09-06: this used to run in a floating async IIFE at
   * mount time. Browsers suspend a freshly-created AudioContext until it
   * is resumed from within a user gesture's call stack; loadSection()
   * awaits ctx.resume(), so calling it eagerly on mount left the context
   * permanently suspended -- no audio ever played and the worklet's
   * process() callback never ran, so 'pass' never fired. Meanwhile
   * tick()'s local wall-clock estimate (module doc, decision 3) kept
   * advancing regardless of any of that and made the ring/waveform/
   * playhead visually complete a lap with nothing behind it -- "it did
   * one rep but the counter still shows 0", because repCount only moves
   * on a genuine 'pass' from the engine, never from the local estimate.
   * @returns {Promise<void>}
   */
  function ensureEngine() {
    if (!engineInitPromise) {
      engineInitPromise = (async () => {
        const e = createEngine();
        e.addEventListener('pass', onPass);
        e.addEventListener('error', (err) => console.error('practice.js: engine error', err.detail?.error));
        await e.loadSection({
          sectionId: section.id,
          audioUrl: `/api/audio/${encodeURIComponent(payload.slug)}`,
          startS: section.start_s,
          endS: section.end_s,
          preRollS: payload.practice.pre_roll_beats * secPerBeat(payload.tempo),
        });
        e.setSpeedPct(speedPct);
        e.setSemitones(shift);
        engine = e;
        engineReady = true;
      })().catch((err) => {
        // player.js/D4's engine may simply not exist yet, or the browser
        // may refuse AudioWorklet -- degrade to local-only state rather
        // than fail the whole screen. See module doc.
        console.warn(`practice.js: RealtimeEngine unavailable (${err && err.message}) — controls update local state only, no audio.`);
      });
    }
    return engineInitPromise;
  }

  function onPass() {
    elapsed = 0;
    const clean = pendingClean;
    pendingClean = false;
    repCount += 1;

    post('/api/rep', {
      song: payload.slug,
      section: section.id,
      speed: speedPct,
      semitones: shift,
      pass: true,
      clean,
      loop_s: loopDurationPlayback(),
      source: 'ui',
    }).then((res) => {
      lastRepId = res.id;
      lastRepWasClean = clean;
    }).catch((err) => console.error('practice.js: POST /api/rep failed', err));

    if (clean) {
      cleanAtSpeed += 1;
      if (cleanAtSpeed >= cfg.repsToAdvance && speedPct < cfg.targetSpeed) {
        const nxt = nextRung(speedPct, cfg);
        if (nxt != null) {
          advanceInfo = { oldSpeed: speedPct, newSpeed: nxt, earnedAt: new Date() };
          speedPct = nxt;
          cleanAtSpeed = 0;
          if (engineReady) engine.setSpeedPct(speedPct);
          advancing = true;
          clearTimeout(advanceTimer);
          advanceTimer = setTimeout(() => {
            advancing = false;
            renderDiscrete();
          }, 4000);
        }
      }
    }
    // A fresh lap starts now (whatever speedPct is in effect this instant --
    // possibly just bumped by the ladder advance above) -- freeze the
    // cosmetic estimate for it. See beginLap()'s doc.
    beginLap();
    renderDiscrete();
  }

  (async () => {
    try {
      engine = createEngine();
      engine.addEventListener('pass', onPass);
      engine.addEventListener('error', (e) => console.error('practice.js: engine error', e.detail?.error));
      await engine.loadSection({
        sectionId: section.id,
        audioUrl: `/api/audio/${encodeURIComponent(payload.slug)}`,
        startS: section.start_s,
        endS: section.end_s,
        preRollS: payload.practice.pre_roll_beats * secPerBeat(payload.tempo),
      });
      engine.setSpeedPct(speedPct);
      engine.setSemitones(shift);
      engineReady = true;
    } catch (err) {
      // player.js/D4's engine may simply not exist yet at the time this
      // screen is exercised (parallel Phase 0 units) — degrade to local-
      // only state rather than fail the whole screen. See module doc.
      console.warn(`practice.js: RealtimeEngine unavailable (${err && err.message}) — controls update local state only, no audio.`);
    }
  })();

  // ---- local wall-clock progress estimate (module doc, decision 3) ----
  let rafId = null;
  let lastTs = null;
  let lastWaveDrawTs = 0;
  function tick(ts) {
    if (playing) {
      if (lastTs != null) elapsed += (ts - lastTs) / 1000;
      lastTs = ts;
    } else {
      lastTs = null;
    }
    renderCheap();
    if (ts - lastWaveDrawTs > 66) {
      lastWaveDrawTs = ts;
      renderWave();
    }
    rafId = requestAnimationFrame(tick);
  }
  rafId = requestAnimationFrame(tick);

  // ---- action wiring (module doc, decision 4) ----
  // Every trigger — the foot chips and transpose buttons above (source
  // 'ui'), and keyboard/MIDI once keys.js/midi.js are wired — calls
  // actions.dispatch(), which fans out to whatever this screen subscribed
  // via on() below. There is exactly one implementation per action name
  // (CLAUDE.md's "one action table shared by mouse, keyboard and MIDI,
  // never two") — this screen's own buttons are not a private shortcut
  // around that table.
  const unsubs = [];
  function bind(name, handler) { unsubs.push(on(name, handler)); }

  const handlers = {
    play_pause() {
      playing = !playing;
      renderCheap();
      if (playing) {
        // Called synchronously, same call stack as the click/keydown that
        // reached here -- ensureEngine()'s AudioContext gets created and
        // resumed as a direct consequence of this user gesture. Do not
        // await this before returning; play() fires once loadSection
        // resolves, whether that is on this press (first time) or already
        // settled (every press after).
        ensureEngine().then(() => { if (engineReady && playing) engine.play(); });
      } else if (engineReady) {
        engine.pause();
      }
    },
    next_section() { gotoSibling(1); },
    prev_section() { gotoSibling(-1); },
    speed_up() {
      speedPct = clampSpeed(speedPct + cfg.ladderStep);
      if (engineReady) engine.setSpeedPct(speedPct);
      renderDiscrete();
    },
    speed_down() {
      speedPct = clampSpeed(speedPct - cfg.ladderStep);
      if (engineReady) engine.setSpeedPct(speedPct);
      renderDiscrete();
    },
    retract_rep() {
      if (!lastRepId) return;
      post('/api/rep', {
        song: payload.slug, section: section.id, speed: speedPct, semitones: shift,
        pass: false, clean: false, loop_s: 0, source: 'ui',
        retracted: true, retracts: lastRepId,
      }).catch((err) => console.error('practice.js: POST /api/rep (retract) failed', err));
      repCount = Math.max(0, repCount - 1);
      if (lastRepWasClean) cleanAtSpeed = Math.max(0, cleanAtSpeed - 1);
      lastRepId = null;
      lastRepWasClean = false;
      renderDiscrete();
    },
    confirm_clean() {
      pendingClean = true; // armed for whichever lap is currently in flight
    },
    restart_section() {
      if (engineReady) engine.restartSection();
      beginLap();
      elapsed = -cosmeticPreRoll;
      advancing = false;
      clearTimeout(advanceTimer);
      renderDiscrete();
    },
    transpose_up() {
      shift = clampShift(shift + 1);
      if (engineReady) engine.setSemitones(shift);
      renderDiscrete();
    },
    transpose_down() {
      shift = clampShift(shift - 1);
      if (engineReady) engine.setSemitones(shift);
      renderDiscrete();
    },
    help() { toggleHelp(); },
  };
  for (const name of Object.keys(handlers)) bind(name, handlers[name]);

  function gotoSibling(dir) {
    const ordered = orderSections(payload.sections);
    const idx = ordered.findIndex((s) => s.id === section.id);
    if (idx === -1) return;
    const next = ordered[(idx + dir + ordered.length) % ordered.length];
    location.hash = `#/practice/${encodeURIComponent(payload.slug)}/${encodeURIComponent(next.id)}`;
  }

  renderAll();

  get(payload.peaks_url).then((data) => { peaks = data; renderWave(); }).catch(() => { peaks = null; renderWave(); });

  const resizeObserver = new ResizeObserver(() => renderWave());
  resizeObserver.observe(waveHost);

  return function unmount() {
    cancelAnimationFrame(rafId);
    clearTimeout(advanceTimer);
    resizeObserver.disconnect();
    window.removeEventListener('resize', applyScale);
    for (const unsub of unsubs) unsub();
    if (engine) {
      try { engine.destroy(); } catch (err) { /* already torn down or never finished loading */ }
    }
  };
}
