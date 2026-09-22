# Implementation Plan: Loudness matching across songs
Plan: 004 | Name: loudness-matching | Created: 2026-09-22 | Status: IMPLEMENTED (manual listening verification pending) | GitRef: 9f46103

## Related Files
- **Prompt**: `.agent_session/004_loudness-matching_prompt.md` - Original mission
- **Context**: `.agent_session/004_loudness-matching_context.md` - Research findings

---

## Context

Paolo just captured "snow" and noticed its waveform sits well below full scale
(-21.6 dBFS peak measured live) while other songs in the library are normally
mastered. Since sources vary — commercial masters, Spotify downloads, and now
live captures off a mixer that may land at any level — there's no guarantee
future songs will match each other either. He doesn't want to ride the volume
knob between songs while practicing.

The fix: measure each song's loudness once, automatically, the same moment
tempo is auto-detected (`analyze_after_bind`) — and apply a corrective gain
**at playback time only**. The stored audio (`audio/*.flac`) and the
render cache (`cache/**/*.flac`) stay bit-exact, untouched transforms of the
source, exactly as `capture_runner.py` already insists recorded audio must
stay ("the tool's first ever alteration of recorded audio... leaves the
recording bit-exact") and as `docs/03-audio-engine.md`'s "never resample to
change speed or pitch" invariant implies for level too. Only what comes out
of the speakers changes.

Three decisions made with Paolo before writing this plan:
1. **Pure-numpy K-weighted integrated loudness (ITU-R BS.1770-style), no new
   dependency.** Same instinct that already produced `beatfit.py`/`tempofit.py`
   instead of trusting librosa's own beat tracker. Because it's pure numpy it
   runs unconditionally in `analyze_after_bind`, like peaks — no
   `librosa_available()`-style gate, so there is no way to repeat the
   `bpm: 0.0`-because-a-package-was-missing bug for loudness.
2. **Target -16 LUFS integrated, -1 dBTP peak ceiling.** The peak ceiling caps
   the gain even when that falls short of the loudness target, so a
   hot-mastered song is never pushed toward clipping.
3. **Both playback surfaces get it**: the practice screen's cached loop
   (`BufferEngine`) and the song page's preview/audition transport
   (`RealtimeEngine`) — today `song.js`'s preview payload threads no
   song-level fields at all (not even the slug), so this plan adds that
   plumbing rather than leaving one surface unmatched.

## Affected components

| Component | File | Role |
|---|---|---|
| Pure loudness DSP | `src/woodshed/loudness.py` (new) | K-weighting filter, gated integrated loudness, peak dBFS, gain formula — all pure numpy |
| Schema | `src/woodshed/manifest.py` | New `Loudness` pydantic model + `Song.loudness` field |
| Auto-measure on bind | `src/woodshed/cli.py` (`analyze_after_bind`, `cmd_analyze`) | Measure loudness alongside peaks/tempo, reusing the same decode |
| Data-model docs | `docs/02-data-model.md` | Document the new field and its degrade path, matching tempo's style |
| Derived gain, served | `src/woodshed/server.py` (`_song`, render route) | Compute `loudness_gain_db` server-side (like `shift`), add to the song JSON payload |
| Real-time engine | `web/player.js` (`RealtimeEngine`) | Apply gain via its existing (currently unused) pass-through `GainNode` |
| Cached-loop engine | `web/player.js` (`BufferEngine`) | Scale the existing per-source crossfade/start gain values by the linear loudness gain — no new node |
| Hybrid wrapper | `web/player.js` (`HybridEngine`) | Confirm the gain field flows through untouched (it forwards the whole `SectionLoad` object already) |
| Practice screen | `web/screens/practice.js` (`sectionLoadParams`) | Thread `payload.loudness_gain_db` into the load params, same pattern as `crossfadeMs` |
| Song page preview | `web/screens/song.js` (`ensureEngine`, `playPreview`) | New plumbing: pass `slug` and `loudnessGainDb` into the preview payload (currently passes neither) |

## Current state → desired end state

**Current**: `song.yaml` has no loudness concept. Playback volume is whatever
the source file happens to contain. A live capture that came in quiet (or a
commercial file that's just mastered louder/quieter than another) stays that
way forever, and the only fix is manual volume riding.

**Desired**: every song, once analyzed, carries a measured
`loudness.integrated_lufs` / `loudness.peak_dbfs` in `song.yaml` (a
declaration, like `tempo`, never a cache-only derived value). The server
computes `loudness_gain_db` per request from that measurement (a derived
value, like `shift` already is — never stored, so it can never disagree with
itself). Both playback engines apply that gain at their existing gain nodes.
An unmeasured song (missing/legacy `song.yaml`, same as `tempo.bpm: 0.0`
today) plays at unity gain — degrade, don't refuse.

## What we're NOT doing

- Not altering `audio/*.flac`, the render `cache/`, or the capture pipeline
  in any way. No new server-write category — `loudness_gain_db` is derived,
  never persisted.
- Not adding true-peak (oversampled) detection — sample-peak dBFS is enough
  to set a safety ceiling for a practice tool; this isn't a broadcast
  loudness compliance meter.
- Not building a manual per-song loudness override UI/endpoint. (There is no
  existing precedent for editing `tempo`/`recording` after bind via HTTP
  either — see research below — so this would be new surface; out of scope
  unless it turns out to be needed after using the automatic version.)
- Not backfilling existing songs automatically. They get `loudness`'s
  sentinel (unity gain) until re-analyzed, exactly like a pre-existing song
  with `tempo.bpm: 0.0` today. `woodshed analyze <slug>` (already exists,
  already recomputes peaks) is the migration path — mention it in the
  rollout note, don't build a new bulk command.
- Not touching `capture_runner.py`'s monitor/level-meter path — unrelated to
  song playback.

## Code placement

| What | Location | Why |
|---|---|---|
| K-weighting filter, integrated loudness, peak dBFS, gain formula | `src/woodshed/loudness.py` (new, pure) | Mirrors `sections.py`/`ladder.py`/`beatfit.py`'s existing "small, single-purpose, numpy-only" module shape. Importable at module top level anywhere (including `server.py`) — it's below CLAUDE.md's heavy-dependency line, same as `beat_grid` (pure) sits safely in `analyze.py` next to the heavy `detect_tempo`. |
| `Loudness` model | `src/woodshed/manifest.py`, next to `Tempo` | Schemas live in the module that owns them (Conventions); mirrors `Tempo`'s exact shape (sentinel default, `source` literal, `extra="allow"`) |
| Measurement call | `cli.py`'s `analyze_after_bind` + `cmd_analyze` | The single existing chokepoint every bind path (`bind_song_file`, `bind_audio_to_song`, `capture.py`'s two bind functions, `cmd_capture`) already goes through for peaks/tempo |
| Derived gain computation | `server.py`, alongside `shift`'s existing derivation | Same "derived, computed at serve time, never stored" precedent already established for `shift` |

## Design: `src/woodshed/loudness.py`

```python
TARGET_LUFS = -16.0
PEAK_CEILING_DBFS = -1.0
GAIN_CLAMP_DB = 24.0  # sanity bound either direction

def measure(samples: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """(integrated_lufs, peak_dbfs). Pure numpy: K-weighting (a high-shelf +
    a high-pass biquad, ITU-R BS.1770-4's own coefficients, re-derived for
    `sample_rate` since the standard's published coefficients are for 48kHz)
    applied via a direct-form-II difference equation, then 400ms gated block
    RMS (absolute gate -70 LUFS, relative gate -10 LU below the ungated mean,
    same two-stage gating BS.1770-4 specifies) -> LUFS. Peak is plain
    max(abs(samples)) in dBFS, no oversampling."""

def gain_db(integrated_lufs: float, peak_dbfs: float, *,
            target_lufs: float = TARGET_LUFS,
            peak_ceiling_dbfs: float = PEAK_CEILING_DBFS) -> float:
    """0.0 for the "never measured" sentinel (integrated_lufs >= 0.0 -- real
    program material is always negative LUFS, same sentinel trick Tempo.bpm
    already uses). Otherwise min(target - measured, ceiling - peak), clamped
    to +-GAIN_CLAMP_DB."""
```

Both are pure functions over numpy arrays / floats — no I/O, no `require_module`.
`measure()`'s output feeds `Song.loudness`; `gain_db()` is called fresh by the
server on every song GET (cheap: two subtractions and a min), never stored.

## Schema (`manifest.py`)

```python
class Loudness(BaseModel):
    model_config = ConfigDict(extra="allow")
    integrated_lufs: float = 0.0   # sentinel: never measured (mirrors Tempo.bpm)
    peak_dbfs: float = 0.0         # sentinel: never measured
    source: Literal["measured", "manual"] = "manual"
```
Added to `Song` as `loudness: Loudness = Field(default_factory=Loudness)`,
same shape as `tempo`. A pre-existing `song.yaml` with no `loudness` key
parses straight to the sentinel via pydantic's default — no migration script,
identical to how a pre-tempo-feature `song.yaml` already degrades today.

## `analyze_after_bind` change (`cli.py`)

```python
samples, sr = load_mono_audio(audio_path)
peaks_module.write_peaks(repo, slug, peaks_module.multi_resolution(samples, sr))

from woodshed.loudness import measure as measure_loudness
integrated, peak = measure_loudness(samples, sr)
path = repo.song_dir(slug) / "song.yaml"
song = load_song(path)
song.loudness = Loudness(integrated_lufs=integrated, peak_dbfs=peak, source="measured")
save_song(song, path)
```
Reuses the exact `samples`/`sr` already decoded for peaks — no second decode.
Runs unconditionally (no `auto_tempo`-style gate), same tier as peaks, right
before the existing tempo block. `cmd_analyze` (the forced `woodshed analyze
<slug>` re-run path) gets the same call, unconditionally, alongside its own
peaks re-write — this is also the documented migration path for the five
untracked/legacy songs currently on disk.

## Server: derived gain (`server.py`)

In `_song()`'s payload dict, alongside the existing `"tempo":
song.tempo.model_dump(mode="json")` (server.py:566):
```python
from woodshed.loudness import gain_db
...
"loudness": song.loudness.model_dump(mode="json"),
"loudness_gain_db": gain_db(song.loudness.integrated_lufs, song.loudness.peak_dbfs),
```
`loudness.py` is pure/numpy-only, so this import is safe at module top level
in `server.py` (same tier as `sections`/`ladder` imports already there) —
unlike `analyze.py`'s heavy functions, which stay lazy per CLAUDE.md's
layering rule.

## Frontend: engine changes (`web/player.js`)

Add a `loudnessGainDb` field to the `SectionLoad` shape (the typedef around
player.js:66-107) and a tiny helper:
```js
const dbToLinear = (db) => 10 ** (db / 20);
```

**`RealtimeEngine.loadSection`** (player.js:356-363): its `gain` node already
exists as an unused pass-through. Set it once:
```js
gain.gain.value = dbToLinear(section.loudnessGainDb ?? 0);
```

**`BufferEngine`**: store `this._loudnessGainDb = section.loudnessGainDb ?? 0`
in `loadSection`. Both places it builds a per-source gain read that field
instead of a bare `1`/curve:
- `_startAt` (player.js:1571-1580): `gain.gain.value = dbToLinear(this._loudnessGainDb)` instead of leaving it at the Web Audio default of 1.
- `_scheduleSwap` (player.js:1454-1465): scale the equal-power crossfade curve itself before `setValueCurveAtTime` — `equalPowerCurve(CROSSFADE_POINTS, 'in').map(v => v * dbToLinear(this._loudnessGainDb))` — so the crossfade envelope and the loudness gain are one curve, not two competing writers of `gain.gain`.

No new persistent master-gain node in either engine — this reuses the
existing per-source/crossfade gain nodes exactly as the research found them,
which avoids restructuring the audio graph or the crossfade math.

**`HybridEngine`**: forwards the whole `SectionLoad` object to whichever
sub-engine is active already (per the research, it creates no nodes of its
own) — `loudnessGainDb` flows through unmodified. No change needed beyond
confirming this in review/testing.

## Frontend: threading (`practice.js`, `song.js`)

**`practice.js`**'s `sectionLoadParams()` (practice.js:781-801): add
`loudnessGainDb: payload.loudness_gain_db` alongside the existing
`crossfadeMs: payload.practice.loop_crossfade_ms` line — identical pattern,
same function, same payload object already in scope.

**`song.js`**'s preview transport (`ensureEngine` at song.js:291-307,
`playPreview` at song.js:356-370): currently builds its `loadSection` payload
from scratch per press with no song-level fields at all. Add `slug` and
`loudnessGainDb: payload.loudness_gain_db` to that payload — this is new
plumbing, not a pattern-match, since `song.js` has none of this threading
today.

## Documentation

`docs/02-data-model.md`: add a `loudness:` block to the `song.yaml` example
(mirroring the existing `tempo:` block's inline-comment style) and a short
prose note next to the existing "if `tempo.bpm` is 0 or absent..." degrade
paragraph, stating the same for `loudness.integrated_lufs`.

`CLAUDE.md`: one new invariant paragraph near the existing "Never resample to
change speed or pitch" / audio-engine rules, stating the playback-only /
never-baked-into-cache rule explicitly, so a future change doesn't
accidentally bake gain into `render.py`'s output.

`docs/03-audio-engine.md`: a short addition to the "Looping, precisely"
section noting the crossfade curve is now scaled by the loudness gain, so a
future reader of `_scheduleSwap` isn't surprised by the multiply.

---

## Phase 1: Pure loudness DSP + schema
**Goal**: `loudness.py` exists and is fully tested in isolation; `Song` has a
`loudness` field that degrades correctly when absent.

**Changes**:
- `src/woodshed/loudness.py` (new): `measure()`, `gain_db()`, constants.
- `src/woodshed/manifest.py`: `Loudness` model, `Song.loudness` field.
- `tests/test_loudness.py` (new): synthetic sine/noise fixtures at known RMS
  to sanity-check `measure()` lands in a plausible LUFS range and is
  monotonic with input level; `gain_db()` cases — sentinel→0dB, quiet source
  gets positive gain, hot/loud source gets ≤0 gain, peak ceiling clamps gain
  below the target-implied value, extreme-input clamp bounds.
- `tests/test_manifest.py`: `Loudness()` defaults to the sentinel; a
  `song.yaml` fixture with no `loudness` key parses without error (mirrors
  the existing `test_tempo_bpm_defaults_to_zero_not_a_validation_error`-style
  test named in the research).

**Success criteria**:
- [x] `uv run --extra dev pytest tests/test_loudness.py tests/test_manifest.py -q` (74 passed)

## Phase 2: Auto-measurement on bind
**Goal**: every bind path writes a real `loudness` into `song.yaml`.

**Changes**:
- `src/woodshed/cli.py`: `analyze_after_bind` measures loudness
  unconditionally (no gate), right after peaks; `cmd_analyze` does the same
  on forced re-run.
- `tests/test_cli.py`: new tests alongside the existing tempo-on-bind tests
  (e.g. mirroring `test_binding_audio_does_auto_detect_when_no_tempo_was_ever_set`)
  confirming `bind_song_file`/`bind_audio_to_song` write a measured
  `loudness`, and that `analyze_after_bind` never needs an optional
  dependency to do it (no monkeypatched "missing" case needed, unlike
  tempo's librosa gate — that in itself is worth asserting: e.g. a test that
  monkeypatches `librosa_available` to `False` and confirms `loudness` is
  STILL measured even though tempo is not).
- `tests/test_capture.py`: confirm `bind_segment_to_song`/
  `bind_segment_as_new_song` also produce a measured `loudness` (they both
  already route through `analyze_after_bind`).

**Success criteria**:
- [x] `uv run --extra dev pytest tests/test_cli.py tests/test_capture.py -q` (118 passed)
- [x] Manual: `uv run woodshed analyze snow` on the real captured song, then
      inspect `songs/snow/song.yaml` for a plausible `loudness` block. Ran
      for real: `loudness -32.7 LUFS, peak -21.6 dBFS` — peak matches the
      earlier live measurement exactly, and `song.yaml` now has a
      `loudness:` block with `source: measured`.

## Phase 3: Served, derived gain
**Goal**: `GET /api/song/<slug>` returns `loudness` and `loudness_gain_db`.

**Changes**:
- `src/woodshed/server.py`: import `loudness.gain_db` at module top level;
  add both keys to `_song()`'s payload dict.
- `tests/test_server.py`: assert both keys are present and correct for a
  fixture song with a known `loudness`, mirroring the existing
  `assert data["tempo"]["bpm"] == 120.0`-style assertion the research found.

**Success criteria**:
- [x] `uv run --extra dev pytest tests/test_server.py -q` (158 passed)

## Phase 4: Playback engines
**Goal**: both `RealtimeEngine` and `BufferEngine` apply the gain; `song.js`
and `practice.js` both thread it through.

**Changes**:
- `web/player.js`: `SectionLoad` gains a `loudnessGainDb` field; `dbToLinear`
  helper; `RealtimeEngine.loadSection`, `BufferEngine._startAt`,
  `BufferEngine._scheduleSwap` all read/apply it as designed above.
- `web/screens/practice.js`: `sectionLoadParams()` adds `loudnessGainDb`.
- `web/screens/song.js`: `ensureEngine`/`playPreview` gain the new
  `slug`/`loudnessGainDb` plumbing.
- `web/tests/`: turned out to have a real synthetic-`AudioContext` harness
  for `player.js` itself (`test_buffer_engine.mjs`), not just pure-JS
  modules — so `web/tests/test_loudness_gain.mjs` (new) exercises
  `dbToLinear`, `BufferEngine._startAt`'s steady-state gain, and
  `_scheduleSwap`'s scaled crossfade curve (both directions, still
  equal-power once un-scaled) directly, plus a regression guard that
  cancelling a swap restores the outgoing node to the loudness gain (not a
  bare `1`, which `_cancelPendingSwap` needed a matching fix for). Picked up
  automatically by `tests/test_web_lint.py`'s `test_*.mjs` auto-discovery —
  no separate command to remember.

**Success criteria**:
- [x] Existing web test suite still passes: `uv run --extra dev pytest
      tests/test_web_lint.py -q` (31 passed, including the 9 new
      `test_loudness_gain.mjs` cases).
- [ ] **Manual** (Web Audio output can't be asserted headlessly): open the
      song page for "snow" (quiet, -21.6 dBFS peak) and a normally-mastered
      song back to back, in both the song-page preview and the practice
      screen's loop, and confirm they now sound similarly loud without
      touching the system volume. **Not yet done — needs Paolo.**

## Phase 5: Docs + full suite
**Goal**: docs reflect the new invariant; everything green together.

**Changes**:
- `docs/02-data-model.md`, `CLAUDE.md`, `docs/03-audio-engine.md` updates as
  described above.

**Success criteria**:
- [x] `uv run --extra dev pytest -q` (full suite) — 1244 passed (was 1220
      before this feature).
- [x] `uv run woodshed doctor` still clean — no new dependency row, all
      existing rows unchanged.
- [x] `uv run --extra dev ruff check` clean on every changed/new file.

## Phase 6 (follow-up, requested live after Phase 5): the waveform display
**Goal**: Paolo noticed "snow"'s waveform still looked tiny next to
normally-mastered songs even though it now sounds level-matched — expected
(the drawing was never touched by Phase 4, peaks.json is real amplitude on
purpose) but he wanted the DISPLAY to look level-matched too, cosmetic only.

**Changes**:
- `web/wave.js`: `drawWave`'s `WaveOpts` gains `loudnessGainDb` (0 default);
  `ampToY`/`dbToLinear` exported (pure, DOM-free) and `buildTickPath` scales
  each tick's amplitude by the linear gain before `ampToY`'s existing
  clamp — a boosted quiet recording tops out at the trough edge rather than
  drawing outside it, never touching `peaks.json` on disk.
- `web/screens/song.js`, `web/screens/practice.js`: both `drawWave()` call
  sites pass `loudnessGainDb: payload.loudness_gain_db` alongside their
  existing `SONG_WAVE_OPTS`/`PRACTICE_WAVE_OPTS`.
- `web/tests/test_wave_loudness_gain.mjs` (new): `ampToY`/`dbToLinear`
  tested directly — 0dB is a no-op, positive gain scales by the exact
  linear ratio (checked against "snow"'s own real +16.7dB), the clamp still
  holds at extreme gain, negative gain shrinks rather than grows. Picked up
  by `test_web_lint.py`'s auto-discovery, no separate command.

**Note on what to expect**: "snow"'s own real gain (+16.7dB) is
*target-loudness-limited*, not peak-limited (the -1 dBTP ceiling would have
allowed +20.6dB) — so its waveform will look substantially taller, but not
full height like a hot-mastered song's, because matching perceived loudness
and matching peak amplitude are different things. That is correct, not a
bug: the alternative (boosting to visual full-scale) would misrepresent how
loud the correction actually is.

**Success criteria**:
- [x] `uv run --extra dev pytest tests/test_web_lint.py -q` — 32 passed
      (was 31; the new file adds one).
- [x] `uv run --extra dev pytest -q` (full suite) — 1245 passed.
- [ ] **Manual**: open the song page for "snow" and confirm the waveform
      now visibly reads taller than before, without exceeding the trough.

---

## Testing & safety
- **Unit**: Phases 1–3 are fully unit-testable in Python with no audio
  device, no browser — same "pure modules need no hardware" property the
  rest of the pure tier already has.
- **Integration**: Phase 4's actual audible effect cannot be asserted by an
  automated test (Web Audio output isn't observable headlessly in this
  repo's test setup) — flagged explicitly above as a manual step, not
  papered over.
- **Risk**: low blast radius. No stored-file mutation, no new dependency, no
  change to the render cache's fingerprint or format (so no cache
  invalidation needed). Worst case a bad gain calculation makes playback too
  loud/quiet, not silent or broken — and the sentinel default (0 dB) is safe
  for every song until re-analyzed.
