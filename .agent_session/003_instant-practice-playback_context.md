# Session Context: Instant playback on the practice screen while the cached render builds
Plan: 003 | Name: instant-practice-playback | Updated: 2026-09-21 | GitRef: c6f9261

## Related Files
- **Prompt**: `.agent_session/003_instant-practice-playback_prompt.md` - Original mission
- **Plan**: `.agent_session/003_instant-practice-playback_plan.md` - Implementation steps

---

## Task Summary
- **Requirement**: `screens/practice.js` blocks audible playback until a cold cache
  render finishes building; `screens/song.js` proves instant playback via
  `RealtimeEngine` alone is possible at any speed. Add a fallback + hard-cut swap so the
  practice screen never sits silent.
- **Scope**: `web/player.js` (new `HybridEngine` orchestration class,
  `BufferEngine.playFromLoopStart()`, `createEngine('hybrid')`),
  `web/screens/practice.js` (wire `ensureEngine`/`startEngine` to the new engine kind,
  listen for a new `'kind'` event, fix a latent `applyMetronome()` bug it exposes), and
  their tests. **Out of scope**: server-side Python (the 200/202 render contract is
  unchanged), `screens/song.js` (already realtime-only, no cache-miss problem).

## Key Discoveries
- `web/player.js` (~1653 lines total). `BufferEngine._fetchRender` fires `'rendering'`
  with a non-null `stage` the moment a fetch first comes back 202 — *before* any poll
  delay; a cache hit never fires `'rendering'` at all. This is the one signal the whole
  design hangs off.
- `BufferEngine.sourcePosition()` converts current playback into absolute source
  seconds — the handoff value for `RealtimeEngine.seek()`.
- `RealtimeEngine` already fires `'pass'` at its own natural loop wraps (the worklet's
  `'boundary'` message) — the hard-cut swap's trigger point.
- `BufferEngine._scheduleSwap`/`_adoptSwap` (crossfaded, cached-rung-to-cached-rung swap)
  stays completely untouched — only activates when `'rendering'` never fires.
- `createEngine(audioContext, {kind='realtime'})` (`player.js` end) is the one factory
  both screens use; `song.js` always requests the (default) `'realtime'` kind and is
  untouched by this work.
- `screens/practice.js`'s `ensureEngine()`/`startEngine()` (around line 1563-1661) is the
  one call site that currently does try-`startEngine('buffer')`-catch-`startEngine('realtime')`
  — this collapses to one `startEngine('hybrid')` call.
- `applyMetronome()` (`screens/practice.js` ~949-962) has a **latent bug** the new
  dynamic `engineKind` exposes: called with `metronomeState === 'on'` and
  `engineKind === 'realtime'`, it sets `metronomeState = 'no-cache'` but never calls
  `metronome.stop()` — unreachable today (the toggle is disabled while realtime) but very
  reachable once a session swaps engines mid-lap. Fix included in scope: add the missing
  `stop()`, and auto-resume (`'no-cache' → 'on'`) once back on `'buffer'`.
- Test infra: no npm/package.json; `tests/test_web_lint.py::test_web_node_tests_pass`
  runs every `web/tests/test_*.mjs` via `node` under `uv run pytest`. Established mocking
  idioms to reuse: `FakeContext`/`FakeSource`/`FakeGain`/`FakeParam` +
  queued/gated-`fetch` stub for `BufferEngine` (`test_buffer_engine.mjs`,
  `test_engine_error_recovery.mjs`, `test_truncated_render.mjs`); manually-rigged
  `_node = {port:{postMessage}}` + synthetic `_onWorkletMessage(...)` calls for
  `RealtimeEngine`, no real worklet/AudioContext (`test_seek.mjs`, `test_ended.mjs`).
  Several files also do static source-grep/lint checks against `screens/practice.js`
  text (`test_practice_seek.mjs`, `test_practice_speed.mjs`) — same technique planned for
  asserting `ensureEngine`'s new shape.

## Design Decisions
- **Approach**: a new `HybridEngine` class in `player.js` wraps one `BufferEngine`
  (eager) + one `RealtimeEngine` (lazy), presents the same external surface both existing
  engines already have, adds a `kind` getter and a `'kind'` change event. All orchestration
  (the race at load, the mid-session drop, the hard-cut swap-back) lives here, not in
  `practice.js` — matches the codebase's existing module boundary (player.js owns audio
  engine mechanics and the pass-detection contract; practice.js is UI only).
- **Why**: reuses every existing mechanism (the `'rendering'` event, `sourcePosition()`,
  `'pass'` on natural wraps, the established `loadSection→seek→play` pattern from
  `song.js`) rather than inventing new signalling — minimizes risk to the many
  already-fixed, comment-documented races in `player.js`/`practice.js`.
- **Swap style**: hard cut only, both directions — no crossfade between the two
  different node graphs (Paolo's explicit choice). Buffer→realtime is immediate/mid-phrase
  (the whole point — don't make the user wait for the old rung's loop end). Realtime→buffer
  waits for the realtime engine's own next natural `'pass'` boundary (so the incoming
  buffer lap always starts at exactly `loopStart`, matching ordinary wrap semantics).
- **Scope of the fallback**: applies to every render-not-cached case (initial load, rung
  change, transpose, Guitar-only toggle) — not just first mount. This replaces the
  existing "old buffer keeps playing while new one builds" behavior specifically for the
  uncached case; the crossfaded swap for an *already-cached* rung is untouched.

## Open Questions
None outstanding — scope and swap style were confirmed with Paolo before planning.
