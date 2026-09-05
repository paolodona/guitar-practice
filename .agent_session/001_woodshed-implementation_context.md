# Session Context: Woodshed — full build
Plan: 001 | Name: woodshed-implementation | Updated: 2026-09-05 | GitRef: 289b0c7

## Related Files
- **Prompt**: `.agent_session/001_woodshed-implementation_prompt.md` — original mission
- **Plan**: `.agent_session/001_woodshed-implementation_plan.md` — implementation steps

---

## Task Summary
- **Requirement**: implement the whole Woodshed tool per `docs/00-spec.md` … `docs/07-roadmap.md`
  and the published design canvas, lifting code from two sibling repos without depending on them.
- **Scope**: all four roadmap phases at equal planning depth, decomposed for parallel
  subagent execution.
- **Out of Scope**: demucs stem practice, Chromaprint fingerprinting, chart export, the
  "session" concept — the roadmap's *later, if it earns it* items.

## Key discoveries

**The repo is docs-only.** 16 tracked files; no `pyproject.toml`, no `src/`, no `tests/`,
no CI. `uv` 0.9.22 and `ffmpeg` 9.0 are on PATH. **`rubberband` 4.0.0 was installed during
planning** (2026-09-05) from the official GPL executable zip — no winget/choco/scoop
package exists — into `%LOCALAPPDATA%\Programs\rubberband\...`, with `WOODSHED_RUBBERBAND`
and user PATH set, and verified: `--fine` selects R3, the frequency ratio for −1 semitone
is `0.943874` = `2^(-1/12)` exactly, zero frame error, 10 s processed in 1.17 s.

**The design artboards exist only inside the published artifact.** `.gitignore:19`
excludes `design/*.html` to keep a 2 MB packaged editor out of git, but that also excludes
the nine `.dc.html` artboard sources, which were never committed. `design/derive.py` reads
`Main.dc.html` from the working directory and cannot run without it, and `.gitignore` names
a `design/seed` script that does not exist. **Losing that artifact currently loses the
design.** → now **prerequisite P1**, a human task gating Groups D/F/K/M — it was unit A2
until the 2026-09-05 review pointed out that no unattended agent can open a
`claude.ai/code/artifact/…` URL, which made it undelegatable rather than merely urgent.

**`design/derive.py` is a second source for the practice screen.** It patches `Main.dc.html`
by literal string replacement to generate the lead-in and advance states, so it records the
exact values: 236px hero numbers, the 520px countdown, `stroke-dashoffset` 854→0,
`text-shadow:0 0 90px rgba(224,145,63,.34)` as the entire ladder-advance celebration.

**The Components sheet contradicts the data model.** Its readiness cell is captioned
"length-weighted mean of the song's sections" — the rule `docs/02-data-model.md` explicitly
supersedes because it double-counts a subdivided solo. Docs win; the caption gets fixed.

### What lifts cleanly from `../rambass-live`
| Item | Lines | Verdict |
|---|---|---|
| `parse_byte_range` (`console.py:58-90`) + its **15**-case parametrised test | 33 | verbatim, zero imports |
| `_send`/`_json`/`_error`/`_body`/`_send_file`, `ConsoleServer`, `make_server` | ~170 | verbatim; route bodies are rambass-only |
| `refine_tempo`, `_comb`, `pulse_wander`, `_circular_mean` (`analyze.py`) | 96 | verbatim — **pure numpy, no librosa** |
| `find_grid_anchor`, `grid_confidence`, **`_runner_up`** | 91 | same — but the signatures in the plan's first draft were wrong; see its "Citation corrections" table. `find_grid_anchor(onsets, bpm, …) -> tuple[float, dict]`, `grid_confidence(times, bpm, …) -> dict` |
| `locate_tool` + winget helpers (`audio.py:129-214`) | ~90 | copy, **parameterise the env var** so it finds rubberband too (~6 lines) |
| `require_module` + installer-hint helpers | ~55 | verbatim, stdlib only |
| `slugify`, `parse_position`, `ProjectError` (`project.py`) | 45 | verbatim |
| `write_wav` (`audio.py:301-335`) | 35 | verbatim, stdlib `wave`+`struct` |
| `click.py` DSP (`_tick`, `_biquad_bandpass`, `_one_pole_lowpass`, `stick_hit`) | ~100 | verbatim, deliberately scipy-free |
| `render_click`/`render_sticks` | ~90 | adapt — needs 4 `Timeline` methods; stub them, Woodshed's grid is one bpm + offset |
| `doctor.py` shape | 169 | copy the shape, replace the table |
| `main()` + `_remember_group_parsers` (`cli.py:2827-2884`), `use_utf8` (**`cli.py:71`**) | ~90 | near-verbatim — gives the exit-2 contract once `ProjectError` (`cli.py:2865`) is renamed to `WoodshedError` |
| `console.html` view/draw layer | ~400 of 2,944 | extract the concepts, not the file |
| `sections.py` model | 439 | **do not copy** — non-overlapping bar-anchored tiles, structurally wrong here |

`console.html` carries twelve documented alignment bugs as in-file comments. The three that
matter most: one gutter width for every row (`:25-29`), `border:1px solid transparent` on
every canvas because `box-sizing:border-box` makes a bordered canvas 2px narrower
(`:128-131`), and never clamp off-window marks to x=0 — skip them, or you draw a bar line
where there is none (`:1680-1683`).

### What `../gx100` contributes
Conventions rather than code: Pydantic models in the module that owns the file with
`extra="allow"` for declarations; per-module `INSTALL_HINT` constants naming the exact fix;
`find_spec` probes rather than try/import for heavy optionals; the `paths.py`-constants
design that lets a test redirect the whole repo layout in one loop. Its `song.yaml` shape is
what a Woodshed section's `patch:` resolves against: `patches: [{id, profile, slot}]` and
`structure: [{section, bars, patch, notes}]`, where a section names a local alias and the
alias resolves to a profile and a device slot exactly once.

## Design decisions

- **Rubber Band both sides.** CLI subprocess for cache renders, WASM in the worklet for the
  real-time engine. GPLv2+ accepted; Paolo confirmed the repo may be public.
  *Revised by the 2026-09-05 review*: the consistency argument was overstated — the CLI
  render uses `--fine` (R3) while the worklet runs the real-time path, so auditioning is
  indicative, not identical. The decision stands on measured quality at ratios 1.4–2.5.
  And GPLv2+ propagates to the published `web/`, so the repo needs a top-level `LICENSE`.
- **argparse, not Typer** — rambass's `main()` lifts verbatim and already produces the
  required exit-2 contract.
- **Lift, never import.** Copies carry a docstring line naming the origin. Neither sibling
  repo becomes a dependency; Woodshed builds from a fresh clone with both absent.
- **`tempofit.py` split out of `analyze.py`** so the pure-numpy fit is tested with librosa
  uninstalled — `refine_tempo` exists *because* librosa returns a tempogram bin centre
  rather than a measurement, and the test proving that must not need librosa.
- **`clock.py` is its own module** with `NewType` source/playback seconds, because the
  two-clock hazard is silent and a bare `float` called `t` will eventually be the wrong one.
- **Drop tunings are not in the tuning table** as uniform shifts — "Drop D" lowers one
  string, so it maps to its parent's pitch centre and the arrangement difference is a note,
  not a shift.

## Open questions
- [ ] The three eyes-on-the-unit GX-100 questions in `docs/05-foot-control.md` — settle at
      the pedal before Phase 3 and write the answers back into that file.
- [ ] Whether `rubberband`'s Windows build should be vendored under `tools/` or left to
      `WOODSHED_RUBBERBAND`. Currently the latter, and working. Note `sndfile.dll` must
      travel with the two exes if it is ever vendored.

---

## Open questions from review

These are the ⚠️ judgement calls from the 2026-09-05 combined review. Each was
raised by a reviewer, survived the tiebreak as *partially* accepted, and is parked
here rather than decided unilaterally — the plan has been edited for the parts that
are unambiguous, and these are the parts a human owns.

- [ ] **Local-server hardening: how far?** Accepted and folded in: path containment
      on the three path-taking routes (resolve `<slug>` through `Repo.list_songs()`,
      never concatenate) and one `Host` check on the mutating verbs. Declined as
      theatre for a loopback single-user tool: authentication, CSRF nonces, body-size
      limits. If that posture is wrong, the place to change it is `server.py`'s
      request preamble and it is a five-line difference either way.
- [ ] **GPL: does the repo licence change?** Decision 1 vendors a Rubber Band WASM
      build into published `web/`, which makes the distributed work GPLv2+. A1 now
      adds a top-level `LICENSE`. Confirm that is intended — the alternative is
      `@soundtouchjs/audio-worklet` (LGPL, named in `docs/01-architecture.md:77`) for
      the *real-time* engine only, keeping the GPL binary in the subprocess where the
      boundary is clean. That would cost preview/render consistency, which the review
      showed was a weaker argument than the plan claimed.
- [x] **Which Rubber Band WASM build, and has it been tested in an `AudioWorklet` at
      ratio 2.0?** ~~Not verified during planning.~~ **Resolved 2026-09-05 by
      measurement, not by reading.** The build is `rubberband-wasm@3.3.0` (Daninet,
      npm; Rubber Band 3.3.0 from the official tarball), and we take `dist/rubberband.wasm`
      alone — it is `STANDALONE_WASM`, so it instantiates synchronously in the
      processor constructor from a `WebAssembly.Module` passed through
      `processorOptions`, and the Emscripten JS glue is not needed. Stereo R3 at
      ratio 2.0 holds real time on this machine at ~17 % of one core, zero underruns
      over 10 402 quanta, no NaN, correct output/input frame ratio, pitch intact, and
      survives a ratio change mid-stream. The harness is `tools/rb-probe/` and the
      recorded runs are in `tools/rb-probe/results/`; the findings are written up in
      `docs/03-audio-engine.md`. **Decision 1 stands and the SoundTouch fallback is
      not needed.** Three consequences for D4, all in the doc: the worklet must own
      the source and pull (a quantum-in/quantum-out processor cannot hold a ratio
      other than 1.0, which is why `rubberband-web` is not usable); `startDelay` is
      2048 frames and belongs in `clock.py`; and `performance` does not exist in
      `AudioWorkletGlobalScope`. Still open and human-owned: the *listening* check,
      and confirming the GPL question above before A2 vendors the binary into `web/`.
- [ ] **`pydantic` in the core: bless it or drop it?** The plan now states the
      deviation explicitly and picks "pydantic is a third core dependency", with a
      note to update `CLAUDE.md` and `docs/01-architecture.md` to say three. The
      unchosen alternative — plain dataclasses in `manifest.py` — is cheaper to
      defend against the invariant as literally written. Decide before B6.
- [ ] **`save_song` round-trip: accept comment loss, or edit surgically?** The plan
      now scopes the guarantee to app-written files and says `safe_dump` drops
      comments, quoting and scalar style. `docs/02-data-model.md` prints `song.yaml`
      *with* explanatory comments, so the first UI save on a hand-written file will
      strip them. Accept, or add `ruamel.yaml` (a fourth core dependency) — the
      question the layering rule makes non-trivial.
- [ ] **Does `pre_roll_every_pass: true` actually ship in Phase 1?** G2 now owns it,
      but `docs/00-spec.md` says the setting defaults to first-pass-only "because
      hearing the same bar 30 times is not the point". If it is never going to be
      turned on, it is a declaration with no consumer and G2 should say so instead of
      implementing it.

---

## Combined Review: Codex + Claude Subagent
**Date**: 2026-09-05

### Sources
- **Codex** (OpenAI Codex CLI 0.150.1, read-only sandboxed filesystem access,
  `model_reasoning_effort=medium`). Read the plan, the context, `CLAUDE.md`, `docs/`,
  and both sibling repos.
- **Claude subagent** (`software-architect`, cold independent review, no access to the
  planning conversation). Verified fourteen sibling-repo citations by hand.

Both reviewers were scoped to correctness, safety and the plan's stated requirements,
and told explicitly not to manufacture concerns or propose extra abstraction.

### Codex's feedback
Verdict: *do not implement as written; revise first.* Risk **High**. Five High
concerns: Phase 0 practises on the live stretcher in breach of prime directive 9; the
cache key cannot see a section edit, so a moved boundary loops the old span silently;
ledger safety is insufficient (no lock, no `fsync`, retraction matched on too few
fields, and the model's `passed` contradicts the documented `pass`); `practice.py` and
tap/manual tempo have no owning unit; capture omits the declared overflow refusal.
Medium: the pydantic/layering contradiction, three wrong sibling signatures,
`yaml.safe_dump` cannot promise a byte-identical round trip, and no Origin/containment
checks on the local server.

### Claude subagent's feedback
Risk **Medium-High**, and the sharper diagnosis: the plan's *stated purpose* is
unattended parallel execution against fixed contracts, and three load-bearing
contracts are not actually fixed — `Span` is undefined and its two signature blocks
use different attribute names (`.start` vs `start_s`); speed is percent in four
places and a fraction in a fifth with no stated convention; and `web/` has file names
but no export signatures, so Group D cannot fan out as claimed. Plus H6: every
front-end unit was gated on a `claude.ai` artifact URL no agent can open. It also
confirmed the `tempofit.py` split is *necessary* rather than merely tidy —
`rambass-live/src/rambass/analyze.py:40` imports `require_module` from `.audio` at
module top level.

### Agreed by both (highest confidence)
- `practice.py` missing entirely — a spec'd core module (`docs/01-architecture.md:44`)
  with no unit, taking the next-up score (`docs/00-spec.md:222`) and the cold rule
  with it.
- Render cache cannot detect a section edit → silent stale loop.
- `Rep.passed` contradicts the documented on-disk `pass` (`docs/02-data-model.md:249`).
- Ledger durability and retraction identity underspecified.
- Pydantic-in-core contradicts the plan's own invariant table row 10.
- Three `analyze.py` signatures do not match the sibling.
- Phase 0 practises on the live stretcher (Codex as a breach, Claude as staging).

### Merged assessment
The plan's hard thinking is genuinely done and mostly right: the audio arithmetic
(`--time 1/speed` independent of `--pitch`), the span/lane/coverage model, the
two-clock separation and the transpose precedence all survived two independent
verification passes. Most citations are exact — I re-checked `console.html:25-29`,
`:128-131`, `:1078-1097`, `:1680-1683`, `audio.py:301`, `console.py:58` and the GX-100
CC ordering myself, and all held.

What failed was not the design but the *contract* the design was supposed to hand to
cheap parallel executors. Two correctness bugs would have shipped silently (the `pass`
key into an append-only file; the stale render), one whole module was missing, and
three of the fan-out interfaces were ambiguous enough that parallel units would have
produced incompatible code. Every one of these is fixable by editing the document, and
none required re-deciding anything — which is the outcome a plan review should have.

Applied in this pass: a **Types and units** section fixing `Span` as a Protocol and
settling percent-vs-fraction; `practice.py` and `config.py` added with signatures and
test contracts; a span fingerprint in the cache key; the `pass` alias, a `uuid4` rep
`id`, `retracts`, a write lock and `fsync`; the crossfade term in `Render.loop_end`;
corrected `analyze.py` signatures plus the omitted `_runner_up`; a **Citation
corrections** table separating the nine verified claims from the nine wrong ones; unit
**D0** (JS export stubs), **D7** (pass detection moved into Phase 0), **E4** (tap and
manual tempo), **H3** (capture overflow refusal) and G2's lead-in scope; prerequisite
**P1** reclassifying the artboard rescue as a human task that gates Groups D/F/K/M;
`-m` instead of `-k` on the no-extras gate with `importorskip` and empty-`__init__`
rules; mtime-based eviction; hash-based three-writes test; endpoint-to-unit
attribution; and the ladder's two conflated counters separated.

One finding was corrected rather than accepted: the claim that a real-time worklet
*cannot* run R3 is overstated — Rubber Band 3+ offers R3 in real-time mode. The
licence half of that finding was accepted and is material.

Two findings were declined outright, recorded here so they are not silently dropped:
Codex's suggestion to move an offline render path into Phase 0 (it would destroy the
roadmap's "Phase 0 is usable on its own" shape — the honest fix was to *document* the
staged exception, which is now done), and its request for body-size limits and
auth on the loopback server.

### Decisions
| # | Suggestion | Source | Verdict | Rationale |
|---|---|---|---|---|
| 1 | `practice.py` missing | [X+C] | ✅ Accepted | Spec'd at `docs/01-architecture.md:44`; the next-up score and cold rule had no owner and `coverage_readiness` already assumes its output |
| 2 | Cache blind to a section edit | [X+C] | ✅ Accepted | Span fingerprint in the filename; keeps "the filename is the cache key" and needs no invalidation step to forget |
| 3 | `passed` vs on-disk `pass` | [X+C] | ✅ Accepted | `docs/02-data-model.md:249` fixes the key; append-only means wrong is permanent |
| 4 | Ledger lock/fsync + retract by id | [X+C] | ✅ Accepted | `ThreadingHTTPServer` can serve a UI and a MIDI rep concurrently; the one irreplaceable file |
| 5 | Pydantic-in-core contradiction | [X+C] | ✅ Accepted | Resolved explicitly in Decisions §5 with a note to fix the two docs; the conflict is in the source docs, not the plan |
| 6 | Wrong `analyze.py` signatures | [X+C] | ✅ Accepted | Verified: `find_grid_anchor` returns `tuple[float, dict]` and takes onsets; `grid_confidence` returns a dict |
| 7 | Server traversal / Origin | [X+C] | ⚠️ Partial | Containment + one `Host` check accepted; auth and body limits declined as theatre on a loopback socket |
| 8 | Crossfade vs `loopEnd` | [X] | ✅ Accepted | Real arithmetic hole; without the term every pass replays the faded head — the tick the Phase 2 gate listens for |
| 9 | `Span` undefined, two vocabularies | [C] | ✅ Accepted | Strongest single finding; it is what lets B3 and B6 run in parallel at all |
| 10 | Percent vs fraction unfixed | [C] | ✅ Accepted | Four units implement against it concurrently; exactly the silent unit mix-up the plan front-loads to prevent |
| 11 | `web/` has no signatures | [C] | ✅ Accepted | Added D0; the plan claimed fan-out it had not enabled |
| 12 | Artboards unreachable by an agent | [C]+[X] | ✅ Accepted | Reclassified as human prerequisite P1; confirmed `design/` holds no `.dc.html` and `design/seed` does not exist |
| 13 | Pass detection is in Phase 2 | [C] | ✅ Accepted | Phase 0's own gate asserts five ledger lines; moved to D7 |
| 14 | Phase 0 uses the live stretcher | [X+C] | ⚠️ Partial | Documented as sanctioned staging per `docs/07-roadmap.md`; invariant 9 becomes binding at the Phase 2 gate. Restructuring Phase 0 declined |
| 15 | Tap / manual tempo unowned | [X] | ✅ Accepted | `docs/07-roadmap.md:29` requires both, "each recording which it was"; added E4 |
| 16 | Capture overflow refusal unowned | [X] | ✅ Accepted | `docs/04-sources.md:92` is explicit; a silent dropout that binds is a corrupt source found months later |
| 17 | `pre_roll_every_pass` + lead-in beats | [C] | ✅ Accepted | `docs/00-spec.md:98` lists lead-in beats per section; nobody owned beats→seconds |
| 18 | `-k` should be `-m` | [C] | ✅ Accepted | `-k` matches names, not markers — the gate would have deselected nothing |
| 19 | `safe_dump` ≠ byte-identical | [X] | ✅ Accepted | Guarantee rescoped to app-written files; open question parked on whether to accept comment loss |
| 20 | Three-writes test → hashes | [X] | ✅ Accepted | A file-list diff passes an in-place ledger rewrite, the one thing it most needs to catch |
| 21 | NTFS atime breaks LRU | [C] | ✅ Accepted | Last-access updates are off by default on Windows; mtime instead |
| 22 | No config loader | [C] | ✅ Accepted | `GET /api/config` was served with nothing loading the file; added `config.py` |
| 23 | Citation corrections | [C]+[X] | ✅ Accepted | Nine verified, nine wrong; table added so the wrong ones cannot be trusted on sight |
| 24 | `validate()` and `duration_s = 0` | [C] | ✅ Accepted | Would refuse every span on a `needs-audio` song, breaking degrade-do-not-refuse |
| 25 | Worklet "cannot run R3" | [C] | ❌ Declined (half) | Overstated — Rubber Band 3+ has an R3 real-time mode. **Confirmed by measurement 2026-09-05**: R3 real-time in a worklet at ratio 2.0, ~17 % of one core, zero underruns (`tools/rb-probe/`). The GPL-propagation half accepted |
| 26 | Move offline render into Phase 0 | [X] | ❌ Declined | Destroys "Phase 0 is usable on its own"; the staged exception is documented instead |

**Risk assessment**
- Codex: High | Claude subagent: Medium-High | **Mine: Medium-High before this pass,
  Low-Medium after it.** The residual risk is concentrated in the four open questions
  above — of which only the Rubber Band WASM build is a genuine unknown that could
  force a Phase 0 redesign. Everything else is a preference to confirm.
- **Update 2026-09-05: the WASM unknown is closed by measurement** (see the ticked
  question above and `tools/rb-probe/`). It came back a pass, so no Phase 0 redesign.
  What is left in that list is preference and one listening test, which puts the
  residual risk at Low.
