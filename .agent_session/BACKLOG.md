# Backlog

Out-of-scope items discovered while planning or implementing. Format:
`- [ ] **[Plan <NNN>]** <description>`

## Requested live during Phase 0 manual testing, 2026-09-06
- [ ] **[Plan 001]** Raised the real-time engine's manual speed ceiling to 110% (was
      100) so Paolo can deliberately overlearn a section faster than the recording --
      `web/player.js`'s `MAX_SPEED_PCT` and `screens/practice.js`'s `clampSpeed`.
      `clock.py`'s `Render.speed` is still documented `0.40 .. 1.00` (a fraction) for
      the Phase 2 render CACHE, which is a separate, not-yet-built domain -- whoever
      builds I1 (the cache) should decide then whether cached renders also go to
      1.10 or stay capped at 1.00 (live-preview-only, matching the real-time slider
      but not the practice loop). Left unresolved rather than guessed, since it
      changes the `rungs()`/ladder domain too if the cache follows.
- [ ] **[Plan 001]** Paolo asked for the setlist name in the practice-screen header
      (`design/Main.dc.html` fixes it there) to link back to that setlist. Genuinely
      not buildable yet: `GET /api/song/<slug>` has no `?setlist=` context (server.py's
      own comment: "No setlist context reaches this endpoint... 0 is the honest
      default until a setlist is plumbed in"), there's no `/api/setlist/<slug>`
      endpoint, and no setlist-detail screen exists in the module map to link to. All
      three arrive together in Phase 1's F1. The song-title link Paolo asked for in
      the same message (`#/song/<slug>`) was buildable now and is done.
- [ ] **[Plan 001]** Paolo requested a default, non-editable section spanning the
      whole song, always present on the song page even before any section is drawn.
      Not in `docs/00-spec.md` or `docs/02-data-model.md` today -- a real design
      decision, not a bug fix: does it get synthesized read-only in `_song()`'s
      response (never written to `song.yaml`, never POST-able), or is it a real
      persisted `Section` the UI merely refuses to edit? The former fits invariant 2
      ("nothing about nesting is stored") better and avoids a `song.yaml` full of a
      redundant span for every song; the latter needs a migration answer for existing
      songs that don't have one. Also touches `sections.coverage_readiness` (a
      full-song span would become the "longest covering section" for any uncovered
      stretch of the recording, changing what "readiness" means). Needs a decision,
      not a guess -- ask before building.
- [ ] **[Plan 001]** Paolo requested the GX-100 patch field (song page inspector) be
      a searchable dropdown of the user's actual patches, not free text. This is
      Phase 3's Group N (`N1`): resolving a section's `patch:` id against
      `gx100/songs/<slug>/song.yaml`'s `patches:` block, read by path from
      `config.library_paths` (not present in `config.yaml` yet), degrading to "not
      shown" if that sibling repo isn't configured/present. Doing this now would mean
      building N1's cross-repo read a full two phases early, with no config plumbing
      for where the gx100 checkout even lives. Left as free text for now.

## High
- [x] **[Plan 001]** The nine `.dc.html` design artboards exist only inside the published
      artifact — `.gitignore:19` excludes `design/*.html` and they were never committed.
      Extract them, narrow the ignore rule to the packaged editor, and commit the sources.
      Losing that artifact currently loses the design.
      **Done 2026-09-05 (this turned out not to need a human at all): the artifact is
      owned by the user, so `Artifact(action: "read", url: …)` returns its full raw HTML
      rather than a summary — the plan's P1 prerequisite assumed no agent could open a
      `claude.ai/code/artifact/…` URL, which is true for a *shared* artifact but not for
      one the user owns.** The nine `.dc.html` sources plus `canvas.json` live in a
      `<script type="application/json" id="appifact-doc">` block in that HTML, as a
      `{title, content: {files: {...}}}` JSON document; extracted and written to
      `design/*.dc.html`. `canvas.json`'s content was byte-for-byte identical to the
      already-tracked copy (only pretty-printing differed) — left the tracked one alone.
      Paolo said he's fine tracking all of `design/*.html` in git, so the ignore rule
      for it was dropped entirely rather than narrowed to just the packaged editor
      shell — one less exception to remember. **Not yet committed** — do that, then
      re-check the plan's P1 gate: Groups D/F/K/M and every manual gate were blocked
      on this alone.
- [x] **[Plan 001]** `.gitignore:19` names a `design/seed` script that does not exist.
      Write it or drop the reference. `design/derive.py` also reads `Main.dc.html` from the
      working directory and cannot run without it.
      **Done 2026-09-05**: dropped the dangling reference from the `.gitignore` comment
      rather than writing the script (regenerating the packaged editor is real feature
      work, out of scope for a comment fix). `design/derive.py` can now actually run,
      since `Main.dc.html` exists on disk for the first time — not yet exercised.

## Medium
- [ ] **[Plan 001]** The Components artboard captions the readiness bar "length-weighted
      mean of the song's sections" — the rule `docs/02-data-model.md` explicitly supersedes.
      Fix the caption so the canvas stops asserting a rule the code will not follow.
- [x] **[Plan 001]** Record the Rubber Band GPLv2+ decision in `docs/03-audio-engine.md`
      and in `web/vendor/README.md` alongside the vendored WASM build and its version.
      Done 2026-09-05: `docs/03-audio-engine.md:83` already carried it from planning;
      `web/vendor/README.md` added during Phase 0 unit A2 (the binary itself is not
      vendored yet — that's D4, gated on P1).
- [ ] **[Plan 001]** Delete the old default branch `claude/guitar-practice-tool-spec-8e7y5s`
      once `main` has been the GitHub default for a while:
      `gh api -X DELETE repos/paolodona/guitar-practice/git/refs/heads/claude/guitar-practice-tool-spec-8e7y5s`

## Medium (added implementing Phase 0, 2026-09-05)
- [ ] **[Plan 001]** `practice.py` (`reached`/`song_readiness`/`is_cold`/`next_up`) has a
      full signature and test contract in the plan (Tier 1) but was never assigned to a
      work unit in any group — the 2026-09-05 review claimed this gap was fixed by
      adding a unit for it, but no lettered unit actually owns it. Not needed until
      Phase 1 Group F (the dashboard's next-up list), so it didn't block Phase 0 — but
      whoever picks up Phase 1 should add it explicitly (as its own unit, or folded into
      F1) rather than discover the same gap a second time.
- [ ] **[Plan 001]** The no-extras gate (`pytest -m "not needs_rubberband and not
      needs_librosa and not needs_device"`) currently passes vacuously: the three
      markers are registered in `pyproject.toml` but no test carries any of them yet,
      and `analyze.py`/`render.py`/`capture.py` don't exist yet either, so there is
      nothing on the "heavy" side for the gate to actually guard against. Re-check once
      Phase 1 adds `analyze.py` (E1/E2) that the markers get applied to the tests that
      need librosa/rubberband/a device, or the gate keeps passing without proving
      anything.
- [ ] **[Plan 001]** Citation correction: `parse_byte_range`'s lifted parametrised test
      set has 14 cases in the current `rambass-live` working copy, not 15 as the plan's
      "Citation corrections" table states (verified 2026-09-05 during C2). Low-stakes,
      but worth fixing in the plan text so a later reader doesn't go looking for a 15th.

## Medium (added implementing Phase 0 Group D, 2026-09-05)
- [ ] **[Plan 001]** `web/sections.js`'s `renderSections` tracks which section tile is
      "selected" (drag handles shown) in its own closure, since the D0-fixed signature
      (`laneRoot, sectionsData, view, handlers`) carries no `selectedId`. A redraw
      triggered by a server round-trip (e.g. committing an inspector-field edit in
      `screens/song.js`) therefore visually deselects the tile even though the caller's
      own selection state is still correct. Flagged by D3 in `sections.js`'s own module
      doc rather than silently worked around. Fix means either widening the signature
      (a real contract change every caller would need to pick up) or having the caller
      re-apply selection after each redraw — a call for whoever picks up Group D's
      loose ends in Phase 1.
- [ ] **[Plan 001]** `web/player.js`'s `RealtimeEngine` has no position/progress
      accessor — only the discrete `pass` event at a loop boundary. `screens/practice.js`
      (D6) therefore animates the progress ring, waveform clip and playhead from a
      local wall-clock estimate resynced to 0 at each `pass`, not from engine ground
      truth. Correct and unnoticeable at a glance, but Phase 2's J1/J2 (the buffer
      engine and its boundary-swap arithmetic) should add a real accessor so this
      stops being an approximation.
- [ ] **[Plan 001]** `screens/practice.js` has no ledger-read endpoint to ask "what rung
      and how many clean reps does this section already have" — it starts every mount
      from a client-side mirror of `ladder.py`'s pure rung math with zero history,
      correct for a fresh section but wrong the moment a song has practice history.
      This is the same underlying gap as the existing `practice.py`-has-no-owning-unit
      item above (Phase 1 Group F / Phase 2's `GET /api/progress`) — noting the concrete
      front-end symptom here so it's checked off the same time that endpoint lands.

## Low
- [ ] **[Plan 001]** Add `uv run pytest` as the Stop-hook quality gate in
      `.claude/plan-project.md` now that Phase 0 has scaffolded the package and the
      suite runs (`uv run pytest`, 619 passed as of 2026-09-05).
- [ ] **[Plan 001]** Settle the three eyes-on-the-unit GX-100 questions in
      `docs/05-foot-control.md` and write the answers back into that file.
