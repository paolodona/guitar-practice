# Woodshed — product spec

## Who it is for, and where

One guitarist. Standing or sitting, guitar in hands, both hands occupied, screen
about two metres away, room probably dim, amp or headphones already loud. A gig
next year and a few years of rust to burn off.

That posture is the whole design constraint. **Every second spent looking at the
screen is a second not spent playing**, and every control that needs a mouse
breaks the loop that practice depends on. If a feature cannot be reached by a
foot or a single glance, it belongs on a setup screen, not in the practice view.

## What it does

1. **Holds a library of songs**, each bound to an audio file — one you already
   have, or one captured off the machine's own output.
2. **Plays them slower** — 40–100 % of the original tempo — at the original pitch.
3. **Plays them in your tuning** — **per song**, because some of these records
   are already in E♭ and must not be touched while others are in E and have to
   come down one. The band's tuning only supplies the default; a `−`/`+` stepper
   is one press away in the practice view and moves it whenever you want.
4. **Cuts them into named sections** you draw on a waveform — *"Can't Stop
   intro"*, *"Master of Puppets — full solo"*, *"…solo, first part, tapping"*.
   Sections **overlap and nest freely**: the drill and the thing it lives inside
   are both real practice targets.
5. **Loops a section seamlessly**, with a lead-in so you arrive at the downbeat
   already playing.
6. **Counts the reps and the speed**, automatically, and walks the speed up a
   ladder as the reps accumulate.
7. **Shows a dashboard** across a setlist: what is ready, what is cold, what is
   still at 55 %.
8. **Supports several setlists** — the band's show, the covers group, a
   technique list — each with its own tuning.
9. **Captures what it cannot import** — records the output device, splits a
   playlist on the gaps, and matches the segments to the tracklist by duration.
10. **Is driven by your feet** — the GX-100's own footswitches over USB MIDI.

## What it deliberately does not do

* **It does not integrate with Spotify for audio.** It cannot — the samples are
  inside a DRM path. Spotify supplies the *list*; the audio comes from your own
  files or from **capture**, which records the machine's own output whatever is
  playing it. See `docs/04-sources.md`.
* **It does not judge your playing.** No pitch detection, no "you played that
  wrong". A rep is a pass through the loop; whether it was *clean* is something
  you tell it with a footswitch. Inventing a measurement the tool cannot make is
  the failure mode `gx100/CLAUDE.md` spends a page on, and it applies here.
* **It does not edit audio.** No mixing, no EQ. If you want the drums replaced,
  that is `rambass-live`. **Revised 2026-09-06**: one narrow exception —
  guitar-only isolation for practice, when a solo needs to go below the
  stretcher's honest floor (see `docs/03-audio-engine.md`'s "What good enough
  sounds like"). One stem, one purpose, cached per section; not a mixing
  surface, and general stem separation is still `rambass-live`'s job.
* **It is not multi-user, not hosted, not authenticated.** `127.0.0.1`, one
  person, one machine.

---

## The six screens

### 1. Dashboard

The entry point. A setlist picker, then one row per song.

Each row carries: title and artist, a **readiness bar**, the number of sections,
the count still under target, when it was last practised, and the tuning offset
if it is not zero. Rows are the setlist's running order, because that is how a
set is thought about — the same rule `rambass-live`'s console follows.

Above the rows, three things and no more:

* **Next up** — one song, one section, one button. The tool's opinion about what
  to practise now (see *Choosing what is next*). This is the button that gets
  pressed 90 % of the time.
* **Weeks to the gig**, if the setlist declares a date, and how many songs are at
  target.
* **Needs audio** — songs in the setlist with no file bound yet. A visible gap,
  never a silent one.

**Readiness is defined and shown, never a vibe.** For each section,
`reached = best_speed_with_at_least_N_clean_reps / target_speed`, clamped to 1.
Because sections overlap, the song's number is measured **over song time**: every
second any section covers contributes once and takes the `reached` of the
**longest** section covering it, with covered time as the denominator. So
subdividing a solo into three drills does not give that solo three votes, and a
drill running ahead of the solo it sits inside does not move the song's number —
which is the honest answer, because the gig asks for the solo, not the lick.
Hovering a bar shows the components — same rule as `gx100`: *any report must show
the component measurements, not just a total*. `docs/02-data-model.md` carries
the full rule.

### 2. Song page

The workbench. Used with a mouse, sitting down, before a practice session.

* **The waveform**, full song width, with a beat grid drawn over it when a tempo
  has been established, and bar numbers on a ruler.
* **A sections lane** underneath, aligned to the same pixel mapping — one
  contiguous strip per section, named, coloured. Drag its edges to adjust; drag
  on empty waveform to draw a new one; double-click to name it.
* **Per-section fields**: name, target speed, ladder step size, lead-in beats,
  notes, and optionally a `gx100` patch id.
* **Transport**: play, loop toggle, speed, transpose, and a "practise this"
  button per section that jumps straight to screen 3.
* **Song settings**: tempo (detected, tapped or typed — and it says which), grid
  offset, the recording's tuning, the bound audio file, the Spotify id.

Zoom is a wheel gesture about the pointer, and **zoom is a view and only a
view** — it never changes what loops or what plays. That is a lesson already
paid for in `rambass-live/docs/review-ui.md`; do not re-learn it.

### 3. Practice view — the actual product

Full screen. Legible from two metres with a guitar on. Six things on it, and
nothing else:

| element | why |
|---|---|
| **Section name**, large, with its containment under it (*inside Full solo · bars 1–8 of 30*) | which thing am I doing, and where it sits |
| **Speed, huge** — `55%` with the resulting BPM under it (`50 bpm`) | the number that is the goal |
| **Rep counter, huge** — `12` with `of 3 to advance` under it | the number that is the progress |
| **A progress ring** filling once per loop pass | peripheral vision only; you never *look* at it |
| **A lead-in countdown** — `3 · 2 · 1` filling the pre-roll | so you arrive playing |
| **A one-line hint of what is next** — `→ 60% after 1 more` | so the ladder is never a surprise |

Under those, a thin waveform of *just the section* with a playhead, and a foot
legend showing the current pedal mapping. That is all. No menus, no sidebars, no
song list, no settings.

**The loop never stops.** Changing speed, marking a rep clean, moving to the next
section — none of them pause the audio; they take effect at the next loop
boundary. A practice tool that stops playing to accept an instruction has broken
the thing it exists to protect. (`rambass-live`'s review console reached the same
conclusion from the other direction: every toggle there is a *mute* on something
already playing, because a restarted element comes back a frame late.)

### 4. Capture

Reached from a `needs-audio` song or from the library. Arm it, press play in
whatever is playing the music, and leave it: the level meters and a growing
waveform show the pass, silence splits it into segments, and each segment binds
to the next track in the imported tracklist by duration. A segment more than
1.5 s from its expected length stops and asks rather than binding on a guess.

One line of copy on that screen earns its place: *everything the output device
plays is recorded, notifications included*. See `docs/04-sources.md`.

### 5. Library / import

Add a song. Three routes, all landing in the same place:

* **Drop a file** — an MP3/FLAC/WAV you own. Tags fill in title and artist.
* **Scan a folder** — point it at your music library once; it indexes and offers
  matches.
* **Import from Spotify** — search, or paste a track/playlist/album URL. Creates
  the song entry with title, artist, album, artwork, duration and the Spotify id
  — **and no audio**, marked `needs-audio` until you bind a file. A playlist
  imports as a setlist in one go.

### 6. Progress

The long view, and the one that makes the tool worth using for a year. Per
section: a small chart of speed over time, total reps, clean reps, best sustained
speed, last practised. Per setlist: readiness over time, and a "cold" list —
sections not touched in 14 days that were previously above 80 %, because that is
where the gig actually gets lost.

---

## Behaviours worth pinning down

### The speed ladder

A section declares `target_speed` (default 100), `ladder_step` (default 5 %) and
`reps_to_advance` (default 3). Practice starts at the highest speed you have
already earned, or `start_speed` (default 50 %) if none.

After `reps_to_advance` **clean** reps at the current step, the speed advances by
`ladder_step` at the next loop boundary, and the counter resets. Two clean reps
followed by a failed one does **not** reset to zero — it drops one, because
resetting a nine-rep streak on one fluff is a punishment, and a punishment
changes what you practise.

**Nothing advances automatically past 100 %.** Above-tempo practice is a real
technique but it is a deliberate choice, not something the tool should do to you
while you are looking at the fretboard.

### What counts as a rep

A **pass** is counted automatically when playback reaches the end of the section
having started within 250 ms of its beginning, with no seek and no pause in
between. That is the number that answers "how many times have I done this".

A **clean rep** is a pass you confirmed — a footswitch, the space bar, or an
auto-confirm mode where every pass counts as clean. Only clean reps move the
ladder. This is an honour system and it is the correct design: the alternative is
a machine judgement the tool cannot actually make.

The default confirm mode is worth choosing carefully. Recommended default:
**auto-confirm on**, with a "that one was bad" pedal that *retracts* the last
rep. Confirming every good pass is 40 extra actions an hour; retracting the rare
bad one is three.

### Sections, snapping and lead-in

* Boundaries are stored in **seconds** of the source file (see the data model for
  why that is the right exception to the bars rule), and **snap to the detected
  beat grid** when one exists, with a millisecond nudge available. Spans may
  overlap and nest; only an exact duplicate span is refused. A solo loop
  that starts 40 ms late is unusable, and hand-dragging on a waveform is not
  accurate to 40 ms.
* `pre_roll_beats` (default 4) plays the audio *before* the section so you enter
  on the downbeat rather than starting cold on it. On the first pass only, or on
  every pass — a setting, defaulting to first-pass-only, because hearing the
  same bar 30 times is not the point.
* An optional **click** on the lead-in (and, if wanted, throughout), generated
  from the tempo grid. `rambass-live/src/rambass/click.py` already does this.
* A **crossfade at the loop point** (default 10 ms) so the seam is not a tick.

### Choosing what is next

The dashboard's *Next up* ranks sections by a simple, explainable score, shown on
hover:

```
score = (1 - reached) * weight_gap
      + days_since_practised / 14 * weight_cold
      + is_in_next_gig_setlist * weight_gig
```

Explainable beats clever. If the suggestion is wrong you should be able to see
*why* in one line and disagree with one click.

### Multiple setlists

A setlist is a name, an optional date and venue, an ordered list of song slugs,
and **a tuning**. Songs are referenced by slug, never copied — the same rule both
other repos enforce. One song can be in three setlists at three different
tunings, and its practice history is per `(section, speed)`, not per setlist, so
work done for the covers gig still counts for the band's.

## Keyboard, for when you are sitting down

| key | action |
|---|---|
| `space` | play / pause |
| `shift`+`space` | restart the section from the top |
| `↑` / `↓` | speed up / down one ladder step |
| `←` / `→` | previous / next section — flat order, `(start, longest first)`, so a container comes immediately before its drills |
| `[` / `]` | nudge the loop start / end by 10 ms |
| `-` / `=` | transpose down / up a semitone |
| `l` | loop on/off |
| `c` | confirm the last pass as clean |
| `x` | retract the last rep |
| `m` | metronome on/off |
| `f` | full-screen practice view |
| `?` | the shortcut list itself |

The foot mapping in `docs/05-foot-control.md` is the same set, and both are the
same commands underneath: one action table, two input devices. Never two.
