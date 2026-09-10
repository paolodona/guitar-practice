# Backlog

Out-of-scope items discovered while planning or implementing. Format:
`- [ ] **[Plan <NNN>]** <description>`

**Swept 2026-09-06.** Everything that could be decided or built from a headless
container was; what is left is genuinely blocked on hardware, on a sibling repo
that is not checked out, or on Paolo saying a word out loud. Resolved entries are
kept with their outcome rather than deleted — the reasoning is the useful part,
and a decision nobody can find gets re-litigated.

## Open — needs the pedal, the hardware, or Paolo's word

- [ ] **[Plan 001]** **N2, the optional Program Change send**, is blocked on two
      things at once. (a) MIDI *output* has no home yet: `midi.js` is Group L,
      itself blocked on the three eyes-on-the-unit questions in
      docs/05-foot-control.md. (b) The slot→memory mapping must come from
      `rambass-live`'s `config/gx100.yaml` — that repo is not checked out beside
      this one, and both docs/05-foot-control.md and `rambass-live/docs/gx100.md`
      say explicitly that the mapping must not be assumed (a PC number names a
      slot; the pedal's PROGRAM MAP decides which of the 300 memories that is).
      `gx100.py` therefore carries no slot→PC arithmetic and a test says so;
      `config.gx100.send_program_changes` exists and defaults to false, so the
      safe default is in place ahead of the feature. When it is built: keep the
      toggle explicit and never send on mount.
      **And settle a contested fact first** (merged 2026-09-07 with
      `docs/08-unification.md`): that doc reports `gx100` testing the program-map
      claim ON THE UNIT and finding `PC n` to be a plain identity, and the
      `CC#0 → CC#32 → PC` ordering this entry used to say to lift **wedged the
      pedal until its power was pulled**. Second-hand here, so neither version is
      settled — but do not build N2 to send that CC pair until someone re-verifies
      it at the pedal. If the plain-identity finding holds, N2 gets simpler: there
      is no mapping to import from anywhere, which is the same conclusion
      `gx100.py` already reaches from the other direction.
- [ ] **[Plan 001]** Settle the three eyes-on-the-unit GX-100 questions in
      `docs/05-foot-control.md` and write the answers back into that file. **Only
      L3 (expression-pedal-as-speed-knob) is actually blocked on these** — see
      prompt 002's own re-read, confirmed correct while building L1/L2
      (2026-09-10): question (1), which CC each switch sends, is config-driven
      (`config.midi.input`/`config.midi.map`), so `midi.js` never depended on it;
      question (2)'s fallback (a dedicated USB MIDI controller) changes the
      hardware, not this code. They still need ten minutes at the actual pedal
      for L3 and for N2 below. **Add a fourth to that sitting**: whether `PC n` is
      a plain identity and
      whether the `CC#0 → CC#32 → PC` ordering wedges the unit — `gx100` reports
      both (see `docs/08-unification.md`) and this repo's own docs still say the
      opposite. Whichever way it falls, one of the two documents is wrong and
      should stop saying so.
- [ ] **[Plan 001]** **Phase 2's manual gate is still open**: loop a real solo at
      55% for twenty passes and listen for a tick at the seam, then let the ladder
      advance to 60% and confirm the change lands at a loop boundary. Everything
      the seam depends on is asserted from both sides; whether it is *inaudible*
      is a listening judgement the tool does not get to make for itself. Phase 3's
      "ten minutes hands-free" is open for the same reason.
- [ ] **[Plan 001]** The device half of `capture.py`
      (`list_devices`/`default_device`/`capture`) has still never run against real
      WASAPI loopback hardware in an automated test, and cannot from here. Monitor
      mode (2026-09-06) deliberately reuses that same path rather than adding a
      second one, precisely so there is only ever one thing to debug live.
- [ ] **[Plan 001]** The old default branch
      `claude/guitar-practice-tool-spec-8e7y5s` still exists on the remote.
      Verified 2026-09-06: GitHub's default is `main`, and nothing is unique to
      the old branch (no file only it has; every doc on it is a subset of
      `main`'s). It is simply not an *ancestor* of `main`, which re-landed that
      content as its own root. Safe to delete whenever you like —
      `gh api -X DELETE repos/paolodona/guitar-practice/git/refs/heads/claude/guitar-practice-tool-spec-8e7y5s`
      — left alone here because deleting a remote branch is irreversible and
      nobody asked for it out loud.
- [ ] **[Plan 001]** Whether `uv run --extra dev pytest -q` should also be wired
      as an actual **Stop hook** in `.claude/settings.json`. It is named as the
      quality gate in `.claude/plan-project.md` now, which is what the planning
      workflow reads; making it a hook costs ~25 s on every single turn, so that
      is a tax to choose knowingly rather than find.

- [ ] **[Plan 001]** `docs/08-unification.md` (merged 2026-09-07, written by
      another session on 2026-09-06) proposes folding the three repos into one
      app. **PROPOSED, nothing agreed**; the argument lives once, in
      `rambass-live/docs/unification.md`. It is in this list only so it is not
      forgotten: its own claim is that the shared-song-identity part is free to
      adopt while `songs/` is small and becomes a three-way migration later. Also
      note its own counter-argument, which quotes this repo's roadmap warning
      about practising the tool instead of the guitar.

## Open — real work, nobody blocked on it

- [ ] **[Plan 001]** A capture that reported an overflow is refused for binding
      (docs/04-sources.md's rule, implemented), but there is no way to re-record
      just that one segment from the review screen — the whole pass has to be
      redone. Not painful yet; would be after a 23-song set.
- [ ] **[Plan 001]** `tests/test_gx100.py::test_repo_path_comes_from_config_and_expands_a_user_path`
      fails on Windows, found 2026-09-10 running the suite on Paolo's own
      machine for the first time (every previous run was a Linux container).
      `monkeypatch.setenv("HOME", ...)` has no effect on `Path.expanduser()`
      under `ntpath` — Windows resolves `~` from `USERPROFILE`, and the test
      never sets that, so it silently expands to the real user's home instead
      of the tmp fixture and `gx100.repo_path` correctly returns `None` for a
      directory that (from its point of view) doesn't exist. `gx100.repo_path`
      itself is not obviously wrong — it is the standard library's own
      cross-platform behaviour — but the TEST only proves the Unix half of it.
      Fix is narrow (monkeypatch both `HOME` and `USERPROFILE`, or use
      `tmp_path`-relative assertions that don't depend on which one wins); not
      fixed here because it was found while building Group L, not owned by it.

## Resolved 2026-09-07 (a code review of the two runs above)

- [x] **[Plan 001]** Eleven findings, all fixed with regression tests; four of
      them silent bugs in `BufferEngine`, one of which (`_adoptSwap` retiring the
      outgoing node when the seam was *observed*, truncating roughly one 10 ms
      crossfade in five) would have produced exactly the click Phase 2's
      listening gate is for. Full account in the plan doc under "Run 5". Worth
      keeping in mind for the gate itself: the engine has been reviewed but still
      never *heard*.

## Resolved 2026-09-06

- [x] **[Plan 001]** **Slug policy: `cant-stop` vs `can-t-stop`.** Settled in
      favour of the code: an apostrophe is a separator, `Can't Stop` is
      `can-t-stop`, and the docs were corrected (CLAUDE.md, docs/02-data-model.md,
      `slugify`'s own docstring). The argument that decided it: a slug is a
      permanent identity because `practice/reps.jsonl` names it on every line and
      that file is append-only and never rewritten, so a prettier directory name
      would cost either rewriting the one irreplaceable file in the repo or
      carrying an alias table forever. Matching is a separate question with a
      separate answer — `sources._tokens` strips apostrophes, so a library file
      called `Cant Stop.flac` still matches the song.
- [x] **[Plan 001]** **The 110% ceiling and the render cache.** The cache follows
      the slider: `Render.speed`'s domain is documented `0.40 .. 1.10`, because
      the practice loop plays from the cache and a cache capped at 1.00 would make
      the one range Paolo asked for the one range served by the real-time
      stretcher. The ladder does NOT follow — `rungs()` still stops at
      `target_speed` and nothing auto-climbs past it; above 100% is always a
      deliberate press. Named tests both sides.
- [x] **[Plan 001]** `woodshed render` was the last `_NOT_YET_IMPLEMENTED` stub.
      Built: `--speed` takes several rungs at once, `--ladder` does every rung to
      the target, neither renders the rung the ledger says you would practise
      next, and `--evict` reaps orphans then trims to budget. Every command
      docs/01-architecture.md names is real now.
- [x] **[Plan 001]** **`render.evict` was never called by anything**, so
      `config.render.cache_max_gb` was a number the tool printed and never
      honoured. The server applies it after every render now
      (`server.render_then_evict`), `woodshed render --evict` runs it
      deliberately, and doctor reports over-budget as a MISS instead of "report
      only".
- [x] **[Plan 001]** **`cleanAtSpeed` zeroed on every mount**, so three clean reps
      across two sittings never advanced a rung. The song payload carries
      `clean_at_speed` beside `starting_speed_pct` now, derived from the ledger
      the same way, and `screens/practice.js` resumes from it.
- [x] **[Plan 001]** **The practice screen said nothing while a render built** —
      minutes of silence for a first guitar-only section. `BufferEngine` fires
      `'rendering'` with the server's own two honest stages (`separating`,
      `rendering`) and once more when the wait ends however it ends; a cache hit
      fires nothing.
- [x] **[Plan 001]** **`RealtimeEngine` has no position accessor.** Resolved for
      the engine that matters: `BufferEngine.position()`/`sourcePosition()` are
      exact (a native loop's position is arithmetic) and `tick()` reads them, so
      the practice ring is truth rather than a resynced estimate.
      `RealtimeEngine` still has none **on purpose** — the only position inside it
      is the worklet's `readPos`, the next SOURCE frame fed to the stretcher,
      which leads the audible output by whatever the stretcher holds. Being
      confidently wrong by a variable margin is worse than an honest estimate, so
      that engine keeps the wall-clock one.
- [x] **[Plan 001]** **`renderSections` lost the selected tile on every redraw.**
      The fifth parameter widened from a handlers bag to an options bag carrying
      `selectedId`; `screens/song.js` already owned that state and now says so.
      One piece of state, one owner.
- [x] **[Plan 001]** **The GX-100 patch field is a suggest field now**, backed by
      the sibling repo's real patch ids (`gx100_patches` on the song payload, N1's
      cross-repo read). A `<datalist>` rather than a `<select>`, deliberately:
      naming a patch before you have designed it in `gx100` is a normal order of
      work, and the line under the field already says when an id does not resolve.
- [x] **[Plan 001]** **The setlist name is in the practice header and links back**
      — the three things it needed (`?setlist=` context, a real setlist endpoint,
      somewhere to link to) all exist now. Shown only when the song is actually in
      that setlist, so a stale query names nothing.
- [x] **[Plan 001]** **Setlist reorder by dragging** — `setlist.reorder` (pure,
      refuses anything that is not a permutation of what is already there),
      `POST /api/setlist/<slug>/order`, and a hover-only grip using native HTML5
      drag-and-drop (no bundler, no framework). A failed commit re-fetches rather
      than leaving the dragged-to order on screen as if it had saved.
- [x] **[Plan 001]** **Capture level before committing.** Built as monitor mode:
      the SAME capture path into a scratch directory outside the repo, discarded
      on stop — not a second device path, because the device half is the one part
      of this repo no test can cover and a parallel implementation would be a
      second thing debuggable only live. The other candidate, **normalising
      captured audio afterwards, is rejected**: it would be the tool's first
      alteration of what it recorded, applied to the one thing it can never record
      again, and it does not solve judging the level *before* you play.
- [x] **[Plan 001]** **"NaN:NaN" on the capture review screen.** The root cause was
      a long-running `woodshed serve` predating the raw-audio routes (it serves
      `web/` from disk but keeps the Python it started with). Both halves fixed:
      `formatElapsed` says `--:--` for an unknown duration rather than NaN, and a
      404 on any `/api/` path now tells you the running server may predate the
      route. The other console error in that report was a browser extension's
      injected script (`reportAllChanges`, `VM<n>`), not this app.
- [x] **[Plan 001]** **A default full-song section.** Shipped as a real, persisted
      `Section` with `full_song: true`, written at bind time by `bind_song_file`
      and `bind_audio_to_song` — the backlog's own "persisted vs synthesised"
      question, answered persisted: it needs no read-path special case, and
      `practice.song_readiness`/`next_up` both drop `full_song` explicitly so it
      can never become the "longest covering span" and swamp coverage. Not forced
      to reappear if deleted: deleting one is a deliberate act and the tool should
      not overrule it.
- [x] **[Plan 001]** `practice.py` had a full contract but no owning unit — built
      in Phase 1 Group F and extended in Phase 2 K2 (`progress()`). Stale entry.
- [x] **[Plan 001]** The no-extras gate was vacuous (no test carried the markers).
      Not any more: three tests carry `needs_rubberband`/`needs_librosa` and get
      deselected, and `analyze.py`/`render.py`/`separate.py` all exist for it to
      guard.
- [x] **[Plan 001]** `parse_byte_range`'s citation: **14** parametrised cases, and
      the "15" in the plan's own corrections table was itself the miscount.
      Corrected in place in the plan.
- [x] **[Plan 001]** The Components artboard captioned the readiness bar
      "length-weighted mean of the song's sections" — the rule
      docs/02-data-model.md supersedes. Caption now says what the code does.
- [x] **[Plan 001]** `.claude/plan-project.md`'s quality gate was a TODO from
      before `pyproject.toml` existed. It names the real commands now (the suite,
      the no-extras variant, and the node tests that run under it).
- [x] **[Plan 001]** The nine `.dc.html` design artboards existed only inside the
      published artifact. Extracted and committed 2026-09-05.
- [x] **[Plan 001]** `.gitignore` named a `design/seed` script that does not
      exist. Dangling reference dropped 2026-09-05.
- [x] **[Plan 001]** Record the Rubber Band GPLv2+ decision in
      `docs/03-audio-engine.md` and `web/vendor/README.md`. Done 2026-09-05.
