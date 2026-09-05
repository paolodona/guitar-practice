# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this repo is

A guitar practice tool for one person: slow a song down without changing its
pitch, drop it to the band's tuning, loop a named section, and count the reps and
the speed. Local web app plus a CLI. Not a product — optimise for *my*
repeatability, not for generality.

Read `docs/00-spec.md` before changing behaviour and `docs/03-audio-engine.md`
before touching anything that makes sound. The engine doc is not preference; it
is the part that is easy to get plausibly wrong and hard to notice.

It is the third of three repos that work together: `gx100` (the sounds),
`rambass-live` (the backing tracks), `woodshed` (the practising). Cross-reference
by slug, never by copying content — the same rule the other two enforce.

## Shell

Windows + **PowerShell 5.1**. Any command written for me to run must be
PowerShell. No `&&`, no ternary, no `??`. Use `A; if ($?) { B }`.

## The invariants

**Section positions are in seconds, and that is deliberate.** `rambass-live`'s
rule is bars-never-seconds, and it is right there because a tempo can change. A
Woodshed section describes a position in an **immutable commercial recording**;
there is nothing for a bar number to protect. It is the same exception that repo
already grants to `lyrics.srt` and `practice/align.yaml`. Bars are *displayed*,
derived from `tempo.bpm` + `grid_offset_s`. Do not "fix" this.

**Two clocks, and mixing them up is silent.** *Source time* is seconds in the
original file (what `song.yaml` stores). *Playback time* is seconds in the
rendered, stretched section, where the pre-roll occupies the head. Two functions,
two names, converted at the boundary and nowhere else.

**Transpose is per song; the setlist only supplies a default.** A band in E♭ does
not mean every record needs moving — some are already in E♭. The shift lives on
the **setlist entry** (`setlist.songs[].shift`), because the same song wants a
different shift in the E♭ band and the E covers duo; the derivation
`pitch(setlist.tuning) - pitch(song.recording.tuning)` fills it in and nothing
more. It is directly editable from the practice view (`−`/`+`, or `-`/`=`), shown
as a number, and range-limited to ±6.

**The ledger is append-only and it is the only irreplaceable file.**
`practice/reps.jsonl`, one line per rep, never rewritten. A retraction is an
appended line, not a deletion. Every count on every screen is an aggregate of it.
**Never write a summary into `song.yaml`** — the moment a count lives in two
files they can disagree and nothing says which is right.

**The repo is the database.** Every screen is derived from disk per request. The
server writes exactly three things: deliberate `song.yaml` / `setlist.yaml`
edits, one appended ledger line per rep, and files under `cache/` that are always
safe to delete. If the UI and the files disagree, the files are right and the UI
has a bug.

**Never resample to change speed or pitch.** That is `rambass-live`'s
`warp_samples` bug, and here the ratios are large enough that it drops the whole
record an octave. And **do not copy that repo's WSOLA conclusion into this one**:
its ratios are 0.81–1.21 on transient-led material, ours are 1.4–2.5 on a dense
mix, and WSOLA flutters there. Rubber Band, `--fine`, `--formant`. The reasoning
is in `docs/03-audio-engine.md` and it is measured, not preferred.

**Practice speeds are discrete, so the renders are.** A loop plays from a
pre-rendered, decoded `AudioBuffer` with a native sample-exact loop — never from
a real-time stretcher, which cannot put the seam in the same place twice. The
real-time engine is for dragging the slider; the cache is for practising.

**The tool never judges the playing.** No pitch detection, no "you played that
wrong". A pass is counted; whether it was *clean* is something a human confirms.
Inventing a measurement the tool cannot make is the failure mode `gx100/CLAUDE.md`
spends a page on and it applies here unchanged.

## Layering

`library` / `manifest` / `sections` / `ladder` / `ledger` / `practice` / `server`
need **pyyaml and numpy and nothing else**. `analyze.py` (librosa) and
`render.py` (the rubberband binary) are the only modules allowed a heavy or
external dependency, and nothing above that line may import them at module top
level. Use a `require_module()` helper so a missing extra prints an install hint
naming the installer that actually exists, not an `ImportError` traceback.

`src/woodshed/` is **generic**: no song names, no artist names, no personal file
paths in code. Those live in `songs/`, `setlists/` and `config.yaml`.

## Conventions

* Python 3.12+, `uv` for env and running (`uv run woodshed ...`), `ruff` for lint.
* Schemas are Pydantic models in the module that owns them; YAML is the on-disk form.
* Slugs are lowercase-kebab, accents folded (`cant-stop`, `sultans-of-swing`).
* CLI errors raise a `WoodshedError` with a message that says what to do next;
  `main()` turns those into one line and exit code 2. Anything else is a
  traceback, which is a bug.
* One action table shared by mouse, keyboard and MIDI. Never two.

## Testing

`pytest`. The pure modules (`sections`, `ladder`, `ledger`, tuning maths,
snapping) run with no audio, no librosa, no ffmpeg, no browser — keep them that
way, and that is what makes test-first cheap.

**Build new features test-first**, including when the change looks too small to
need one. A number that came from a measurement gets a test naming the
measurement. Verification done by hand belongs in `tests/`, not in a transcript.

## Don't

* Don't commit audio. `.gitignore` covers it; check before `git add -A`. These
  are commercial recordings I have a licence to listen to, not to publish.
* Don't put a secret in `config.yaml` — it is tracked. Tokens go in
  `~/.woodshed/credentials.json`.
* Don't build a Spotify audio path, a DRM-decrypting path, or a bundled
  downloader for a service whose terms forbid it. `capture.py` records an **audio
  device** and knows nothing about any service — that is the general answer, and
  what it is pointed at is a human's call. `docs/04-sources.md` has the reasoning.
* Don't send Program Changes to the GX-100 without an explicit toggle. Same
  instinct as that repo's edit-buffer-only rule: a practice tool should not
  silently change the pedal I am about to play a gig on.
