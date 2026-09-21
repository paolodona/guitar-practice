# Implementation Plan: Instant playback on the practice screen while the cached render builds
Plan: 003 | Name: instant-practice-playback | Created: 2026-09-21 | Status: IMPLEMENTED (Phase 5 manual verification pending) | GitRef: c6f9261

## Related Files
- **Prompt**: `.agent_session/003_instant-practice-playback_prompt.md` - Original mission
- **Context**: `.agent_session/003_instant-practice-playback_context.md` - Research findings

---

## Overview

The practice screen (`screens/practice.js`) blocks audible playback whenever the
requested `(section, speed, semitones, source)` render isn't already cached, because
`BufferEngine.loadSection()` polls the server until it exists. `screens/song.js` proves
instant playback at any speed is possible via `RealtimeEngine` alone. This plan adds a
`HybridEngine` orchestration layer in `web/player.js` that plays instantly on
`RealtimeEngine` whenever a render is cold, and hard-cuts to `BufferEngine`'s
sample-exact native loop at the next loop boundary once the render lands — for the
initial load and for every mid-session change that can land on an uncached combo.

## Affected Components & Reference Material
| Component | Reference | Notes |
|---|---|---|
| `web/player.js` | `docs/03-audio-engine.md` ("So: two engines, and the seam decides which") | Constrains: never `playbackRate` a stretched buffer (`tests/test_web_lint.py` enforces this by grep); the pass-detection contract (exactly one piece of code decides a pass); two-clock discipline (source vs. playback seconds). |
| `web/screens/practice.js` | `player.js`'s module doc (D4/D6 split) | practice.js is a `'pass'` **listener only** — never re-derives a pass; UI must reflect `engineKind` as reported, not assume it's fixed for the session. |

## Current State → Desired End State
**Current**: `ensureEngine()` tries `BufferEngine`, and only falls back to
`RealtimeEngine` if `loadSection()` throws outright (no `rubberband` binary). A cold
cache (202) makes `loadSection()` block silently until the render exists.
**Desired**: A cold cache never blocks sound — `RealtimeEngine` covers instantly and a
hard-cut swap to `BufferEngine` happens at the next loop boundary once the render is
ready, for the initial load and every later uncached press.
**Key Discoveries**: see `_context.md`'s "Key Discoveries" — in particular that
`BufferEngine._fetchRender` already fires `'rendering'` with a stage the instant a fetch
first comes back 202, which is the one signal this whole design needs and doesn't have to
invent.

## What We're NOT Doing
- No server-side (Python) changes — `GET /api/render/...`'s 200/202 contract is
  untouched.
- No crossfade between `RealtimeEngine` and `BufferEngine` — hard cut only, both
  directions (Paolo's explicit choice).
- No change to `screens/song.js` — it already only uses `RealtimeEngine` and has no
  cache-miss problem.
- No change to the existing cached-rung crossfaded swap (`_scheduleSwap`/`_adoptSwap`) —
  it keeps working exactly as today whenever `'rendering'` never fires.

## Code Placement
| Component | Location | Justification |
|---|---|---|
| `HybridEngine` class, `BufferEngine.playFromLoopStart()`, `createEngine('hybrid')` | `web/player.js` | Audio engine mechanics and the pass-detection contract live here exclusively (module doc); `practice.js` is a listener only. |
| Engine-kind wiring, `applyMetronome()` fix | `web/screens/practice.js` | UI reaction to engine state, same file that already owns `engineKind`/`statusBarState`/`metronomeControl` wiring. |
| Tests | `web/tests/test_hybrid_engine.mjs` (new), `web/tests/test_buffer_engine.mjs` (add a case), `web/tests/test_practice_speed.mjs` or a small new static-inspection test | Matches this repo's existing per-concern test file layout. |
| Docs | `docs/03-audio-engine.md` | Source of truth for engine behavior; a change here must be reflected there per CLAUDE.md. |

## Assumptions Requiring Verification
| Assumption | Verification Method | Owner |
|---|---|---|
| `RealtimeEngine.loadSection()` is fast enough in practice to feel "instant" for the fallback window | Manual verification (Phase 5) — this is exactly what `song.js` already demonstrates, but confirm on a real section | Phase 5 |
| The `applyMetronome()` stop/resume fix doesn't regress the existing metronome test suite | `uv run pytest` (runs `test_metronome*.mjs`) | Phase 3 |

---

## Phase 1: `BufferEngine.playFromLoopStart()`
**Goal**: Add the one small, isolated primitive the hard-cut swap needs — starting a
decoded-but-idle `BufferEngine` exactly at its own `loopStart`, freshly qualified.

**Changes**:
- `web/player.js`: add `BufferEngine.prototype.playFromLoopStart()`:
  ```js
  playFromLoopStart() {
    this._position = this._clock().loopStart;
    this._qualified = true;
    this.play();
  }
  ```
- `web/tests/test_buffer_engine.mjs`: add a case asserting it sets `_position` to
  `clock.loopStart` (not 0) and results in a playing, freshly-qualified engine (next
  natural wrap fires `'pass'`).

**Success Criteria**:
- [ ] Automated: `uv run pytest` (runs `test_web_lint.py`, which runs every
      `web/tests/test_*.mjs` via `node`, including the updated `test_buffer_engine.mjs`)

## Phase 2: `HybridEngine` + `createEngine('hybrid')`
**Goal**: The full orchestration layer — cache-hit/cache-miss race at load, the
mid-session drop-to-realtime, the hard-cut swap-back, `setSpeedPct`/`setSemitones`
delegation, and error-forwarding rules — as specified in the plan's design (see
`_context.md` and the design notes below), fully covered by tests before `practice.js`
depends on any of it.

**Design** (see prompt/context files for the full reasoning — summarized here):
- Internal state: `_buffer` (eager), `_realtime` (lazy), `_active`
  (`'buffer'|'realtime'|null`), `_section`, `_pendingBufferReady`, `_bufferUsable`.
- `loadSection(section)`: races `_buffer.loadSection()` against `_buffer`'s own
  `'rendering'` event. Cache hit → `_active='buffer'`, `_realtime` never created. Cache
  miss → lazily create + load `_realtime`, `_active='realtime'`, fire `'kind'`;
  `loadSection()` resolves here; the still-building `bufferPromise` is awaited in the
  background and sets `_pendingBufferReady=true` on success (never swaps immediately),
  or `_bufferUsable=false` on failure (permanent realtime fallback for this section).
- Hard-cut swap (realtime→buffer): one `'pass'` listener on `_realtime`, attached once
  when it's first created — forwards `'pass'` (the lap that just finished still counts),
  then if `_pendingBufferReady && _active==='realtime'`, calls `_swapToBuffer()`: pause
  `_realtime`, `_buffer.playFromLoopStart()` if playing, `_active='buffer'`, fire
  `'kind'`.
- Mid-session drop (buffer→realtime): one `'rendering'` listener on `_buffer`, always
  forwards the event outward; if `stage` is set and `_active==='buffer'`, calls
  `_dropToRealtime()`: capture `sourceSeconds = _buffer.sourcePosition()` before
  touching anything, pause `_buffer`, lazily create/load `_realtime`,
  `_realtime.seek(sourceSeconds)`, `_realtime.play()` if it was playing,
  `_active='realtime'`, fire `'kind'`. Immediate/mid-phrase — not deferred to the old
  rung's own loop end.
- `setSpeedPct`/`setSemitones`: always delegate target/fetch bookkeeping to
  `_buffer.setSpeedPct`/`setSemitones`; additionally, if already `_active==='realtime'`,
  also apply immediately to `_realtime` and re-arm `_pendingBufferReady` only once the
  matching `bufferPromise` resolves (guards a stale resolve from a superseded press).
- Error forwarding: `_buffer`'s `'error'` forwarded as Hybrid's own only when
  `_active==='buffer'` at the time; otherwise swallowed to `console.warn` +
  `_bufferUsable=false`. `_realtime`'s `'error'` always forwarded.
- `createEngine(audioContext, {kind})` gains `kind:'hybrid'` → `new
  HybridEngine(audioContext)`.

**Changes**:
- `web/player.js`: add `HybridEngine` class, extend `createEngine`.
- `web/tests/test_hybrid_engine.mjs` (new): the 7 orchestration cases from the approved
  plan (cache hit stays buffer-only; cache miss falls back then hard-cuts in at
  `loopStart`; exactly one `'pass'` for the lap that finishes right before the swap;
  cached-rung `setSpeedPct` while already on buffer is unchanged/regression-guarded;
  uncached-rung `setSpeedPct` while playing on buffer drops to realtime immediately then
  swaps back at the new target; total buffer failure stays on realtime permanently;
  `destroy()` tears down both sub-engines and is double-call-safe).

**Success Criteria**:
- [ ] Automated: `uv run pytest` (includes `test_hybrid_engine.mjs` and the full existing
      `web/tests/` suite — regression guard for `test_buffer_engine.mjs`/`test_seek.mjs`/
      `test_ended.mjs`/`test_engine_error_recovery.mjs`/`test_truncated_render.mjs`)

## Phase 3: `web/screens/practice.js` wiring
**Goal**: Point the practice screen at `HybridEngine`, react to its `'kind'` changes, and
fix the `applyMetronome()` bug the new dynamic engine kind exposes.

**Changes**:
- `web/screens/practice.js`:
  - `ensureEngine()`: replace the try-`startEngine('buffer')`-catch-`startEngine('realtime')`
    pair with a single `engine = await startEngine('hybrid'); engineKind = engine.kind;`.
  - `startEngine()`: add an `e.addEventListener('kind', (event) => { engineKind =
    event.detail.kind; renderEngineKind(); applyMetronome(); renderStatusBar(); });`
    alongside the existing `'pass'`/`'rendering'`/`'rung'`/`'error'` listeners.
  - `applyMetronome()`: add the missing `metronome.stop()` when transitioning to
    `'no-cache'` (currently absent — unreachable before this change, reachable now), and
    auto-resume (`metronomeState = 'on'`) when called with `metronomeState==='no-cache'`
    and `engineKind==='buffer'` again.
- Tests: a static-inspection addition (in `web/tests/test_practice_speed.mjs` or a small
  new file alongside it) asserting `ensureEngine`'s source contains `startEngine('hybrid')`
  and an `addEventListener('kind'`, and no longer contains the old two-branch
  try/catch shape.

**Success Criteria**:
- [ ] Automated: `uv run pytest` (full suite, including the updated/new static-inspection
      test and the existing `test_metronome_control.mjs`/`test_metronome.mjs`)

## Phase 4: Docs
**Goal**: `docs/03-audio-engine.md` reflects the new behavior — CLAUDE.md requires
`docs/03-audio-engine.md` to be read before touching anything that makes sound, so it
must stay accurate.

**Changes**:
- `docs/03-audio-engine.md`: one new paragraph under "So: two engines, and the seam
  decides which," describing the cold-cache instant-fallback and hard-cut swap-back
  (`HybridEngine`), matching the file's existing tone. No new measurements required —
  the render pipeline itself is unchanged.

**Success Criteria**:
- [ ] Manual: read the new paragraph in context for consistency with the rest of the file.

## Phase 5: Manual verification
**Goal**: Confirm end-to-end behavior a unit test can't reach (no real `AudioContext`/
browser in this repo's test suite).

**Success Criteria** (all manual):
- [ ] Pick a section/speed never rendered before (or delete its cached FLAC), load
      `#/practice/<slug>/<section>`, confirm sound starts immediately with no silent gap,
      status bar shows "Rendering…".
- [ ] Confirm the loop seam becomes sample-exact (no more audible tick at the loop point)
      once the render finishes and the current lap ends.
- [ ] Press "faster" to a never-visited rung mid-lap; confirm the speed change is audible
      immediately (not deferred to the old rung's loop end), and later hard-cuts to the
      cached render once ready.
- [ ] Metronome on, buffer active; trigger an uncached rung change; confirm it silently
      stops during the realtime excursion and silently resumes once swapped back.
- [ ] Do several loops spanning a live cache build + swap; check `practice/reps.jsonl`
      has exactly one line per completed lap (none missing, none doubled), with
      `speed`/`semitones` matching what was actually audible at each pass.
- [ ] `uv run pytest` green end to end.

## Testing & Safety
- **Unit**: `web/tests/test_hybrid_engine.mjs` (new), `test_buffer_engine.mjs` (extended),
  a static-inspection addition for `practice.js`'s wiring — all run via
  `tests/test_web_lint.py::test_web_node_tests_pass` under `uv run pytest`.
- **Integration**: manual only (Phase 5) — this repo's test suite has no real
  `AudioContext`/browser; do NOT attempt to spin one up for this plan.
- **Risk**: contained to `web/player.js` and `web/screens/practice.js`. The existing
  cached-rung crossfaded swap path is structurally untouched (only reachable when
  `'rendering'` never fires), which is the main regression risk to watch for in Phase 2's
  test suite run.
