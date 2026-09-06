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
      **Both of the phase's human-only items were still open and this run stopped at
      them rather than guessing**: the manual gate (a real file, two overlapping
      sections, `uv run woodshed serve`, loop the inner one at 60% for five passes,
      confirm the rep count reads 5 and `practice/reps.jsonl` has five lines) and
      listening to D4's real-time engine in a worklet before calling it done.
      - **Manual gate: PASSED, 2026-09-05.** Paolo looped `tutti-in-fila`'s `tapping`
        section (nested inside `full-solo` — the required "two overlapping sections")
        at 60% for 6 passes (one better than the gate's 5). `practice/reps.jsonl`
        carries all 6, `loop_s: 25.714654265498687` on every line — exactly
        `(end_s − start_s) / 0.6` for the section's current boundaries — with
        real-clock gaps of 25.71–25.72 s between consecutive lines, no drift, no
        duplicate, no drop. An earlier 50% pass from an older section boundary was
        correctly **retracted** (a second appended line, invariant 5, not a rewrite)
        rather than removed.
      - **Found live during the gate, fixed same session**: pressing `speed_up`
        mid-loop looked like a dropped rep followed by an early restart ("it did not
        record the rep... a few seconds into the second loop it started again from
        the beginning"). Root cause was cosmetic only: `screens/practice.js`'s
        progress-ring/waveform/playhead estimate (`currentP()`) recomputed its
        assumed loop duration from the *live* `speedPct` every frame, while `elapsed`
        kept counting real wall-clock time since the last genuine `pass` — a mid-lap
        speed_up shrinks that recomputed duration, so the modulo wraps and the ring
        visually snaps to 0 before the real engine (worklet.js's boundary detection,
        driven by input-sample position, not by ratio) actually reaches the section
        end. The ledger above proves the real pass-detection was never affected.
        Fixed by freezing the cosmetic duration once per lap (`beginLap()`, only
        called on mount/restart/the real `pass` event) instead of recomputing it
        live — a mid-loop speed change now only reshapes the *next* lap's cosmetic
        estimate, matching what the audio engine itself does. `web/screens/practice.js`
        changed; `node --check` clean on every `web/*.js` file; committed (`81a581f`).
      - **Deliberately deferred, not blocking**: listening to D4's real-time engine in
        a worklet — does the Rubber Band stretch actually sound right, and is the
        loop-seam tick (if any) the documented Phase 0 defect (trap 3: a real-time
        stretcher cannot loop sample-exact) rather than something worse. Paolo's call
        (2026-09-05): his audio interface is tied up on another project right now, so
        this stays open and Phase 1 work proceeds without it — "we'll fix it if
        needed" once he listens. This is still the one remaining human-only item
        before Group D can be called fully done; it is not gating anything else.

- [x] Group E — analysis, done directly (Paolo's explicit choice over a
      Workflow run for Phase 1: "I execute it directly, group by group"),
      test-first, one unit per commit. **Before starting**, re-checked the
      plan's own pre-flight note: `sections.snap`'s signature and
      `clock.Render`'s fields both still match what Phase 0 shipped — no
      rebasing needed.
      - **E1** (`6df43eb`) `tempofit.py`: `refine_tempo`, `_comb`,
        `pulse_wander`, `_circular_mean`, `find_grid_anchor`, `_runner_up`,
        `grid_confidence` lifted verbatim from
        `rambass-live/src/rambass/analyze.py`. Confirmed green under the exact
        librosa-uninstalled gate command the plan specifies.
      - **E2** (`6df43eb`) `analyze.py`: `detect_tempo` (onset envelope ->
        coarse librosa guess -> `tempofit.refine_tempo` -> onset times ->
        `tempofit.find_grid_anchor`) and the pure `beat_grid`. Decodes via
        ffmpeg (`load_mono_audio`), not librosa's own loader, specifically so
        the same decode also feeds the peaks cache —
        `docs/01-architecture.md:111` documents `woodshed analyze <slug>` as
        producing tempo, grid offset **and** peaks together, and nothing in
        the plan's Group E bullet named a unit for the peaks half of that —
        picked up here rather than left as a second gap alongside D3's
        already-logged one. `confidence` is the grid anchor's own `on_beat`
        score. Verified against a synthetic click track with librosa actually
        installed (`uv run --extra analyze pytest`), not mocked: bpm recovered
        within 1 BPM.
      - **E4** (`3d1f6d6`) `tempofit.tap_tempo` (median of the last 8
        inter-tap intervals, 25% outlier rejection) and `woodshed analyze
        --bpm`/`--tap` (mutually exclusive), both forcing `confidence: null`.
        **Found and fixed a real gap while building this unit's named degrade
        test**: `manifest.Song.tempo` was a required field with a required
        `bpm`, so a song.yaml missing its `tempo:` block failed to parse —
        contradicting `docs/02-data-model.md:160`'s explicit "if tempo.bpm is
        0 or absent, the app still works." Every `Tempo` field now defaults
        and `Song.tempo` gets a `default_factory`, so "0" and "the key was
        never there" collapse to the one value `analyze.beat_grid` (and later
        `click.py`/the front end's bar ruler) already has to check.
      - **E3** (`fef1405`) `click.py`: `render_click(bpm, grid_offset_s,
        time_signature, duration_s)`, built directly against Woodshed's
        one-bpm-plus-one-offset grid rather than lifting the sibling's
        `Timeline` (per the plan's own instruction) — `_beat_times` duplicates
        `analyze.beat_grid`'s few lines rather than importing it, since
        `analyze.py` sits behind the heavy-dependency line and nothing below
        it may import that module at all, pure function or not. `_tick`,
        `_biquad_bandpass`, `_one_pole_lowpass`, `_guard`, `_db` lifted
        verbatim; the bandpass/lowpass pair is currently unused by
        `render_click` (they only ever voiced the sibling's drumstick
        count-in, which nothing in Woodshed's spec asks for) but kept
        exercised by their own tests per the plan's explicit lift list.
      - **Automated gate**: `uv run pytest` green (682, up from 620 at the
        end of Group D); `uv run ruff check .` clean; the librosa-uninstalled
        gate (`tests/test_tempofit.py` + `tests/test_click.py`, both provably
        pure) green under
        `uv run --no-project --with pytest --with numpy --with pyyaml --with
        pydantic pytest tests/test_tempofit.py tests/test_click.py`.
      - **Not yet done**: Group E's own manual gate ("detect the tempo of a
        real song, check the bar ruler lands on the downbeat by ear with the
        click on; step the shift from −1 to 0 and hear the pitch move") needs
        G1's bar ruler and F3's transpose stepper, neither built yet — that
        gate is Phase 1's, not Group E's alone, and is deferred to the end of
        the phase.

- [x] Group F — transpose and setlists, done directly, test-first, one unit
      per commit (continuing the same run/style as Group E). **Before
      starting**, re-checked the plan's own signatures against what Phase 0
      actually shipped: `manifest.py`'s B6 already built `Setlist`/
      `SetlistEntry`/`load_setlist`/`save_setlist` (the plan's "Tier 1"
      section had assigned those to this unit; they had moved), so F1's
      actual job narrowed to the operations layer over them. Also found:
      `practice.py` (readiness/cold-list/next-up) had no owning unit at all
      — B9's note flagged this and deferred it to "not needed until Phase 1
      Group F" — so it was built first, ahead of F1 proper, since F1's
      dashboard endpoint depends on it.
      - **F-prerequisite** (`32bbd72`) `practice.py`: `reached()`
        (`best_sustained_speed / target_speed`, clamped, section-level
        `reps_to_advance` overriding `song.practice`'s), `song_readiness()`
        (delegates to `sections.coverage_readiness` after filtering
        `counts_toward_readiness`), `is_cold()` (>14 days unpractised AND
        reached > 0.8 — never-practised and reached ≤ 0.8 both resolve
        false, per the "unlearned, not cold" rule), `next_up()`
        (docs/00-spec.md's `gap + cold + gig` score, components kept
        separate; `is_in_next_gig_setlist` resolved by comparing the given
        setlist's date against every other setlist on disk). 13 tests.
      - **F1** (`d2ef827`) `setlist.py`: create/load/save/delete a setlist
        file, `add_song`/`remove_song` (pure), `effective_shift()` (invariant
        4's arithmetic, clamped), `set_shift()` (the write path). Server
        gains `GET /api/setlists`, `GET /api/setlist/<slug>` (the dashboard
        payload: rows, next_up, weeks_to_gig, songs_at_target,
        needs_audio_count) and `POST /api/shift`. `GET /api/song/<slug>` now
        accepts `?setlist=` to resolve a real shift and carries `readiness`
        for real — both were documented placeholders in the C2 report,
        closed now that `setlist.py`/`practice.py` exist.
        `test_api_song_payload_has_lanes_and_ancestors` and
        `test_server_writes_nothing_else` updated to match (readiness now
        present; `setlists/<slug>.yaml` is CLAUDE.md's "setlist.yaml", not a
        literal filename — the assertion checked the directory, not the
        name). 28 new tests.
      - **F2** (`3236db0`) `web/screens/dashboard.js`: the real screen
        against `design/Dashboard.dc.html`, replacing D3's throw-stub. Setlist
        pills, the next-up hero card, the two stat cards, one row per song.
        Switching setlists is a per-viewer `localStorage` preference handled
        entirely client-side (no new route) — recorded as a scope decision
        in the module doc, along with the footer's two counts being limited
        to what F1's payload actually knows rather than a repo-wide song
        total. `app.js`'s `#/` route really fetches now.
      - **F3** (`ab9a317`) the transpose stepper persists: `app.js` exports
        `currentSetlist()`/`setCurrentSetlist()` (the shared per-viewer
        setlist preference), `song.js` and `practice.js`'s routes append
        `?setlist=`, and both screens' steppers `POST /api/shift` (debounced
        400ms) when a current setlist exists — session-only otherwise, same
        as before. `song.js`'s cluster goes from a static display to fully
        interactive (`-`/`+` buttons, `-`/`=` keys via `actions.js`'s global
        `on()`).
      - **Automated gate**: `uv run pytest` green (723, up from 682 at the
        end of Group E); `uv run ruff check .` clean; the librosa-uninstalled
        gate still green (unaffected by this group). No JS test framework in
        this repo (front end is the manual gate's job per CLAUDE.md's
        Testing section) — the four touched `web/*.js` files checked with
        `node --check`.
      - **Not yet done**: Phase 1's manual gate (deferred to the end of the
        phase, same as Group E's own — see that note above) now has both of
        its prerequisites close to hand: F3's stepper is done, G1's bar
        ruler is still outstanding.

- [x] Group G — grid and snapping, done directly, test-first, one unit per
      commit. **Before starting**, re-checked `web/timeline.js` against
      what Phase 0 shipped: `drawGrid` already existed, built ahead of a
      grid-computing function that didn't — G1 supplied the missing half
      rather than duplicating the drawing.
      - **G1** (`5f92263`) `timeline.computeGrid(tempo, durationS)` (bars/
        beats in source seconds, `{[], []}` with no tempo) and
        `snapToGrid` (a client-side mirror of `woodshed.sections.snap` —
        a network round trip per pointermove isn't an option). `song.js`
        and `practice.js` replace their Phase 0 placeholder grids (a
        fixed-pixel CSS gradient copied from the static artboard, which
        has no tempo to be accurate to) with a real canvas layer;
        `song.js`'s bar ruler now positions real bar numbers with `viewX`
        against `grid.bars` instead of evenly-spacing `barOf()` at
        arbitrary time marks. `sections.js`'s `attachDragHandlers` snaps a
        dragged edge live while `section.snapped` is `'beat'`/`'bar'`, and
        commits with `snapped: section.snapped` instead of a hardcoded
        `'free'` — `renderSections`/`attachDragHandlers` both gained a
        `grid` parameter (a documented signature change). `song.js`'s
        inspector gained a Snap (free/beat/bar) selector — the only way to
        ever set `snapped` away from `'free'` before this unit — and
        keys.js's new `[`/`]` (nudge_start/nudge_end) widen the selected
        section's boundary by a fixed 10ms, debounced like F3's shift.
      - **G2** (`12e10fd`) `Section.lead_in_beats` (docs/00-spec.md:98,
        missing from the model until now) + `manifest.
        effective_pre_roll_beats` (precedence only, in beats) +
        `clock.pre_roll_seconds` (beats -> source seconds, 0.0 with no
        tempo) + `clock.Render.pre_roll_every_pass` (`loop_start` moves to
        0; `loop_end`/`total` stay defined relative to it, so lap duration
        and total length are unaffected). Server gains `GET
        /api/click/<slug>/<section>?speed=&mode=lead_in|full` — the
        "mixing it in" `render_click`'s own docstring assigns to this unit
        — generated fresh at `bpm*speed` assuming the section boundary is
        grid-snapped (an unsnapped section's click won't agree with the
        music, named rather than silently wrong). `player.js`'s
        `loopStartFrame` honours `preRollEveryPass`; `practice.js` gains
        `playClick()`/`stopClick()` (a dedicated AudioContext + GainNode,
        started on engine load and section restart, honouring
        `preRollEveryPass` on the click's own loop points too) and fixes
        the pre-roll conversion's live divide-by-zero at `bpm 0`.
        **Found live while wiring this in**: a second, orphaned copy of
        the eager engine-load-at-mount-time bug `ensureEngine()` had
        already been written to fix (its own "FOUND LIVE 2026-09-06"
        note) had survived alongside it — a competing, never-resumed
        `AudioContext` created on every mount, racing the real lazy-loaded
        one. Removed.
      - **Automated gate**: `uv run pytest` green (739, up from 723 at the
        end of Group F); `uv run ruff check .` clean; the
        librosa-uninstalled gate still green. `node --check` on every
        touched `web/*.js` file (no JS test framework in this repo).
      - **Not yet done**: Phase 1's manual gate — now has all three of its
        prerequisites (G1's bar ruler, F3's transpose stepper, G2's
        audible click) in place, still deferred to the end of the phase
        per Paolo's own instruction (a human, once, at the actual
        machine).

- [x] Group H — capture, done directly, test-first, one unit per commit
      folded into a single commit (`b7ca8ce`) rather than split H1/H2/H3
      three ways: H3's overflow rule is inseparable from H1's own
      `bind_segments` contract (folding it in after the fact would mean
      writing `bind_segments` wrong first, on purpose, then fixing it),
      and H2/H3's doctor check are two small, tightly coupled pieces of
      the same device-half unit. **Before starting**, confirmed
      `pyaudiowpatch` is genuinely absent from this dev venv (checked
      directly), which shaped the scope decision below rather than being
      discovered partway through.
      - **H1**: `split_on_silence`, `bind_segments`, `Segment`/
        `TracklistEntry`/`Binding` -- pure, numpy only, tested with
        synthetic audio (13 tests). `Segment.overflowed` and
        `bind_segments`' unconditional refusal of an overflowed segment
        (H3's rule) are part of the type from the start.
      - **H2**: `list_devices`/`default_device`/`capture` (device half,
        gated behind `require_module("pyaudiowpatch", "capture")`, ring-
        buffered to disk during the live recording, per-chunk overflow
        tracking via `exception_on_overflow=True` rather than the
        silently-swallowing default) -- **unverified without real
        hardware**, named as such in the module doc, the same honesty the
        plan itself asks for ("the device half is one thin unverified
        call"). `woodshed capture "<title>"` (single-song, the documented
        fallback path) and `--queue <setlist>` (refuses cleanly, naming
        Phase 3's M1 -- Spotify import -- as the real missing
        prerequisite for a tracklist to match against, rather than
        silently no-op-ing or fabricating one).
      - **Found and fixed alongside this group**: `woodshed setlist` had
        no CLI subcommands at all -- `setlist.py` itself (F1) landed with
        the server routes only, and the CLI surface was left in
        `_NOT_YET_IMPLEMENTED` by oversight. Wired `list`/`create`/
        `add-song`/`rm-song`/`shift`, smoke-tested end to end against a
        scratch repo.
      - **H3**: `doctor.py` gains a loopback-open check (opens and closes
        the default WASAPI device, degrading -- not failing -- when
        `pyaudiowpatch` is absent, per docs/04-sources.md's "doctor checks
        the loopback opens and reports it"); "ring buffer to disk, never
        memory" is `capture()`'s own streaming design, not a separate
        piece.
      - **`web/screens/capture.js`**: real, but deliberately narrower than
        `design/Capture.dc.html` — that artboard is a LIVE session with no
        endpoint that could honestly back it this phase (no `/api/capture`
        in server.py's routes; a matched tracklist needs the same M1
        prerequisite `--queue` does). Shows the current setlist's
        `needs_audio` rows (F1's genuine data) with the exact capture
        command for each, rather than fabricated meters.
      - **Automated gate**: `uv run pytest` green (758, up from 739 at the
        end of Group G); `uv run ruff check .` clean; the full no-extras
        gate (`-m "not needs_rubberband and not needs_librosa and not
        needs_device"`) green. `node --check` on the one touched
        `web/*.js` file. Smoke-tested by hand against a scratch repo
        (`setlist create/add-song/shift/rm-song`, `capture`/`--queue`/
        `--list-devices` degrade paths) and `woodshed doctor` against the
        real repo.
      - **Not yet done, and cannot be from here**: the device half
        (`list_devices`/`default_device`/`capture`) has never run against
        real WASAPI loopback hardware. This is Group D's own deferred
        real-time-engine listen check's sibling gap, not a new one --
        both wait on time at the actual machine, Paolo's choice from
        earlier in this run.

---

## Phase 1 gate — CLOSED (Paolo, 2026-09-06)

Every unit above is built and its own automated checks are green. The
manual half ran at the actual machine: slowing down and speeding up sound
right, and the pitch shift is correct. **The metronome/click is
deliberately not being chased further** — some of these are live band
recordings without perfect timing underneath them, so a rough click is the
honest ceiling, not a bug to keep fixing; this is Paolo's own product call,
the same kind of "yours to make" judgement `docs/04-sources.md` already
makes explicit for capture. No further click/grid-accuracy work is queued
because of this gate — it passed.

Two small things were found and fixed the same day, after the gate, while
using the app for real rather than during a Group's own build: a hand-typed
`tuning: Eb` (not "Eb standard") in a real `setlists/*.yaml` blanked the
whole dashboard silently (load succeeded, the 400 only surfaced when
something later needed the shift, and `app.js`'s router has no `.catch`) —
fixed by validating `tuning` at the `Recording`/`Setlist` model itself
(same reasoning as `SetlistEntry`'s existing shift-range check) and by
turning every place a tuning name is typed (the dashboard's "new setlist"
form, `--tuning` on `add`/`capture`/`setlist create`) into a fixed choice
instead of free text, so the typo can't recur (`00976b0`, `361c75e`). And
the "Needs audio" stat card's own "BIND A FILE →" caption was never
actually a link — `#/capture` (H2) had no path to it anywhere in the UI
(`04d65b0`).

Phase 2 (the render cache, the ladder in the UI, the progress screen) can
start. **Phase 1.5, below, was inserted ahead of it** — four things Paolo
asked for after using the practice screen for real, sized and contracted
the same way every other phase in this document is, before implementation
starts in a fresh session.

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
| 7 | The repo is the database; the server writes exactly four things (Phase 1.5 amends this from three — see Group U) | UI and disk drifting apart |
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

## Phase 1.5 — a real transport, a real add-song path, and one isolated string

**Goal**: six things Paolo asked for after actually practising with the app, sized and
contracted the same way every other phase here is, so a fresh session can implement
against this document without re-deriving the design. Inserted ahead of Phase 2 because
none of it depends on the render cache, and two of the groups touch the same files
Phase 2's Group J will — see the ordering note at the end.

**Sixth thing, added 2026-09-06, after Groups O/P/R had already landed**: Paolo hit the
dashboard and found Capture reachable only through the "Needs audio" stat card — gone
if a setlist has nothing needing audio, and there is no door to it at all for a
brand-new, empty setlist. Fixed immediately (a small, uncontroversial nav-link addition,
already committed — see Group T's note below) — but raised a real, larger gap while
fixing it: **capture is currently one-song-at-a-time and tied to a song that already
exists** (H2's `woodshed capture "<title>"`, T2's planned `/api/capture/*`). Paolo's
actual workflow is the opposite order — record a whole set (ten songs, say) in one
pass with an empty setlist, THEN split the recording and name each piece, creating the
song entries from the split rather than the other way around. **Group U, below,** is
that — spec'd here, not yet built; see its own "Before starting" for what it changes
about CLAUDE.md's own invariants.

**Before starting**: this phase requires companion edits to three files outside
`.agent_session/`, already made alongside this plan update, not left for the
implementer to discover:
- `CLAUDE.md`'s Layering section now names `separate.py` as a third module (beside
  `analyze.py` and `render.py`) allowed a heavy/external dependency.
- `docs/00-spec.md`'s "It does not edit audio" bullet now scopes the stem-separation
  prohibition to mixing/EQ, carving out guitar-only isolation by name.
- `docs/07-roadmap.md`'s "later, if it earns it" list no longer carries the demucs-stem
  bullet — it points here instead.
Re-read `docs/03-audio-engine.md`'s "What good enough sounds like" section before Group
S: it already names the destination ("an isolated guitar track... is what `demucs` in
`rambass-live` already does"), Group S is only automating and caching that, in-app.

**Seventh thing, found 2026-09-06 during Paolo's own first hands-on review of T1/T2/S1**:
a song bound with no sections had no way to Practice at all -- `full_song: true` existed
as a field but was documented (docs/02-data-model.md) as something a person creates by
hand, and nothing auto-created one. Paolo's call: every song should be practiceable the
moment it exists. Fixed at the root, not per-caller: `manifest.whole_song_section
(duration_s) -> Section` (`id: whole-song`, spanning `[0, duration_s]`, `full_song: true`,
`target_speed: 100` — matching the doc's own example exactly) is now included by
`cli.py`'s `bind_song_file` (T1's shared binding function) and `capture.py`'s
`bind_segment_to_song`/`bind_segment_as_new_song` (Group U) — every path that writes a
fresh `song.yaml` gets one, CLI or browser. Still an ordinary section afterward: `woodshed
section rm whole-song` (or the inspector's delete) removes it like any other. Several
existing `test_cli.py` tests that assumed a freshly-added song started with zero sections
were updated to account for the new default entry (`git blame` on this commit finds them);
two that manually created their OWN "Whole song"/`[0, duration]` section for an unrelated
reason (testing `--full-song`/`--lead-in-beats` round-tripping) now use a different name
and span so they don't collide with the auto-created one on id or exact-duplicate-span.
2 new `test_manifest.py` tests for `whole_song_section` itself; existing bind-function
tests in `test_capture.py`/`test_server.py` gained an assertion that the default section
is present. Full suite 894 passed (was 892); ruff clean; no-extras gate 891 passed, 3
deselected.

**Eighth thing, found 2026-09-06, same review pass — five small UI bugs in `song.js`/
`sections.js`/`timeline.js`, none Python-side, no new tests (matching this repo's own
"no JS framework" convention outside P1/R1's two narrow exceptions — verified by
`node --check` and reasoning, not a new harness)**:
- **Empty-lane affordance.** The empty lane strip `attachCreateHandler` (`sections.js`)
  owns had no visual sign it was an active drop zone — same cursor as dead space. It now
  sets `cursor: crosshair` on `laneRoot` for as long as the handler is attached (a section
  tile's own `cursor: pointer`, from `buildTile`, still wins on hover since it is the more
  specific element) plus a faint accent tint on pointerenter/leave. Fixing this surfaced a
  real, pre-existing bug: `song.js`'s `renderLanes()` called `attachCreateHandler` on
  every redraw without ever detaching the previous call's listeners — harmless while the
  only listener was a plain pointerdown, but a growing pile of never-removed
  pointerenter/leave handlers is not something to leave now that it is visible. Fixed by
  having `renderLanes` store and call the previous detach before attaching again, and
  `unmount()` now detaches on the way out too.
- **Live speed change while previewing.** `song.js`'s rung buttons only updated the local
  `previewSpeed` variable a FUTURE press would read — a mid-playback click did nothing
  audible. `practice.js`'s own speed slider already established
  `engine.setSpeedPct()` takes effect immediately (its own "FOUND LIVE 2026-09-05" note);
  `song.js` just never called it. One line: `if (engineReady) engine.setSpeedPct(previewSpeed)`
  on rung click.
- **Live pitch change while previewing** — the identical bug, reported separately once
  Paolo tried the transpose `−`/`+` buttons during playback: `bumpShift` updated `shift`,
  re-rendered the number, and persisted it, but never touched the live engine.
  `practice.js`'s transpose handlers already call `engine.setSemitones(shift)` on every
  press; `song.js`'s own transpose cluster (Phase 1, F3) predates this screen having an
  engine at all (R1, added later) and was never revisited once it got one. Same one-line
  fix, same place `bumpShift` already does everything else a press should do.
- **No playhead during preview.** Named in `song.js`'s own module doc as a still-open D4
  gap (`RealtimeEngine` has no real position accessor) — Paolo's actual need ("I want to
  see where playback is so I know it needs to end here") doesn't require a true
  audio-graph read, so this is a COSMETIC wall-clock estimate, the same kind
  `practice.js`'s own ring/waveform already uses. Unlike that one (a single elapsed*speed
  multiplication, fine for a value that resets every lap), this is a per-frame
  INTEGRATION (`playheadTick`, added each frame's own real-time delta times the speed
  ACTIVE during that frame) — deliberately, so a mid-playback speed change (the fix above)
  reshapes only time from that point forward, matching the same distortion the practice.js
  note already warns a naive recompute causes. Hidden (`display:none`) whenever nothing is
  playing; positioned via the same `viewX`/`view()` mapping the selection box already uses.
- **Dense beat/bar grid lines read as solid stripes.** A song with no real tempo analysed
  yet still has SOME `tempo.bpm` (`cli.py`'s placeholder default, 120) — `computeGrid` has
  no way to know that is a guess, so at whole-song zoom it can return several hundred beat
  marks landing closer together than a device pixel, which reads as broken rendering, not
  a grid (this is what the screenshot Paolo sent actually showed — not a waveform bug, no
  peaks were loaded at all; the "waveform" in that image was 100% grid lines). `timeline.
  drawGrid` now skips a WHOLE TIER of marks (beats, or separately bars) once consecutive
  marks would land closer than `MIN_BEAT_PX`(4)/`MIN_BAR_PX`(2) apart — never a decimated
  subset, since a beat line every 3rd beat is not a beat grid, it is a wrong one.

### Work units

**Group O — design first (∥ with nothing; everything else in this phase reads it)**
- **O1** Using the `design` skill, produce/update the artboards this phase's screens are
  built against — the same ground-truth role `design/Main.dc.html`,
  `design/SongPage.dc.html` etc. played for Phase 0. Concretely: `design/Main.dc.html`
  gains (a) an icon inside each of the six foot chips, right-aligned and vertically
  centred against the existing label, and (b) a "Guitar only" toggle placed beside the
  transpose stepper in the header (not a seventh foot chip — the six-chip rule in "Rules
  the brief states as prohibitions" is unchanged) with its progress state (isolating /
  ready) designed as a real state, not an afterthought. A new `design/AddSong.dc.html`
  (or an added state on `design/Dashboard.dc.html` — implementer's call, whichever the
  design skill produces more coherently) covers the real add-song form: file picker,
  title/artist fields, the tuning **dropdown** (Phase 1's fixed-choice convention, not
  free text — see `web/screens/dashboard.js`'s `createSetlistFormHtml`), and a capture
  variant (arm / level meter / elapsed timer / stop). Sign off with Paolo before P–T
  build against it, matching how every existing screen's ground truth was fixed before
  its D-unit implemented it.
  - **Done, 2026-09-06, awaiting sign-off.** Working files in `design/` were confirmed
    byte-identical to the published canvas before editing (canvas.json only differed by
    pretty-printing — semantically equal), so this was a direct edit-and-republish, not
    a fresh seed. Changes: `Main.dc.html` gained six inline SVG foot-chip icons (filled
    play/skip-forward/skip-back triangles for CC80–82; stacked chevron-up/down for
    faster/slower CC83–84; a distinct undo-arc, deliberately unlike the skip-back
    triangle, for retract CC85) plus a "Guitar only" toggle beside the Shift stepper
    (OFF shown as the artboard's default state) with a small labelled strip showing the
    other two real states — two honest stages ("Isolating guitar… 1/2", "Rendering…
    2/2") and "Ready — instant" — never a fabricated smooth percentage. New
    `design/AddSong.dc.html` (chose a new artboard over a Dashboard state, since the
    two flows — file bind vs. live capture — read better side by side than as toggled
    states of one screen): left panel is the file-based form (styled file-picker shown
    in its chosen-file state, title/artist fields, a styled tuning dropdown-look control
    naming all six fixed choices), right panel is the live-capture variant (arm ring
    shown recording, elapsed timer, L/R level meters, Stop) with a caption noting the
    pre-start idle-arm state it's not currently showing. `Capture.dc.html` gained a
    small idle-arm control next to the existing "● RECORDING" badge, additive — the
    existing tracklist/queue view stays as the future (Phase 3 M1-dependent) multi-song
    state; T2's actual single-song capture is the idle/recording pair alone. `canvas.json`
    gained the `AddSong.dc.html` artboard entry and a `note-addsong` annotation.
    Seeded, `--check`ed clean, republished to the existing canvas
    (`https://claude.ai/code/artifact/618bb99d-abcd-4827-a11d-413c6dc3a6c8`) rather than
    a new one. A background content-consistency pass (against the working files, not the
    seeded output) found six real drifts from the established system, all fixed and
    republished same session: the three "Guitar only" state chips had gained a border
    and three off-palette colours not used anywhere else in the canvas (no badge in
    `Dashboard.dc.html`/`Components.dc.html` carries a border) — replaced with the exact,
    borderless colour pairs `Components.dc.html`'s own "+2 s · confirm" and "at target"
    badges already use, and dropped the mini toggle-track illustration that was the
    source of the new colours; `Capture.dc.html`'s new idle-arm control was a 20px pill,
    the only fully-round container shape in the system (everything else is a 3–5px
    rounded rectangle) — squared to 5px; its "IDLE — PRESS TO ARM" label sat at 11px
    beside "● RECORDING" at 13px despite the two being shown as a directly-comparable
    pair — matched to 13px; `AddSong.dc.html`'s own "● RECORDING" used `.1em` letter-
    spacing against `Capture.dc.html`'s `.08em` for the identical string — matched; and
    its "Change file" button read `#9CAAA4` against `Components.dc.html`'s analogous
    "Bind file" outline button at `#E8EEEB` — matched. **Signed off: 2026-09-06, by
    Paolo** — P–T's UI-facing halves are unblocked.

**Group P — waveform click-to-seek**
- **P1 — done, 2026-09-06 (engine half only; P2's UI wiring stays blocked on
  Group O's sign-off).** `worklet.js` gains a `{type:'seek', frame}` message,
  handled like `restart` (`rb_reset`, re-feed the warm-up pad) but without
  forcing `playing` true and without posting a `boundary` itself; the frame
  is clamped authoritatively against `sourceLen` there, since the worklet
  is the one thing that actually knows it. `player.js`'s `RealtimeEngine`
  gains `seek(sourceSeconds)` — takes an absolute `SourceSeconds` (position
  in the original file, same clock `section.startS`/`endS` are in),
  converts it to a frame offset within the loaded slice using the
  `rawStartFrame`/sample-rate bookkeeping `loadSection` already computes
  (now stored as `_sliceStartS`/`_sliceEndS`/`_sampleRate`), clamps to the
  loaded slice's own bounds (documented judgement call: the plan's "[0,
  sectionDuration]" is read as the loaded slice's span, since "source
  seconds" is unambiguously absolute per CLAUDE.md's clock invariant and a
  literal `[0, ...]` range would contradict that), and disqualifies the
  in-flight lap exactly like `pause()` — **unconditionally**, regardless of
  how close the seek lands to the true beginning, per the module doc's
  pass-detection contract reading "no seek ... in between" as independent
  of the "started within 250ms" clause. No change was needed to
  `_onBoundary()` itself — the existing disqualify/requalify state machine
  already generalizes from "after a pause" to "after any disqualifying
  event," so re-qualification on the next natural wrap falls out for free.
  `PASS_START_TOLERANCE_S` is still not read in a numeric comparison
  anywhere; its comment now explains why, rather than staying stale.
  Test-first, in `web/tests/test_seek.mjs` — a synthetic-worklet test (a
  fake `_node` standing in for the real `AudioWorkletNode`, since the thing
  actually at risk lives entirely in `RealtimeEngine`'s own bookkeeping, not
  the WASM DSP), run as a plain `node` script rather than via a framework.
  This is a deliberate, narrow, one-unit exception to the "no JS test
  framework" choice Group D made and CLAUDE.md's Testing section documents
  — made because the plan itself flags this unit's interaction as one of
  the two highest-risk in the phase. 8 assertions, all passing; `node
  --check` clean on both touched files; `uv run pytest` (789) and the
  no-extras gate (786) unaffected.
- **P2** `screens/practice.js` wires a pointerdown/click handler on the section
  waveform's container: click position → fraction of the visible window (same view math
  the ring/playhead already use) → source seconds → `engine.seek(...)`. The local
  progress estimate (decision 3 in this file's own module doc — "reset to an exact 0 at
  every authoritative sync point") gains a new sync point here, alongside `'pass'`: a
  seek resets the wall-clock estimate to the clicked position immediately, not on the
  next frame.
  *Test contract*: clicking mid-waveform moves the playhead to the clicked fraction and
  does not itself count a rep.

**Group Q — chip icons**
- **Q1** A small self-hosted icon set (inline SVG, matching this repo's "no network at
  runtime" rule — same reason the fonts are vendored, not a CDN icon font) for the six
  foot actions: play, pause (swapped live on the engine's actual playing state, not a
  static play glyph), next section, previous section, faster, slower, retract. Update the
  chip markup (`footEl.innerHTML` in `screens/practice.js`) to place the icon right-
  aligned and vertically centred against the label, per Group O's artboard. A
  completeness test — every `ACTIONS` entry with a foot `cc` has a matching icon —
  belongs beside `actions.js`'s existing "one action table" discipline: an icon silently
  missing for a real foot action is the same class of bug as a missing CC.
  *Test contract*: `node --check`; the icon-completeness assertion above.

**Group R — the song page gets a real preview**
- **R1 — done, 2026-09-06.** Took the `loop: false` option (not a second player):
  `worklet.js`'s `_feedSource` now branches on `this.loop` (default true, unchanged for
  practice.js) — `false` flushes Rubber Band once (`rb_process(..., final=1)`, the one
  call site that ever passes it) instead of wrapping, sets `this.ended`, and never posts
  `boundary`; `process()` drains what's left and posts `{type:'ended'}` exactly once,
  gating `playing` false the same way `pause` does. `restart`/`seek` both clear
  `ended`/`endedPosted` so a non-looping source can be replayed or seeked back into
  range. `player.js`'s `RealtimeEngine.loadSection` threads `section.loop` through;
  its port-message handling was pulled out into a named `_onWorkletMessage(msg, onReady)`
  method specifically so it could be driven directly in a synthetic-worklet test without
  a real node (same technique P1's `test_seek.mjs` already used for `_onBoundary`) —
  `'ended'` dispatches a `CustomEvent('ended', ...)` and never touches
  `_qualified`/`_onBoundary`, so a `loop: false` section cannot produce `'pass'` by a
  missing check, because there is no check to miss.
  `screens/song.js`'s transport now creates one engine lazily on first press (same
  suspended-AudioContext-needs-a-user-gesture reasoning as practice.js's
  `ensureEngine`), but — unlike practice.js, which owns one section for its whole
  mount — **reloads the section on every press**, since the selected lane tile can
  change between presses: `playPreview()` loads whatever `selectedId` currently is (or
  the whole recording, unselected) at `previewSpeed`/`shift`, `loop: false`, and plays
  it once; `'ended'` resets the transport icon. A found-and-fixed race: pausing while a
  fresh `loadSection` is still in flight would throw (`engine._node` is briefly torn
  down between loads) — `playPreview`'s own `if (!transportPlaying) return` checks
  (before and after its `await`) already abort the load correctly, so only the throw
  itself needed swallowing in the click handler. `engine.destroy()` added to `unmount()`.
  Deliberately not wired: a live playhead/elapsed-time readout — `RealtimeEngine` has no
  position accessor (D6's own flagged gap on practice.js, still open, not this unit's
  job) — so the transport clock stays its existing static total-duration display.
  Test-first, in `web/tests/test_ended.mjs`: a synthetic-worklet test (same technique as
  `test_seek.mjs`) asserting `'ended'` fires and `'pass'` never does, that `'ended'`
  doesn't touch `_qualified`, and that `'boundary'`/`'ready'` still route correctly
  through the same dispatch table — plus the test contract's other half enforced as a
  permanent lint-style check (the same pattern the plan's own Phase 2 J2 bullet already
  proposes for `playbackRate`): a regex assertion that `screens/song.js`'s source never
  calls `addEventListener('pass', ...)` and never contains `/api/rep`. 5 assertions, all
  passing; `node --check` clean on every touched file; `uv run pytest` (789, unaffected)
  and the no-extras gate (786, unaffected) still green.

**Group S — guitar-only isolation (∥ with T)**

**Flagged, 2026-09-06, before building any of S2/S4's evict-touching half**:
this group's own goal line says Phase 1.5 was "inserted ahead of Phase 2
because none of it depends on the render cache" — true for O/P/Q/R/T/U, but
**not true for S2/S4**. `render.py` (`render_section`, `span_fingerprint`,
`cache_path`) is Phase 2 Group I's own deliverable and does not exist yet
(confirmed: no `src/woodshed/render.py`, no `/api/render` route, Phase 2's
Group I carries no "done" annotation) — so S2 ("`render.py`'s `render_section`
gains a `source` parameter") has nothing to extend, and S4's "`evict()` is
extended to also walk `cache/stems/`" has no `evict()` to extend either (also
Group I3, Phase 2). This is a real phase-ordering gap in the plan, not an
implementation detail — S1 (`separate.py`/`isolate_guitar` itself, which only
needs `ffmpeg` + a section's own start/end, no render cache) is independent
and buildable now; S2/S4 are not, until Phase 2 Group I lands first (either
build Group I out of its documented order, or defer S2/S4 until it does).
Raised rather than silently worked around, same as this plan's other
architectural calls get raised for Paolo rather than asserted.
- **S1** `src/woodshed/separate.py` (Demucs, optional heavy dependency — the third
  module CLAUDE.md's Layering section now names, beside `analyze.py`/`render.py`).
  ```python
  def demucs_available() -> bool
  def stem_fingerprint(song, section, *, pre_roll_s) -> str
      # same shape as render.span_fingerprint, minus crossfade_ms/speed/semitones
      # (isolation happens BEFORE the speed/pitch stretch, not after) plus a
      # SEPARATOR_VERSION constant, bumped by hand when the demucs invocation changes
  def stem_cache_path(repo, slug, section_id, fp) -> Path
      # cache/stems/<section_id>-guitar-<fp>.flac -- a NEW cache subdirectory,
      # still "always safe to delete", still owed to Group I's evict() (see the
      # note on I below)
  def isolate_guitar(repo, song, section, *, pre_roll_s, model="htdemucs_6s",
                     device=None, force=False) -> Path
      # ffmpeg-cuts [start - pre_roll, end] (the same span render.render_section
      # cuts) THEN runs demucs --two-stems guitar -n htdemucs_6s on that clip only
      # -- not the whole song; a section is usually far shorter, and
      # htdemucs_6s is the only Demucs model with a guitar stem at all (the
      # default htdemucs/htdemucs_ft four-stem split has no guitar output --
      # confirmed against rambass-live/src/rambass/stems.py's own MODELS list
      # and its own reasoning comment). Keeps the "guitar" file, discards
      # "no_guitar", writes FLAC, skips the work when force=False and the
      # fingerprinted cache file already exists (same skip-if-cached rule as
      # render_section).
  ```
  Cross-reference, never copy, per CLAUDE.md's rule for the sibling repos: the demucs
  invocation (model list, `--two-stems`, `-j`/`-d` flags, the flatten-output-then-
  `shutil.rmtree` cleanup) mirrors `rambass-live/src/rambass/stems.py`'s `separate()` —
  read it for the subprocess shape, don't import it (no cross-repo import exists
  anywhere in this codebase and this is not the place to start). The `separate` extra
  name (`pyproject.toml`, `demucs>=4.0.1`) is deliberately the same name
  `rambass-live/pyproject.toml` uses for the same dependency.
  - **Done, 2026-09-06.** Built exactly as specced, with one deliberate simplification
    from the sibling's own shape, named rather than silently diverged from: `rambass-
    live`'s `separate()` looks for a `demucs` console script on PATH first, falling
    back to `python -m demucs`; here, `require_module("demucs", "separate")` already
    confirms the package imports in THIS interpreter before anything runs, so the
    subprocess always goes through `sys.executable -m demucs` -- one invocation path,
    not two, because the fallback is the only one that check can actually promise
    works. No `-j`/`shifts`/`overlap` flags either -- those tune quality/speed
    trade-offs `rambass-live`'s CLI exposes as options; nothing here calls for that
    yet, and they can be added the day something does. `stem_fingerprint`/
    `stem_cache_path` match `render.py`'s own future scheme exactly (Phase 2, still
    not built) -- `songs/<slug>/cache/stems/<section_id>-guitar-<fp>.flac`, confirmed
    against `docs/01-architecture.md`'s disk-layout tree (updated alongside this, same
    "companion doc edits made now" pattern Group S's own "Before starting" note
    already used elsewhere in this phase). `isolate_guitar` skips its own work up
    front (`force=False` and the cache file exists -- zero subprocess calls, asserted
    directly in the test) before ever checking `demucs_available` or touching a
    source file, so a fully-cached section never even requires Demucs to be
    installed. 19 new tests, all subprocess/import mocked (fake `demucs` module
    installed into `sys.modules`, same technique T2's `capture.py` tests used for
    `pyaudiowpatch`) -- no real Demucs invocation anywhere in this suite, and no
    `needs_demucs`-marked test yet either (nothing here justified the extra
    complexity of a genuine end-to-end run; S4, not yet built, is where that marker
    was specced to matter). Full suite 892 passed (was 873); ruff clean; no-extras
    gate 889 passed, 3 deselected (unaffected -- `separate.py` needs no heavy
    dependency to pass its own tests, only to actually isolate real audio).
- **S2** `render.py`'s `render_section` gains a `source: Literal["mix", "guitar"] = "mix"`
  parameter, threaded through `span_fingerprint`/`cache_key`/`cache_path` (so the two
  variants get different cache filenames, e.g. `solo@60x-1st-mix-3f9a2c11.flac` vs
  `...-guitar-...`, never collide, and a guitar-only render survives a fingerprint change
  exactly like a mix render does). `source="guitar"` calls `separate.isolate_guitar(...)`
  first (itself cached) and feeds *that* file into the existing rubberband/crossfade
  pipeline instead of the original recording.
  `GET /api/render/<slug>/<section>` gains `?source=guitar` (default `mix`, so every
  existing caller is unaffected). The 202 "not built yet" response gains a coarse
  `"stage": "separating" | "rendering"` field when `source=guitar` — **not** a fabricated
  smooth percentage. Demucs has no natural fine-grained progress readout and inventing
  one is exactly the failure mode CLAUDE.md spends a page on ("the tool never judges...
  inventing a measurement it cannot make" — same instinct, different measurement). Two
  honest stages is what there actually is to report.
  *Test contract*: mirrors `render.py`'s existing suite — argv asserted, demucs
  subprocess mocked; moving the section boundary misses BOTH the isolated-stem cache and
  the render cache; `source="mix"` behaviour is provably unchanged (the whole existing
  `test_render.py` suite still passes with the new parameter defaulted).
- **S3** `screens/practice.js` gains a "Guitar only" toggle (Group O's header placement,
  not a seventh foot chip). On: request `?source=guitar`; while the first isolation for
  this section is in flight, show Group O's progress state (the two honest stages from
  S2, not a bar with invented granularity); cached thereafter, so toggling off and back
  on is instant. Degrades — disabled, with a message naming the fix — when `doctor`
  reports `demucs` missing, the same `require_module` pattern `pyaudiowpatch`/`librosa`
  already use.
- **S4** `doctor.py` gains a `demucs` check (installed? — and a CPU-only note, since
  Demucs on CPU is genuinely slow; `rambass-live`'s own doc already measures
  `htdemucs_ft` at roughly 4× `htdemucs`'s time, worth surfacing rather than letting the
  first isolation feel like a hang). `evict()` (Group I) is extended to also walk
  `cache/stems/` — a new, potentially large cache directory the 20 GB budget doctor
  check must actually see, or "cache: X GB used of a 20 GB budget" quietly stops being
  true.
  A new pytest marker, `needs_demucs`, joins `needs_rubberband`/`needs_librosa`/
  `needs_device` in `pyproject.toml`; the one real end-to-end isolation test is marked
  with it and excluded from the no-extras gate, same as the other three.

**Group T — add a song for real (∥ with S)**
- **T1** `POST /api/song/upload` (multipart) — the file-binding half of `cli.py`'s
  `cmd_add` (hash, duration via the already-doctor-checked `ffprobe`, write
  `songs/<slug>/audio/<file>` + a minimal `song.yaml`), reachable from a browser for the
  first time. Refactor the binding logic into one function `cli.py`'s `cmd_add` and this
  endpoint both call — CLAUDE.md's "one action table" instinct extends naturally to "one
  binding function", not a second copy that can drift. Replaces `dashboard.js`'s current
  "Add song" text field (which only ever produced a `needs_audio` placeholder row,
  never actually bound a file) with Group O's real form: file picker, title/artist, the
  tuning **dropdown**.
  - **Done, 2026-09-06.** `cli.py` gained `bind_song_file(repo, source, *, title, artist,
    album, tuning, slug, bpm, grid_offset_s, time_signature, dest_filename)` -- the
    refactored binding logic, byte-identical behaviour to the old inline `cmd_add` body
    (same slug-collision/missing-file refusals, same `_read_duration_s` dispatch on
    `.wav` vs. ffprobe); `cmd_add` is now four lines calling it. `dest_filename` is the
    signature addition beyond the plan text: the server's own copy of the uploaded
    bytes lives in a generated temp file, so `source.name` there is never the browser's
    real filename. `server.py` gained a hand-rolled `parse_multipart` (stdlib only --
    `cgi.FieldStorage` is gone as of 3.13, so this is the forward-compatible choice, not
    a shortcut) and `POST /api/song/upload`, which is NOT JSON -- `do_POST` special-
    cases that one path to read the raw body itself before the generic `self._body()`
    JSON parse would consume it. The uploaded filename is run through `Path(...).name`
    before ever touching `songs/<slug>/audio/`, so a crafted `filename` can't smuggle a
    directory component. `dashboard.js`'s "Add song" is a real form now (file input +
    title/artist + the tuning dropdown, matching `design/AddSong.dc.html`'s "from a
    file" panel; its "or capture live" panel stays T2's job) -- `POST /api/song/upload`
    (via a new `app.js` `postForm` helper, since this is the one route in the app that
    isn't JSON) followed by the existing `POST /api/setlist/<slug>/songs` to add the
    freshly-bound slug to the current setlist, two calls rather than folding setlist
    membership into the upload endpoint's own contract. 4 new `cli.py` tests
    (`bind_song_file` exercised directly), 5 new `server.py` tests (`parse_multipart` +
    upload success/refusal), `test_server_writes_nothing_else` extended with one upload
    call. `node --check` clean on `app.js`/`dashboard.js`; no dedicated JS test added for
    the DOM-heavy form itself (no precedent in this file -- `dashboard.js` has never had
    one), consistent with how this repo's DOM-heavy screens are tested elsewhere. Full
    suite 856 passed (was 847); ruff clean; no-extras gate 853 passed, 3 deselected.
- **T2** `POST /api/capture/start` / `GET /api/capture/status` / `POST /api/capture/stop`
  — runs `capture.py`'s `capture()` generator (which already accepts an `on_level`
  callback for exactly this, unused until now) on a background thread, one capture at a
  time (a loopback device has one reader; a second `start` while one is running is
  refused, not queued). `status` reports elapsed time, current level, and whether an
  overflow was seen (H3's existing `Segment.overflowed` rule — refuse to bind a segment
  that reported one, same as the CLI path); `stop` finalizes the in-progress segment the
  same way the silence-detector would. This is the endpoint `capture.js`'s own module doc
  named as the reason its live meters couldn't be built honestly yet ("no `/api/capture`
  in server.py's routes... building the live meters against nothing would be exactly the
  failure mode CLAUDE.md spends a page on") — it now can be, because it is backed by a
  real running capture, not a fabricated one.
  `screens/capture.js` gains Group O's live variant (arm / level meter / elapsed timer /
  stop) and falls back to the current copy-paste command list when `doctor` reports
  `pyaudiowpatch` missing.
  *Test contract*: a second `start` while one is running is refused, not silently
  dropped or queued; the status shape is asserted; the background-thread wrapper is
  tested against a mocked `capture()` generator — never a real device in this suite.
  **T2 is incomplete as written** — it never says how a finished segment actually
  becomes the target song's bound audio. Group U's `bind_segment_to_song` (U1, below)
  is that missing step; T2 and U share it rather than each inventing a copy.
  - **Done, 2026-09-06.** A second, real gap found and fixed the same way U1's was:
    `capture()`'s `while True:` loop had NO way to stop from another thread at all --
    only `KeyboardInterrupt`, which a background thread never receives (Ctrl-C is
    main-thread-only), so a server-run capture could never actually be stopped as
    specced. Fixed at the root: `capture()` gained `stop_event: threading.Event | None`
    (checked once per ~100ms chunk, `None` preserving the CLI's original Ctrl-C-only
    behaviour exactly) and a symmetrical `on_overflow: Callable[[], None] | None`
    (T2's own "whether an overflow was seen" needs to be live, not only baked into a
    finished `Segment.overflowed` after the fact). Both are exercised in
    `test_capture.py` against a FAKE `pyaudiowpatch` module installed into
    `sys.modules` -- still no real hardware, but the loop's own control flow (does a
    stop request actually stop it, does the overflow callback actually fire) is now
    tested rather than merely asserted in a docstring.
    New module `capture_runner.py`'s `CaptureRunner` is the background-thread wrapper
    itself: `start()`/`stop()`/`status()`, one shared instance per server (a class
    attribute on the dynamic handler subclass, same binding trick `repo` already
    uses). `stop()` blocks until the thread actually finishes, so its response can
    honestly say the resulting segments are already visible via `GET /api/capture/
    segments` -- and it IS the same bridge into Group U's bookkeeping the plan
    expected: once segments exist, `capture_session.start_session` is called directly
    (not `bind_segment_to_song`, since nothing has a target song yet in this
    capture-first order -- U2's own bind endpoints are what a human uses afterward).
    A capture that yields zero segments (stopped instantly, or pure silence) deletes
    its own raw file rather than leaving uncatalogued debris under `capture/`, since
    nothing exists to bind or discard it later.
    Tested exactly per the plan's own contract, mocking `capture_runner.capture`
    itself (never a real device): idle/running/error status shapes, a second `start`
    refused while one runs, `stop` refused with nothing running, a background
    exception surfacing via `status().error` without crashing the thread and without
    blocking a fresh `start` afterward. `GET /api/capture/status` also carries an
    `available` flag (`importlib.util.find_spec`, the same non-importing technique
    `doctor.py`'s own loopback check already uses) -- `capture.js` reads this BEFORE
    ever offering the arm button, not only after a failed `start`.
    `screens/capture.js`'s live panel: arm -> running (elapsed timer, level bar,
    overflow note, Stop) -> stopped (a real segment count from `GET /api/capture/
    segments`, not a placeholder -- explicitly notes that naming/adjusting those
    segments is U3's still-unbuilt job, rather than pretending to do it). Falls back
    to a plain note, no arm button, when `available` is false. A found-and-fixed race,
    same class as R1's own: `mount()` returns its unmount callback synchronously but
    the live panel's own cleanup closure does not exist until its first `await`
    resolves -- closed with a `{current: bool}` box `mount()` flips immediately,
    checked before the panel's poll loop ever starts. The existing needs-audio
    terminal-command list (H2) is unchanged, shown unconditionally below the live
    panel. `node --check` clean; no dedicated JS test added for the DOM-heavy panel
    itself, same precedent as T1's `dashboard.js` change.
    13 new Python tests (4 `capture.py`, 9 `capture_runner.py`) + 6 new `server.py`
    tests (start/status/stop, refusal cases) + `test_server_writes_nothing_else`
    extended with a zero-segment start/stop cycle. Full suite 873 passed (was 856);
    ruff clean; no-extras gate 870 passed, 3 deselected.

**Group U — capture-first: split now, name later** (spec'd 2026-09-06, not yet built —
see this phase's own "sixth thing" note above for why). Paolo's actual workflow:
capture a whole set with an empty setlist, THEN split and name each piece — the reverse
of T2's "song already exists, needs only its audio" order. `capture.py`'s `capture()`
already yields one `Segment` per detected track from a single continuous recording
(H1/H2, Phase 1) — nothing about the device/silence-splitting half needs to change;
what's missing is everything **after** a segment exists and before it is a bound song.
- **U0** (design — Group O extension, same sign-off discipline as every other screen)
  A new `Capture.dc.html` state: a list of the raw, not-yet-bound segments from the most
  recent capture (index, duration, an overflow badge when H3's flag is set), each with a
  play/preview control and an inline title/artist/tuning form, plus Add-to-setlist and
  Discard actions per row. Not covered by the existing artboard (which depicts a
  tracklist MATCHED by duration — Phase 3's M1 dependency) or by `AddSong.dc.html`'s two
  states (one song at a time, title known up front) — this is genuinely a third shape:
  N unnamed segments, named after the fact.
  - **Scope addition, 2026-09-06, raised by Paolo before sign-off**: the automatic
    silence-splitter can get a cut wrong in both directions — a quiet passage inside a
    song crossing the floor and over-splitting one song into two segments, or too short a
    gap between two songs under-splitting them into one — and U0 as drafted had no way to
    catch or fix either before naming. Resolved (Paolo's pick, of three sketched
    directions) as ONE combined screen, not a separate "adjust splits" step: the whole
    pass's own waveform leads the page, drawn once as a single strip with every segment's
    span highlighted over it (an interactive version of `Capture.dc.html`'s existing "the
    pass so far" element) — a draggable handle at each boundary to nudge a cut, a "merge"
    button at each boundary for the over-split case, a "+ split at playhead" action for
    the under-split case, and a scrubbable playhead (reusing `RealtimeEngine.seek()`, P1,
    already built) to preview around a boundary before deciding. The per-segment naming
    cards sit below, unchanged, and always act on whatever the cuts currently are.
    **This is a real, not-yet-built backend surface**: `capture_session.py` (U2) today can
    only mark a pending entry bound or discarded — nothing lets a caller adjust a segment's
    start/end frame, merge two adjacent entries into one, or split one entry into two.
    U1/U2 as already built do NOT cover this; three new operations are needed (working
    names: `adjust_boundary`, `merge_segments`, `split_segment`, all in `capture_session.py`,
    all needing new `/api/capture/*` endpoints) before U3 can wire the real screen — this is
    additional scope beyond what U1/U2's "done" notes below cover, not something either
    already secretly handles.
    Drafted in `design/CaptureReview.dc.html` (a new sibling artboard, same "two flows read
    better side by side" reasoning O1 already used for `AddSong.dc.html`, not a toggled
    `Capture.dc.html` state) and republished to the existing canvas. One thing drafted but
    deliberately left open, not resolved here: every row only creates a brand-new song —
    there is no picker for matching a segment to an existing needs-audio slug already in
    the setlist, which `bind_segment_to_song` (below) also supports. **Signed off:
    2026-09-06, by Paolo** — U3 is unblocked (still needs U2b, above, which is now done).
- **U1** `capture.py` gains the shared extraction primitive and two bind functions —
  ```python
  def extract_segment(raw_audio_path: Path, segment: Segment, dest_path: Path) -> None
      # ffmpeg-cuts [start_frame/sr, end_frame/sr) into dest_path (FLAC) -- same
      # subprocess-cut pattern render.py already uses for section spans, not a
      # manual sample copy, so the output is real, playable audio.

  def bind_segment_to_song(repo: Repo, slug: str, raw_audio_path: Path,
                            segment: Segment, *, tuning: str | None = None) -> None
      # T2's missing finishing step. `slug` must already exist and be
      # needs_audio (no `recording` block yet) -- refuses otherwise, so this
      # can never silently overwrite a song that already has real audio.
      # `tuning` defaults to the setlist's own tuning when not given (`add`'s
      # existing default). Refuses an overflowed segment, unconditionally --
      # bind_segments' own rule, not relaxed just because the caller is new.

  def bind_segment_as_new_song(repo: Repo, raw_audio_path: Path, segment: Segment,
                                *, title: str, artist: str, tuning: str) -> str
      # Group U's own step. slugify(title) must NOT already exist -- refuses
      # otherwise, naming the collision rather than silently colliding.
      # Writes songs/<slug>/audio/<file> + a fresh song.yaml
      # (recording.source: "capture"). Returns the new slug. Same
      # unconditional overflow refusal as above.
  ```
  *Test contract*: `extract_segment`'s ffmpeg argv asserted, subprocess mocked (mirrors
  `render.py`'s own test style); `bind_segment_to_song` refuses a slug that already has
  a `recording`; `bind_segment_as_new_song` refuses a title that slugifies to an
  existing song; both refuse an overflowed segment unconditionally; both accept an
  explicit `tuning` and default to the setlist's own when none is given.
  - **Done, 2026-09-06.** Built exactly as specced, with one judgement call and
    one signature addition beyond what's written above. `capture()` gained an
    optional `raw_path` parameter (H2's existing single-song CLI path is
    unchanged when it's omitted — still one WAV per segment under `out_dir`;
    given, the whole continuous recording is kept as ONE file there instead
    and no per-segment files are written, so `extract_segment` has something
    to cut a named segment out of later) — the post-recording split/write
    logic was pulled into a new `_finish_capture(samples, sample_rate, ...,
    out_dir, raw_path)` (plain arrays and paths, no device, no PyAudioWPatch),
    so both modes are exercisable with synthetic audio, the same "pure-enough
    functions carry the actual risk" reasoning the module already gives for
    `split_on_silence`/`bind_segments`. `library.Repo` gained a `capture_dir`
    property. Judgement call: the "default to the setlist's own tuning" note
    above doesn't fit `bind_segment_to_song`'s signature (no setlist parameter
    exists to derive one from) — `add`'s *actual* existing default (`cli.py`'s
    `--tuning`) is the fixed literal `"E standard"`, reused instead. Also fixed
    alongside, found while reviewing the capture/ amendment itself:
    `docs/01-architecture.md`'s disk-layout tree never listed `capture/`, and
    `.gitignore` had no explicit `capture/` entry. 45 new tests, all passing;
    full suite 804 passed; ruff clean; no-extras gate (801 passed, 3
    deselected) unaffected.
- **U2** Server: capture-session bookkeeping + endpoints. Storage decision, made here
  so U2 doesn't have to re-derive it: one raw file per capture-and-stop cycle under
  `capture/<timestamp>.<ext>` (extension matches whatever `capture()`'s own device-write
  path already produces) — kept until every segment split from it is resolved (bound or
  discarded), then deleted. This is CLAUDE.md's fourth server-write category (already
  amended, alongside this plan update, in `CLAUDE.md` and `docs/01-architecture.md` —
  same "companion doc edits made now, not left for the implementer" pattern Group S's
  own "Before starting" note used).
  - `GET /api/capture/segments` — the still-unresolved segments from the most recent raw
    file (ephemeral 0-based index, `duration_s`, `overflowed`); `[]` once every segment
    from it is resolved (and the raw file gone).
  - `GET /api/capture/segment-audio/<index>` — range-served preview audio for one
    unresolved segment (mirrors `_audio`'s range-serving; cut on the fly or via a small
    per-segment temp extract — implementer's call, name it).
  - `POST /api/capture/bind` — body `{index, mode: "existing"|"new", slug?, title?,
    artist?, tuning, setlist?}`. `"existing"` calls `bind_segment_to_song` — this is
    also how T2's own single-song live capture actually finishes, the same code path,
    not a second one; `"new"` calls `bind_segment_as_new_song` and, when `setlist` is
    given, adds the new song to it (mirrors `POST /api/setlist/<slug>/songs`'s existing
    add-by-slug behaviour). Removes the segment from the pending list; deletes the raw
    file once none remain.
  - `POST /api/capture/discard` — body `{index}` — drops a segment (a false positive
    from noise, say) without creating anything. Same cleanup-when-none-remain rule.
  *Test contract*: binding an already-resolved index refuses; discarding then binding
  the same index refuses; the raw file is deleted exactly when the last pending segment
  resolves (bound or discarded), asserted against a temp dir, never the real repo.
  - **Done, 2026-09-06.** A new module, `capture_session.py`, owns the sidecar:
    one `<raw file>.json` beside the raw recording, entries never removed or
    renumbered, only marked — `index` is a stable, permanent position, which is
    what makes both named test contracts hold by construction rather than a
    lucky renumbering. `current_session` reads "the most recent raw file" only
    (newest `*.json` sidecar by filename) — an older, not-fully-resolved cycle
    becomes unreachable through this API once a newer one starts (still on
    disk, just invisible to the GET); a documented limitation, not hidden.
    `segment-audio` cuts to a `tempfile.NamedTemporaryFile` (system temp, never
    under the repo — a throwaway preview extract isn't one of CLAUDE.md's four
    write categories) and deletes it after serving. Found while wiring bind:
    `extract_segment` is imported by name into two module namespaces
    (`server.py`'s own direct call, and `capture.py`'s two bind functions'
    internal one) — a test mocking it has to patch both. Found while extending
    `test_server_writes_nothing_else`: binding a segment writes the song's own
    audio bytes under `songs/<slug>/audio/` alongside its `song.yaml` — the
    same thing `woodshed add`/`woodshed capture` always did from the CLI, just
    reachable from the server for the first time (T1 will hit the identical
    thing) — allow-listed alongside `cache/`/`capture/` rather than treated as
    a surprise. 23 new tests, all passing; full suite 823 passed; ruff clean;
    no-extras gate (820 passed, 3 deselected) unaffected.
- **U2b — split-boundary editing, not yet built** (added 2026-09-06, per U0's own scope
  addition above; sequence before U3, which wires the UI against it). Three new
  `capture_session.py` operations, all acting on the CURRENT session only (same
  `current_session`/permanent-index discipline `resolve` already established — none of
  these renumber or remove entries; a merge/split replaces the affected entries' rows in
  place, keeping every OTHER entry's index stable) and all refusing outright on a
  non-pending index, same as `resolve`:
  ```python
  def adjust_boundary(repo: Repo, index: int, *, start_frame: int | None = None,
                      end_frame: int | None = None) -> SessionEntry
      # Moves one pending entry's own start_frame and/or end_frame. Refuses a result
      # where start_frame >= end_frame, or where the new range would overlap the
      # PREVIOUS or NEXT pending entry's own range (dragging a handle past a neighbour
      # is a merge, not an overlap -- refuse and name the fix, never silently clamp).

  def merge_segments(repo: Repo, first_index: int, second_index: int) -> SessionEntry
      # first_index and second_index must be adjacent pending entries (by index, in
      # either order) -- refuses two non-adjacent indices, since "merge segment 1 and
      # segment 4" has no principled meaning. Replaces both with ONE new pending entry
      # spanning [first.start_frame, second.end_frame) at first_index's own index;
      # second_index's row is removed from the session (not merely marked resolved --
      # it never existed as its own bound/discarded song, so it shouldn't linger as a
      # third status). overflowed is the OR of both (a dropout in either half still
      # makes the merged segment unsafe to bind).

  def split_segment(repo: Repo, index: int, at_frame: int) -> tuple[SessionEntry, SessionEntry]
      # at_frame must fall strictly inside [entry.start_frame, entry.end_frame) --
      # refuses a split point at or past either end (nothing to split). Replaces the one
      # pending entry with TWO: [start_frame, at_frame) keeps index's own index,
      # [at_frame, end_frame) gets a fresh index one past the session's current highest
      # -- appended, not inserted, so it never collides with or renumbers a later entry.
  ```
  Each of these rewrites the sidecar via the same `_save`/`_load` pair `resolve` already
  uses -- no new on-disk format, just more shapes the `entries` array can take between
  saves. Server gains `POST /api/capture/adjust`, `POST /api/capture/merge`,
  `POST /api/capture/split` (bodies: `{index, start_frame?, end_frame?}`,
  `{first_index, second_index}`, `{index, at_frame}` respectively) — each a thin call onto
  the function above, same "routes only" discipline every other endpoint follows.
  *Test contract*: `adjust_boundary` refuses `start_frame >= end_frame` and refuses
  overlapping a neighbour; `merge_segments` refuses non-adjacent indices and ORs
  `overflowed`; `split_segment` refuses a boundary at or past either end and the new
  entry's index is never one already in use; all three refuse a non-pending index, same
  message shape as `resolve`.
  - **Done, 2026-09-06.** Built exactly as specced. `capture_session.py` gained a
    shared `_pending_entry(session, index)` lookup (same refusal messages `resolve`
    already used) and `resolve` itself was refactored onto it -- no behaviour change,
    one lookup instead of two copies. Judgement calls, both raised rather than
    silently resolved: (1) "adjacent" for `merge_segments` is checked by index
    (`abs(first_index - second_index) == 1`), matching the plan text's own "(by
    index, in either order)" phrasing, but which of the pair supplies the merged
    entry's start vs. end is decided by comparing `start_frame`, not by which
    argument was named `first_index` -- so `merge_segments(1, 0)` and
    `merge_segments(0, 1)` produce the identical result, which is what "in either
    order" has to mean for the operation to be safe. (2) `adjust_boundary`'s
    previous/next neighbour is found by `start_frame` order among the other still-
    pending entries, not by index order -- a prior `split_segment` can leave a
    high index chronologically in the middle of the session, and overlap is about
    time, not index. (3) `split_segment` does not try to divide `overflowed`
    between the two halves (nothing records which half saw the dropout) -- both
    inherit the original entry's flag, the conservative reading. Server: three thin
    routes (`POST /api/capture/adjust|merge|split`), each parses its body and calls
    straight through, returning the touched `SessionEntry`/pair as JSON (`index`,
    `start_frame`, `end_frame`, `duration_s`, `overflowed`). `test_server_writes_
    nothing_else` extended to a 5-segment capture session and now exercises all
    three new endpoints before the existing bind. 24 new capture_session tests + 6
    new server tests, all passing; full suite 847 passed (was 823); ruff clean;
    no-extras gate 844 passed, 3 deselected (was 820/3) -- unaffected by the new
    endpoints since none of them touch a heavy dependency.
- **U3** `screens/capture.js` gains U0's segment-review UI: the whole-pass waveform strip
  (draggable boundary handles calling `merge_segments`/`adjust_boundary`, a "+ split at
  playhead" action calling `split_segment`, a scrubbable playhead using `player.js`'s
  `loop: false` engine from **R1** and its `RealtimeEngine.seek()` from **P1** — literally
  the same "audition, no rep" mechanism P1/R1 already built, pointed at `segment-audio`/the
  raw pass instead of `/api/audio/<slug>`) above one row per pending segment, each with its
  own preview-play control plus the inline title/artist/tuning form and Add/Discard
  actions. Degrades to nothing (no new UI at all) when `GET /api/capture/segments` is
  empty, the same "show only what's real" instinct H2's own screen already follows.
  **Cannot add (or discard) the same segment twice** — two layers, not one:
  1. Already true at the data layer (U1/U2, already built and tested): `resolve()` refuses
     a non-pending index outright, and a resolved segment simply stops appearing in
     `GET /api/capture/segments` — there is no request this screen could send that
     double-binds a segment. Nothing new needed here.
  2. What U3 itself must still get right: the row's own Add/Discard controls disable the
     instant they're clicked (before the request round-trips), so an impatient double-click
     can't even fire a second request, and the row shows a brief inline "Added" / "Discarded"
     confirmation rather than silently vanishing — a resolved row disappearing from a
     re-fetched list with no acknowledgement is exactly the kind of ambiguity that makes
     someone click Add again "just in case." Same reasoning as invariant 12 in spirit
     (never leave the human guessing what the tool actually did), applied to a click instead
     of a rep.
  *Test contract*: `node --check`; the same lint-style "never `/api/rep`, never
  `addEventListener('pass', ...)`" assertion R1's test added for `song.js`, copied for
  this file rather than reimplemented by hand; a synthetic-DOM test (same technique as
  `test_seek.mjs`/`test_ended.mjs`) asserting a second click on an already-clicked
  Add/Discard control never issues a second `fetch` to `/api/capture/bind` or `/api/capture/
  discard`.
- **U4** (lower priority — may be cut without blocking this phase's gate) CLI parity:
  `woodshed capture --split` records until stopped and prints one line per detected
  segment (index/duration/overflow) instead of trying to bind anything; `woodshed
  capture bind <index> "<title>" --artist "..." --tuning "..." [--setlist <slug>]` calls
  the same `bind_segment_as_new_song` the server endpoint uses. Both read the same
  on-disk raw-file/pending-segment state U2 defines — not a second bookkeeping scheme.

### Ordering

Group O blocks the UI-facing halves of P/Q/S/T/U (icon placement, toggle placement, the
add-song form layout, U0's segment-review layout) but not their engine/server halves,
which can start immediately against the contracts fixed above — same "the contract is
in this plan, not in the code" rule Phase 2 already relies on. **P1 and Group J
(Phase 2) both touch `player.js`** — sequence P1 before J, or have one person own both,
rather than two units editing the same file's seek/loop-boundary logic in parallel.
**U3 depends on R1** (it reuses R1's `loop: false` preview engine for segment audition)
— sequence U3 after R1, which is already done. **U3 also depends on U2b** (the waveform
strip's drag/merge/split controls call straight into it) — sequence U2b before U3, not in
parallel; U2b itself has no UI half and no dependency beyond U1/U2 (already done), so it
can start immediately. S, T and U's backend halves (U1/U2/U2b) are otherwise independent
of everything else and may run in parallel with each other and with P/Q/R; U2 and T2
should be built by the same person or in sequence, since T2's own "finish the capture"
step is U1's `bind_segment_to_song`, not a separate function.

**Phase 1.5 gate**
- Automated: `uv run pytest` green; the no-extras gate green and unaffected by
  `separate.py` (same "nothing above the heavy-dependency line imports it" rule
  `analyze.py`/`render.py` already prove); `needs_demucs` registered and excluded the
  same way the other three hardware/binary markers are.
- Manual (Paolo, at the machine): click mid-waveform during practice and confirm the
  playhead jumps there and no false rep is counted; recognise every foot action by its
  icon alone from normal sitting distance; open a song's page and confirm pressing play
  actually plays it; toggle "Guitar only" on a section never isolated before, watch the
  two-stage progress state, toggle off and back on and confirm the second time is
  instant; add a new song through the dashboard's real form, by file; start and stop one
  real capture from the Capture screen; **and (Group U) capture several songs in one
  pass against a brand-new, empty setlist, split them into segments, preview each before
  naming it, and add all of them to the setlist with distinct titles and tunings** —
  this is the workflow Group U exists for, and the gate should exercise it for real, not
  just each endpoint in isolation. **Also (U2b/U3): capture a pass containing at least one
  quiet passage inside a song and one short gap between two songs**, confirm the
  auto-splitter gets at least one of them wrong, then actually use the waveform strip to
  fix it — merge an over-split pair back into one segment, split an under-split pair into
  two, scrub across a boundary to confirm it lands where expected — and add the corrected
  segments with confidence they're the right length; **and confirm double-clicking Add (or
  Discard) on the same row never produces two ledger/setlist entries or a visible error**,
  the specific failure this session was asked to close off.

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
5. **Model allocation**: B3 (`sections.py` coverage maths), I2 (the crossfade), J2 (the
   boundary swap), P1 (the worklet seek/pass-qualification interaction), S2 (threading
   `source` through the render cache's fingerprint without breaking the existing mix path)
   and U2 (the raw-file lifecycle — exactly-once deletion when the last pending segment
   resolves, shared correctly between T2's finishing path and U's own bind-as-new-song
   path) carry the most subtle risk — give those the strongest model. Everything else is
   mechanical against a fixed contract and a cheaper model is appropriate.

---

## What we are NOT doing

- No pitch detection, no scoring of the playing, ever.
- No Spotify audio path, no DRM decryption, no bundled downloader.
- No mobile layout, no light theme, no authentication, no multi-user.
- No mixing or EQ, and no general stem separation — that is still `rambass-live`'s job.
  **Revised 2026-09-06**: guitar-only isolation for practice is now explicitly in scope
  (Phase 1.5, Group S) — Paolo asked for it directly, `docs/03-audio-engine.md` already
  named "an isolated guitar track" as the honest answer below 50% speed, and it was
  sitting on the roadmap's "later, if it earns it" list. It earned it. This is narrower
  than what was ruled out: one stem (guitar), one purpose (practice below the stretcher's
  honest floor), reusing `rambass-live`'s own Demucs choice rather than reinventing it —
  not a mixing/EQ surface, which stays out.
- No Chromaprint fingerprinting, no chart export, no "session" concept. Those are the
  roadmap's "later, if it earns it" and stay there.
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
