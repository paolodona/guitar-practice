# Plan project profile

Read by the `engineering` plugin's plan commands. Keep it accurate — it is the grounding for every
plan created in this repo. Update it when the repo changes shape (`/plan-init refresh`).

## What this repo is

A single-user guitar practice tool (working name **Woodshed**): slow a commercial recording without
changing its pitch, transpose it to the band's tuning, loop a named section, and count the reps and
the speed. A local Python server plus a vanilla-JS browser front end, plus a CLI.

**The repo is currently docs-only.** `docs/` is the specification; no implementation exists yet.
Phase 0 of `docs/07-roadmap.md` is the first thing to build.

The thing a newcomer gets wrong: this is not a product. Optimise for *Paolo's* repeatability, not
for generality — and never invent a measurement the tool cannot make (no pitch detection, no
"you played that wrong").

## Components

Specified in `docs/01-architecture.md`. None of these files exist yet — the table is the target
layout that plans should place code into.

| Component | Path | Purpose | Key constraint |
|---|---|---|---|
| Pure core | `src/woodshed/{library,manifest,sections,ladder,ledger,practice}.py` | repo layout and slugs, YAML schemas, spans/snapping/containment/lanes/coverage, speed ladder and rep counting, append-only rep log, readiness and next-up | pyyaml + numpy + pydantic and nothing else; must import from a fresh clone with no extras. Pydantic is scoped to the parsing boundary (`manifest`, `setlist`); `sections` and `ladder` are pure functions with stdlib and numpy only. |
| Server | `src/woodshed/server.py` | JSON endpoints, range-served media | Standard library only (`ThreadingHTTPServer`). Writes exactly three things: deliberate `song.yaml`/`setlist.yaml` edits, one appended ledger line per rep, files under `cache/`. |
| Sources / peaks | `src/woodshed/sources.py`, `src/woodshed/peaks.py` | Spotify search/import and library scan; multi-resolution waveform peaks | `urllib` and `numpy` respectively. No audio path for any service whose terms forbid it. |
| Heavy edge | `src/woodshed/{analyze,render,capture}.py` | tempo/beat grid (librosa), offline stretch/shift (rubberband binary), loopback recording (pyaudiowpatch) | The **only** modules allowed a heavy or external dependency. Nothing above this line may import them at module top level. |
| CLI | `woodshed <command>` | every UI action is a thin call onto a function the CLI also calls | Errors raise `WoodshedError` with a message saying what to do next; `main()` turns those into one line and exit code 2. A traceback is a bug. |
| Front end | `web/` | Web Audio graph, canvas waveform, section drawing, ladder state, Web MIDI, the five screens | Vanilla ES modules — no bundler, no framework. The WASM stretcher is vendored under `web/vendor/` with its licence and version; never a CDN. |
| Data | `songs/`, `setlists/`, `practice/reps.jsonl`, `config.yaml` | the database | `practice/reps.jsonl` is append-only and the one irreplaceable file. Audio bytes never enter git. |
| Docs | `docs/` | source of truth for behaviour | Read `docs/00-spec.md` before changing behaviour and `docs/03-audio-engine.md` before touching anything that makes sound. |
| Design | `design/` | Claude Design canvas seed (`gen.py`, `derive.py`, `canvas.json`) | `design/*.html` is generated and gitignored — rebuilt by the seed, not hand-edited. |

## Code placement rules

- **Default target for new logic: the pure core in `src/woodshed/`.** Only reach for
  `analyze.py` / `render.py` / `capture.py` when the work genuinely needs librosa, the rubberband
  binary, or an audio device.
- **Dependency direction**: pure core ← server ← CLI. `library` / `manifest` / `sections` /
  `ladder` / `ledger` / `practice` / `server` need **pyyaml, numpy and pydantic and nothing
  else** — three core dependencies, and the word doing the work is *heavy*: no librosa, no audio
  device, no external binary. **Pydantic is scoped to the parsing boundary**: only `manifest.py`
  and `setlist.py` import it, and `sections`, `ladder`, `ledger`, `clock` and `tuning` import
  stdlib and numpy and nothing else, so the pure maths stays testable with the minimum installed.
  Nothing above the `capture` / `analyze` / `render` line may import them at module top level. Use a
  `require_module()`-style helper so a missing extra prints an install hint naming the installer
  that actually exists, not an `ImportError` traceback.
- **`src/woodshed/` is generic**: no song names, no artist names, no personal file paths in code.
  Those live in `songs/`, `setlists/` and `config.yaml`.
- `songs/*/audio/` and `songs/*/cache/` are gitignored and stay that way. These are commercial
  recordings there is a licence to listen to, not to publish — check before `git add -A`.
- Secrets never go in `config.yaml` (it is tracked). Tokens go in `~/.woodshed/credentials.json`.
- **One action table shared by mouse, keyboard and MIDI. Never two.**
- This repo is the third of three (`gx100` the sounds, `rambass-live` the backing tracks). Cross-
  reference by slug, never by copying content. Code may be **lifted** from the other two repos
  (copied in and adapted, with attribution in the docstring) — but never imported: this repo must
  build and run with the other two absent.

## Architectural principles

1. **The repo is the database.** Derive every screen from disk per request. If the UI and the files
   disagree, the files are right and the UI has a bug.
2. **The ledger is append-only.** A retraction is an appended line, not a deletion. Every count on
   every screen is an aggregate of `practice/reps.jsonl`. **Never write a summary into `song.yaml`** —
   the moment a count lives in two files they can disagree and nothing says which is right.
3. **Section positions are in seconds, deliberately.** A section describes a position in an
   immutable commercial recording; there is nothing for a bar number to protect. Bars are
   *displayed*, derived from `tempo.bpm` + `grid_offset_s`. Do not "fix" this to bars.
4. **Sections overlap and nest; they are spans, not tiles.** Containment and lane assignment are
   derived from the spans and nothing about the nesting is stored, so a dragged boundary cannot
   leave a stale parent behind. Order and lanes are both `(start_s, -duration)`. Only an exact
   duplicate span is refused.
5. **Readiness is measured over covered song time**, each second taking the `reached` of the
   *longest* section covering it — not a length-weighted mean over sections, which double-counts a
   subdivided solo; and "longest" rather than "best" because playing the lick in isolation is not
   playing the solo.
6. **Two clocks, and mixing them up is silent.** *Source time* is seconds in the original file (what
   `song.yaml` stores). *Playback time* is seconds in the rendered, stretched section, where the
   pre-roll occupies the head. Two functions, two names, converted at the boundary and nowhere else.
7. **Transpose is per song; the setlist only supplies a default.** The shift lives on the setlist
   entry (`setlist.songs[].shift`), because the same song wants a different shift in the E♭ band and
   the E covers duo. The derivation `pitch(setlist.tuning) - pitch(song.recording.tuning)` fills it
   in and nothing more. Directly editable from the practice view (`−`/`+`, or `-`/`=`), shown as a
   number, range-limited to ±6.
8. **Never resample to change speed or pitch.** Rubber Band, `--fine`, `--formant`. Do not copy
   `rambass-live`'s WSOLA conclusion into this repo: its ratios are 0.81–1.21 on transient-led
   material, ours are 1.4–2.5 on a dense mix, and WSOLA flutters there. The reasoning is in
   `docs/03-audio-engine.md` and it is measured, not preferred.
9. **Practice speeds are discrete, so the renders are.** A loop plays from a pre-rendered, decoded
   `AudioBuffer` with a native sample-exact loop — never from a real-time stretcher, which cannot
   put the seam in the same place twice. The real-time engine is for dragging the slider; the cache
   is for practising.
10. **The tool never judges the playing.** No pitch detection. A pass is counted; whether it was
    *clean* is something a human confirms.
11. **Never send Program Changes to the GX-100 without an explicit toggle.** A practice tool should
    not silently change the pedal that is about to be played on a gig.
12. Don't build a Spotify audio path, a DRM-decrypting path, or a bundled downloader for a service
    whose terms forbid it. `capture.py` records an **audio device** and knows nothing about any
    service; what it is pointed at is a human's call. `docs/04-sources.md` has the reasoning.

## Reference material

| Path | What it is |
|---|---|
| `docs/00-spec.md` | Source of truth for behaviour, screen by screen. Read before changing behaviour. |
| `docs/01-architecture.md` | Layering table, stack choices, and what to lift from the other two repos. |
| `docs/02-data-model.md` | `song.yaml`, `setlist.yaml`, the rep ledger. |
| `docs/03-audio-engine.md` | Time-stretch, pitch shift, seamless looping. Measured, load-bearing — read before touching anything that makes sound. |
| `docs/04-sources.md` | Capture, local files, and what Spotify can and cannot give you. |
| `docs/05-foot-control.md` | Hands-free MIDI control; six actions. |
| `docs/06-design-brief.md` | The visual brief. |
| `docs/07-roadmap.md` | Build order. Phase 0 is usable on its own. |
| `CLAUDE.md` | The invariants, verbatim. Outranks this file where they differ. |
| `../rambass-live/` (sibling checkout, **not** a dependency) | Source of lifted code: `console.py` (`parse_byte_range`, handler shape), `analyze.py` (`refine_tempo`, `pulse_wander`), `click.py`, `audio.py` (`locate_tool`, `require_module`), `project.py` (`slugify`, `parse_position`), `console.html` (waveform/ruler/playhead alignment). |
| `../gx100/` (sibling checkout, **not** a dependency) | Patch/`structure:` block shapes for the cross-reference; the "sequencing as data" pattern in `data/workflow.yaml`. |

## Build & verification

- **Build**: no build step — vanilla ES modules served directly, no bundler.
- **Unit tests**: `uv run pytest` — **TODO: not yet runnable.** There is no `pyproject.toml` in the
  repo. Until the first plan scaffolds the package, no plan can specify a working automated gate;
  say so rather than pretending the command passes.
- **Scoped tests**: `uv run pytest tests/test_<module>.py -k <expr>` — pick the filter from the pure
  module under change (`sections`, `ladder`, `ledger`, tuning maths, snapping).
- **Lint / format**: `uv run ruff check .` (same TODO — needs the package scaffolded first).
- **Warnings as errors**: no convention established.
- **Do NOT run unprompted**: anything that opens a real audio device (`capture.py`), talks to the
  GX-100 over MIDI, or calls a Spotify endpoint. Never send a Program Change without the toggle.
- **Quality gate (Stop hook)**: not configured. Deliberate — with no `pyproject.toml` the command
  would fail on every turn and block the session. Add `uv run pytest` here once Phase 0 scaffolding
  lands and the suite actually runs.

## Success-criteria conventions

**Build new features test-first**, including when the change looks too small to need one. A number
that came from a measurement gets a test naming the measurement. Verification done by hand belongs
in `tests/`, not in a transcript.

The pure modules (`sections`, `ladder`, `ledger`, tuning maths, snapping) must run with no audio, no
librosa, no ffmpeg and no browser — keep them that way; that is what makes test-first cheap. A plan
phase whose only verification needs a real audio device or the pedal must say so explicitly and
leave that check to a human.

Python 3.12+, `uv` for env and running (`uv run woodshed ...`), `ruff` for lint. Schemas are Pydantic
models in the module that owns them; YAML is the on-disk form. Slugs are lowercase-kebab with
accents folded (`cant-stop`, `sultans-of-swing`).

## Version management

This repo does not version-bump per change. No tags, no CHANGELOG.

## Repo etiquette

- **Default branch**: `main`. **Work directly on `main`** — Paolo is the only maintainer and the only
  user, so there are no feature branches and no pull requests here unless explicitly asked.
- **Branch naming**: n/a — no branching convention, because there is no branching.
- **Issue tracker**: github (`paolodona/guitar-practice`, `gh` authenticated). The `/issue-start`,
  `/issue-pr` and `/backlog-sync` commands are available — though `/issue-pr` conflicts with the
  work-on-`main` rule, so use it only when a PR was explicitly asked for.
- **Commit convention**: sentence-case imperative subject, no prefix, no conventional-commit type,
  no ticket reference. Existing examples: "Sections are overlapping spans, not tiles",
  "Fix rendering and arithmetic faults found reviewing the artboards".
- **AI attribution in commits**: required trailer —
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` — present on every commit in history.
- **Shell**: Windows + **PowerShell 5.1**. Any command written for Paolo to run must be PowerShell.
  No `&&`, no ternary, no `??`. Use `A; if ($?) { B }`.
- **Permission pre-configuration**: accepted — see `.claude/settings.json`.
