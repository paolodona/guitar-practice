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
 *    Once stopped, this screen shows only the real, already-available
 *    count from `GET /api/capture/segments` — naming and adjusting those
 *    segments is Group U3's own screen, not yet built, so nothing here
 *    pretends to do that job.
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

const POLL_MS = 300;

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
 * @param {HTMLElement} container
 * @param {{current: boolean}} cancelled
 * @returns {Promise<() => void>}
 */
async function mountLiveCapture(container, cancelled) {
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
      const stopBtn = container.querySelector('[data-stop]');
      stopBtn.disabled = true;
      try {
        const result = await post('/api/capture/stop', {});
        await renderStopped(result);
      } catch (err) {
        await tick();
      }
    });
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
          Naming and adjusting them is a later screen's job — this count is real,
          straight from <code>GET /api/capture/segments</code>, not a placeholder.
        </div>
        <button data-again style="align-self:flex-start;background:var(--raised,#1B2422);
                    color:var(--ink-2,#9CAAA4);border:none;border-radius:4px;padding:8px 16px;
                    font-size:13px;cursor:pointer">Capture more</button>
      </div>`;
    container.querySelector('[data-again]').addEventListener('click', () => tick());
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

export function mount(el, payload) {
  el.innerHTML = `
    <div style="min-height:100vh;background:var(--ground,#0C1211);color:var(--ink,#E8EEEB);
                font-family:Archivo,'Helvetica Neue',Arial,sans-serif;padding:34px 48px">
      <div style="font-size:26px;font-weight:600;letter-spacing:-.015em">Capture</div>
      <div style="font-size:15px;color:var(--ink-2,#9CAAA4);margin-top:6px;max-width:640px;line-height:1.5">
        Record this machine's own output, notifications included — mute them first.
      </div>
      <div data-live style="background:var(--surface,#131B19);border:1px solid var(--hairline,#1C2523);
                  border-radius:5px;padding:22px 26px;margin-top:22px;max-width:420px;
                  display:flex;flex-direction:column;align-items:center"></div>
      <div data-body style="margin-top:28px;color:var(--ink-3,#6A7873);font-size:14px"></div>
    </div>`;
  const body = el.querySelector('[data-body]');
  const liveEl = el.querySelector('[data-live]');
  let liveCleanup = null;
  const liveCancelled = { current: false };

  (async () => {
    liveCleanup = await mountLiveCapture(liveEl, liveCancelled);

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

  return () => {
    liveCancelled.current = true;
    if (liveCleanup) liveCleanup();
  };
}
