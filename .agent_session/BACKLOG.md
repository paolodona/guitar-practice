# Backlog

Out-of-scope items discovered while planning or implementing. Format:
`- [ ] **[Plan <NNN>]** <description>`

## High
- [ ] **[Plan 001]** The nine `.dc.html` design artboards exist only inside the published
      artifact — `.gitignore:19` excludes `design/*.html` and they were never committed.
      Extract them, narrow the ignore rule to the packaged editor, and commit the sources.
      Losing that artifact currently loses the design.
- [ ] **[Plan 001]** `.gitignore:19` names a `design/seed` script that does not exist.
      Write it or drop the reference. `design/derive.py` also reads `Main.dc.html` from the
      working directory and cannot run without it.

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

## Low
- [ ] **[Plan 001]** Add `uv run pytest` as the Stop-hook quality gate in
      `.claude/plan-project.md` now that Phase 0 has scaffolded the package and the
      suite runs (`uv run pytest`, 619 passed as of 2026-09-05).
- [ ] **[Plan 001]** Settle the three eyes-on-the-unit GX-100 questions in
      `docs/05-foot-control.md` and write the answers back into that file.
