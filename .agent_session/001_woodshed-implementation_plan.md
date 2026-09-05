# Implementation Plan: Woodshed — full build
Plan: 001 | Name: woodshed-implementation | Created: 2026-09-05 | Status: IN PROGRESS | GitRef: 289b0c7
Reviewed: 2026-09-05 (Codex + cold Claude subagent). Accepted findings are folded in
below; the judgement calls are parked in the context file under "Open questions from
review". `GitRef` still reflects when the plan was written, not when it was revised.

**Phase 0 gained two units (D0 stubs, D7 pass detection) and a human prerequisite
(P1), and Phase 1 gained three (E4, G2 scope, H3).** Re-check the group orderings
before fanning out — D0 must land before D1–D7, and P1 gates Groups D / F / K / M.

## Related Files
- **Prompt**: `.agent_session/001_woodshed-implementation_prompt.md` — the original request
- **Context**: `.agent_session/001_woodshed-implementation_context.md` — research findings and decisions

---

## Implementation progress (this run started 2026-09-05, unattended)

**Scope decision for this run**: P1 (exporting the nine `.dc.html` artboards from the
published Claude Design artifact) is an explicit human prerequisite in this plan — no
unattended agent can open a `claude.ai/code/artifact/…` URL. Groups D, F, K, M and every
manual gate (which requires a served UI to look at or listen to) are blocked on it.
This run therefore executes **Phase 0, Groups A/B/C only** — everything that does not
depend on P1 or a human — and stops there to report the blocker rather than guessing at
the front end from the token table alone. Resume by running P1 by hand, then continuing
with Group D.

- [x] Group A — scaffold
  - [x] A1 `pyproject.toml`, `.python-version`, `src/woodshed/__init__.py`, `tests/conftest.py`
  - [x] A2 top-level `LICENSE` (GPL-2.0-or-later), `web/vendor/README.md` — the workflow
        agent hit a content-filter error mid-unit after writing `LICENSE` correctly;
        `web/vendor/README.md` was finished by hand afterwards.
- [x] Group B — pure core (test-first)
  - [x] B1 `errors.py` + `library.py`
  - [x] B2 `clock.py`
  - [x] B3 `sections.py`
  - [x] B4 `ladder.py`
  - [x] B5 `tuning.py`
  - [x] B6 `manifest.py` — one gap found and fixed by hand after the run: `Section` was
        missing the `duration` property the "Types and units" section requires for it
        to satisfy `sections.Span`; added, and a workaround adapter C2 had built around
        the gap in `server.py` was removed once the real fix landed.
  - [x] B7 `ledger.py`
  - [x] B8 `tools.py`
  - [x] B9 `config.py` — not a lettered unit in the plan; added here (Core phase) so C2
        and C3, which both need it, don't race on it in the Server phase. See the
        BACKLOG entry: `practice.py` has the same kind of full contract with no owning
        unit and is still unassigned — not needed until Phase 1 Group F, so left alone.
- [x] Group C — depends on B
  - [x] C1 `peaks.py`
  - [x] C2 `server.py`
  - [x] C3 `cli.py` + `doctor.py`
- [x] Phase 0 automated gate — `uv run ruff check .` clean, `uv run pytest` 619 passed,
      the no-extras gate 619 passed (currently vacuous: no test yet carries
      `needs_rubberband`/`needs_librosa`/`needs_device`, and there is no
      `analyze.py`/`render.py`/`capture.py` yet for it to actually guard — worth
      re-checking once Phase 1 adds `analyze.py`), `test_server_writes_nothing_else`
      passed. Re-verified independently after the two hand fixes above.
- [x] **P1 — was "BLOCKED, human-only", turned out not to be.** The plan's premise —
      "no unattended agent can open a `claude.ai/code/artifact/…` URL" — is true for an
      artifact *shared with* an agent but not for one the *user owns*: `Artifact(action:
      "read", url: …)` returns the full raw HTML for an owned artifact. This design
      canvas's `<script type="application/json" id="appifact-doc">` block holds
      `{title, content: {files: {"Main.dc.html": "...", ..., "canvas.json": "..."}}}` —
      all nine `.dc.html` artboards plus `canvas.json`, extracted and written to
      `design/*.dc.html` on 2026-09-05. `canvas.json`'s content matched the already-
      tracked copy exactly (pretty-printing only), left alone. `.gitignore`'s
      `design/*.html` rule was dropped entirely -- Paolo is fine tracking all of
      `design/*.html`, packaged editor included if one ever lands, so the rule wasn't
      merely narrowed. The dangling `design/seed` reference in that comment went with
      it (writing that script is real feature work, out of scope here). See
      `BACKLOG.md`'s High section for the full account. **Not yet committed.**
      Groups D/F/K/M and the manual gates are consequently open again, not blocked —
      but this run's scope was Groups A/B/C only and stopped there; Group D is a
      separate piece of work, not started.

**Minor plan citation to correct**: `parse_byte_range`'s lifted test set has **14**
parametrised cases in the current `rambass-live` working copy, not 15 as the plan and
its "Citation corrections" table state — C2 verified by reading the sibling file
directly rather than trusting the remembered count.

- [x] Group D — the front end, code complete. P1 resolved and committed (`63655e6`)
      ahead of this unit. Built 2026-09-05 via a `Workflow` run (dynamic multi-agent
      orchestration, one agent per file-owning unit, run ID `wf_df7d4573-dbb`): **D0**
      first and alone (fixes every `web/*.js` export signature and the cross-unit
      contracts), then **D1–D7 in parallel** against that contract. **D7 was not
      spun up as its own agent** — its pass-detection rule (started within 250 ms of
      the section's beginning, no seek/pause between) was folded into D4 (`player.js`,
      which detects and fires a `pass` `CustomEvent`) and D6 (`screens/practice.js`,
      which only ever listens and POSTs `/api/rep`), so the two halves stay on
      opposite sides of the file-ownership line without a ninth agent.
      - **D0** fixed: the screen `mount(el, payload)` / `unmount()` contract (`payload`
        carries the route's GET response plus a merged-in `params` key for path
        segments with no home in that JSON, e.g. practice's `sectionId`); the route
        table; `ACTIONS` (16 names, CCs 80–85 on the six foot actions verified against
        `design/Main.dc.html`, `null` elsewhere) with working `on`/`dispatch`;
        `RealtimeEngine extends EventTarget` firing `pass`; `timeline.js`'s
        `{startS, endS, widthPx}` view shape. Left three open questions for D1/D5 to
        settle (the `screen-root` mount-point id, the four payload-less screens
        resolving `{}` this phase, whether `app.js`'s router loop was D0's or D1's) —
        D1 picked them up correctly, see below.
      - **D1** (`tokens.css`, `index.html`, `app.js`'s router body, `web/vendor/fonts/`):
        built from the artboards, not the plan's summary — caught that the plan's own
        `.lbl` snippet cites a `--muted` token that doesn't exist in the table and
        resolved it as `--ink-3` (what `design/_css.txt` actually hardcodes there)
        rather than inventing a 23rd token. Self-hosted both font families for real —
        network access for the Google Fonts CSS API worked in this sandbox, so no
        system-font fallback was needed; discovered Archivo ships as one variable
        woff2 covering all four weights, IBM Plex Mono as three static files.
      - **D2** (`timeline.js`, `wave.js`): built from `design/Main.dc.html` +
        `design/SongPage.dc.html`; caught that the song-page waveform's unplayed
        colour is `#3F544E` on the artboard, not `--recessive` as the plan's summary
        implied — went with the artboard. Could not cross-check the gutter/DPR trap
        against `rambass-live/console.html` directly (sibling checkout not present on
        this machine) — re-derived from the plan's own description of the trap
        instead; worth a spot-check later if that checkout is ever available here.
      - **D3** (`sections.js`): built from `design/SongPage.dc.html`. **Found a real
        gap and worked around it, correctly flagged rather than silently patched**:
        `POST /api/section`'s response omitted the `lane`/`ancestors` fields `GET
        /api/song` computes, so redrawing lanes from a commit response (rather than
        re-fetching) would collapse every section onto lane 0 after the first edit.
        **Fixed at the source** (this session, not by a D-unit — `server.py`'s
        `_post_section` now returns the same derived shape `_song` does; regression
        test `test_post_section_response_carries_lane_and_ancestors` added,
        620 tests passing). D3 also flagged that `renderSections`' "selected" tile
        state lives in its own closure (the fixed signature carries no `selectedId`)
        and resets on every server-round-trip redraw — a real but minor UX rough
        edge, logged to BACKLOG rather than fixed here since it means widening a
        signature D0 already fixed for every other unit to build against.
      - **D4** (`player.js` + half of D7, `web/vendor/rubberband/*`): adapted
        `tools/rb-probe/rb-worklet.js` into a real `AudioWorkletProcessor`
        (`worklet.js`) — added play/pause gating, looping (wrap to `loopStartFrame`
        on every pass, never finalizing the stream), and actually discarding
        `preferredStartPad`/`startDelay` frames of warm-up output (the probe computed
        this but never used it). Vendored the hash-verified `rubberband.wasm` plus
        `build.sh`/`upstream-shim.c`/`upstream-LICENSE` from `tools/rb-probe/`, per
        `web/vendor/README.md` (updated in this session — it still read "not yet
        vendored" after D4 landed). Resolved an apparent tension in
        `docs/03-audio-engine.md`'s own text: the doc's `r = 2^(s/12)`, `ρ = r/v`
        identity is explicitly pedagogical ("you will not normally write it") —
        Rubber Band's own API exposes `time_ratio`/`pitch_scale` as independent
        parameters and does the composition internally, which is what D4 implemented
        (`timeRatio = 1/speedFraction`, `pitchScale = 2^(semitones/12)`, set directly,
        no manual composition). Checked against the doc during this session's review:
        no contradiction, no doc fix needed. **D4 said plainly it cannot verify the
        stretch sounds right or that the loop-seam tick is the documented Phase 0
        defect rather than something worse — this is one of the two things that need
        Paolo in person.**
      - **D5** (`keys.js`): verified `actions.js`'s CC table against `design/Main.dc.html`
        directly rather than re-trusting D0's transcription (matched exactly), filled
        in the key map, left nudge_start/nudge_end unbound (Phase 1's `[`/`]`).
      - **D6** (`screens/song.js` + `screens/practice.js`, half of D7): built from all
        four relevant artboards. Flagged (not silently worked around) three gaps
        outside its own files: no ledger-read endpoint yet for a section's starting
        rung/clean-rep history (mirrors the plan's own open `practice.py`/BACKLOG
        item — practice.js currently mirrors `ladder.py`'s pure rung math starting
        from zero history every mount); no setlist-scoped endpoint yet to persist a
        transpose edit (`POST /api/shift` is Phase 1's F1 — the stepper is live and
        drives the engine but the value doesn't survive a reload); and `player.js`'s
        `RealtimeEngine` has no position/progress accessor, only the discrete `pass`
        event, so the ring/waveform/playhead animate from a local wall-clock estimate
        resynced at each `pass` rather than ground truth — flagged for a future D4
        pass to add one.
      - **Verification this session** (beyond the phase's own automated gate, done as
        due diligence before declaring Group D's code complete): `uv run ruff check .`
        clean; `uv run pytest` 620 passed (619 + the new regression test); every
        `web/*.js` file passes `node --check`; a real server stood up against a
        synthetic scratch repo (isolated tmp dir, a genuine 1 s silent WAV, two
        overlapping sections) confirmed `GET /`, every `/web/*` static asset
        (including the vendored `.wasm` and fonts), `GET /api/song` (lane/ancestors
        now present and correct on both the initial fetch and, per the fix above, a
        section-edit response), range-served `GET /api/audio`, and a path-traversal
        attempt all behave correctly; a headless Edge `--dump-dom` against
        `#/song/<slug>` and `#/practice/<slug>/<id>` confirmed the real song
        title/section names/practice-mode chrome actually render — the front end
        mounts and talks to the real server, not just passes `node --check`.
      - **Not run, deliberately**: no JS unit-test framework exists or was added (the
        plan gates this phase on the Python suite plus a human manual test, not a JS
        suite) and the headless check above cannot hear audio or press a foot pedal.
      - **Found live, fixed same-day (`b8038c8`)**: Paolo hit the practice view with
        the pedalboard busy on the gx100 project and the keyboard shortcuts did
        nothing. D5 built `keys.js` correctly and D1 built `app.js` correctly, but
        neither unit's file list crossed the other's, so nothing ever called
        `keys.attach()` — every keyboard binding was dead code despite existing and
        despite `node --check` and the headless smoke test both passing (neither
        exercises a keypress). One line in `app.js`'s `start()` closes it. The
        headless smoke test above did not, and structurally could not, catch this —
        worth remembering next time a phase's automated check is "does it render",
        not "does every input path actually fire".
      **Both of the phase's human-only items are still open and this run stops at
      them rather than guessing**: the manual gate (a real file, two overlapping
      sections, `uv run woodshed serve`, loop the inner one at 60% for five passes,
      confirm the rep count reads 5 and `practice/reps.jsonl` has five lines) and
      listening to D4's real-time engine in a worklet before calling it done.

---

## Context

`guitar-practice` is a spec with no code. Sixteen tracked files: eight documents in
`docs/`, a design-canvas seed in `design/`, `CLAUDE.md`, `README.md`, `.gitignore`.
No `pyproject.toml`, no `src/`, no `tests/`, no CI.

The documents are unusually complete — `docs/00-spec.md` fixes the behaviour screen by
screen, `docs/02-data-model.md` fixes the file formats, `docs/03-audio-engine.md` fixes
the audio architecture with measurements — and the published design canvas fixes the
visual language. So the uncertainty here is not *what to build*. It is that a dozen
invariants in `CLAUDE.md` are each individually easy to violate in a way that produces
software which looks right and is wrong: seconds silently mixed with playback seconds,
a rep count written into a declaration file, a resample where a stretch was meant.

This plan therefore front-loads the thinking. Every module's signature, every test
contract, and every lift from the sibling repos is decided here, so implementation is
mechanical: work units that a cheaper model can execute in parallel without needing to
re-derive any of the reasoning. **Hard thinking now, simple execution later.**

**Scope**: all four roadmap phases at equal depth. Phase 0 alone is a usable looper;
that is the roadmap's own test of whether the order is right, and it is preserved.

---

## Prime directives

Carried from `CLAUDE.md`. Every work unit is subordinate to these; a unit that satisfies
its tests while breaking one of these has failed.

| # | Invariant | The failure it prevents |
|---|---|---|
| 1 | Sections are stored in **seconds**, bars are derived for display | A tempo re-detect silently moving every loop |
| 2 | Sections are **overlapping spans**; containment and lanes are **derived, never stored** | A dragged boundary leaving a stale parent |
| 3 | **Two clocks** — source time and playback time — two functions, two names, converted at one boundary | Everything off by the pre-roll, invisibly |
| 4 | Transpose lives on `setlist.songs[].shift`; the derivation only supplies a default | A record already in E♭ getting moved anyway |
| 5 | `practice/reps.jsonl` is **append-only**; a retraction is an appended line | An unreconstructible loss of the only irreplaceable file |
| 6 | **Never write a summary into `song.yaml`** | Two counts that disagree with nothing to arbitrate |
| 7 | The repo is the database; the server writes exactly three things | UI and disk drifting apart |
| 8 | **Never resample** to change speed or pitch | The whole record an octave down at 50% |
| 9 | Practice loops play from a **pre-rendered decoded buffer**, never a live stretcher | A tick at the seam, once per pass, forever |
| 10 | Layering: pure core needs **pyyaml + numpy and nothing else** | A test suite that needs librosa to run |
| 11 | **One action table** for mouse, keyboard and MIDI | "Speed up" meaning two different things |
| 12 | The tool **never judges the playing** | A measurement the tool cannot honestly make |
| 13 | `src/woodshed/` is generic — no song names, no personal paths | — |
| 14 | Audio bytes never enter git | Publishing commercial recordings |

---

## Decisions taken (do not re-litigate during implementation)

1. **Rubber Band everywhere.** The `rubberband` CLI as a subprocess for cache renders,
   and a Rubber Band WASM build in the `AudioWorklet` for the real-time exploring engine.
   Rationale: the preview and the render then use the same algorithm, so auditioning a
   speed tells you what practising at it will actually sound like. Paolo has accepted the
   GPLv2+ consequence and that the repo may be public. Record the licence decision in
   `web/vendor/README.md` and in `docs/03-audio-engine.md`.

   Two corrections to how this was argued. **The consistency claim is weaker than
   stated**: the CLI render uses `--fine` (R3) while the worklet runs Rubber Band's
   real-time path, and although R3 does have a real-time mode its latency and CPU
   profile in a 128-frame `AudioWorklet` quantum are not the offline render's. Treat
   auditioning as *indicative* of what practising will sound like, not identical — the
   decision stands on quality at ratios 1.4–2.5, which is the measured argument in
   `docs/03-audio-engine.md`, and does not need the consistency claim. **And GPLv2+
   propagates**: vendoring a GPL WASM build into a published `web/` makes the
   distributed work GPL, so this needs a top-level `LICENSE` file in the repo, not only
   a note under `web/vendor/`. Add it in A1.

   **Third correction, 2026-09-05, from actually finding the build.** The consistency
   claim is weaker again: the only maintained standalone WASM build is Rubber Band
   **3.3.0**, while the CLI installed here is **4.0.0**. So preview and render differ
   by a major version as well as by engine mode. This changes nothing about the
   decision — which rests on quality at 1.4–2.5, verified in a worklet at ~17 % of one
   core — but "auditioning tells you what practising will sound like" is now indicative
   twice over, and nothing should be built that depends on the two matching. If a 4.0.0
   WASM is ever wanted, the vendored `build.sh` rebuilds it against a different tarball
   version in one line; it needs emscripten, and it is not Phase 0 work.
2. **`rubberband` 4.0.0 is installed** (2026-09-05), unpacked to
   `%LOCALAPPDATA%\Programs\rubberband\rubberband-4.0.0-gpl-executable-windows` with
   `WOODSHED_RUBBERBAND` and user `PATH` set. There is no winget/choco/scoop package —
   it is the official GPL executable zip from <https://breakfastquay.com/rubberband/>,
   so `doctor`'s install hint must name that download, not a package manager.
   The folder holds `rubberband.exe`, `rubberband-r3.exe` and `sndfile.dll`; keep them
   together, the DLL is required.

   **Verified end to end**, and the numbers are worth keeping because they are the ones
   Phase 2 is designed around:
   ```
   rubberband --time 1.8182 --pitch -1 --formant --fine in.wav out.wav
   Using R3 (finer) engine
   Using time ratio 1.8182 and frequency ratio 0.943874
   in: 441000, out: 801826, ratio: 1.8182, ideal output: 801826, error: 0
   elapsed time: 1.17 sec
   ```
   `--fine` does select R3. The frequency ratio `0.943874` is exactly `2^(-1/12)`, which
   confirms the identity in `docs/03-audio-engine.md` rather than assuming it. Zero frame
   error, and 10 s in 1.17 s — so a 30-second solo renders in **~3.5 s**, fast enough to
   render the next ladder rung in the background while the current one loops.
3. **Lift, never import.** Code is copied from `../rambass-live` and `../gx100` and
   adapted, with a docstring line naming the origin file. Neither repo becomes a
   dependency; Woodshed must build and run from a fresh clone with both absent.
4. **argparse, not Typer.** `rambass-live`'s `main()` error handling lifts verbatim and
   already produces the `WoodshedError` → one line → exit 2 contract `CLAUDE.md` requires.
   (`gx100` uses Typer, but its per-module refusal-to-exit helper is the same idea.)
5. **Pydantic v2 models in the module that owns the file**, loose (`extra="allow"`) for
   declarations, strict where a wrong value would be silently harmful (spans, shift range).

   **This deviates from the layering rule as literally written and the deviation is
   deliberate.** `CLAUDE.md` says the pure core needs "pyyaml and numpy and nothing
   else", and its Conventions section says "Schemas are Pydantic models in the module
   that owns them". Those two sentences conflict; the source docs, not this plan,
   contain the conflict. Resolution: **pydantic is a third core dependency**, the
   invariant means "no *heavy* dependency — no librosa, no audio device, no external
   binary — above the analyze/render/capture line", and the no-extras gate installs
   `pyyaml numpy pydantic`. `doctor` reports all three as core. Update
   `CLAUDE.md` and `docs/01-architecture.md` to say three, so the next reader does not
   find a directive the code breaks. The alternative — plain dataclasses in
   `manifest.py` — is a real option and cheaper to defend; it is not chosen, and this
   paragraph exists so that choice is visible rather than accidental.

   **Confirmed 2026-09-05, and scoped.** Pydantic is a dependency of the *parsing
   boundary* only: `manifest.py` and `setlist.py` import it, and `sections`, `ladder`,
   `ledger`, `clock` and `tuning` import stdlib and numpy and nothing else. The hard
   maths therefore stays provably pure whatever happens to the dependency. **The doc
   edit is already done** (2026-09-05): `CLAUDE.md`'s Layering section and
   `docs/01-architecture.md` both name three core dependencies and state the scoping
   rule, so B6 implements against docs that already agree with it rather than editing
   them afterwards.
6. **stdlib `ThreadingHTTPServer`**, no FastAPI. The server does three things and
   `rambass-live/src/rambass/console.py` already implements two of them correctly.
7. **Vanilla ES modules, no bundler.** Served straight from `web/`.

---

## Module map, with the signatures fixed now

This is the contract that lets work units run in parallel. An agent implementing
`ladder.py` does not need to read `sections.py`; it needs this table.

### Types and units — fixed before anything else

Two contracts were ambiguous in an earlier draft of this plan and both are the
kind that a parallel fan-out turns into rework. They are settled here.

**`Span` is a Protocol, not `manifest.Section`.** `sections.py` is Tier 0 and must
not import Tier 1, so it depends on a structural type, and `manifest.Section`
satisfies it by having the same attribute names:
```python
class Span(Protocol):
    id: str
    start_s: float
    end_s: float
    @property
    def duration(self) -> float: ...        # end_s - start_s
```
Every signature in `sections.py` uses `start_s` / `end_s` / `duration` and **never**
`.start` / `.end`. This is what lets B3 and B6 run in parallel: B3 implements against
the Protocol, B6 satisfies it, and neither reads the other.

**Speed is a percent everywhere except one place.** `50.0` means 50 %. It is a percent
on disk (`song.yaml`, `reps.jsonl`), in `LadderConfig`, in `cache_key`, in every
endpoint and in every signature below — *except* `clock.Render.speed`, which is a
fraction `0.40 .. 1.00` because it is arithmetic, not a declaration. `Render` is
constructed at exactly one place and the division happens there:
```python
Render(..., speed=speed_pct / 100.0)
```
A parameter carrying a percent is named `speed_pct` wherever both could be in scope.

### Tier 0 — no dependencies

**`src/woodshed/errors.py`**
```python
class WoodshedError(RuntimeError):
    """A refusal addressed to the human. main() prints it and exits 2."""
```

**`src/woodshed/library.py`** — repo layout and slugs. Lifts `find_root`, `slugify`
from `rambass-live/src/rambass/project.py:48-77`.
```python
ROOT_MARKERS = ("pyproject.toml", "woodshed.toml", ".git")
def find_root(start: Path | None = None) -> Path
def slugify(text: str) -> str                      # folds accents; "Perché No" -> "perche-no"

@dataclass(frozen=True)
class Repo:
    root: Path
    @property songs_dir / setlists_dir / practice_dir / config_path / web_dir -> Path
    def song_dir(self, slug: str) -> Path           # songs/<slug>
    def audio_dir(self, slug: str) -> Path          # songs/<slug>/audio
    def cache_dir(self, slug: str) -> Path          # songs/<slug>/cache
    def ledger_path(self) -> Path                   # practice/reps.jsonl
    def list_songs(self) -> list[str]               # slugs, sorted
    def list_setlists(self) -> list[str]
    def find_song(self, needle: str) -> str         # slug or unambiguous title; raises listing matches
```

**`src/woodshed/clock.py`** — invariant 3, isolated so it can be tested alone.
```python
SourceSeconds = NewType("SourceSeconds", float)     # position in the original file
PlaybackSeconds = NewType("PlaybackSeconds", float) # position in the rendered, stretched section

@dataclass(frozen=True)
class Render:
    """Describes one rendered section: what was cut, and at what speed."""
    start_s: float          # source seconds, section start (NOT including pre-roll)
    end_s: float            # source seconds, section end
    pre_roll_s: float       # source seconds of lead-in rendered ahead of start_s
    speed: float            # 0.40 .. 1.00, FRACTION (see "Types and units")
    crossfade_ms: float = 10.0
    @property loop_start(self) -> PlaybackSeconds   # pre_roll_s / speed
    @property loop_end(self) -> PlaybackSeconds     # loop_start + (end_s-start_s)/speed - crossfade_ms/1000
    @property total(self) -> PlaybackSeconds

**The baked crossfade shortens the loop, and the clock must carry the term.**
`ffmpeg` cuts `[start_s - pre_roll_s, end_s]` and nothing past `end_s`, so the
equal-power crossfade has no tail to fade in *from* — it folds the section's final
`crossfade_ms` over the head of the loop region. The looped span is therefore
`crossfade_ms` shorter than the section and `loop_end` says so. Without the term,
`loopEnd` sits past the fade and every pass replays the crossfaded head: an audible
tick once per loop, which is precisely what the Phase 2 manual gate listens for.
`crossfade_ms` is *playback* milliseconds — already stretched, so it is not divided
by `speed`. A test asserts `loop_end` against a render whose length ffmpeg reports.

def to_playback(t: SourceSeconds, r: Render) -> PlaybackSeconds   # (t - (start_s - pre_roll_s)) / speed
def to_source(t: PlaybackSeconds, r: Render) -> SourceSeconds     # inverse
def grid_to_playback(grid_s: Sequence[float], r: Render) -> list[PlaybackSeconds]
```
*Test contract*: round-trip identity to 1e-9 over a fuzz of speeds 0.4–1.0 and pre-rolls
0–8 s; `to_playback(start_s) == loop_start`; `to_playback(end_s) == loop_end`; a named
test asserting that at speed 0.5 a 30 s section renders 60 s of playback.

**`src/woodshed/tuning.py`** — invariant 4.
```python
TUNINGS: dict[str, int]     # "E standard": 0, "Eb standard": -1, "D standard": -2,
                            # "C# standard": -3, "C standard": -4, "B standard": -5
MAX_SHIFT = 6
def pitch_of(name: str) -> int                      # raises WoodshedError naming known tunings
def default_shift(setlist_tuning: str, recording_tuning: str) -> int
def clamp_shift(semitones: int) -> int              # to ±6
def tuning_label(name: str, shift: int) -> str      # "E♭ standard  −1"
```
**Drop tunings are deliberately not in the table**, and this is a decision rather than an
omission. "Drop D" lowers one string; it is not a uniform transposition, so there is no
single semitone number that describes it and inventing one would move the whole record
when only the low string moved. If a song's `recording.tuning` reads `Drop D`, the honest
answer is that its pitch centre is E standard — so the table maps drop names to their
parent (`"Drop D" -> 0`, `"Drop C#" -> -1`) and the *arrangement* difference is a note on
the section, not a shift. Say this in the docstring; someone will otherwise "fix" it.

*Test contract*: E♭ setlist + E recording → −1; E♭ setlist + E♭ recording → **0**
(the invariant's whole point, name the test for it); an explicit entry shift overrides
the derivation; ±7 clamps to ±6; an unknown tuning name raises with the list in the
message; `Drop D` resolves to the same shift as `E standard` and a test says why.

**`src/woodshed/sections.py`** — invariant 2. Pure functions, no I/O.
```python
def contains(a: Span, b: Span) -> bool              # a.start_s <= b.start_s and b.end_s <= a.end_s and a is not b
def order(spans: Sequence[Span]) -> list[Span]      # sorted by (start_s, -duration)
def assign_lanes(spans: Sequence[Span]) -> dict[str, int]
        # order(), then first lane with no overlap. Longest-first puts containers on lane 0.
def ancestors(spans, span_id) -> list[Span]         # outermost first
def validate(spans: Sequence[Span], duration_s: float | None) -> None
        # raises WoodshedError: start >= end; outside [0, duration]; duplicate id;
        # EXACT duplicate span (only that — overlap and nesting are legal)
        # duration_s None or <= 0 (a needs-audio song) SKIPS THE BOUNDS CHECK ONLY.
        # Refusing every span on a song whose audio is not bound yet breaks the
        # degrade-do-not-refuse rule in docs/02-data-model.md:160. Name a test.
def snap(t: float, grid: Sequence[float], mode: str, tolerance_s: float = 0.12) -> float
        # mode: "beat" | "bar" | "free"; returns t unchanged when mode == "free" or grid is empty
def coverage_readiness(spans, reached: Mapping[str, float]) -> CoverageResult
```
`coverage_readiness` is the subtle one and its algorithm is fixed here:
> Drop every span with `counts_toward_readiness is False`. Collect all remaining
> start/end values into a sorted set of boundaries. For each elementary interval between
> consecutive boundaries, find the spans covering it; if none, the interval is **outside
> the denominator entirely**. Otherwise the interval contributes `length` to the
> denominator and `length * reached[longest covering span]` to the numerator. Ties on
> length break by `order()`. Return numerator/denominator, plus the per-interval
> breakdown so the UI can show the components.

*Test contract*: a solo subdivided into three drills gives the same song readiness as the
undivided solo when the drills are ahead (name the test for the double-count it prevents);
a drill at 100% inside a solo at 50% does not move the song number; uncovered seconds are
excluded from the denominator; two identical spans are refused, an overlapping pair is not;
lanes are deterministic across a re-sort; `counts_toward_readiness: false` is invisible to
both numerator and denominator.

**`src/woodshed/ladder.py`** — pure.
```python
@dataclass(frozen=True)
class LadderConfig:
    start_speed: float = 50.0
    ladder_step: float = 5.0
    reps_to_advance: int = 3
    target_speed: float = 100.0

@dataclass(frozen=True)
class LadderState:
    speed: float
    clean_at_speed: int
    def remaining(self, cfg) -> int
    def hint(self, cfg) -> str          # "-> 60% after 1 more"

def rungs(cfg: LadderConfig) -> list[float]
def starting_speed(clean_by_speed: Mapping[float, int], cfg) -> float
        # The rung ABOVE the highest one with >= reps_to_advance cleans (capped at
        # target_speed), else cfg.start_speed. Returning the earned rung itself would
        # make you re-earn work on_clean already advanced past.
def on_clean(state, cfg) -> LadderState     # advance at reps_to_advance, reset counter
def on_retract(state, cfg) -> LadderState   # DROP ONE, never reset to zero
```
**Two counters, and an earlier draft conflated them.** `clean_at_speed` is the
*progress toward the next rung*: it lives in `0 .. reps_to_advance` and resets to 0 on
advance. The ledger's total cleans at a speed is a different number and belongs to
`ledger.clean_by_speed`. So "nine clean reps then one retraction leaves eight" is a
statement about the **ledger**, not about `clean_at_speed`, which cannot hold 9 when
`reps_to_advance` is 3. Test each against the right quantity.

*Test contract*: `on_retract` at `clean_at_speed == 0` leaves the speed alone and does
not go negative; `on_retract` at 2 gives 1 — never 0 (name the test for the punishment
it avoids); nine cleans then one retraction leaves **eight in `ledger.clean_by_speed`**,
which is the test the earlier draft was reaching for; nothing advances past
`target_speed` automatically — assert it explicitly at 100; `starting_speed` with three
cleans at 55 and `ladder_step` 5 returns **60**, not 55.

### Tier 1 — pyyaml

**`src/woodshed/manifest.py`** — Pydantic v2 models + load/save. Owns `song.yaml`
and `setlist.yaml` exactly as `docs/02-data-model.md` prints them.
```python
class Recording(BaseModel)   # file, sha256, duration_s, tuning, spotify_id, source
class Tempo(BaseModel)       # bpm, source: detected|refined|tapped|manual, grid_offset_s,
                             # time_signature, confidence: float | None
class PracticeDefaults(BaseModel)
class Section(BaseModel)     # id, name, start_s, end_s, snapped, target_speed,
                             # ladder_step|None, reps_to_advance|None, notes, patch|None,
                             # counts_toward_readiness: bool = True
class Song(BaseModel)        # extra="allow"
class SetlistEntry(BaseModel)  # slug, shift: int | None = None
class Setlist(BaseModel)     # name, tuning, date|None, venue, songs: list[SetlistEntry], notes

def load_song(path) -> Song ; def save_song(song, path) -> None
def load_setlist(path) -> Setlist ; def save_setlist(setlist, path) -> None
def hash_file(path) -> str                  # sha256, streamed
def check_binding(song, repo) -> str | None # None, or a sentence naming the drift
```
Two validators carry real weight:
- `SetlistEntry` accepts **either** a bare string (`- cant-stop`) or a mapping
  (`- {slug: manlio, shift: 0}`) — `model_validator(mode="before")`. `shift: None` means
  "derive"; `shift: 0` means "explicitly zero" and the two must not collapse.
- `save_song` round-trips **for files the app wrote**: load-then-save with no edit is
  byte-identical, so a UI save never reformats the whole document. Use
  `yaml.safe_dump(sort_keys=False)` and preserve field order from the model.
  Be honest about the limit: `safe_dump` cannot preserve **comments, quoting style or
  scalar style** in a hand-edited file, so the guarantee is "stable for app-written
  files", not "byte-identical for any input". `docs/02-data-model.md` prints
  `song.yaml` with explanatory comments, and a first UI save will drop them.
  **Decided 2026-09-05: accept it — `song.yaml` is tracked in git, so a rewrite that
  drops comments shows up in `git diff` and is one `git checkout` from being back.**
  `ruamel.yaml` was declined: it is not just a fourth core dependency, it is a second
  document representation to keep in sync with the models on every write. Two
  conditions ship with the acceptance: `save_song`'s docstring states plainly what it
  drops, and `docs/02-data-model.md` says its comments are documentation — durable
  prose about a section belongs in `notes:`, which is a real field. **That doc edit is
  already done** (2026-09-05); what B6 still owes is the docstring. Do not claim a
  guarantee the serialiser cannot make.
- `SetlistEntry.shift` is validated to ±`MAX_SHIFT` **at the model**, not only in the
  UI stepper. `POST /api/shift` and a hand-edited `setlist.yaml` reach the same
  validator; a range enforced in one of three paths is not enforced.

*Test contract*: the exact YAML in `docs/02-data-model.md` parses; round-trip is stable;
a bare-string entry and a mapping entry both load; `shift: 0` survives as `0` and not
`None`; a section with `end_s <= start_s` is refused on load.

**`src/woodshed/ledger.py`** — invariants 5 and 6.
```python
class Rep(BaseModel)   # id (uuid4 hex), t (UTC iso, Z), song, section, speed, semitones,
                       # passed, clean, loop_s, setlist|None,
                       # source: midi|keyboard|ui|auto,
                       # retracted: bool = False, retracts: str | None = None
def append(repo: Repo, rep: Rep) -> None
        # opens "a", writes one json line + "\n", flush + os.fsync. Creates parent dir.
        # NEVER opens "w". NEVER reads first. Held under a module-level threading.Lock:
        # ThreadingHTTPServer can serve a UI rep and a MIDI rep concurrently, and an
        # interleaved write corrupts the one file that cannot be reconstructed.

**The on-disk key is `pass`, not `passed`.** `docs/02-data-model.md:249` fixes the
JSONL field as `"pass"`. `pass` is a Python keyword, so the model field is `passed`
and an alias carries the disk name:
```python
passed: bool = Field(alias="pass", serialization_alias="pass")
model_config = ConfigDict(populate_by_name=True)
```
Emitting `"passed"` would make the documented example unparseable, and the ledger is
append-only — a wrong key is permanent, not a migration. Name a test that asserts the
exact bytes of one appended line against the example in the data model.

**A retraction names its target.** Every rep carries a `uuid4` `id`; a retraction is
an appended line with `retracted: true` and `retracts: "<id>"`. Matching on
`(song, section, speed)` was underspecified: it omits `semitones`, which the transpose
stepper can change mid-session, and it cannot express "undo *that* one". A retraction
line is itself excluded from `totals`, `clean_by_speed` and `best_sustained_speed`
— name a test for that too.
def read(repo: Repo) -> Iterator[Rep]       # skips and counts unparseable lines, never raises
def resolve(reps: Iterable[Rep]) -> list[Rep]
        # applies retractions in order: a retracted line cancels the most recent
        # not-yet-cancelled rep matching (song, section, speed). Returns survivors.
def clean_by_speed(reps, song, section) -> dict[float, int]
def best_sustained_speed(reps, song, section, reps_to_advance) -> float
def last_practised(reps, song, section=None) -> datetime | None
def totals(reps, song, section=None) -> Totals   # passes, cleans, minutes
```
*Test contract*: `append` twice then read gives two lines and the file was never truncated —
assert the byte length only grows; a retraction removes exactly one rep and the file still
has three lines; a corrupt middle line does not stop the read; `best_sustained_speed`
needs `reps_to_advance` cleans, not one. **A test asserting no function in this module
ever opens the ledger for writing in any mode but append.**

**`src/woodshed/practice.py`** — readiness, the cold list, next-up.
`docs/01-architecture.md:44` names this module and an earlier draft of this plan
omitted it entirely, leaving the dashboard's central algorithm with no owner. Pure.
```python
def reached(reps, song, section, cfg) -> float
        # best_sustained_speed(section) / section.target_speed, clamped 0..1.
        # THE input sections.coverage_readiness consumes: it takes `reached` as a
        # Mapping and something has to produce it.
def song_readiness(song, reps, cfg) -> CoverageResult   # coverage_readiness(spans, reached)
def is_cold(reps, song, section, *, now, days=14) -> bool
        # docs/02-data-model.md: now - max(t) > 14 days AND reached > 0.8
def next_up(repo, setlist, reps, cfg) -> list[Ranked]
        # docs/00-spec.md:222 — and the parts come back, not just the total:
        #   score = (1 - reached) * weight_gap
        #         + days_since_practised / 14 * weight_cold
        #         + is_in_next_gig_setlist * weight_gig
        # "Explainable beats clever": the UI shows the three terms on hover, so the
        # function returns them separately and never collapses them to one number.
```
The three weights are **not in `config.yaml` as documented** and must be added to its
schema under `defaults:` with stated values, or the score is unreproducible.

*Test contract*: a section at target speed ranks below one never played; a cold
section outranks a warm one at equal `reached`; a song outside the dated setlist loses
exactly `weight_gig`; the returned parts sum to the total; `is_cold` is **false** for a
section at `reached <= 0.8` however long ago it was touched — that section is not
cold, it is unlearned, and conflating them would put beginner work in the cold list.

**`src/woodshed/config.py`** — `config.yaml` had no loader in an earlier draft, while
`GET /api/config` served it.
```python
class Config(BaseModel)   # library_paths, defaults (incl. auto_confirm and the three
                          # next-up weights), midi {input, channel, map}, spotify
                          # {client_id}, render {engine, formant_preserve, cache_max_gb}
def load_config(repo: Repo) -> Config     # a missing file yields defaults, never an error
```
*Test contract*: the exact YAML in `docs/02-data-model.md` parses; an absent
`config.yaml` gives usable defaults; the `midi.map` round-trips as `int -> action name`.

### Tier 2 — numpy

**`src/woodshed/peaks.py`**
```python
def compute_peaks(samples: np.ndarray, buckets: int) -> list[tuple[float, float]]
def multi_resolution(samples, sample_rate, levels=(1024, 4096, 16384)) -> dict
def write_peaks(repo, slug, data) -> Path       # cache/peaks-<n>.json
def read_peaks(repo, slug, level) -> dict | None
```
Lift the bucketing approach from `console.html:1078-1097` (stride 4 normally, 1 once a
bucket is small, or a deep zoom reads past its bucket and reports silence).

### Tier 3 — stdlib subprocess / optional heavy

**`src/woodshed/tools.py`** — lifted from `rambass-live/src/rambass/audio.py:28-214`,
**parameterised** so it locates `rubberband` as well as `ffmpeg`.
```python
@dataclass(frozen=True)
class ToolLocation: path: str ; route: str      # "env" | "path" | "discovered"

TOOLS = {
  "ffmpeg":     ToolSpec(env="WOODSHED_FFMPEG", winget_patterns=[...], install={...}),
  "ffprobe":    ToolSpec(env="WOODSHED_FFMPEG", ...),   # sits beside ffmpeg
  "rubberband": ToolSpec(env="WOODSHED_RUBBERBAND", winget_patterns=[], install={
      "Windows": "download rubberband-4.0.0-gpl-executable-windows.zip from "
                 "https://breakfastquay.com/rubberband/ and set WOODSHED_RUBBERBAND to the folder",
      "macOS": "brew install rubberband", "Linux": "apt install rubberband-cli"}),
}
def locate_tool(name: str) -> ToolLocation
def require_module(name: str, extra: str)
```
Keep the three properties that make the original good: a set-but-wrong override is an
**error**, not a fall-through; discovery ranks **below** PATH; the `route` is reported so
`doctor` never says "(via WOODSHED_FFMPEG)" about something found on PATH.

*Test contract*: lift the 12 tests from `rambass-live/tests/test_audio_tools.py` and
rename the env var. Add one asserting the rubberband message names the actual download.

**`src/woodshed/analyze.py`** (librosa, optional) — lifts `refine_tempo`, `_comb`,
`pulse_wander`, `_circular_mean`, `find_grid_anchor`, `grid_confidence` from
`rambass-live/src/rambass/analyze.py`. **All six are pure numpy** and go in a
`_fit.py` submodule importable without librosa; only `detect_tempo` needs librosa,
behind `require_module`.
**The signatures below were verified against the sibling on 2026-09-05 and two of the
four in an earlier draft were wrong.** Copy these, not the earlier ones:
```python
# woodshed/tempofit.py  (pure numpy, tier 2 — tested with no librosa installed)
def refine_tempo(env, times, coarse_bpm, *, span=0.04, coarse_step=0.01,
                 fine_step=0.0005) -> float                      # matches sibling exactly
def pulse_wander(env, times, bpm, *, window=20.0) -> list[tuple[float, float]]   # exact
def find_grid_anchor(onsets, bpm, *, subdivision=4, tolerance=0.025,
                     beat_tolerance=0.030, step=0.002,
                     tie_band=0.02) -> tuple[float, dict]
        # rambass analyze.py:214. Takes ONSET TIMES, not (env, times), and returns
        # (anchor, report) — not a bare float. Guards len(onsets) < 12.
def _runner_up(candidates, scored, winner, beat) -> float
        # analyze.py:286 — find_grid_anchor calls this; the earlier lift list omitted it
def grid_confidence(times, bpm, *, tolerance=0.030) -> dict
        # analyze.py:299. No `env`, no `offset`, and it returns a DICT.
# woodshed/analyze.py  (librosa)
def detect_tempo(path) -> Tempo             # onset envelope -> coarse -> refine -> anchor
def beat_grid(tempo: Tempo, duration_s: float) -> list[float]   # pure; derived from bpm+offset
```
The sibling entry point is `analyze_tempo` (`analyze.py:327`), not `detect_tempo`;
Woodshed keeps its own name and lifts the body. **The onsets-vs-envelope difference is
load-bearing**: `find_grid_anchor` needs a discrete onset list, so `detect_tempo`'s
pipeline is *onset envelope -> coarse -> refine -> **onset times** -> anchor*, and E2
owns producing those onsets (librosa) while E1 owns everything downstream of them.
Note also that the sibling's `analyze.py:40` does `from .audio import require_module`
at module top level — which is exactly why `tempofit.py` is split out rather than
copied wholesale.
The split matters: `refine_tempo` exists *because* librosa returns a tempogram bin centre
rather than a measurement, and the test that proves it must run without librosa.

**`src/woodshed/render.py`** (rubberband binary, optional)
```python
def span_fingerprint(song, section, *, pre_roll_s, crossfade_ms) -> str
        # 8 hex of sha256 over (recording.sha256, start_s, end_s, pre_roll_s,
        # crossfade_ms, RENDERER_VERSION), each formatted to fixed precision
def cache_key(section_id, speed_pct, semitones, fp) -> str   # "solo@60x-1st-3f9a2c11.flac"
def cache_path(repo, slug, section_id, speed_pct, semitones, fp) -> Path
def render_section(repo, song, section, speed_pct, semitones, *, crossfade_ms=10,
                   pre_roll_s=0.0, force=False) -> Path
def plan_ahead(repo, song, section, state, cfg, semitones) -> list[Path]   # the next rung
def evict(repo, max_gb: float) -> list[Path]
        # mtime-ordered, NOT atime: NTFS last-access updates are off by default on
        # Windows, so an atime LRU silently degenerates to arbitrary order. Also
        # reaps orphans — files whose fingerprint no longer matches any section.
```
`render_section` is `ffmpeg` (cut `[start - pre_roll, end]`, decode to wav) →
`rubberband --time <1/speed> --pitch <semitones> --formant --fine` → equal-power
crossfade of the last `crossfade_ms` into the head of the loop region → FLAC.
**The crossfade is baked into the file** so the browser just loops.

**The fingerprint is the fix for a silent stale loop.** `(section_id, speed, semitones)`
alone does not see a dragged boundary: the rendered file also bakes in `start_s`,
`end_s`, `pre_roll_s`, `crossfade_ms` and which source file was on disk. Edit a span
through `POST /api/section` and the next session loops the **old** span under the same
filename — the exact class of failure invariant 2 exists to prevent, and invisible,
because the audio is plausible. Putting the fingerprint *in the filename* keeps the
data model's rule that "the filename **is** the cache key" while making a stale render
impossible rather than merely unlikely, and it needs no invalidation step that could be
forgotten: a changed span simply misses the cache, the same way a changed shift already
does. `evict` reaps the orphan. `RENDERER_VERSION` is bumped by hand whenever the argv
or the crossfade maths changes.

*Test contract*: `cache_key` is stable and round-trips through `cache_path`; the ratio
passed to rubberband is `1/(speed_pct/100)` and the pitch is the raw semitone count —
assert the exact argv, with a test named for trap 1 (`--time`, never a resample); a
changed `semitones` misses the cache; **moving `end_s` by 10 ms misses the cache**
(name that test for the stale loop it prevents); renders are skipped when the file
exists and `force` is False.
The subprocess itself is mocked; a single opt-in integration test runs the real binary and
is marked `@pytest.mark.needs_rubberband`.

**`src/woodshed/capture.py`** (pyaudiowpatch, optional) — Phase 1.
```python
def list_devices() -> list[Device]
def capture(device, out_dir, *, floor_db=-50.0, gap_s=1.2, on_level=None) -> Iterator[Segment]
def split_on_silence(samples, sample_rate, floor_db, gap_s) -> list[tuple[int, int]]  # PURE
def bind_segments(segments, tracklist, tolerance_s=1.5) -> list[Binding]              # PURE
```
The two pure functions carry the risk and are tested with synthetic audio; the device
half is one thin unverified call. `bind_segments` **stops and asks** beyond ±1.5 s —
never binds on a guess, because a whole album shifted by one is invisible until you
practise the wrong song.

### Tier 4 — the server and the CLI

**`src/woodshed/server.py`** — lifts `parse_byte_range` (verbatim, plus its 15-case
parametrised test), `_send`/`_json`/`_error`/`_body`/`_send_file`, `ConsoleServer`
(`allow_reuse_address = False` and the reason), and `make_server` from
`rambass-live/src/rambass/console.py`.

Endpoints, all derived from disk per request:
```
GET  /                              -> web/index.html
GET  /web/*                         -> static
GET  /api/config
GET  /api/setlists                  -> [{slug, name, tuning, date, song_count}]
GET  /api/setlist/<slug>            -> dashboard payload: rows, next_up (with score parts),
                                       needs_audio, weeks_to_gig
GET  /api/song/<slug>?setlist=      -> song page payload: song, sections with lanes and
                                       containment, tempo, peaks url, shift, readiness parts
GET  /api/peaks/<slug>?level=       -> cached peaks json
GET  /api/audio/<slug>              -> the source file, RANGE-SERVED
GET  /api/render/<slug>/<section>?speed=&semitones=  -> the cache file, RANGE-SERVED;
                                       202 + {"rendering": true} if not yet built
GET  /api/progress/<slug>           -> per-section series from the ledger
POST /api/rep                       -> appends ONE ledger line
POST /api/section                   -> create/update/delete a span in song.yaml
POST /api/shift                     -> writes setlist.songs[].shift
POST /api/song                      -> song settings (tempo, tuning, binding)
POST /api/render                    -> request a render, returns a job id
POST /api/shutdown
```
**Every path segment is resolved, never concatenated.** `<slug>` is checked against
`Repo.list_songs()` and `<section>` against the song's own section ids; `/web/*` resolves
under `repo.web_dir` and asserts `is_relative_to` before opening. The lifted
`_send_file` takes an already-resolved `Path`, so the sibling does no containment for
us — the burden is on Woodshed's routes. `..%2f` in a slug should be a 404 from the
allow-list, not a file read.

**A `Host` and an `Origin` check on the mutating verbs.** Binding to `127.0.0.1`
(`console.py:766`) keeps the network out but not the browser: any page open in the
same browser can `POST` to `127.0.0.1:<port>` and append junk to the ledger or
`/api/shutdown` the server. Reject a `POST` whose `Host` is not `127.0.0.1|localhost:<port>`
— that is what defeats DNS rebinding, the only real threat to a loopback socket. In the
same preamble, reject a `POST` that carries an `Origin` header which is not the server's
own origin: a cross-site form `POST` carries the attacker's `Origin`, the app's own
`fetch` carries `http://127.0.0.1:<port>`, so one line closes classic CSRF without a
nonce. That is six lines and it protects the one irreplaceable file. Deliberately **not**
doing: authentication, tokens, CSRF nonces, or body-size limits — this is a
single-user tool on a loopback socket and that would be theatre.
*Test contract*: a `POST` with a foreign `Host` is refused; a `POST` with a foreign
`Origin` is refused; a `POST` with no `Origin` header at all (curl, the CLI) is allowed.

**Endpoint ownership.** C2 owns `/`, `/web/*`, `/api/song`, `/api/peaks`, `/api/audio`,
`POST /api/rep`, `POST /api/section` and `POST /api/shutdown`. `/api/config` ships with
`config.py` in C3. `/api/setlists`, `/api/setlist/<slug>` and `POST /api/shift` are
**Phase 1, unit F1**. `/api/render`, its 202 path and `/api/progress/<slug>` are
**Phase 2, units I3 and K2**. An endpoint with no unit is an endpoint nobody writes.

*Test contract*: the range tests lift verbatim (**15** parametrised cases — see the
citation corrections below); `POST /api/rep` appends exactly one line and the ledger
grows; `POST /api/section` with an exact-duplicate span is a 400 whose body has an
`error` key; a traversal attempt on each of the three path-taking routes is a 404;
**the three-writes test hashes rather than lists** — record `(path, sha256)` for every
file in a tmp repo, exercise every endpoint, and assert the only entries that changed
are `song.yaml`, `setlist.yaml`, the ledger and files under `cache/`. A file-list diff
would pass an in-place rewrite of the ledger, which is the single thing it most needs
to catch.

**`src/woodshed/cli.py`** — argparse. Lifts `main()`'s error handling,
`_remember_group_parsers`, and `use_utf8` from `rambass-live/src/rambass/cli.py:2827-2884`.
Commands exactly as `docs/01-architecture.md` lists them: `add`, `analyze`, `section`,
`setlist`, `capture`, `render`, `status`, `log`, `serve`, `doctor`, `scan`.

**`src/woodshed/doctor.py`** — lifts the `Check` dataclass and report shape from
`rambass-live/src/rambass/doctor.py`. Checks: python, installer, ffmpeg, ffprobe,
**rubberband**, numpy/pyyaml/pydantic (core), librosa (analyze), pyaudiowpatch (capture),
the cache size against `cache_max_gb`, MIDI availability, and **the browser note** —
Web MIDI is Chrome/Edge only, and saying so here is cheaper than a mysterious dead pedal.

### The front end — `web/`

```
web/index.html            one shell, hash-routed
web/tokens.css            the design canvas's palette and type scale, as custom properties
web/app.js                router, fetch helper, screen mounting
web/actions.js            THE ACTION TABLE — invariant 11
web/keys.js               keyboard -> actions.js
web/midi.js               Web MIDI -> actions.js
web/timeline.js           viewX / positionAt / sizeCanvas / drawGrid  (lifted concepts)
web/wave.js               peaks drawing, zoom-as-a-view-only
web/sections.js           lane drawing, dragging, creation
web/player.js             the two engines, buffer swap at the loop boundary
web/vendor/rubberband/    the WASM build + LICENCE + README naming the version
web/screens/{dashboard,song,practice,capture,library,progress}.js
```

`actions.js` is the load-bearing one:
```js
export const ACTIONS = {
  play_pause: {...}, next_section: {...}, prev_section: {...},
  speed_up: {...}, speed_down: {...}, retract_rep: {...},
  confirm_clean: {...}, restart_section: {...}, nudge_start: {...}, nudge_end: {...},
  transpose_up: {...}, transpose_down: {...}, loop_toggle: {...}, metronome: {...},
  fullscreen: {...}, help: {...},
};
export function dispatch(name, source)   // source: "midi" | "keyboard" | "ui"
```
`keys.js` and `midi.js` contain **only** a map from input to action name. Neither may
contain behaviour. The six-action foot vocabulary is a subset, and resisting a seventh
is a design rule, not an oversight.

---

## Citation corrections

Every sibling-repo citation in this plan and its context file was checked against the
working copies on 2026-09-05. **These verified exact** and can be lifted on sight:
`parse_byte_range` at `console.py:58`; `write_wav` at `audio.py:301`; `slugify`
`:48` / `find_root` `:67` / `ProjectError` `:16` in `project.py`; `require_module`
`audio.py:75` inside the cited `:28-214`; `locate_tool` inside `:129-214`;
`_remember_group_parsers` at `cli.py:2829`; `refine_tempo` `analyze.py:136` and
`pulse_wander` `:168` signatures **verbatim**; all six `click.py` DSP helpers; the
`console.html` comments at `:25-29`, `:128-131`, `:1078-1097` and `:1680-1683`; the
GX-100 CC#0 -> CC#32 -> PC ordering in `gx100.py`; and `gx100`'s
`patches: [{id, profile, slot}]` shape.

**These were wrong and are corrected here.** An implementer who trusts the earlier
numbers writes code against a signature that does not exist:

| Claim as written | What is actually there |
|---|---|
| `find_grid_anchor(env, times, bpm) -> float` | `find_grid_anchor(onsets, bpm, *, subdivision=4, tolerance=0.025, beat_tolerance=0.030, step=0.002, tie_band=0.02) -> tuple[float, dict]` — `analyze.py:214` |
| `grid_confidence(env, times, bpm, offset) -> float` | `grid_confidence(times, bpm, *, tolerance=0.030) -> dict` — `analyze.py:299` |
| the six analyze lifts | seven — `_runner_up` (`analyze.py:286`) is called by `find_grid_anchor` and was omitted |
| librosa entry point `detect_tempo` | the sibling's is `analyze_tempo` (`analyze.py:327`); Woodshed keeps its own name and lifts the body |
| `use_utf8` within `cli.py:2827-2884` | `use_utf8` is at `cli.py:71`; only `_remember_group_parsers` and `main()` are in that range |
| `main()` lifts "verbatim" | it catches `ProjectError` (`cli.py:2865`) — rename to `WoodshedError`; the exit-2 contract itself is unchanged |
| `locate_tool` at `audio.py:129` | `:129` is `_binary_in`; `locate_tool` is at `:163`. The *range* `:129-214` is right |
| `parse_byte_range`'s "14-case" test | **15** parametrised cases (`tests/test_console.py:1173`) |
| "lift the 12 tests from `test_audio_tools.py`" | accurate — 15 test functions, of which 12 are tool-location |

## Phase 0 — a looper that counts

**Goal**: slow a solo to 60% and loop it while the app counts. No transpose, no cache,
no ladder, no dashboard.

**Before starting**: nothing to re-verify; this is the first code in the repo.

### Work units

Units marked **∥** in the same group have no file overlap and can run concurrently.

**Prerequisite P1 — a human task, and Groups D / F / K / M are blocked on it.**
The nine `.dc.html` artboards exist **only inside the published artifact**. Verified on
disk: `design/` holds `_css.txt`, `canvas.json`, `derive.py`, `gen.json`, `gen.py` and
no artboards; `canvas.json` names all nine and none exist; `design/gen.py` produces
`gen.json` (SVG path data) and **cannot** regenerate an artboard; `.gitignore:19`'s
`design/*.html` does match `*.dc.html`; and the `design/seed` that comment names does
not exist. So `design/derive.py` cannot run, and **losing that artifact loses the
design.**

This cannot be a work unit, because a `claude.ai/code/artifact/…` URL is not
fetchable by an unattended agent — which also means every "re-open the artifact and
read the artboard" instruction later in this plan is an instruction no subagent can
follow. Paolo does this once, by hand, before front-end work starts:

1. Export the nine `.dc.html` artboards from the artifact into `design/`.
2. Narrow `.gitignore:19` to the packaged editor only (e.g. `design/canvas.html`).
3. Commit the artboards — a few hundred KB of plain HTML, and the valuable half.
4. Either write the missing `design/seed` or drop the reference in the comment.

Until that is done, the front-end units build from this plan's token table and
per-screen constants, which the plan itself calls "a checksum, not a substitute". Say
which screens were built that way so they can be checked against the drawing later.

**Group A — scaffold (must complete first, single agent)**
- **A1** `pyproject.toml` (hatchling, src-layout, `requires-python = ">=3.12"`,
  core deps `pyyaml pydantic numpy`, extras `analyze`/`render`/`capture`/`dev`, script
  `woodshed = "woodshed.cli:main"`, ruff line-length 100 select `E,F,I,UP,B`,
  pytest `testpaths=["tests"]` `pythonpath=["src"]`), `.python-version`, `src/woodshed/__init__.py`,
  `tests/conftest.py` with a `repo(tmp_path)` fixture building a scratch library.
  Three rules that exist only to keep the no-extras gate honest, because each one
  breaks it silently: markers `needs_rubberband` / `needs_librosa` / `needs_device`
  are **registered in `pyproject.toml`** and selected with `-m`, never `-k` (which
  matches test *names* and would deselect nothing); every test module that touches
  `analyze` / `render` / `capture` opens with `pytest.importorskip`, or the bare
  environment fails at collection; and **`src/woodshed/__init__.py` stays empty** —
  a convenience re-export there pulls a heavy module into every import and no test
  would notice.
  Gate: `uv sync --extra dev; uv run pytest` (collects zero tests, exits 5 — accept), `uv run ruff check .`
- **A2** the top-level `LICENSE` (GPLv2+, per the correction to Decision 1) and
  `web/vendor/README.md` naming the WASM build and its version. The design-source
  rescue that used to live here is **P1 above** — it is a human task, not a unit.
  The build is now identified: `rubberband-wasm@3.3.0` (Rubber Band 3.3.0),
  `dist/rubberband.wasm`, sha256 `496d880b…f07c04dc`, GPLv2+ — vendor that one file
  into `web/vendor/rubberband/` and put `build.sh` and the C shim beside it, because
  those are the corresponding source GPLv2 §3 asks for and a bare blob does not have.
  `tools/rb-probe/fetch-wasm.ps1` already downloads and hash-checks all four.
  **The licence question is settled (2026-09-05): the repo becomes GPL-2.0-or-later
  and A2 is unblocked.** `LICENSE` is `GPL-2.0-or-later` at the top level, and
  `build.sh` plus the C shim are committed beside the `.wasm` as the corresponding
  source GPLv2 §3 requires. This also makes vendoring the Windows `rubberband` exe
  under `tools/` a convenience call rather than a licence one.

**Group B — the pure core (∥, one agent each, all test-first)**
- **B1** `errors.py` + `library.py` + `tests/test_library.py`
- **B2** `clock.py` + `tests/test_clock.py`
- **B3** `sections.py` + `tests/test_sections.py`  ← the hardest; give it the best model
- **B4** `ladder.py` + `tests/test_ladder.py`
- **B5** `tuning.py` + `tests/test_tuning.py`
- **B6** `manifest.py` + `tests/test_manifest.py`
- **B7** `ledger.py` + `tests/test_ledger.py`
- **B8** `tools.py` + `tests/test_tools.py` (lift the 12 rambass tests)

Each unit writes its tests from the contract above *before* the implementation. No unit
in B may import another B module except `errors` and the model types in `manifest`.

**Group C — depends on B**
- **C1** `peaks.py` + `tests/test_peaks.py` (needs `library`)
- **C2** `server.py` + `tests/test_server.py` — the range tests, the three-writes test,
  `/api/song`, `/api/peaks`, `/api/audio`, `POST /api/rep`, `POST /api/section`
- **C3** `cli.py` + `doctor.py` + `tests/test_cli.py` — `add`, `section`, `log`, `serve`, `doctor`

**Group D — the front end (after C2 exposes the endpoints, and after P1)**
- **D0 — must complete before D1–D7, single agent.** Write every `web/*.js` module as
  **exported stubs with real signatures and no bodies** — `timeline.js`'s
  `viewX/positionAt/sizeCanvas/drawGrid`, `wave.js`, `sections.js`, `player.js`,
  `actions.js`, and the `mount(el, payload)` shape each screen exports. Rule 2 of
  "Running this with subagents" says a unit implements against fixed signatures; the
  Python tiers have that and `web/` did not, so D1–D7 could not actually fan out as
  claimed. D0 is the missing contract and it is twenty minutes of work.
- **D1** `web/tokens.css` + `web/index.html` + `web/app.js` — the shell and the palette
- **D2** `web/timeline.js` + `web/wave.js` — the mapping and the waveform
- **D3** `web/sections.js` — lanes, drag to create, drag edges, double-click to name
- **D4** `web/player.js` — the real-time engine only (WASM worklet, speed slider).
  **The build question is settled and the answer is measured**, not chosen: it is
  `rubberband-wasm@3.3.0`, `dist/rubberband.wasm` used bare with no Emscripten glue,
  instantiated synchronously in the processor constructor from a `WebAssembly.Module`
  passed through `processorOptions`. `tools/rb-probe/rb-worklet.js` is a working
  prototype of exactly this processor and D4 should start from it. Three constraints
  it discovered, all measured on 2026-09-05 and written up in `docs/03-audio-engine.md`:
  **(a)** the worklet owns the source samples and pulls — a quantum-in/quantum-out
  processor cannot hold a ratio other than 1.0, which is the reason the published
  `rubberband-web` worklet is unusable here; **(b)** `preferredStartPad` and
  `startDelay` are 2048 frames on R3 (2170 with a pitch shift, 1024 on R2) and both
  belong in `clock.py`'s conversion, not in `player.js`; **(c)** `performance` does
  not exist in `AudioWorkletGlobalScope`. Budget: ~17 % of one core, stereo, ratio 2.0.
- **D5** `web/actions.js` + `web/keys.js` — the action table and the keyboard map
- **D6** `web/screens/song.js` + `web/screens/practice.js` — the workbench and the hero
- **D7 — pass detection, moved here from Phase 2 (was J3).** A pass counts when
  playback reaches the section end having started within 250 ms of its beginning with
  no seek and no pause between. Phase 0's own gate asserts "the rep count reads 5 and
  `practice/reps.jsonl` has five lines", which is unreachable if nothing counts a pass
  until Phase 2. On the real-time engine the boundary is observed from
  `AudioContext.currentTime`; J2 later replaces that with buffer-boundary arithmetic
  and D7's contract does not change.

**A stated exception, not a silent breach.** Phase 0 ships only the real-time WASM
engine (D4) and its manual gate loops on it — which is prime directive 9 ("practice
loops play from a pre-rendered decoded buffer, never a live stretcher") not yet
satisfied. `docs/07-roadmap.md` sanctions this staging: Phase 0 is *"a looper that
counts"* and the cache is Phase 2's whole subject. So it is deliberate, and the plan
says so rather than leaving an implementer to discover a directive already broken:
**expect a tick at the seam in Phase 0, do not try to fix it in the worklet, and do
not let it pass unremarked into Phase 2.** Invariant 9 becomes binding at the Phase 2
gate, where the manual test is listening for exactly this.

**Phase 0 gate**
- Automated: `uv run ruff check .` clean; `uv run pytest` green; the three-writes test passes.
- Manual (a human, once): `uv run woodshed add <a real file>`, draw two overlapping
  sections, `uv run woodshed serve`, loop the inner one at 60% for five passes, confirm
  the rep count reads 5 and `practice/reps.jsonl` has five lines.

### Traps this phase must avoid
- Drawing before reading `console.html:25-29` and `:128-131`. The gutter width and the
  transparent-border trick are two alignment bugs already paid for. One gutter constant,
  `border: 1px solid transparent` on every canvas, `DPR = 2` fixed not `devicePixelRatio`.
- Clamping off-window marks to x=0. Skip them; a clamp draws a bar line where there is none.
- Zoom changing what loops. It is a view and only a view.

---

## Phase 1 — in tune, on the grid

**Goal**: transpose, a real tempo, a bar ruler, snapping, a click, setlists, and capture.

**Before starting**: re-read `sections.py`'s snapping signature against what Phase 0
actually shipped; confirm `clock.py`'s `Render` is what `player.js` ended up consuming.
Both are likely to have moved.

### Work units

**Group E — analysis (∥)**
- **E1** `tempofit.py` (pure numpy: `refine_tempo`, `_comb`, `pulse_wander`,
  `_circular_mean`, `find_grid_anchor`, `grid_confidence`) + `tests/test_tempofit.py`.
  **Tested with librosa absent** — that is the point of the split. Include the test
  naming the measurement: near 117 BPM librosa's only returnable values are ~112.4 /
  117.5 / 123.1, and the refined fit must land between them.
- **E2** `analyze.py` (librosa behind `require_module`) + `beat_grid` + `woodshed analyze`.
  Owns producing the **onset times** `find_grid_anchor` consumes (see the signature
  note above) — E1 owns everything downstream of them.
- **E4 — tap tempo and manual override.** `docs/07-roadmap.md:29` requires
  `refine_tempo` "plus a **tap-tempo** fallback and a manual override, each recording
  which it was", and an earlier draft of this plan had no unit for either. `Tempo.source`
  already carries `detected | refined | tapped | manual`; nothing was writing `tapped`
  or `manual`. Tap tempo is a keyboard/UI action through `actions.js` (median of the
  last 8 inter-tap intervals, discard outliers beyond 25 %), manual is a typed bpm plus
  `grid_offset_s`, and **both set `confidence: null`** — a typed number is not a fit and
  must not report one. `woodshed analyze --tap` / `--bpm` reach the same code path.
  *Test contract*: each of the four `source` values round-trips through `song.yaml`; a
  manual bpm forces `confidence` to null; the degrade path in
  `docs/02-data-model.md:160` — `bpm` 0 or absent gives no grid, no click, no ruler and
  free-dragged boundaries rather than an error — gets a named test, because every
  grid consumer has to tolerate it and none of them will unless one test says so.
- **E3** `click.py` — lift `_tick`, `_biquad_bandpass`, `_one_pole_lowpass`, `_guard`,
  `_db` from `rambass-live/src/rambass/click.py` (numpy only, deliberately scipy-free).
  **Do not lift `Timeline`** — Woodshed's grid is one bpm plus an offset, so the four
  methods `render_click` wants stub in ~20 lines.

**Group F — transpose and setlists (∥)**
- **F1** `setlist.py` — CRUD, `effective_shift(setlist, entry, song)`, `POST /api/shift`
- **F2** `web/screens/dashboard.js` — rows, readiness bars, needs-audio, weeks-to-gig
- **F3** the transpose stepper: `−`/`+` in the practice header and on the song page,
  `-`/`=` keys, written through `actions.js`, range-limited to ±6

**Group G — grid and snapping**
- **G1** bar ruler + beat grid in `web/timeline.js`; boundary snapping in `sections.js`
  wired to `sections.snap`; millisecond nudge on `[` / `]`
- **G2** lead-in bars and the generated click, gain independent of the music.
  Also owns the two lead-in declarations nothing else claimed: `Section` gains
  **`lead_in_beats: int | None`** (`docs/00-spec.md:98` lists it as a per-section
  field and the model omitted it), and `practice.pre_roll_every_pass`
  (`docs/02-data-model.md:47`) becomes real — `false` means `loopStart` sits at the end
  of the pre-roll, `true` means `loopStart = 0` and every pass replays the lead-in.
  G2 owns the **beats-to-seconds conversion** (`beats * 60 / bpm`, section override
  before song default) because `render_section(pre_roll_s=...)` is in seconds while both
  declarations are in beats, and no unit previously converted them.
  *Test contract*: `pre_roll_every_pass: true` puts `loopStart` at 0; a section
  `lead_in_beats` overrides the song's `pre_roll_beats`; with `bpm` absent the pre-roll
  is 0 s rather than a divide-by-zero.

**Group H — capture (∥, isolated)**
- **H1** `capture.py`'s two pure functions + `tests/test_capture.py` with synthetic audio
- **H2** the device half + `woodshed capture --queue` + `web/screens/capture.js`
- **H3 — the declared failure modes, which an earlier draft omitted.**
  `docs/04-sources.md:92` is explicit: *"A dropout is silent. Log the callback's
  overflow flag per segment and refuse to bind a segment that reported one."* So
  `Segment` carries `overflowed: bool`, `bind_segments` **refuses** an overflowed
  segment rather than warning, and the capture screen says which segment and why. Also
  from the same section: `doctor` checks the loopback device actually opens
  (exclusive-mode apps produce silence), and a long capture streams to disk rather than
  accumulating in memory. A silent dropout that binds is a corrupt practice source you
  discover months later.
  *Test contract*: an overflowed segment is refused by `bind_segments` and the message
  names the segment; a clean segment beside it still binds.

**Phase 1 gate**
- Automated: `uv run pytest` green including `test_tempofit.py` **with librosa uninstalled**:
  `uv run --no-project --with pytest --with numpy --with pyyaml --with pydantic pytest tests/test_tempofit.py`.
  Note `pyyaml` and `pydantic` are in that list on purpose — `tests/conftest.py`'s
  `repo()` fixture builds a scratch library and imports `manifest`, so a numpy-only
  invocation fails at **collection**, before the test it was meant to prove ever runs.
- Manual: detect the tempo of a real song, check the bar ruler lands on the downbeat by
  ear with the click on; step the shift from −1 to 0 and hear the pitch move.

---

## Phase 2 — the ladder, and a seam you cannot hear

**Goal**: the render cache, the discrete-speed practice engine, the ladder, retraction,
the progress screen, `doctor`.

**Before starting**: `rubberband` 4.0.0 is already installed and verified (see Decisions §2),
but confirm with `uv run woodshed doctor` — a machine rebuild or a cleared user PATH would
lose it. Re-read `docs/03-audio-engine.md` in full; this phase is the one it was written for.

### Work units

**Group I — the cache**
- **I1** `render.py` + `tests/test_render.py` (argv asserted, subprocess mocked)
- **I2** the equal-power crossfade, baked in; a test that the last `crossfade_ms` of the
  loop region and the head are the fade pair, measured on a synthetic tone
- **I3** `POST /api/render` + the 202 path + `plan_ahead` rendering the next rung in a
  background thread + LRU `evict`

**Group J — the practice engine (the invariant-9 work)**
- **J1** `player.js` gains the buffer engine: fetch the render, `decodeAudioData`,
  `AudioBufferSourceNode` with `loop = true`, `loopStart` at the end of the pre-roll,
  `loopEnd` at the section end. First pass plays from sample 0 and includes the lead-in.
- **J2** the boundary swap: queue the next buffer, start it at the exact
  `AudioContext.currentTime` the current loop ends, overlap for one crossfade.
  **Never `playbackRate` a stretched buffer** — a lint-style test grepping `web/` for
  `playbackRate` and failing on a hit is cheap and worth having.
- **J3** re-base pass detection (shipped as **D7** in Phase 0) onto buffer-boundary
  arithmetic: with a native loop there is no boundary event, so the *n*th seam is
  `startTime + loop_start + n * (loop_end - loop_start)` computed from `clock.Render`,
  not sampled from `currentTime`. D7's contract is unchanged — only the clock it reads.

**Group K — the ladder in the UI (∥)**
- **K1** `ladder.js` wiring `ladder.py`'s rules to the loop boundary; auto-confirm default
  on; `c` confirms, `x` retracts
- **K2** `web/screens/progress.js` — sparklines, totals, best sustained, the cold list
- **K3** `doctor.py` completion + `woodshed status --setlist`

**Phase 2 gate**
- Automated: `uv run pytest` green; the `playbackRate` grep test passes; the render argv
  test passes.
- Manual, and it is the acceptance test for the whole product: loop a real solo at 55%
  for twenty passes and **listen for a tick at the seam**. There must not be one. Then let
  the ladder advance to 60% and confirm the change happens at a loop boundary with no gap.
  This cannot be automated and the plan says so rather than pretending otherwise.

---

## Phase 3 — feet, and the rest of the toolset

**Goal**: the requirement that started the project.

**Before starting**: settle the three eyes-on-the-unit questions in
`docs/05-foot-control.md` §"Three things are still eyes-on" at the pedal in one sitting,
and write the answers back into that file. The fallback if a switch cannot send a CC
without also changing a patch is a dedicated USB MIDI foot controller, and the app does
not care which device sent the CC.

### Work units

**Group L — MIDI**
- **L1** `web/midi.js` — `requestMIDIAccess({sysex: false})`, substring match on
  `config.midi.input`, `onstatechange` for hot-plug, CC ≥ 64 counts as a press,
  150 ms debounce. Maps CC → action name and **nothing else**.
- **L2** the foot legend in the practice view; the settings screen naming Chrome/Edge
- **L3** optional expression-pedal → speed, snapping to rungs on release

**Group M — sources (∥)**
- **M1** `sources.py` — Spotify search/import (urllib, PKCE, token in
  `~/.woodshed/credentials.json`, **never** `config.yaml`), track/album/playlist → songs
  and setlists, all badged `needs-audio`
- **M2** library scan over `config.library_paths`, tags first then filename, candidates
  shown, **never bind automatically on a fuzzy match**
- **M3** `web/screens/library.js`

**Group N — the gx100 cross-reference (∥)**
- **N1** a section's `patch:` id resolved against `gx100/songs/<slug>/song.yaml`'s
  `patches:` block (`{id, profile, slot}`) — **read by path from config, never imported**,
  and absent-repo degrades to "not shown", not an error
- **N2** optional PC send, **off by default and behind an explicit toggle**. Lift the
  CC#0 → CC#32 → PC ordering from `rambass-live/src/rambass/gx100.py`. Note the gotcha:
  a PC number names a slot, not a memory; the mapping lives in `rambass-live`'s
  `config/gx100.yaml` and must not be assumed.

**Phase 3 gate**
- Automated: `uv run pytest` green; a test that `midi.js` contains no behaviour beyond a
  CC→name map (grep for anything but the map and the debounce).
- Manual: practise a solo for ten minutes without touching the keyboard or mouse. That is
  the requirement the project exists for; if it fails, the phase is not done.

---

## Running this with subagents

The plan is shaped for fan-out. The rules that make it safe:

1. **One unit owns its files.** No two units in a ∥ group name the same path. A unit that
   discovers it needs to edit another's file stops and reports rather than editing.
2. **The contract is in this plan, not in the code.** A unit implements against the
   signatures above; it does not need to read a sibling module it depends on. That is why
   the signatures are fixed here in this much detail.
3. **Test-first is the coordination mechanism.** A unit writes `tests/test_<module>.py`
   from its contract first. If the test file is right, the implementation can be graded
   without a human reading it.
4. **The phase gate is the join.** All units in a phase must be green before the next
   phase starts — and the next phase begins by re-reading what actually shipped, because
   these signatures will have moved.
5. **Model allocation**: B3 (`sections.py` coverage maths), I2 (the crossfade) and J2 (the
   boundary swap) carry the most subtle risk — give those the strongest model. Everything
   else is mechanical against a fixed contract and a cheaper model is appropriate.

---

## What we are NOT doing

- No pitch detection, no scoring of the playing, ever.
- No Spotify audio path, no DRM decryption, no bundled downloader.
- No mobile layout, no light theme, no authentication, no multi-user.
- No mixing, EQ or stem separation — that is `rambass-live`.
- No `demucs` stem practice, no Chromaprint fingerprinting, no chart export, no "session"
  concept. Those are the roadmap's "later, if it earns it" and stay there.
- No feature branches, no PRs. Work on `main`.

---

## Verification summary

| Level | Command | When |
|---|---|---|
| Lint | `uv run ruff check .` | every unit |
| Unit | `uv run pytest` | every unit |
| Scoped | `uv run pytest tests/test_<module>.py` | while implementing one unit |
| No-extras | `uv run --no-project --with pytest --with numpy --with pyyaml --with pydantic pytest tests/ -m "not needs_rubberband and not needs_librosa and not needs_device"` | every phase gate — proves invariant 10 |
| Three-writes | `uv run pytest -k test_server_writes_nothing_else` | Phase 0 gate onward |
| Real binary | `uv run pytest -m needs_rubberband` | Phase 2, opt-in |
| Human | loop a real solo and listen for the seam | Phase 2 gate |
| Human | ten minutes hands-free | Phase 3 gate |

---

---

## The design system

The published canvas at
<https://claude.ai/code/artifact/618bb99d-abcd-4827-a11d-413c6dc3a6c8> is the visual
source of truth. **The artboards are not in the repo** — `.gitignore` excludes
`design/*.html` and they are not on disk, so that artifact is the only surviving copy.
`design/derive.py` reads `Main.dc.html` from the working directory and patches it by
literal string replacement to produce the two other practice states, which means it
records the exact values below. `design/_css.txt` holds the shared head.

**Before implementing any screen, open the artboard being built** — after P1 that is
`design/<Screen>.dc.html` on disk. Do not design from this summary alone: it is a
checksum, not a substitute. If P1 has not happened, the artifact URL is the only copy
and **no unattended agent can open it**, so build from the token table and per-screen
constants below and record which screens were built that way.

### Tokens → `web/tokens.css`

| Token | Value | Used for |
|---|---|---|
| `--ground` | `#0C1211` | the page (near-black, not black). **Also the ink _on_ accent fills** — a primary button's label, the play triangle, the current ladder rung — and the 2px halo stroke that keeps a chart's end dot readable over its own line |
| `--sunken` | `#0F1614` | recessed wells: form fields, pedal chips, the Components trough |
| `--surface` | `#131B19` | cards, panels, tiles, the waveform trough |
| `--raised` | `#1B2422` | stepper buttons, section-lane tiles, active nav pill, `needs audio` badge |
| `--hairline` | `#1C2523` | dividers inside cards; the empty half of every track, meter and ring |
| `--line` | `#26302E` | the visible 1px border; the bar grid line |
| `--ink` | `#E8EEEB` | primary text, the rep number, the ring percentage |
| `--ink-2` | `#9CAAA4` | secondary prose, stepper glyphs, table body |
| `--ink-3` | `#6A7873` | `.lbl` labels, mono metadata |
| `--ink-4` | `#5B6A64` | 11.5px micro-annotations |
| `--recessive` | `#4C635C` | the unplayed waveform; and the rep count at `0` after an advance — a zero that reads *drained*, not *absent* |
| `--accent` | `#E0913F` | THE accent: speed, ring arc, played waveform, primary fill, selected rung, links |
| `--accent-hover` | `#EFA95C` | buttons and links on hover |
| `--accent-hi` | `#F6C98A` | the playhead — one step brighter than the accent, so the moving thing is the brightest thing |
| `--accent-dim` | `#8A5C29` | 1px-border weight: CC numbers, the Next-up card border, the 34px rule, a spent lead-in pip |
| `--accent-tint` | `#2A2118` | accent-tinted surface: selected lane, pressed pedal, disabled button |
| `--on-tint` | `#F0C48A` | text on `--accent-tint` (sub-text `#A8845C`) |
| `--good` | `#5FA88F` | semantic: at target, matched, clean, ladder advanced, transpose `0` |
| `--good-tint` | `#16261F` | background of an `at target` / `matched` badge |
| `--warn` | `#C9805E` | semantic: cold, recording, clipping |
| `--warn-tint` | `#2A1D17` | background of a `cold` / `● recording` badge |

**One accent, and semantic colour never doubles as it.** Every badge carries a word as
well as a hue. There is no accent-*tint* button variant — the accent is either the whole
fill or a 1px line.

Fonts: **Archivo** 400/500/600/700 and **IBM Plex Mono** 400/500/600. Three utility
classes, lifted from `design/_css.txt` verbatim:
```css
.mono { font-family: 'IBM Plex Mono', ui-monospace, Menlo, Consolas, monospace }
.lbl  { font-family: 'IBM Plex Mono', …; letter-spacing: .2em; text-transform: uppercase;
        color: var(--muted) }
.num  { font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1;
        letter-spacing: -.035em; line-height: .84; font-weight: 700 }
```
`.num`'s tabular figures are non-negotiable — the rep count and the percentage change
constantly and must not shift width. Self-host both families under `web/vendor/fonts/`;
this has to work on a laptop with no network.

### Practice mode — the hero, exact values

Artboard 1920×1080, `background: #0C1211`, `display:flex; flex-direction:column;
padding: 68px 80px 60px`.

Five stacked regions: header, hero row, next-rung strip, the section drawn, foot strip.

**Header** — `space-between; align-items:flex-start; gap:48px`.
*Left* (`gap:8px`): the state eyebrow; `23px --ink-2` "Can't Stop · Red Hot Chili Peppers";
`68px Archivo 600 -.025em lh 1.04` section name; `.mono 17px --ink-3 +.05em` breadcrumb —
"INSIDE FULL SOLO · BARS 1–8 OF 16". *Right* (`align-items:flex-end; gap:12px`): `19px
--ink-2` setlist name; the transpose stepper — wrapper `1px solid --line`, `radius 5px`,
`padding 5px`, `gap 8px`, buttons **38×38** `radius 3px` `--raised` `22px --ink-2` with
glyphs U+2212 and `+`, value `.mono .num 28px --accent width 56px center 600`; then
`.mono 14px --ink-3 +.03em` — "RECORD IN E · PLAYING IN E♭".

**Hero row** — `align-items:flex-end; gap:110px; flex-grow:1; padding-top:44px`. Speed and
reps blocks are `flex-column; gap:14px`; the ring is pushed right with `margin-left:auto`.

| Element | Spec |
|---|---|
| State label | `.lbl` 13px `--accent` — "Practising" |
| **Speed** | `.num` **236px** `--accent`; sub-line "50 bpm · 91.5 at full speed" |
| **Rep count** | `.num` **236px** `--text` weight 600 ; under it "2 of 3 clean to advance" |
| Progress ring | SVG circle, circumference **854**, `stroke-dashoffset` from 854 (empty) to 0; `<text>` in the centre shows the same percentage |
| Section waveform | played portion via `clip-path: inset(0 <remaining>% 0 0)` |
| Playhead | `left: <pct>%`, `width: 2px`, `background: var(--accent-hi)`, handle `margin-left: -6px` |
| Loop length caption | "38.2 s AT 55%" |
| Next-rung line | 27px `--ink-2`, preceded by a 34×1px `--accent-dim` rule; the rung itself `--accent` weight 600 |
| Section captions | three `.mono 14px --ink-3 +.06em`, `space-between` — "BAR 63.1" / "4 BARS LEAD-IN · 8 BARS · 38.2 s AT 55%" / "BAR 71.1" |
| Foot strip | six equal `flex:1` chips, `gap:12px; padding-top:30px`. Chip `--surface`, `1px --hairline`, `radius 4px`, `padding 12px 16px`: `.mono 12px --accent-dim +.14em` CC number over `17px Archivo 500 --ink` action. Fixed order: **80 Play/pause · 81 Next section · 82 Previous · 83 Faster · 84 Slower · 85 Retract rep** |

Both hero numbers inherit `line-height:.84`, so a 236px digit occupies ~198px of box, and
they baseline-align via `align-items:flex-end`. **There is nothing between 236px and the
19px mono caption beneath it** — that gap is the design, not an omission.

**State B — lead-in.** The whole practice layout drops to `opacity: .2` and an absolutely
positioned overlay centres: `.lbl` 18px `--accent-dim` letter-spacing `.34em` ("Lead-in");
the countdown `.num` **520px** `--accent` `line-height: .9`; four 64×6px pills with
`border-radius: 3px` (`--accent-dim` spent, `--accent` current, `--surface` pending);
then `.mono` 20px `--muted` — "4 bars, then bar 63.1".

**State C — ladder advanced.** The emotional payoff, and the brief says *earned and quiet,
not confetti*. The label becomes "Ladder advanced" in `--good`; the old speed appears at
15px `--muted` with `text-decoration: line-through` beside a `+5` in `--good`; the new
speed keeps 236px `--accent` and gains exactly one flourish —
`text-shadow: 0 0 90px rgba(224,145,63,.34)`; the rep counter resets to `0` in `--dim`;
the next-rung line becomes a record of what was earned ("Three clean reps at 55% · earned
at 19:44 · next rung 65%"). **That text-shadow is the entire celebration.** Do not add
motion beyond a brief fade on it.

### Rules the brief states as prohibitions

- No navigation sidebar in practice mode. Six elements, nothing else.
- No small text in practice mode. If it is worth showing there, it is large.
- No decoration on the numbers — no tick marks, no dials, no gradients on the figures.
- No mobile layout, no light theme.
- No gamification: no badges, levels, streaks or "you're on a roll".
- Restrained motion. The ring fills, the countdown pulses, an advance gets one emphasis.
  Nothing else animates — motion in peripheral vision while concentrating on a fretboard
  is an irritant.
- Transpose is a **control**, not a label: a `−` / signed value / `+` stepper with the
  tuning names beside it as the reasoning.

### Two mechanics worth lifting exactly

These are the non-obvious parts, and both are cheaper than what an implementer would
otherwise invent.

**The waveform is two identical paths and one `clip-path`.** The beat grid is *CSS on the
trough*, not SVG:
```css
height:104px; background:#131B19; border-radius:3px; overflow:hidden;
background-image:
  repeating-linear-gradient(to right,#26302E 0 1px,transparent 1px 220px),  /* bars  */
  repeating-linear-gradient(to right,#1C2523 0 1px,transparent 1px 55px);   /* beats */
```
Over it, one `<svg viewBox="0 0 1776 104" preserveAspectRatio="none">` containing the
**same `d` twice** — once `stroke="#4C635C"` (unplayed), once `stroke="#E0913F"` with
`clip-path="inset(0 <remaining>% 0 0)"` (played). The path is independent vertical ticks,
`M{x} {top}V{bottom}` repeated, no curves, no fill — one DOM node for ~300 ticks.
`preserveAspectRatio="none"` lets the 1776-unit viewBox stretch to any container width,
so **JS only ever writes one percentage.** The playhead is a 2px `#F6C98A` div plus a
12×8 CSS triangle cap, keyed to the same percentage.

Per-screen tuning of the same treatment: practice `104px`/tick 5.9/stroke 3.32/grid
220:55; song page `132px`/tick 2.8/stroke 1.41/grid 150:37.5 with the *played* copy in
grey `#7E9089` — because there the accent is already spent on the loop region, and the
sheet forbids two accents in one object.

**The ring is `stroke-dasharray` arithmetic.** `r=136`, circumference `2π·136 = 854.5`,
so `stroke-dasharray="854"` and `stroke-dashoffset = 854 * (1 - p)`. `rotate(-90 150 150)`
starts it at twelve o'clock, `stroke-linecap="round"` rounds the leading tip, track
`#1C2523` and arc `#E0913F` both at `stroke-width:9`. The two centred `<text>` baselines
are `y=143` and `y=186` — deliberately not centred on 150, so the pair is *optically*
centred.

**Everything on the practice screen is driven by one 0–1 pass fraction** — ring offset,
waveform clip inset, playhead `left`. There is no second source of truth for progress, and
there must not be one.

### A conflict the implementer must not resolve by guessing

The Components sheet's readiness cell is captioned *"LENGTH-WEIGHTED MEAN OF THE SONG'S
SECTIONS"*. **That rule is superseded.** `docs/02-data-model.md` replaced it explicitly,
and says why: a length-weighted mean over sections silently double-counts the moment a
solo is subdivided, giving that solo three votes. The binding rule is the covered-song-time
one in `sections.coverage_readiness` above.

Implement the document. Then **fix the artboard caption** as part of Phase 1 Group F, so
the canvas stops asserting a rule the code does not follow. Where the canvas and `docs/`
disagree anywhere else, `docs/` wins and the canvas gets corrected — it was drawn before
the data model settled.

### Per-screen constants

Enough to lay out; read the artboard for the rest.

| Screen | Root padding | Structure |
|---|---|---|
| Dashboard 1440×900 | `34px 48px 30px` | top bar (wordmark `.mono 14px/.32em` + nav pills) → "Next up" band (hero card `1px #8A5C29` + two 250px stat cards) → table `grid-template-columns:380px 1fr 210px 130px 96px 20px` → footer |
| Song page 1440×900 | `30px 44px 28px` | header with transpose cluster → body `flex; gap:22px`: left = bar ruler + 132px wave + `position:relative; height:88px` lane stack (two 44px rows, children at **percentage** `left`/`width`) + transport bar; right = 326px inspector |
| Capture 1440×900 | `30px 44px 26px` | three stat tiles → live segment card with level meters → 118px pass timeline with `.seg` overlays → segment label strip → action row; right 322px queue |
| Library 1440×900 | `30px 44px 26px` | four "ways in" cards → import table `grid-template-columns:42px 300px 1fr 66px 250px` |
| Progress 1440×900 | `30px 44px 26px` | range chips → 150px area chart → section table `290px 156px 66px 74px 74px 96px` with inline sparklines; right 308px rail |
| Components 1440×1180 | `32px 44px` | `grid-template-columns:repeat(3,1fr); gap:16px`, twelve cells |

Component details that carry meaning rather than styling:
- **Section lane variants**: ordinary `#1B2422`/`#26302E`; container `#1E2724`/`#3A4844`;
  **selected** `#2A2118`/`#E0913F` with two 5×28px bronze drag handles at `left:-3px` and
  `right:-3px` — handles appear only on the selected span; **dragging** `#161E1C` with
  `1px dashed #5B6A64`.
- **Speed ladder** has four tiers: below floor `#3E4A46`, available `#6A7873`, current
  `background:#E0913F; color:#0C1211`, at-or-above target `#5FA88F`.
- **Transpose `0` is green, not bronze** — zero means "the record is already in your
  tuning", which is semantic, not accent. `−1` and `+2` are bronze.
- **Readiness bar** 6px tall, radius 3, rounded fill (a settled statistic). **Level meter**
  9px tall, radius 2, square fill (a fast instrument reading). The distinction is
  deliberate; do not unify them.
- **Badges always pair a tint background with matching text and carry a word**, never a
  bare hue: `at target` `#16261F`/`#5FA88F`, `cold` `#2A1D17`/`#C9805E`, `needs audio`
  `#1B2422`/`#9CAAA4`, `+2 s · confirm` `#2A2118`/`#E0913F`.
- **Icons are two strokes**: the meaningful one `#E0913F`, the container/context one
  `#3E4A46`, both `stroke-width:1.7` `stroke-linecap:round`.
- **Disabled is a dimmed accent tint**, `#2A2118`/`#6A5540` — never grey.

### Work unit D1, restated

`web/tokens.css` implements the token table plus the three utility classes;
`web/index.html` carries the shell and self-hosted `@font-face` rules. Each remaining
artboard is read from `design/*.dc.html` by the unit that builds that screen, at the
time it builds it — a summary in a plan is a checksum, not a substitute for the
drawing. That requires P1 to have run; see the note above for what to do if it has not.
