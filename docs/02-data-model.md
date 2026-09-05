# Data model

Three kinds of file, and the distinction between them is the whole model:

| file | what it holds | who writes it |
|---|---|---|
| `songs/<slug>/song.yaml` | **declarations** — what is true about the song and what you intend | you, and the UI on your behalf |
| `practice/reps.jsonl` | **events** — what actually happened, one line per rep | the app, append-only, never rewritten |
| `songs/<slug>/cache/` | **derived** — peaks, rendered variants | the app, always safe to delete |

Anything that can be derived is not stored. Anything measured off audio says so.
Anything you decided is a declaration. That three-way split is lifted straight
from `rambass-live` (`declared, not detected`) and it is what stops a guess
becoming a fact six months later.

---

## `song.yaml`

```yaml
slug: cant-stop
title: Can't Stop
artist: Red Hot Chili Peppers
album: By the Way

recording:
  file: audio/cant-stop.flac      # relative to the song dir. Bytes never in git.
  sha256: 9f2c…                   # so a re-encode or a re-rip is visible, not silent
  duration_s: 269.41
  tuning: E standard              # what the RECORD is in — not what you play it in
  spotify_id: 3ZOEytgrvLwQaqXreDs2Jx
  source: "own rip, CD"           # free text. Where the bytes came from.

tempo:
  bpm: 91.53
  source: refined                 # detected | refined | tapped | manual
  grid_offset_s: 0.412            # where bar 1 beat 1 lands in the FILE
  time_signature: 4/4
  confidence: 0.86                # what the fit reported. null when typed by hand.

practice:
  start_speed: 50
  ladder_step: 5
  reps_to_advance: 3
  pre_roll_beats: 4
  pre_roll_every_pass: false
  click: lead-in                  # off | lead-in | always
  loop_crossfade_ms: 10

sections:
  - id: intro
    name: Intro riff
    start_s: 0.412
    end_s: 21.874
    snapped: beat                 # beat | bar | free — how the boundary was placed
    target_speed: 100
    notes: "16ths, muted. The right hand never stops."
    patch: rhythm                 # optional: a patch id in gx100 songs/<slug>/song.yaml
  - id: solo
    name: Solo
    start_s: 118.402
    end_s: 146.930
    snapped: bar
    target_speed: 95              # you are allowed to decide 95 is enough
    ladder_step: 2.5              # per-section override of the song default
```

### Why sections are in seconds

`rambass-live`'s invariant is *positions are in bars, never seconds*, and it is
right there — because a tempo can change and every bar-anchored thing must move
with it.

**Here the opposite is true, for the same reason.** A Woodshed section describes
a position in an **immutable commercial recording**. The file will never be
re-rendered at a different tempo; there is nothing for a bar number to protect.
This is the identical exception `rambass-live` already grants twice: `lyrics.srt`
(hand-timed against a specific master) and `practice/align.yaml` (bars → seconds
*in the original album recording*). Same justification, third instance.

Bars are still shown — a ruler and a beat grid derived from `tempo` — and a
section records *how* its boundary was placed (`snapped: beat`) so that
re-detecting the tempo can offer to re-snap. But seconds are what is stored, and
`grid_offset_s` + `bpm` is what turns them into bars for display.

If `tempo.bpm` is 0 or absent, the app still works: no grid, no click, no bar
ruler, free-dragged boundaries. Degrade, do not refuse.

### `sha256`, and why it is not fussy bookkeeping

The whole library is bound to files you can move, re-rip, re-tag or replace with
a remaster. A section at 118.402 s means something different in a remaster whose
intro is 300 ms longer, and **nothing in the UI would say so**. The hash is
checked on load; a mismatch shows a banner naming the song and offering to
re-detect the grid. Cheap to compute once, and it is the only thing standing
between you and silently wrong loops.

---

## `setlists/<slug>.yaml`

```yaml
name: Ramba S.S. — the set
tuning: Eb standard             # THE BAND'S tuning. This is what makes the transpose.
date: 2027-04-17                # optional. Drives the countdown.
venue: ""
songs:
  - cant-stop
  - manlio
  - by-the-way
notes: |
  Free text. Why the order is what it is.
```

Songs by slug, never copied. A song in three setlists is one song with one
practice history.

### Transpose is derived, never typed

```
semitones = pitch_of(setlist.tuning) - pitch_of(song.recording.tuning)
```

with a table of named tunings (`E standard` 0, `Eb standard` −1, `D standard`
−2, `drop D` 0 — same pitch reference, different string) and an explicit
`transpose_override` on the song for the cases a name cannot express.

Two consequences, both good:

* Changing the band's tuning re-tunes the **whole setlist** in one edit. That is
  the same argument `rambass-live` makes for bar-anchored positions, applied to
  pitch: store the intent, derive the number.
* The same song practised for the E♭ band and the E covers group is one song
  with two derived offsets, and the app can render both variants into the cache
  without either being "the" version.

A tuning is a name in the UI, never a number: you pick "E♭ standard", you do not
type "−1". The number appears next to it as a check.

---

## `practice/reps.jsonl`

One JSON object per line, appended, never edited, never rewritten. This is the
only file in the repo that cannot be reconstructed from anything else, and
append-only is what makes it survive a crash mid-session.

```json
{"t":"2026-09-05T19:22:41Z","song":"cant-stop","section":"solo","speed":55,
 "semitones":-1,"pass":true,"clean":true,"loop_s":31.2,"setlist":"gig","source":"midi"}
```

| field | meaning |
|---|---|
| `pass` | the loop completed without a seek or a pause |
| `clean` | you confirmed it (or auto-confirm was on) |
| `speed` | percent of original tempo. The other half of every statistic |
| `semitones` | so a stat can tell "practised in E♭" from "practised in E" |
| `loop_s` | wall-clock length of the pass; makes "minutes practised" free |
| `source` | `midi` / `keyboard` / `ui` / `auto` — which input filed it |
| `retracted` | present and `true` on a line that undoes the previous rep. **A retraction is an appended line, not a deletion** |

Everything on the dashboard is an aggregate of this file:

* *"practised 25 times at 55 %"* = `count(pass) where speed == 55`.
* *best sustained speed* = the highest `speed` with at least `reps_to_advance`
  clean reps.
* *last practised* = `max(t)`.
* *cold* = `now - max(t) > 14 days` and `reached > 0.8`.

Aggregates are computed on read and cached in memory, not written back. A
year of daily practice is on the order of tens of thousands of lines — a few
megabytes, parsed in milliseconds. If it ever is not, the fix is a derived index
under `cache/`, not a mutable state file.

**Never write a summary into `song.yaml`.** The moment "reps: 25" lives in a
declaration file, a hand edit and a crash can disagree with the ledger and
nothing says which is right. This is the same rule both other repos state as
*never write results back into knowledge automatically — a human promotes a
finding*.

---

## `config.yaml`

```yaml
library_paths:                    # scanned by `woodshed scan`
  - "D:/Music"
defaults:
  start_speed: 50
  ladder_step: 5
  reps_to_advance: 3
  pre_roll_beats: 4
  auto_confirm: true
midi:
  input: "BOSS GX-100"            # substring match on the port name
  channel: 1
  map:                            # see docs/05-foot-control.md
    80: play_pause
    81: next_section
    82: prev_section
    83: speed_up
    84: speed_down
    85: retract_rep
spotify:
  client_id: ""                   # your own app. Never a secret in this file — see 04.
render:
  engine: rubberband              # rubberband | soundtouch | none
  formant_preserve: true
  cache_max_gb: 20
```

---

## `cache/`

```
songs/<slug>/cache/
  peaks-1024.json                 # multi-resolution waveform peaks
  grid.json                       # detected beat positions, if analysis ran
  solo@60x-1st.flac               # section `solo`, 60 % speed, −1 semitone
  intro@100x0.flac
```

The filename **is** the cache key: `(section, speed, semitones)`. Anything in
here is reproducible from `song.yaml` + the source file + `render.py`, so a
cache wipe costs CPU and nothing else. `woodshed doctor` reports its size; a
`cache_max_gb` evicts least-recently-used.
