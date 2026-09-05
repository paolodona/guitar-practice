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
- [ ] **[Plan 001]** Record the Rubber Band GPLv2+ decision in `docs/03-audio-engine.md`
      and in `web/vendor/README.md` alongside the vendored WASM build and its version.
- [ ] **[Plan 001]** Delete the old default branch `claude/guitar-practice-tool-spec-8e7y5s`
      once `main` has been the GitHub default for a while:
      `gh api -X DELETE repos/paolodona/guitar-practice/git/refs/heads/claude/guitar-practice-tool-spec-8e7y5s`

## Low
- [ ] **[Plan 001]** Add `uv run pytest` as the Stop-hook quality gate in
      `.claude/plan-project.md` once Phase 0 scaffolds the package and the suite runs.
- [ ] **[Plan 001]** Settle the three eyes-on-the-unit GX-100 questions in
      `docs/05-foot-control.md` and write the answers back into that file.
