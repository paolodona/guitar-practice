/**
 * screens/capture.js — Phase 1, H2; live capture added Phase 1.5, T2.
 *
 * Two ways to capture, shown together:
 *
 * 1. **Live capture** (T2, new): a real arm/level-meter/elapsed-timer/stop
 *    panel, wired to `POST /api/capture/start`, `GET /api/capture/status`
 *    (polled every 300ms while running) and `POST /api/capture/stop` —
 *    `capture_runner.py`'s background-thread wrapper around
 *    `capture.py`'s `capture()`. This is the capture-FIRST workflow
 *    (Phase 1.5's own "sixth thing"): record a whole set with no target
 *    song chosen up front, then split and name the pieces afterward.
 *    Once stopped, this screen shows the real, already-available count
 *    from `GET /api/capture/segments` — naming and adjusting those
 *    segments is section 3's own job, below.
 *    **Degrades to a plain note, no arm button at all**, when
 *    `GET /api/capture/status`'s `available` field is false (`doctor`'s
 *    own check, cheaply mirrored server-side via
 *    `importlib.util.find_spec` — see server.py's `_capture_status`) —
 *    `pyaudiowpatch` is missing, so there is nothing this panel could
 *    honestly offer beyond the terminal instructions below it.
 *
 * 2. **Terminal command per needs-audio song** (H2, unchanged): for the
 *    viewer's current setlist (app.js's `currentSetlist()`), every song
 *    still flagged `needs_audio` by `GET /api/setlist/<slug>` (F1) with
 *    the exact `woodshed capture "<title>"` command to bind it — the
 *    one-song-at-a-time path, still useful when a title is already known
 *    up front.
 *
 * 3. **Segment review** (Group U, U3): whenever `GET /api/capture/segments`
 *    is non-empty — right after mount, and again whenever a capture cycle
 *    finishes (`mountLiveCapture`'s `onStopped`) — `mountSegmentReview`
 *    below mounts U0's whole-pass waveform strip (draggable boundary
 *    handles calling `adjust_boundary`, a round merge button at each
 *    boundary calling `merge_segments`, a "+ Split at playhead" action
 *    calling `split_segment`) plus one row per still-pending segment, each
 *    with its own preview-play control and an inline title/artist/tuning
 *    form feeding `POST /api/capture/bind`/`discard`. The strip and the
 *    per-row previews share ONE `player.js` `RealtimeEngine` (`loop:
 *    false`, R1, `seek()`, P1) — the same "audition, no rep" mechanism
 *    already built for song.js, pointed at `/api/capture/raw-audio` (the
 *    whole pass) or `/api/capture/segment-audio/<i>` (one row) instead of
 *    a bound song's `/api/audio/<slug>`. Degrades to nothing when there
 *    are no pending segments, same "show only what's real" instinct as
 *    the rest of this file.
 *
 * mount(el, payload) / unmount — app.js's contract 1. `payload` is `{}`
 * this phase (no GET endpoint feeds this route). Fetches `/api/setlists`
 * and `/api/setlist/<current>` itself, the same two calls app.js's `#/`
 * loadPayload makes for the dashboard.
 *
 * @param {HTMLElement} el
 * @param {any} payload
 * @returns {() => void}
 */
import { currentSetlist, get, post } from '../app.js';
import { drawWave, SONG_WAVE_OPTS } from '../wave.js';
import { viewX, positionAt } from '../timeline.js';
import { createEngine } from '../player.js';

const POLL_MS = 300;

// Found live 2026-09-06 ("stuck at 'Stopping...' forever, no sign of
// progress"): a genuinely wedged capture thread (capture_runner.py's own
// force-close is the server-side fix) can still, in principle, outlast
// even that -- settleStop() below polled unconditionally until `running`
// read false, which is honest about the state but indistinguishable from
// this screen having silently broken if that never happens. Past this
// many ms of polling, stop telling the user nothing and say so plainly
// instead: comfortably longer than stop()'s own two ~2s joins server-side,
// so an ordinary stop (even the force-close path) never trips it.
const STOP_TIMEOUT_MS = 8000;

// tuning.KNOWN_TUNINGS (Python, src/woodshed/tuning.py) mirrored here --
// this file has no way to import a Python module, and there is no shared
// JS module for it yet. Same duplication (and the same reasoning)
// dashboard.js's own KNOWN_TUNINGS already accepts for its add-song form's
// tuning dropdown — see that file's comment.
const KNOWN_TUNINGS = [
  'E standard', 'Eb standard', 'D standard', 'C# standard', 'C standard',
  'B standard', 'Drop D', 'Drop C#',
];

/** `<option>`s for every fixed tuning choice, *selected* pre-selected when
 * it names one of them — the review row's tuning select starts on the
 * active setlist's own tuning (still an explicit, overridable value in the
 * POST body, never silently assumed). */
function tuningOptionsHtml(selected) {
  return KNOWN_TUNINGS.map((t) =>
    `<option value="${escapeHtml(t)}"${t === selected ? ' selected' : ''}>${escapeHtml(t)}</option>`
  ).join('');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function captureCommand(row) {
  const title = row.title.replace(/"/g, '\\"');
  return `woodshed capture "${title}"`;
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Mounts the live arm/level/elapsed/stop panel into *container*, polling
 * GET /api/capture/status while a capture is running. Returns a cleanup
 * function that stops the poll — called from capture.js's own unmount.
 *
 * *cancelled* is a `{current: boolean}` box the caller flips BEFORE this
 * async function's own first await settles, closing a real race: `mount()`
 * returns its unmount callback synchronously, but this function's own
 * cleanup closure does not exist until its first `await` resolves — a
 * near-instant navigate-away, in between those two moments, would
 * otherwise start a poll loop nothing could ever stop.
 *
 * *onStopped* (Group U, new) fires once a capture cycle actually finishes
 * (`renderStopped`, below) — `mount()`'s own segment-review panel (U3)
 * needs to know a fresh raw recording with pending segments may now exist,
 * and this is the one place that state transition happens.
 * @param {HTMLElement} container
 * @param {{current: boolean}} cancelled
 * @param {() => void} [onStopped]
 * @returns {Promise<() => void>}
 */
async function mountLiveCapture(container, cancelled, onStopped) {
  let timer = null;
  let stopped = true;

  function renderUnavailable() {
    container.innerHTML = `
      <div style="font-size:13px;color:var(--ink-3,#6A7873);line-height:1.5;max-width:360px">
        Live capture needs <code>pyaudiowpatch</code>, not installed on this machine —
        use one of the terminal commands below instead.
      </div>`;
  }

  function renderIdle(errorMessage) {
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;gap:10px">
        <div style="font-size:13px;color:var(--ink-2,#9CAAA4);text-align:center;max-width:340px">
          Records this machine's own output, a whole set at once — mute notifications first.
        </div>
        <button data-arm style="background:var(--accent,#E0913F);color:var(--ground,#0C1211);border:none;
                    border-radius:4px;padding:11px 28px;font-size:15px;font-weight:600;cursor:pointer">Arm</button>
        ${errorMessage ? `<div style="font-size:12px;color:var(--warn,#C9805E);text-align:center;max-width:340px">${escapeHtml(errorMessage)}</div>` : ''}
      </div>`;
    container.querySelector('[data-arm]').addEventListener('click', async () => {
      const armBtn = container.querySelector('[data-arm]');
      armBtn.disabled = true;
      try {
        await post('/api/capture/start', {});
        await tick();
      } catch (err) {
        renderIdle(err.message);
      }
    });
  }

  function renderRunning(runningStatus) {
    const pct = Math.min(100, Math.round(runningStatus.level * 100));
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;gap:12px">
        <div style="width:56px;height:56px;border-radius:50%;border:2px solid var(--warn,#C9805E);
                    display:flex;align-items:center;justify-content:center">
          <div style="width:18px;height:18px;border-radius:3px;background:var(--warn,#C9805E)"></div>
        </div>
        <div style="font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;font-size:12px;
                    letter-spacing:.08em;color:var(--warn,#C9805E)">&#9679; RECORDING</div>
        <div style="font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;font-size:30px;
                    font-weight:700;font-variant-numeric:tabular-nums">${formatElapsed(runningStatus.elapsed_s)}</div>
        <div style="width:220px;height:8px;border-radius:2px;background:var(--hairline,#1C2523);overflow:hidden">
          <div style="height:8px;background:var(--accent,#E0913F);width:${pct}%"></div>
        </div>
        ${runningStatus.overflowed ? '<div style="font-size:12px;color:var(--warn,#C9805E)">Overflow seen — a dropout may be in this recording</div>' : ''}
        <button data-stop style="background:var(--warn,#C9805E);color:var(--ground,#0C1211);border:none;
                    border-radius:4px;padding:10px 26px;font-size:15px;font-weight:600;cursor:pointer">Stop</button>
      </div>`;
    container.querySelector('[data-stop]').addEventListener('click', async () => {
      // Found live 2026-09-06 ("capture doesn't stop"): tick()'s own poll
      // loop kept running independently of this click -- ~300ms later it
      // would re-fetch status (still `running: true`, since the capture
      // thread hadn't noticed stop_event yet), call renderRunning() again,
      // and overwrite this very button with a FRESH, un-disabled one. To
      // an impatient click it looked exactly like Stop did nothing.
      // Cancelling the poll HERE, before the request even goes out, is
      // what actually fixes it -- disabling the button alone was never
      // enough once something else could still blow the whole view away.
      if (timer !== null) { clearTimeout(timer); timer = null; }
      renderStopping();
      const stopDeadline = Date.now() + STOP_TIMEOUT_MS;
      try {
        const result = await post('/api/capture/stop', {});
        await settleStop(result, stopDeadline);
      } catch (err) {
        await tick();
      }
    });
  }

  function renderStopping() {
    container.innerHTML = `
      <div style="font-size:13px;color:var(--ink-2,#9CAAA4);text-align:center;max-width:340px">
        Stopping…
      </div>`;
  }

  /** Past STOP_TIMEOUT_MS with no confirmed stop -- tell the truth (a real
   * device-level hang, not a UI freeze) instead of polling in silence
   * forever. The audio recorded so far is already safe on disk (capture()
   * writes it as it arrives, never buffered in memory) regardless of
   * whether the thread itself ever unwedges -- this screen just cannot
   * SEE that from here, so it says what it actually knows. */
  function renderStuck() {
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;max-width:360px;text-align:center">
        <div style="font-size:14px;color:var(--warn,#C9805E)">
          Still stopping after ${Math.round(STOP_TIMEOUT_MS / 1000)}s — the capture
          thread may be stuck.
        </div>
        <div style="font-size:12.5px;color:var(--ink-3,#6A7873);line-height:1.4">
          Whatever was already recorded is safe on disk. Restarting
          <code>woodshed serve</code> should clear it; reload this page afterwards.
        </div>
      </div>`;
  }

  /** *result* is either POST /api/capture/stop's own response, or a later
   * GET /api/capture/status poll -- both carry {running, segment_count}.
   * server.py's own stop() only waits BRIEFLY for the capture thread to
   * actually finish (found live 2026-09-06, same session: a real device's
   * chunk read can take a moment to notice stop_event, and blocking the
   * whole HTTP response on that made a slow stop look identical to a
   * broken one) -- `running: true` here means "signalled, not confirmed
   * yet", so this keeps polling status until it genuinely is false rather
   * than trusting one response. */
  async function settleStop(result, stopDeadline) {
    if (stopped) return; // unmounted mid-settle -- see mountLiveCapture's own cancelled/stopped doc
    if (result.running) {
      if (Date.now() >= stopDeadline) {
        renderStuck();
        return;
      }
      timer = setTimeout(async () => {
        timer = null;
        if (stopped) return;
        let status;
        try {
          status = await get('/api/capture/status');
        } catch {
          await tick();
          return;
        }
        await settleStop(status, stopDeadline);
      }, POLL_MS);
      return;
    }
    await renderStopped(result);
  }

  async function renderStopped(result) {
    let segmentCount = result.segment_count;
    try {
      const segments = await get('/api/capture/segments');
      segmentCount = segments.length;
    } catch {
      // best effort -- fall back to stop()'s own count, already real, just
      // possibly stale by a resolved segment or two
    }
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;max-width:360px">
        <div style="font-size:14px;color:var(--ink,#E8EEEB)">
          Stopped — ${segmentCount} segment${segmentCount === 1 ? '' : 's'} captured, pending review.
        </div>
        <div style="font-size:12.5px;color:var(--ink-3,#6A7873);line-height:1.4">
          Review, adjust and name them below.
        </div>
        <button data-again style="align-self:flex-start;background:var(--raised,#1B2422);
                    color:var(--ink-2,#9CAAA4);border:none;border-radius:4px;padding:8px 16px;
                    font-size:13px;cursor:pointer">Capture more</button>
      </div>`;
    container.querySelector('[data-again]').addEventListener('click', () => tick());
    onStopped?.();
  }

  async function tick() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    let status;
    try {
      status = await get('/api/capture/status');
    } catch (err) {
      if (stopped) return;
      container.innerHTML = `<div style="font-size:13px;color:var(--warn,#C9805E)">Couldn't check capture status: ${escapeHtml(err.message)}</div>`;
      return;
    }
    if (stopped) return;
    if (!status.available) {
      renderUnavailable();
      return;
    }
    if (status.running) {
      renderRunning(status);
      timer = setTimeout(tick, POLL_MS);
    } else {
      renderIdle();
    }
  }

  stopped = cancelled.current;
  if (!stopped) await tick();

  return () => {
    stopped = true;
    if (timer !== null) clearTimeout(timer);
  };
}

// ── Segment review (Phase 1.5, Group U, U3) ─────────────────────────────

function playIconSvg(playing) {
  return playing
    ? '<svg width="12" height="12" viewBox="0 0 14 16" aria-hidden="true"><rect x="1" y="1" width="4" height="14" fill="#9CAAA4"/><rect x="9" y="1" width="4" height="14" fill="#9CAAA4"/></svg>'
    : '<svg width="12" height="12" viewBox="0 0 22 22" aria-hidden="true"><path d="M7 5.2v11.6l10-5.8z" fill="#9CAAA4"/></svg>';
}

function mergeIconSvg() {
  return '<svg width="11" height="9" viewBox="0 0 11 9" aria-hidden="true"><path d="M1 4.5h9M1 4.5l2.5-2.5M1 4.5l2.5 2.5M10 4.5l-2.5-2.5M10 4.5l-2.5 2.5" fill="none" stroke="#9CAAA4" stroke-width="1.1" stroke-linecap="round"/></svg>';
}

/**
 * A shared boundary between two time-adjacent pending segments must move
 * via TWO `adjust_boundary` calls (the left entry's `end_frame`, the right
 * entry's `start_frame`) — `capture_session.adjust_boundary` refuses a
 * result that overlaps a neighbour's own STORED range, so moving only one
 * side first can ask it to overlap the other side's not-yet-moved edge.
 * Shrinking first always avoids that: moving the boundary right frees the
 * space by pulling the right entry's start forward past nothing (its
 * previous neighbour, the left entry, hasn't moved yet and is still
 * behind the new value); moving it left frees the space by pulling the
 * left entry's end back past nothing, symmetrically. Pure and exported
 * for its own unit test (web/tests/test_capture_review.mjs) — the subtle
 * half of `commitBoundaryDrag`, below.
 * @param {number} newS
 * @param {number} oldS
 * @returns {'left-first' | 'right-first'}
 */
export function boundaryMoveOrder(newS, oldS) {
  return newS >= oldS ? 'right-first' : 'left-first';
}

/**
 * A one-shot async action guard: `run(fn)` calls *fn* and returns its
 * result the first time; every call after that -- while that first call
 * is still in flight, or once it has settled either way -- returns `null`
 * without calling *fn* again. `reset()` un-latches it (a definitive
 * FAILURE should let the row be retried, not brick it permanently — only
 * a genuine double-click/double-success is what this guards against).
 *
 * This is the DOM-independent half of U3's "cannot add (or discard) the
 * same segment twice" contract: `buildRowCard` shares ONE guard between
 * its Discard and Add handlers (a row resolves to exactly one outcome, so
 * a click on either locks out both), and disabling the actual buttons —
 * the DOM-coupled half, exercised by the manual gate, not this — still
 * happens synchronously inside *fn*, before its first await, so an
 * impatient click during that same synchronous turn is what visibly greys
 * the controls out. Pulled into its own pure function so the guard LOGIC
 * itself has a plain unit test (web/tests/test_capture_review.mjs) rather
 * than only an in-browser one — this repo has no DOM/testing library to
 * simulate real click events with (test_seek.mjs/test_ended.mjs made the
 * same trade for RealtimeEngine).
 * @returns {{run: (fn: () => any) => any, reset: () => void}}
 */
export function onceGuard() {
  let used = false;
  return {
    run(fn) {
      if (used) return null;
      used = true;
      return fn();
    },
    reset() { used = false; },
  };
}

/**
 * Mounts Group U's segment-review UI (design/CaptureReview.dc.html) into
 * *container* — the whole-pass waveform strip (peaks from `GET /api/
 * capture/raw-peaks`, fetched ONCE per mount: the raw pass itself never
 * changes while its segments are still resolving, only the pending list
 * shrinks/splits), draggable boundary handles (`POST /api/capture/adjust`,
 * via `commitBoundaryDrag`), a round merge button at each boundary and a
 * "Merge at playhead" toolbar action (both `POST /api/capture/merge`), a
 * "+ Split at playhead" action (`POST /api/capture/split`), and a
 * scrubbable playhead reusing `player.js`'s `loop: false` engine (R1) and
 * its `seek()` (P1) — literally the same "audition, no rep" mechanism
 * already built, pointed at `/api/capture/raw-audio` (the whole pass) or
 * `/api/capture/segment-audio/<i>` (one row's own preview) instead of a
 * bound song's `/api/audio/<slug>`. One shared `RealtimeEngine` serves
 * both — only one of the strip or one row is ever "active" at a time, and
 * `loadSection`'s own hard-cut teardown (see player.js's module doc)
 * already makes switching between them safe.
 *
 * One row per still-pending segment (title/artist/tuning + Add-to-setlist/
 * Discard), sorted chronologically to match the strip's own left-to-right
 * order — NOT by `index`, which is a stable identity, not a display order
 * (a prior split can leave a high index chronologically in the middle of
 * the session; same reasoning `capture_session.adjust_boundary`'s own
 * neighbour lookup already uses).
 *
 * Degrades to clearing *container* when `GET /api/capture/segments` is
 * empty — the same "show only what's real" instinct H2's own screen
 * already follows.
 *
 * **Cannot add (or discard) the same segment twice**: `resolve()` already
 * refuses a non-pending index server-side (U1/U2) and a resolved segment
 * simply stops appearing in a fresh `GET /api/capture/segments` — nothing
 * this screen could send double-binds one. This function's own half of
 * that contract is disabling a row's Add/Discard controls the INSTANT
 * either is clicked, before the request round-trips (so an impatient
 * double-click can't even fire a second one), and showing a brief inline
 * "Added"/"Discarded" confirmation rather than letting the row silently
 * vanish on the next refresh — the specific ambiguity this session's own
 * hands-on review pass was asked to close off.
 *
 * *activeSetlistSlug* / *defaultTuning* come from whatever setlist this
 * Capture screen is already working within (mount()'s own dashboard
 * fetch): "Add to setlist" always targets that setlist when one exists,
 * and the tuning select starts on its tuning — still an explicit,
 * overridable value in the POST body, never silently assumed.
 * @param {HTMLElement} container
 * @param {string | null} activeSetlistSlug
 * @param {string} defaultTuning
 * @returns {Promise<() => void>} cleanup
 */
async function mountSegmentReview(container, activeSetlistSlug, defaultTuning) {
  let segments;
  try {
    segments = await get('/api/capture/segments');
  } catch {
    segments = [];
  }
  if (segments.length === 0) {
    container.innerHTML = '';
    return () => {};
  }

  let peaks = null;
  try {
    peaks = await get('/api/capture/raw-peaks');
  } catch {
    peaks = null; // 404 -- degrades to no ticks, per wave.js's own contract
  }
  const durationS = peaks ? peaks.duration_s
    : segments.reduce((m, s) => Math.max(m, s.end_frame / s.sample_rate), 0);

  let destroyed = false;

  // ---- the shared preview engine (R1/P1, reused exactly as song.js does) ----
  let engine = null;
  let engineReady = false;
  let engineInitPromise = null;
  function ensureEngine() {
    if (!engineInitPromise) {
      engineInitPromise = (async () => {
        const e = createEngine();
        e.addEventListener('ended', onPreviewEnded);
        e.addEventListener('error', (err) => console.error('capture.js: engine error', err.detail?.error));
        engine = e;
        engineReady = true;
      })().catch((err) => {
        console.warn(`capture.js: RealtimeEngine unavailable (${err && err.message}) — preview will not play audio.`);
      });
    }
    return engineInitPromise;
  }

  let activeKey = null; // null | 'strip' | `row-${index}`
  let stripPlayheadS = 0;
  let stripPlaying = false;
  let playheadRafId = null;
  let playheadLastTs = null;

  function stopPlayheadTick() {
    if (playheadRafId !== null) cancelAnimationFrame(playheadRafId);
    playheadRafId = null;
    playheadLastTs = null;
  }

  function playheadTick(ts) {
    if (playheadLastTs !== null) stripPlayheadS += (ts - playheadLastTs) / 1000;
    playheadLastTs = ts;
    if (stripPlayheadS >= durationS) {
      stripPlayheadS = durationS;
      renderStrip();
      return; // 'ended' stops everything else
    }
    renderStrip();
    playheadRafId = requestAnimationFrame(playheadTick);
  }

  function onPreviewEnded() {
    if (activeKey === 'strip') {
      stripPlaying = false;
      stripPlayheadS = durationS;
      stopPlayheadTick();
      renderStrip();
    } else if (typeof activeKey === 'string' && activeKey.startsWith('row-')) {
      setRowPlayIcon(activeKey.slice(4), false);
    }
    activeKey = null;
  }

  async function toggleStripPlay() {
    if (stripPlaying) {
      stripPlaying = false;
      if (engineReady && activeKey === 'strip') { try { engine.pause(); } catch { /* no node between loads */ } }
      stopPlayheadTick();
      renderStrip();
      return;
    }
    // Taking over from a playing ROW: loadSection's own hard-cut teardown
    // (player.js's module doc) already stops its audio, but nothing else
    // would reset that row's own icon back to "not playing" -- do it here,
    // symmetric to toggleRowPlay's own handling of the reverse direction.
    if (typeof activeKey === 'string' && activeKey.startsWith('row-')) setRowPlayIcon(activeKey.slice(4), false);
    if (stripPlayheadS >= durationS - 0.05) stripPlayheadS = 0; // replay from the top
    stripPlaying = true;
    renderStrip();
    await ensureEngine();
    if (destroyed || !engineReady || !stripPlaying) return;
    activeKey = 'strip';
    await engine.loadSection({
      sectionId: 'raw-pass',
      audioUrl: '/api/capture/raw-audio',
      startS: stripPlayheadS,
      endS: Number.MAX_SAFE_INTEGER, // clamped to the decoded length -- see player.js's loadSection
      preRollS: 0,
      loop: false,
    });
    if (destroyed || !stripPlaying) return;
    engine.play();
    playheadLastTs = null;
    playheadRafId = requestAnimationFrame(playheadTick);
  }

  function setRowPlayIcon(indexStr, playing) {
    const btn = container.querySelector(`[data-row-play="${indexStr}"]`);
    if (btn) btn.innerHTML = playIconSvg(playing);
  }

  async function toggleRowPlay(entry) {
    const key = `row-${entry.index}`;
    if (activeKey === key) {
      setRowPlayIcon(String(entry.index), false);
      activeKey = null;
      if (engineReady) { try { engine.pause(); } catch { /* mid-load */ } }
      return;
    }
    if (activeKey === 'strip') { stripPlaying = false; stopPlayheadTick(); renderStrip(); }
    else if (typeof activeKey === 'string' && activeKey.startsWith('row-')) setRowPlayIcon(activeKey.slice(4), false);
    activeKey = key;
    setRowPlayIcon(String(entry.index), true);
    await ensureEngine();
    if (destroyed || !engineReady || activeKey !== key) return;
    await engine.loadSection({
      sectionId: key,
      audioUrl: `/api/capture/segment-audio/${entry.index}`,
      startS: 0,
      endS: Number.MAX_SAFE_INTEGER,
      preRollS: 0,
      loop: false,
    });
    if (destroyed || activeKey !== key) return;
    engine.play();
  }

  // ---- backend calls ----
  async function callAdjust(index, patch) { return post('/api/capture/adjust', { index, ...patch }); }
  async function callMerge(firstIndex, secondIndex) {
    return post('/api/capture/merge', { first_index: firstIndex, second_index: secondIndex });
  }
  async function callSplit(index, atFrame) { return post('/api/capture/split', { index, at_frame: atFrame }); }

  /** Commits a boundary drag using `boundaryMoveOrder`'s own ordering. */
  async function commitBoundaryDrag(leftEntry, rightEntry, newBoundaryS, oldBoundaryS) {
    const atFrame = Math.round(newBoundaryS * leftEntry.sample_rate);
    const moveLeft = () => callAdjust(leftEntry.index, { end_frame: atFrame });
    const moveRight = () => callAdjust(rightEntry.index, { start_frame: atFrame });
    if (boundaryMoveOrder(newBoundaryS, oldBoundaryS) === 'right-first') {
      await moveRight(); await moveLeft();
    } else {
      await moveLeft(); await moveRight();
    }
  }

  // ---- layout ----
  container.innerHTML = `
    <div style="background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);border-radius:5px;
                padding:16px 20px;display:flex;flex-direction:column;gap:10px;margin-top:22px;max-width:900px">
      <div class="mono" style="font-size:12.5px;color:var(--ink-3,#6A7873);letter-spacing:.08em">
        ${segments.length} SEGMENT${segments.length === 1 ? '' : 'S'} &middot; ${formatElapsed(durationS)} PASS &middot; SPLIT ON THE GAPS, NOT YET NAMED
      </div>
      <div style="display:flex;align-items:center;gap:14px">
        <button data-strip-play style="width:30px;height:30px;border-radius:50%;border:1px solid var(--line,#26302E);
                    background:none;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0">${playIconSvg(false)}</button>
        <div data-strip-clock class="mono" style="font-size:14px;color:var(--ink-2,#9CAAA4);font-variant-numeric:tabular-nums"></div>
        <div style="margin-left:auto;display:flex;gap:10px">
          <button data-merge-playhead style="font-size:13.5px;color:var(--ink-2,#9CAAA4);border:1px solid var(--line,#26302E);
                      border-radius:4px;padding:7px 13px;background:none;cursor:pointer;white-space:nowrap">Merge at playhead</button>
          <button data-split-playhead style="font-size:13.5px;color:var(--accent,#E0913F);border:1px solid #8A5C29;
                      border-radius:4px;padding:7px 13px;background:none;cursor:pointer;white-space:nowrap">+ Split at playhead</button>
        </div>
      </div>
      <div data-wave-host style="position:relative;height:84px;margin-top:24px;background:var(--sunken,#0F1614);
                  border-radius:3px;cursor:pointer">
        <svg data-wave-svg preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none"></svg>
        <div data-overlay-host style="position:absolute;inset:0"></div>
        <div data-playhead style="position:absolute;top:0;bottom:0;width:2px;background:#F6C98A;transform:translateX(-50%);pointer-events:none;z-index:4">
          <div data-playhead-flag style="position:absolute;bottom:100%;left:50%;transform:translateX(-50%);margin-bottom:4px;
                      white-space:nowrap;background:#F6C98A;color:var(--ground,#0C1211);border-radius:3px;padding:2px 6px;
                      font-size:10.5px;font-weight:600"></div>
        </div>
      </div>
      <div class="mono" style="font-size:11px;color:var(--ink-4,#5B6A64)">
        DRAG A HANDLE TO MOVE A CUT &middot; CLICK THE WAVE TO SCRUB AND PREVIEW &middot; THE ROUND BUTTON MERGES THE TWO SEGMENTS EITHER SIDE OF IT</div>
    </div>
    <div data-rows style="display:flex;flex-direction:column;gap:12px;margin-top:14px;max-width:900px"></div>
    <div class="mono" style="font-size:11.5px;color:var(--ink-4,#5B6A64);line-height:1.6;padding-top:14px;max-width:900px">
      A ROW NEVER BINDS ITSELF. "ADD TO SETLIST" WRITES A NEW SONG FROM ITS TITLE/ARTIST/TUNING;
      "DISCARD" DROPS THE SEGMENT WITH NOTHING CREATED. THE RAW PASS IS KEPT UNTIL EVERY ROW RESOLVES.</div>`;

  const stripPlayBtn = container.querySelector('[data-strip-play]');
  const headerClock = container.querySelector('[data-strip-clock]');
  const mergeAtPlayheadBtn = container.querySelector('[data-merge-playhead]');
  const splitBtn = container.querySelector('[data-split-playhead]');
  const waveHost = container.querySelector('[data-wave-host]');
  const waveSvg = container.querySelector('[data-wave-svg]');
  const overlayHost = container.querySelector('[data-overlay-host]');
  const playheadEl = container.querySelector('[data-playhead]');
  const playheadFlag = container.querySelector('[data-playhead-flag]');
  const rowsHost = container.querySelector('[data-rows]');

  function view() {
    return { startS: 0, endS: durationS || 1, widthPx: waveHost.clientWidth || 1 };
  }

  /** Chronological order (by source-seconds start), NOT by `index` — see
   * this function's own module doc for why. */
  function sortedPending() {
    return [...segments].sort((a, b) => a.start_frame / a.sample_rate - b.start_frame / b.sample_rate);
  }

  function segmentContaining(s, ordered) {
    return ordered.find((e) => s > e.start_frame / e.sample_rate && s < e.end_frame / e.sample_rate) ?? null;
  }

  function makeHandle(left, right, boundaryS, v) {
    const h = document.createElement('div');
    h.style.cssText = `position:absolute;top:0;bottom:0;left:${(viewX(boundaryS, v) / v.widthPx) * 100}%;` +
      'transform:translateX(-50%);width:13px;background:#1B2422;border:1px solid #3A4844;border-radius:3px;' +
      'cursor:ew-resize;z-index:2';
    h.addEventListener('pointerdown', (downEvt) => {
      downEvt.stopPropagation();
      downEvt.preventDefault();
      const minS = left.start_frame / left.sample_rate + 0.02;
      const maxS = right.end_frame / right.sample_rate - 0.02;
      let liveS = boundaryS;
      const onMove = (moveEvt) => {
        const rect = waveHost.getBoundingClientRect();
        liveS = Math.min(maxS, Math.max(minS, positionAt(moveEvt.clientX - rect.left, v)));
        h.style.left = `${(viewX(liveS, v) / v.widthPx) * 100}%`;
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        commitBoundaryDrag(left, right, liveS, boundaryS).then(refreshSegments, (err) => {
          console.error('capture.js: boundary drag failed', err);
          refreshSegments();
        });
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    });
    return h;
  }

  function makeMergeButton(left, right, boundaryS, v) {
    const b = document.createElement('div');
    b.style.cssText = `position:absolute;top:-15px;left:${(viewX(boundaryS, v) / v.widthPx) * 100}%;` +
      'transform:translateX(-50%);width:24px;height:24px;border-radius:50%;background:#131B19;' +
      'border:1px solid #26302E;cursor:pointer;z-index:3;display:flex;align-items:center;justify-content:center';
    b.title = 'Merge these two segments';
    b.innerHTML = mergeIconSvg();
    b.addEventListener('pointerdown', (e) => e.stopPropagation());
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      callMerge(left.index, right.index).then(refreshSegments, (err) => {
        console.error('capture.js: merge failed', err);
      });
    });
    return b;
  }

  function renderStrip() {
    const v = view();
    drawWave(waveSvg, peaks, v, 0, SONG_WAVE_OPTS);
    overlayHost.innerHTML = '';
    const ordered = sortedPending();
    for (const entry of ordered) {
      const startS = entry.start_frame / entry.sample_rate;
      const endS = entry.end_frame / entry.sample_rate;
      const leftPx = viewX(startS, v);
      const rightPx = viewX(endS, v);
      const flagged = entry.overflowed;
      const seg = document.createElement('div');
      seg.style.cssText = `position:absolute;top:0;bottom:0;left:${(leftPx / v.widthPx) * 100}%;` +
        `width:${Math.max(0, (rightPx - leftPx) / v.widthPx) * 100}%;` +
        `background:${flagged ? 'rgba(201,128,94,.13)' : 'rgba(224,145,63,.10)'};` +
        `border-left:1px solid ${flagged ? '#C9805E' : '#E0913F'};border-right:1px solid ${flagged ? '#C9805E' : '#E0913F'}`;
      overlayHost.appendChild(seg);
    }
    for (let i = 0; i < ordered.length - 1; i++) {
      const left = ordered[i];
      const right = ordered[i + 1];
      const boundaryS = (left.end_frame / left.sample_rate + right.start_frame / right.sample_rate) / 2;
      overlayHost.appendChild(makeHandle(left, right, boundaryS, v));
      overlayHost.appendChild(makeMergeButton(left, right, boundaryS, v));
    }
    const phPct = v.widthPx ? (viewX(stripPlayheadS, v) / v.widthPx) * 100 : 0;
    playheadEl.style.left = `${phPct}%`;
    playheadFlag.textContent = formatElapsed(stripPlayheadS);
    headerClock.textContent = `${formatElapsed(stripPlayheadS)} / ${formatElapsed(durationS)}`;
    stripPlayBtn.innerHTML = playIconSvg(stripPlaying);

    const containing = segmentContaining(stripPlayheadS, ordered);
    splitBtn.style.opacity = containing ? '1' : '.4';
    splitBtn.style.pointerEvents = containing ? '' : 'none';
    mergeAtPlayheadBtn.style.opacity = ordered.length > 1 ? '1' : '.4';
    mergeAtPlayheadBtn.style.pointerEvents = ordered.length > 1 ? '' : 'none';
  }

  function buildRowCard(entry) {
    const card = document.createElement('div');
    card.style.cssText = 'background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);' +
      'border-radius:5px;padding:16px 20px 18px;display:flex;flex-direction:column;gap:14px' +
      (entry.overflowed ? ';border-color:#8A5C29' : '');
    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:14px">
        <button data-row-play="${entry.index}" style="width:34px;height:34px;border-radius:50%;border:1px solid var(--line,#26302E);
                    background:none;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0">${playIconSvg(false)}</button>
        <div class="mono" style="font-size:12px;color:var(--ink-4,#5B6A64);letter-spacing:.14em">SEGMENT ${String(entry.index).padStart(2, '0')}</div>
        <div class="mono" style="font-size:15px;color:var(--ink-2,#9CAAA4);font-variant-numeric:tabular-nums">${formatElapsed(entry.duration_s)}</div>
        ${entry.overflowed ? `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.12em;
                    text-transform:uppercase;padding:3px 8px;border-radius:3px;background:#2A1D17;color:#C9805E;
                    white-space:nowrap">overflow &middot; re-capture</div>` : ''}
        <div data-row-status style="margin-left:auto;font-size:14px"></div>
        <div data-row-discard style="font-size:14px;color:var(--ink-2,#9CAAA4);cursor:pointer;padding:9px 4px">Discard</div>
      </div>
      <div style="display:flex;gap:14px;align-items:flex-end">
        <div style="flex:1;display:flex;flex-direction:column;gap:6px">
          <div class="mono" style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--ink-3,#6A7873)">Title</div>
          <input data-row-title placeholder="Song title" style="background:var(--sunken,#0F1614);border:1px solid var(--line,#26302E);
                      border-radius:4px;padding:11px 14px;font-size:15px;color:var(--ink,#E8EEEB);font-family:inherit">
        </div>
        <div style="flex:1;display:flex;flex-direction:column;gap:6px">
          <div class="mono" style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--ink-3,#6A7873)">Artist</div>
          <input data-row-artist placeholder="Artist" style="background:var(--sunken,#0F1614);border:1px solid var(--line,#26302E);
                      border-radius:4px;padding:11px 14px;font-size:15px;color:var(--ink,#E8EEEB);font-family:inherit">
        </div>
        <div style="width:180px;display:flex;flex-direction:column;gap:6px">
          <div class="mono" style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--ink-3,#6A7873)">Tuning</div>
          <select data-row-tuning style="background:var(--sunken,#0F1614);border:1px solid var(--line,#26302E);
                      border-radius:4px;padding:11px 14px;font-size:15px;color:var(--ink,#E8EEEB);font-family:inherit">
            ${tuningOptionsHtml(defaultTuning)}
          </select>
        </div>
        <button data-row-add style="background:var(--accent,#E0913F);color:var(--ground,#0C1211);border:none;
                    border-radius:4px;padding:11px 22px;font-size:15px;font-weight:600;white-space:nowrap;cursor:pointer">
          Add to setlist</button>
      </div>`;

    card.querySelector(`[data-row-play="${entry.index}"]`).addEventListener('click', () => {
      toggleRowPlay(entry).catch((err) => console.error('capture.js: row preview failed', err));
    });

    const discardBtn = card.querySelector('[data-row-discard]');
    const addBtn = card.querySelector('[data-row-add]');
    const statusEl = card.querySelector('[data-row-status]');

    // U3's own contract: disable BOTH controls the instant either is
    // clicked, before the request round-trips -- an impatient double-click
    // can't even fire a second one -- and show a brief inline confirmation
    // rather than letting the row silently vanish on the next refresh.
    function lockRow() {
      discardBtn.style.pointerEvents = 'none';
      discardBtn.style.opacity = '.4';
      addBtn.disabled = true;
      addBtn.style.opacity = '.5';
      addBtn.style.cursor = 'default';
    }
    function unlockRow(guard, message) {
      guard.reset(); // a definitive failure is retryable, not a permanent brick
      discardBtn.style.pointerEvents = '';
      discardBtn.style.opacity = '';
      addBtn.disabled = false;
      addBtn.style.opacity = '';
      addBtn.style.cursor = 'pointer';
      statusEl.textContent = message;
      statusEl.style.color = 'var(--warn,#C9805E)';
    }
    function resolveRow(message, color) {
      statusEl.textContent = message;
      statusEl.style.color = color;
      discardBtn.remove();
      addBtn.remove();
      setTimeout(() => { if (!destroyed) refreshSegments(); }, 900);
    }

    // ONE guard shared between Discard and Add -- a row resolves to
    // exactly one outcome, so a click on either must lock out both (see
    // onceGuard's own doc for the full reasoning and why this is the
    // DOM-independent half of U3's double-click contract).
    const guard = onceGuard();

    discardBtn.addEventListener('click', () => {
      guard.run(async () => {
        lockRow();
        try {
          await post('/api/capture/discard', { index: entry.index });
        } catch (err) {
          unlockRow(guard, err.message);
          return;
        }
        resolveRow('Discarded', 'var(--ink-3,#6A7873)');
      });
    });

    addBtn.addEventListener('click', () => {
      const title = card.querySelector('[data-row-title]').value.trim();
      if (!title) {
        statusEl.textContent = 'A title is required';
        statusEl.style.color = 'var(--warn,#C9805E)';
        return; // no request sent -- the guard is never consumed for this
      }
      const artist = card.querySelector('[data-row-artist]').value.trim();
      const tuning = card.querySelector('[data-row-tuning]').value;
      guard.run(async () => {
        lockRow();
        try {
          await post('/api/capture/bind', {
            index: entry.index, mode: 'new', title, artist, tuning,
            ...(activeSetlistSlug ? { setlist: activeSetlistSlug } : {}),
          });
        } catch (err) {
          unlockRow(guard, err.message);
          return;
        }
        resolveRow('Added', 'var(--good,#5FA88F)');
      });
    });

    return card;
  }

  function renderRows() {
    rowsHost.innerHTML = '';
    for (const entry of sortedPending()) rowsHost.appendChild(buildRowCard(entry));
    // A refresh mid-preview (another row resolved, or a split/merge/adjust
    // landed) rebuilds every card fresh -- including the one still actively
    // playing, whose new card otherwise shows the default "not playing"
    // icon even though its audio never stopped. Re-sync it immediately
    // rather than leaving that lie on screen until the next toggle.
    if (typeof activeKey === 'string' && activeKey.startsWith('row-')) setRowPlayIcon(activeKey.slice(4), true);
  }

  async function refreshSegments() {
    if (destroyed) return;
    try {
      segments = await get('/api/capture/segments');
    } catch (err) {
      console.error('capture.js: refresh segments failed', err);
      return;
    }
    if (destroyed) return;
    if (segments.length === 0) {
      stopPlayheadTick();
      if (engine) { try { engine.destroy(); } catch { /* already gone */ } }
      container.innerHTML = '';
      destroyed = true;
      return;
    }
    renderStrip();
    renderRows();
  }

  stripPlayBtn.addEventListener('click', () => {
    toggleStripPlay().catch((err) => console.error('capture.js: strip play failed', err));
  });
  splitBtn.addEventListener('click', () => {
    const target = segmentContaining(stripPlayheadS, sortedPending());
    if (!target) return;
    const atFrame = Math.round(stripPlayheadS * target.sample_rate);
    callSplit(target.index, atFrame).then(refreshSegments, (err) => {
      console.error('capture.js: split failed', err);
    });
  });
  mergeAtPlayheadBtn.addEventListener('click', () => {
    const ordered = sortedPending();
    if (ordered.length < 2) return;
    let best = null;
    let bestDist = Infinity;
    for (let i = 0; i < ordered.length - 1; i++) {
      const left = ordered[i];
      const right = ordered[i + 1];
      const boundaryS = (left.end_frame / left.sample_rate + right.start_frame / right.sample_rate) / 2;
      const dist = Math.abs(boundaryS - stripPlayheadS);
      if (dist < bestDist) { bestDist = dist; best = [left, right]; }
    }
    if (best) {
      callMerge(best[0].index, best[1].index).then(refreshSegments, (err) => {
        console.error('capture.js: merge at playhead failed', err);
      });
    }
  });
  waveHost.addEventListener('pointerdown', (e) => {
    const rect = waveHost.getBoundingClientRect();
    const t = Math.min(durationS, Math.max(0, positionAt(e.clientX - rect.left, view())));
    stripPlayheadS = t;
    if (activeKey === 'strip' && engineReady) {
      try { engine.seek(t); } catch { /* not loaded yet -- nothing to seek */ }
    }
    renderStrip();
  });

  renderStrip();
  renderRows();

  return () => {
    destroyed = true;
    stopPlayheadTick();
    if (engine) { try { engine.destroy(); } catch { /* already torn down, or never finished loading */ } }
  };
}

export function mount(el, payload) {
  el.innerHTML = `
    <div style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
                font-family:Archivo,'Helvetica Neue',Arial,sans-serif;padding:34px 48px">
      <div style="display:flex;align-items:center;gap:16px">
        <div data-back title="Back to dashboard" style="cursor:pointer;color:var(--ink-3,#6A7873);font-size:20px;line-height:1;padding:2px 6px">&#8249;</div>
        <div style="font-size:26px;font-weight:600;letter-spacing:-.015em">Capture</div>
      </div>
      <div style="font-size:15px;color:var(--ink-2,#9CAAA4);margin-top:6px;max-width:640px;line-height:1.5">
        Record this machine's own output, notifications included — mute them first.
      </div>
      <div data-live style="background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);
                  border-radius:5px;padding:22px 26px;margin-top:22px;max-width:420px;
                  display:flex;flex-direction:column;align-items:center"></div>
      <div data-review></div>
      <div data-body style="margin-top:28px;color:var(--ink-3,#6A7873);font-size:14px"></div>
    </div>`;
  const body = el.querySelector('[data-body]');
  const liveEl = el.querySelector('[data-live]');
  const reviewEl = el.querySelector('[data-review]');
  el.querySelector('[data-back]').addEventListener('click', () => { location.hash = '#/'; });
  let liveCleanup = null;
  let reviewCleanup = null;
  const liveCancelled = { current: false };

  /** (Re-)checks GET /api/capture/segments and (re-)mounts U3's review
   * panel when non-empty -- called once at mount, and again whenever a
   * capture cycle finishes (mountLiveCapture's onStopped), since a fresh
   * raw recording with pending segments may now exist. */
  async function refreshReview(activeSetlistSlug, defaultTuning) {
    reviewCleanup?.();
    reviewCleanup = null;
    let segments;
    try {
      segments = await get('/api/capture/segments');
    } catch {
      segments = [];
    }
    if (segments.length === 0) {
      reviewEl.innerHTML = '';
      return;
    }
    reviewCleanup = await mountSegmentReview(reviewEl, activeSetlistSlug, defaultTuning);
  }

  (async () => {
    let setlists;
    try {
      setlists = await get('/api/setlists');
    } catch (err) {
      liveCleanup = await mountLiveCapture(liveEl, liveCancelled);
      body.textContent = `Couldn't reach the server: ${err.message}`;
      return;
    }

    let slug = setlists.length ? currentSetlist() : null;
    if (slug && !setlists.some((s) => s.slug === slug)) slug = null;
    if (!slug && setlists.length) slug = setlists[0].slug;

    // The active setlist's own tuning is the review panel's default tuning
    // choice (still explicit, overridable per row) -- best-effort: a
    // failed fetch here degrades to the first KNOWN_TUNINGS entry rather
    // than blocking the rest of this screen.
    let defaultTuning = KNOWN_TUNINGS[0];
    let dashboard = null;
    if (slug) {
      try {
        dashboard = await get(`/api/setlist/${encodeURIComponent(slug)}`);
        if (dashboard.tuning) defaultTuning = dashboard.tuning;
      } catch {
        dashboard = null;
      }
    }

    liveCleanup = await mountLiveCapture(liveEl, liveCancelled, () => {
      refreshReview(slug, defaultTuning).catch((err) => console.error('capture.js: review refresh failed', err));
    });
    await refreshReview(slug, defaultTuning);

    if (setlists.length === 0) {
      body.textContent = 'No setlists yet — nothing to check for missing audio against.';
      return;
    }
    if (!dashboard) {
      body.textContent = "Couldn't load the current setlist.";
      return;
    }
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

  return () => {
    liveCancelled.current = true;
    if (liveCleanup) liveCleanup();
    reviewCleanup?.();
  };
}
