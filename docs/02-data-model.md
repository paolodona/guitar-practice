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
slug: can-t-stop               # slugify turns the apostrophe into a separator
title: Can't Stop
artist: Red Hot Chili Peppers
album: By the Way

recording:
  file: audio/cant-stop.flac      # relative to the song dir. Bytes never in git.
                                  # (the FILE keeps whatever name it was bound with;
                                  #  only the slug is derived)
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
  loop_crossfade_ms: 10

sections:
  - id: intro
    name: Intro riff
    start_s: 0.412
    end_s: 21.874
    snapped: beat                 # beat | bar | free — how the boundary was placed
    target_speed: 100
    notes: "16ths, muted. The right hand never stops."

  # Sections may overlap and nest. These three describe the same music
  # at three grains, and all three are real practice targets.
  - id: solo-full
    name: Full solo
    start_s: 178.400
    end_s: 262.900
    snapped: bar
    target_speed: 100
  - id: solo-tapping
    name: Solo — first part, tapping
    start_s: 178.400
    end_s: 201.150
    snapped: bar
    target_speed: 100
    start_speed: 45                # per-section override of the song default
    ladder_step: 2.5               # per-section override of the song default
    notes: "Right hand from the 12th. Start at 45%, this is the whole problem."
  - id: solo-run
    name: Solo — the descending run
    start_s: 236.700
    end_s: 248.300
    snapped: beat
    target_speed: 95
  - id: whole-song
    name: Whole song
    start_s: 0
    end_s: 269.410
    snapped: free
    target_speed: 100
    full_song: true                 # reps count; excluded from next/prev and readiness

# #3: one song-level timeline of program changes, replacing a per-section
# `patch:` field entirely. `patch` names a GX-100 memory reachable by a
# bare Program Change (U01-1..U32-4 -- docs/05-foot-control.md); resolving
# "the patch for section [X, Y)" is the last entry with at_s <= X, falling
# back to U01-1 when the list is empty or nothing qualifies.
patch_changes:
  - at_s: 0.0
    patch: U01-1
  - at_s: 88.0
    patch: U02-3
```

**The comments in that example are documentation, not data.** The app writes
`song.yaml` with `yaml.safe_dump`, which preserves field order but not comments,
quoting or scalar style — so the first save from the UI on a hand-annotated file
strips them. That is accepted rather than worked around: `song.yaml` is tracked in
git, so a rewrite is a visible diff and one `git checkout` from being back, and the
alternative (`ruamel.yaml`) means carrying a second document representation and
keeping it in sync with the models on every write. **Durable prose about a section
belongs in `notes:`, which is a real field the app round-trips**, not in a YAML
comment.

### Sections may overlap, and nest, and that is the point

*"Master of Puppets — full solo"* and *"Master of Puppets — solo, first part,
tapping"* are both things to practise, and the second lives inside the first. So
a section is **an arbitrary span, not a tile**: sections are free to overlap, to
nest to any depth, and to leave parts of the song covered by nothing at all.

Nothing about containment is stored. It is computed from the spans, which means
dragging a boundary can never leave a stale parent pointer behind:

```
contains(a, b)  ==  a.start <= b.start and b.end <= a.end and a is not b
```

Four consequences, each of which had to be decided rather than assumed:

**Lanes are derived, never stored.** Sort by `(start_s, -duration)` and assign
each section to the first lane with no conflict. Longest-first puts the
containing section on lane 0 with its drills stacked underneath, which is the
reading a human expects, and a re-sort after an edit is deterministic — the same
spans always draw the same way.

**Order is `(start_s, -duration)` too**, so `next section` walks a flat list in
which a container comes immediately before the drills inside it. Not a tree walk:
descending into a nesting level would need a seventh foot action, and six is the
limit for a reason. The practice view instead prints the containment under the
section name — *inside Full solo · bars 1–8 of 30* — so where you are is a fact
on screen rather than a thing to remember.

**Readiness is measured over song time, not over sections.** The old rule (a
length-weighted mean of the sections' `reached`) silently double-counts the
moment a solo is subdivided: three overlapping views of one solo would give that
solo three votes and swamp the rest of the song. Instead:

> Every second of the song that **any** section covers contributes exactly once.
> A second covered by several sections takes the `reached` of the **longest**
> section covering it. The denominator is covered time, not the song's length.

Longest, not best, and the reason is musical: nailing the tapping lick at 100 %
in isolation does not mean you can play the solo through, and the section that
most nearly means "played in context" is the widest one. So a drill that runs
ahead of its container does **not** move the song's number — it shows as *ahead*
beside the parent, and the parent is what the gig will ask for. That is the tool
telling the truth rather than flattering you.

Seconds no section covers are outside the denominator entirely: they are not
part of what you set out to learn, and counting them would make a song you have
deliberately only half-mapped look unfinished forever.

`counts_toward_readiness: false` is the escape hatch for a section that is pure
exercise — a chromatic warm-up carved out of a verse — and it is the only stored
thing about a section's role. Everything else is derived.

**`full_song: true` is the whole-song entry** — a section spanning the entire
recording, kept so its reps count like any other section's, but excluded from
two things `counts_toward_readiness: false` alone would not exclude it from:
`next section`/`previous section` (it is a rep counter, not a practice target
to step through), and the readiness bar itself — being the longest possible
span, it would otherwise always win the "longest covering span" tie-break
above and silently replace every other section's contribution with its own.
One field, so creating one is a single decision rather than two flags that
have to be remembered together.

**A `full_song: true` section is created automatically** the moment a
recording is first bound (`woodshed add`, `POST /api/song/upload`, and both
capture-binding paths — `manifest.whole_song_section`) — every song has
something to Practice from the moment it exists, with no section drawn by
hand first. It is an ordinary section in every other respect — trimming its
`start_s`/`end_s` for a long intro or outro is a plain update — **except
deletion, which is refused** (`manifest.ensure_deletable`, checked by both
`woodshed section rm` and the inspector's own delete) while `full_song` is
still set: removing it would leave a song with no default practice target,
and orphan its ledger rows (still on disk — the ledger never loses a line —
but with no section left to show them against). Un-checking `full_song`
first is the escape hatch for someone who really means to remove it; that
turns it into an ordinary section, deletable like any other.

**The invariants that remain.** `start < end`; both inside the file; `id` unique
within the song. Two sections with the *identical* span are refused — that is a
duplicate, not an overlap, and it makes lanes and ordering ambiguous for no
gain. Nothing else about the arrangement of spans is checked, because there is
nothing left that would be wrong.

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

If `tempo.bpm` is 0 or absent, the app still works: no grid, no bar ruler,
free-dragged boundaries. Degrade, do not refuse.

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
  - can-t-stop                  # takes the derived shift for this setlist
  - slug: manlio
    shift: 0                    # the record is already in E♭ — nothing to move
  - slug: sultans-of-swing
    shift: -2                   # I want this one lower than the band's tuning
notes: |
  Free text. Why the order is what it is.
```

Songs by slug, never copied. A song in three setlists is one song with one
practice history.

### Transpose is per song. The setlist only supplies a default.

**A band in E♭ does not mean every record needs moving.** Some of these
recordings are already in E♭ and must be left alone; some are in E and have to
come down one; and sometimes you will just want a song lower than the band's
tuning because it sits better. So the shift is **a property of the song in this
setlist**, and it is directly editable — the derivation exists to save typing,
not to take the decision.

Three values, in precedence order:

| | where it lives | what it is |
|---|---|---|
| the record's tuning | `song.recording.tuning` | a fact about the recording. Global to the song |
| the band's tuning | `setlist.tuning` | a fact about the group playing it |
| **the shift** | `setlist.songs[].shift` | **what actually happens.** An integer number of semitones |

```
default_shift = pitch_of(setlist.tuning) - pitch_of(song.recording.tuning)
effective     = entry.shift  if the entry declares one  else  default_shift
```

So a record in E♭ inside an E♭ setlist derives **0** and is left untouched, a
record in E derives **−1**, and either can be overridden to anything.

**The override belongs to the setlist entry, not to the song**, because the same
song is a different problem in each group: *Sultans of Swing* wants −1 for the
E♭ band and 0 for the covers duo playing in E. One song, one file, one practice
history, two shifts.

**And it is always one press away.** A `−`/`+` stepper sits in the practice
view's header and on the song page; `-` and `=` do it from the keyboard.
Stepping it writes the entry's `shift` and re-renders at the next loop boundary.
The number is shown as a number — `−1`, `0`, `+2` — with the tuning names beside
it as the explanation, not instead of it. (An earlier draft of this document said
"a tuning is a name in the UI, never a number". That was wrong: the name is the
default's reasoning, and the number is the control.)

Range is ±6 semitones. Beyond that the stretch quality collapses and the answer
is a different recording, not a bigger shift.

**Changing a shift invalidates that song's renders in this setlist**, and nothing
else — the cache key already carries `semitones`, so a stale file is impossible
and a changed shift simply misses the cache and re-renders.

---

## `practice/reps.jsonl`

One JSON object per line, appended, never edited, never rewritten. This is the
only file in the repo that cannot be reconstructed from anything else, and
append-only is what makes it survive a crash mid-session.

```json
{"t":"2026-09-05T19:22:41Z","song":"can-t-stop","section":"solo","speed":55,
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
