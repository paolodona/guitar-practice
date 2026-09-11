# Design brief — paste this into Claude Design

Everything below is self-contained. It restates the context because the design
tool will not have this repo.

---

## Brief

**Product:** a personal practice tool for one guitarist. It plays a song slower
without changing its pitch, shifts it by a few semitones where the record's
tuning does not match the band's, loops a named section (a solo, an intro riff)
over and over, and counts how many times you have played it and at what
percentage of full speed. It can also *record* a song off the machine's own audio
output, for the songs he has no file for. Local web app, single user, runs on his
own machine.

**Design a clean, calm, dark UI for it.**

### The user, and the posture — this drives everything

One person. Late 40s, technical, returning to guitar after a few years off,
preparing for a gig next year. He is **standing with an electric guitar on, both
hands occupied, about two metres from the screen**, in a room that is probably
dim, with an amp or headphones already loud.

So:

* **He cannot use a mouse while playing.** During practice the only inputs are
  his feet (a MIDI pedalboard) and the occasional glance.
* **He reads the screen in peripheral vision.** The two numbers that matter —
  the speed percentage and the rep count — must be legible at two metres, which
  in practice means enormous: think 120–180 px, not 32.
* **Anything he has to squint at, he will ignore**, and then the tool has failed.

There are therefore **two distinct modes**, and they should look like they belong
to the same product but not like the same screen:

* **Setup mode** — sitting at the desk, mouse in hand. Dense, informative,
  ordinary desktop-app density is fine. Dashboard, song page, library.
* **Practice mode** — standing, guitar on, two metres away. Almost empty. Five
  elements. Enormous type. This is the screen that makes or breaks the product.

### Artboards to produce

1. **Dashboard** — desktop, ~1440×900
2. **Song page / editor** — desktop, ~1440×900
3. **Practice mode** — full screen, ~1920×1080. **The hero. Spend the most time here.**
4. **Capture** — desktop, ~1440×900
5. **Library / add a song** — desktop, ~1440×900
6. **Progress** — desktop, ~1440×900
7. A small **component sheet**: buttons, the section chip, the speed control, the
   transpose stepper, the readiness bar, the foot-pedal legend, the waveform +
   beat-grid treatment.

If time is limited: 3, then 1, then 2.

---

### 1 · Dashboard

The home screen. A setlist picker at the top (he keeps several — his band's
show, a covers group, a technique list — and each has its own tuning, e.g. "E♭
standard").

Above the fold, three things and no more:

* **Next up** — one suggested song + section + a single large primary button
  ("Practise — Can't Stop · Solo · 60 %"). This is pressed 90 % of the time.
  Make it unmissable.
* **Weeks to the gig** (e.g. "31 weeks · 8 of 23 songs at target") when the
  setlist has a date.
* **Needs audio** — a quiet strip listing songs with no audio file bound yet.
  Present, not alarming.

Then one row per song, in the setlist's running order:

`title · artist · readiness bar (0–100 %) · "6 sections, 2 under target" · last
practised · tuning offset chip (−1) if not zero`

Rows are calm and scannable; the readiness bar is the only strong colour. A song
untouched for two weeks that used to be ready gets a subtle "cold" marker —
noticeable, never a red alert. Nothing here should feel like a productivity app
nagging him.

### 2 · Song page / editor

The workbench, used sitting down before practice.

* **Full-width waveform** of the whole song, with a faint beat grid over it and a
  bar-number ruler above.
* Directly beneath, perfectly aligned to the same horizontal scale, the
  **section lanes**. **Sections overlap and nest** — "Full solo" and, inside it,
  "Solo — first part, tapping" — so this is not a single strip of tiles but a
  small stack of rows: longest spans on the top lane, the drills that sit inside
  them on the rows below, and gaps where nothing is mapped. Draggable edges, drag
  on empty space to create one, click to select. The alignment between waveform
  and lanes must read as exact — that is the whole credibility of the screen, and
  a nested span must obviously start and end *inside* its parent.
  Show at least one nested pair and one bare stretch of song with no section on it.
* **Transport bar**: play/pause, loop toggle, a speed control, and a transpose
  readout (a *tuning name*, not a number: "E♭ standard", with "−1 semitone"
  small beneath).
* **Selected-section inspector** (a right rail or a bottom sheet): name, start
  and end (as `bar.beat` and as seconds), target speed, ladder step, lead-in
  bars, free-text notes, its containment ("inside Full solo"), and a "Practise
  this" button.

Show one **section chip in a selected state** and one being dragged.

### 3 · Practice mode — the hero screen

Full screen, no browser chrome, no navigation, no sidebar. Five elements:

1. **Section name** — top, medium-large, with the song title smaller above it
   and, where the section sits inside another, a quiet line beneath saying so:
   `Master of Puppets` / **`Solo — first part, tapping`** / `inside Full solo ·
   bars 1–8 of 30`
2. **The speed** — the single largest thing on the screen. `55%`, with the
   resulting tempo small beneath it (`50 bpm`).
3. **The rep counter** — the second largest. `12`, with `of 3 to advance` beneath.
4. **A progress ring or arc** that fills once per loop pass. It exists for
   peripheral vision — he never looks *at* it. Large, thin, low contrast.
5. **A one-line preview of the ladder**: `→ 60 % after 1 more clean rep`.

Beneath those, small and quiet: a **thin waveform of just this section** with a
playhead, and a **foot-pedal legend** — six labelled icons showing what each
footswitch does right now (play/pause, prev, next, slower, faster, "that one was
bad"). The legend is how he learns the pedals in week one and ignores them from
week two, so it must be present and must not compete.

**Show two states of this screen:**

* **A — looping.** Mid-pass, ring part-filled, rep count 12.
* **B — the ladder advancing.** The moment the speed goes 55 → 60: show how that
  is celebrated. It should feel *earned and quiet* — a brief emphasis, not
  confetti. This is the emotional payoff of the whole product and it happens
  maybe six times an hour.

### 4 · Capture

Reached from a song with no audio. He arms it, presses play in whatever app is
playing the music, and walks away.

* **Three stat tiles**: the source device, elapsed time, captured-of-total.
* **The live signal**: L/R level meters and the segment currently being recorded,
  with its elapsed time against the expected duration.
* **The pass so far**: a long waveform of the whole session with the detected
  splits marked, and under it one labelled block per segment showing which track
  it matched and by how much the duration differed.
* **A queue rail** on the right: the imported tracklist, each row captured /
  capturing / waiting.
* One line of plain copy: *everything the output device plays is recorded,
  notifications included*. It is a caution, not a warning — style it as
  information.

Show it mid-capture, not idle.

### 5 · Library / add a song

Four entry paths on one screen, equal weight:

* **Drop a file** — a large drop target.
* **Capture what's playing** — leads to screen 4.
* **Scan a folder** — pick a folder, then a result list with checkboxes.
* **Search Spotify** — a search field and results (artwork, title, artist,
  album, duration). Also accepts a pasted Spotify URL; a pasted **playlist**
  becomes a whole setlist.

Show the result of a Spotify import: rows carrying artwork and metadata, each
badged **"needs audio"** with a "bind file" affordance. Design that badge so it
reads as *a next step*, not as an error — it is the normal state of a
freshly-imported song, and it must not make a 23-song import look like 23
failures.

### 6 · Progress

The long view. Per section: a small sparkline of speed over time, total reps,
best sustained speed, last practised. Per setlist: readiness over time, and a
"cold" list. Restrained; charts are thin and unlabelled where a label would not
be read. No gamification, no badges, no streak guilt — the numbers are already
motivating.

---

## Visual direction

* **Dark, low-glare, high-contrast where it counts.** A near-black ground (not
  pure black), with content sitting on very slightly lifted surfaces. This is
  looked at in a dim room for an hour at a time.
* **One accent colour, used sparingly** — for the current speed, the active
  section, the primary action, the filling ring. Everything else is greys. If
  three things are accented, none of them is.
* **A confident type scale with a huge top end.** The gap between the largest
  number and body text should be roughly 10:1. A humanist or geometric sans;
  **tabular figures**, non-negotiable — the rep count and the percentage change
  constantly and must not shift width.
* **Generous space, few borders.** Separate by space and surface, not by lines.
* **Restrained motion.** The ring fills; the countdown pulses; a ladder advance
  gets one brief emphasis. Nothing else animates. Motion in peripheral vision
  while someone is concentrating on a fretboard is an irritant.
* **Waveforms are quiet.** Mid-grey, the beat grid fainter still, the playhead
  the accent. The waveform is a map, not the subject.
* **It should feel like a good instrument**, not like a SaaS dashboard: closer to
  a tuner, a studio compressor or a pro DAW's transport than to a project
  tracker. Calm, dense where it needs to be, empty where it can be.

## Constraints and do-nots

* **Do not put a navigation sidebar in practice mode.** Nothing but the six
  elements.
* **Do not use small text for anything in practice mode.** If it is worth showing
  there, it is worth showing large; if not, it belongs on the song page.
* **Do not decorate the numbers.** No gauges with tick marks, no skeuomorphic
  dials, no gradients on the big figures.
* **Do not design a mobile layout.** Desktop and full-screen only. He is not
  practising off a phone.
* **Do not gamify.** No badges, no levels, no streak fire icons, no "you're on a
  roll!". He is a working musician preparing for a gig.
* **Assume a light theme is not needed.** One theme, done properly, beats two
  done adequately.
* **Transpose is a control, not a label.** A `−` / value / `+` stepper, showing
  the signed number (`−1`, `0`, `+2`) with the tuning names beside it as the
  reasoning. It has to be reachable in one press from the practice view — he
  changes it song by song and sometimes mid-session.

## Content to use in the mockups

Real-feeling content, not lorem:

* Songs: *Master of Puppets* — Metallica · *Can't Stop* — Red Hot Chili Peppers · *Plush* — Stone Temple Pilots ·
  *Sultans of Swing* — Dire Straits · *Manlio* — Ramba S.S. (his own band, and
  already recorded in E♭, so its shift is 0) · *Whole Lotta Love* — Led Zeppelin
* Sections, including a deliberately nested set: "Intro riff", "Verse",
  "Chorus", "Full solo", "Solo — first part, tapping" (inside it), "Solo — the
  descending run" (also inside it), "Outro jam"
* Setlists: "Ramba S.S. — the set" (E♭ standard, 23 songs), "Covers duo"
  (E standard, 14 songs), "Technique"
* The line that started the whole project, and should be legible somewhere:
  **"Can't Stop — intro · practised 25 times · 55 %"**
