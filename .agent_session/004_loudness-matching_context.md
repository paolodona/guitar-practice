# Session Context: Loudness matching across songs
Plan: 004 | Name: loudness-matching | Updated: 2026-09-22 | GitRef: 9f46103

## Related Files
- **Prompt**: `.agent_session/004_loudness-matching_prompt.md` - Original mission
- **Plan**: `.agent_session/004_loudness-matching_plan.md` - Implementation steps

---

## Task Summary
- **Requirement**: measure each song's loudness once (automatically, on
  bind/analyze), and apply a corrective gain at playback time so all songs
  sound similarly loud without manual volume riding.
- **Scope**: Python measurement + schema + derived server gain, both frontend
  playback engines (`RealtimeEngine`, `BufferEngine`), both playback surfaces
  (practice screen, song page preview).
- **Out of Scope**: altering stored audio or the render cache; true-peak
  (oversampled) detection; a manual override UI/endpoint; automatic backfill
  of existing songs; `capture_runner.py`'s monitor/level-meter path.

## Key Discoveries

### Tempo pattern (mirrored by loudness)
- `src/woodshed/manifest.py:82-103` — `Tempo` model: `bpm: float = 0.0`
  sentinel default (0 = "never measured", same value real bpm can never take
  in practice), `source: Literal["detected","refined","tapped","manual"]`,
  `confidence: float | None`.
- `src/woodshed/manifest.py:259-272` — `Song.tempo: Tempo = Field(default_factory=Tempo)`.
- `src/woodshed/cli.py:156-195` — `analyze_after_bind(repo, slug, audio_path, *, auto_tempo=True)`:
  peaks always written (hard dep: ffmpeg+numpy); tempo only if `auto_tempo`
  AND `librosa_available()`; failures degrade silently (printed, not
  raised); re-reads/re-writes `song.yaml` via `load_song`/`save_song`.
- Call sites: `bind_song_file` (cli.py:198-269, calls at :268 with
  `auto_tempo=(bpm is None)`), `bind_audio_to_song` (cli.py:272-316, calls
  at :314 with `auto_tempo=(song.tempo.bpm <= 0)`), `capture.py`'s
  `bind_segment_to_song` (:534-608, calls at :608) and
  `bind_segment_as_new_song` (:611-666, calls at :665) — both via a lazy
  `from woodshed.cli import analyze_after_bind` inside the function, and
  both with NO `bpm` parameter so `auto_tempo` stays at its default `True`.
  `cmd_capture` (cli.py:1017-1122, calls at :1114).
- `cmd_analyze` (cli.py:923-977) — forced re-run: `woodshed analyze <slug>
  [--bpm N | --tap SECONDS...] [--grid-offset N] [--time-signature X]`.
  Unconditionally overwrites `song.tempo` and re-writes peaks. This is the
  loudness feature's migration path for pre-existing songs.
- `src/woodshed/analyze.py` (148 lines) — `load_mono_audio` (32-75, decodes
  via ffmpeg subprocess, NOT librosa's loader), `librosa_available()`
  (78-89), `detect_tempo` (92-124, the ONLY `require_module("librosa",
  "analyze")` call in the file, at line 106), `beat_grid` (127-147, PURE,
  degrades to `[]` when `tempo.bpm <= 0`). Module docstring states the
  pipeline and cites CLAUDE.md's layering rule directly: "only this module
  and `render.py` may import a heavy or external dependency." Confirms a
  pure derived function (`beat_grid`) safely co-locates in the same file as
  a heavy detector (`detect_tempo`) — precedent for `loudness.py`'s own
  `measure()`/`gain_db()` split (both pure here, unlike analyze.py, so no
  split is even needed).
- `src/woodshed/server.py:566` (`_song()` handler, :520-608+) — `"tempo":
  song.tempo.model_dump(mode="json")`, a plain top-level sibling key of
  `"recording"`/`"practice"`/`"shift"`. `"shift"` is itself a DERIVED,
  never-stored value (computed from `pitch(setlist.tuning) -
  pitch(song.recording.tuning)`) — the exact precedent `loudness_gain_db`
  follows.
- **No existing HTTP route edits song metadata (tempo/title/artist/recording)
  after bind** — `_post_section` and `_post_patch_change` only touch
  `sections`/`patch_changes`. Confirms "no manual override endpoint" is
  correctly out of scope for v1, not an oversight.
- `docs/02-data-model.md:36-41` — the `tempo:` YAML block's inline-comment
  doc style to mirror. `docs/02-data-model.md:214-215` — "If `tempo.bpm` is
  0 or absent, the app still works... Degrade, do not refuse." — the exact
  sentence to mirror for `loudness.integrated_lufs`.
- `CLAUDE.md:108-147` (current, as of this session) — Layering section.
  Lines 110-112: pure tier needs "pyyaml, numpy and pydantic and nothing
  else." Lines 119-124: `analyze.py`/`render.py`/`separate.py`/
  `gx100_sync.py` are the only modules allowed a heavy/external dependency,
  "nothing above that line may import them at module top level." Lines
  128-147 (this session's own edit): `demucs`, `pyaudiowpatch`, `librosa`
  are core `pyproject.toml` dependencies despite being heavy, because `uv
  run --extra dev pytest` re-syncs away an extra-installed one — directly
  motivates going pure-numpy for loudness instead of adding a 4th promoted
  package.
- `pyproject.toml` / `tests/test_packaging.py` current state (this session's
  own edits) — `PROMOTED = ("demucs", "pyaudiowpatch", "librosa")` tuple in
  the test; going pure-numpy means loudness never needs to join this list.

### Real-time engine (`web/player.js`, `RealtimeEngine`)
- `createEngine(audioContext, {kind})` factory at player.js:2096-2099 picks
  `RealtimeEngine` (default), `BufferEngine` (`kind:'buffer'`), or
  `HybridEngine` (`kind:'hybrid'`).
- `RealtimeEngine` (player.js:272-582, extends EventTarget). Node graph built
  in `loadSection()` at player.js:356-363:
  ```js
  const node = new AudioWorkletNode(this.ctx, 'woodshed-rubberband', {...});
  const gain = this.ctx.createGain();
  node.connect(gain).connect(this.ctx.destination);
  ```
  `gain.gain` is currently NEVER set/read anywhere in the class — a pure
  unused pass-through. This is the exact node to repurpose for the loudness
  gain (`gain.gain.value = dbToLinear(section.loudnessGainDb ?? 0)`).
  `_teardownNode()` (player.js:565-581) disconnects it on section
  change/destroy — new gain value just gets re-set on the next `loadSection`.

### Cached-loop engine (`web/player.js`, `BufferEngine`)
- `BufferEngine` (player.js:779-1656) — cache-backed, native
  `AudioBufferSourceNode` sample-exact loop. Decode: `_fetchRender` (starts
  player.js:1182) → `this.ctx.decodeAudioData(arrayBuffer)` (same pattern as
  `RealtimeEngine.loadSection`'s own `decodeAudioData` call at :314) →
  cached in `this._buffers` Map.
- First-start node graph, `_startAt()` (player.js:1553-1582 per one research
  pass / 1571-1580 per the other — confirm exact lines during
  implementation):
  ```js
  const gain = this.ctx.createGain();
  const source = this.ctx.createBufferSource();
  source.buffer = this._buffer;
  source.loop = true;
  source.loopStart = clock.loopStart;
  source.loopEnd = clock.loopEnd;
  source.connect(gain).connect(this.ctx.destination);
  source.start(startTime, safeOffset);
  this._active = { source, gain, anchor: {...} };
  ```
- Rung/speed-swap node graph, `_scheduleSwap()` (player.js:1432-1492 /
  1454-1465):
  ```js
  const gain = this.ctx.createGain();
  const source = this.ctx.createBufferSource();
  source.buffer = buffer;
  source.loop = true; source.loopStart = ...; source.loopEnd = ...;
  source.connect(gain).connect(this.ctx.destination);
  gain.gain.value = 0;
  gain.gain.setValueCurveAtTime(equalPowerCurve(CROSSFADE_POINTS, 'in'), seamTime, crossfadeS);
  source.start(seamTime, clock.loopStart);
  ```
  **No persistent master gain node exists** — every `gain` is per-source,
  created fresh in `_startAt`/`_scheduleSwap`, torn down in
  `_retire`/`_teardownNode`. The plan's approach: don't add a new node:
  (a) in `_startAt`, set `gain.gain.value = dbToLinear(this._loudnessGainDb)`
  instead of leaving Web Audio's default 1; (b) in `_scheduleSwap`, scale
  the equal-power curve itself before `setValueCurveAtTime` —
  `equalPowerCurve(CROSSFADE_POINTS, 'in').map(v => v * dbToLinear(this._loudnessGainDb))`
  — one curve, not two competing writers of `gain.gain`. `this._loudnessGainDb`
  gets set once in `loadSection` (mirrors how `this._section`/`this._speedPct`
  are already stored as instance fields).
- During a rung swap, TWO source→gain chains are briefly live simultaneously
  (outgoing fading out, incoming fading in) — confirmed by
  `docs/03-audio-engine.md:190-206`'s "Speed changes at the boundary" section.
  Both must carry the same loudness scaling independently (handled
  automatically since each swap re-reads `this._loudnessGainDb` fresh).
- `HybridEngine` (player.js:1703-2072) creates no audio nodes of its own —
  wraps one `BufferEngine` + lazily one `RealtimeEngine`, forwards the whole
  `SectionLoad` object to whichever sub-engine is active. `loudnessGainDb`
  flows through automatically; no HybridEngine-specific code needed, just
  confirm in testing.

### No existing volume/gain control anywhere else
- Full grep across `web/*.js` and `web/screens/*.js` for
  `GainNode|gain|volume|Volume` found exactly one other real gain control:
  `web/metronome.js:237-239` — a fixed, independent click-level gain
  (documented in `docs/03-audio-engine.md:315-316`: "Click level was −9
  dBFS, is −6 dBFS"), structurally separate from song playback and
  irrelevant to this feature.

### `song.js` preview transport — currently threads NOTHING song-level
- `web/screens/song.js:291-307` (`ensureEngine`) creates a bare
  `createEngine()` (defaults to `RealtimeEngine`) lazily on first gesture.
- `web/screens/song.js:356-370` (`playPreview`) builds its `loadSection`
  payload FRESH per press containing only `sectionId, audioUrl, startS,
  endS, preRollS, loop` — no `slug`, no `tempo`, nothing song-level at all.
  This is new plumbing to add, not a pattern to copy from within `song.js`
  itself — copy the *shape* from `practice.js` instead (below).

### `practice.js` — the pattern to copy for threading
- `web/screens/practice.js:781-801` — `sectionLoadParams()`, "the one place
  a SectionLoad object is built" per its own comment:
  ```js
  function sectionLoadParams() {
    return {
      sectionId: section.id,
      audioUrl: guitarOnly ? `/api/stem/...` : `/api/audio/...`,
      startS: section.start_s,
      endS: section.end_s,
      preRollS: preRollSourceSeconds(),
      preRollEveryPass: payload.practice.pre_roll_every_pass,
      clipOffsetS: guitarOnly ? guitarClipOffsetS() : 0,
      slug: payload.slug,
      source: guitarOnly ? 'guitar' : 'mix',
      crossfadeMs: payload.practice.loop_crossfade_ms,
    };
  }
  ```
  `payload` (the full `GET /api/song/<slug>` response) is already in scope
  for the whole `mount()` closure. Add `loudnessGainDb: payload.loudness_gain_db`
  as one more line here, identical pattern to `crossfadeMs`.

### Render/cache pipeline — confirmed untouched by this feature
- `src/woodshed/render.py` — `render_section()` (262-370): ffmpeg cut →
  `rubberband --time <ratio> --pitch <semitones> --formant --fine` → numpy
  equal-power crossfade baked in (`_bake_crossfade`, 218-259) → ffmpeg-encode
  to FLAC, published atomically. **No loudness/gain field anywhere** in the
  `Render` clock object, `span_fingerprint()` (105-125, hashes
  `recording.sha256, start_s, end_s, pre_roll_s, crossfade_ms,
  pre_roll_every_pass, RENDERER_VERSION`), or the cache filename regex
  (`_RENDER_RE`, 87-90). Confirms: adding loudness gain does NOT need a
  fingerprint/cache-invalidation change, since gain is applied only in the
  browser at playback, never baked into the render.
- `src/woodshed/server.py:1122-1239` (`_render` handler) — resolves
  `song.yaml` → fingerprint → cache path → serves FLAC bytes range-served on
  a hit, or kicks off a background render + `202 {"rendering": true}` on a
  miss. No loudness computation happens here — confirmed out of scope for
  this route; `loudness_gain_db` only needs to be added to `_song()`'s
  payload (the metadata GET), not this byte-serving route.

### `docs/03-audio-engine.md` — invariants this must not violate
- Lines 142-162 ("Trap 3: a real-time stretcher cannot loop seamlessly") —
  the reason two engines exist at all; unaffected by a gain multiply.
- Lines 190-206 ("Looping, precisely") — crossfade baked/exposed as
  `loop_crossfade_ms`; pre-roll is part of the buffer; "speed changes at the
  boundary, never mid-loop... two source nodes overlap for one crossfade";
  "Never `playbackRate` a stretched buffer." None of these are touched by
  scaling the existing crossfade curve's amplitude — the loop points, seam
  timing, and pre-roll placement are all unchanged.
- No existing output-level/headroom/clipping documentation for song
  playback itself (only the metronome click's level is documented) — this
  plan's peak-ceiling clamp is the first such policy, worth writing down in
  `docs/03-audio-engine.md`.
- `render.py:203-210` — `_write_wav`/`_bake_crossfade` already
  `np.clip(samples, -1.0, 1.0)` before 16-bit quantization, on the
  INTERMEDIATE wav before FLAC encoding — unrelated to this feature (that's
  clipping protection for the stretch/crossfade math, not loudness gain).

## Design Decisions
- **Pure-numpy K-weighted integrated loudness, no new dependency** — see
  prompt file's refined-prompt §3. Rejected `pyloudnorm` explicitly to avoid
  repeating the dependency-promotion churn from earlier this session.
- **-16 LUFS target, -1 dBTP peak ceiling** — chosen over -14 LUFS for extra
  headroom margin given at least one known real song ("snow") is very quiet
  (-21.6 dBFS peak) and needs a large boost; the peak ceiling still clamps
  regardless of target.
- **Both playback surfaces (practice + song-page preview) get the gain** —
  chosen over practice-only for consistency; required adding new plumbing to
  `song.js` since it has none today.
- **Gain is DERIVED (computed server-side per request from stored
  `loudness.integrated_lufs`/`peak_dbfs`), never stored itself** — mirrors
  the existing `shift` field precedent exactly, and CLAUDE.md's general "the
  ledger is append-only... never write a summary into song.yaml" +
  "containment... derived from the spans, nothing stored" instincts against
  storing two numbers that could disagree.
- **No new persistent master-gain node in either JS engine** — reuse the
  existing per-source gain nodes (`RealtimeEngine`'s unused pass-through,
  `BufferEngine`'s per-source/crossfade gains) rather than restructuring the
  audio graph. Smaller diff, less risk of breaking the crossfade math.
- **Sentinel pattern for "never measured"**: `integrated_lufs: float = 0.0`,
  `peak_dbfs: float = 0.0` — mirrors `Tempo.bpm`'s `0.0` sentinel exactly
  (real program material is always negative LUFS / negative-or-zero dBFS in
  practice, so 0.0 is safe as "never measured").

## Open Questions
None remaining — all three fork points (measurement rigor, target level,
preview scope) were resolved interactively before the plan was finalized.
See the plan file's "Context" section for the resolved decisions.
