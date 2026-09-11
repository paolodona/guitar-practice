# The audio engine

This is the part that is easy to get plausibly wrong and hard to notice. Two
of the three traps below are already documented, measured, in
`rambass-live` — and **one of them does not transfer**, which matters more than
the two that do.

## The two knobs, and the one identity behind them

* **Speed** `v` — a fraction of the original tempo. 0.55 is 55 %.
* **Transpose** `s` — semitones, derived from the setlist's tuning. −1 for a
  band in E♭ playing a record in E.

They look like two features. They are one, and it is worth writing down because
any fallback implementation you ever write needs it:

```
r = 2^(s/12)                 # resample ratio: pitch AND rate move together
stretch ratio  ρ = r / v      # output duration / resampled-input duration
```

Read a −1 semitone, 55 % pass as: resample by `r = 0.94387` (pitch drops a
semitone, playback also slows 5.6 %), then time-stretch by `ρ = 1.716` to land on
55 % speed at the new pitch. **One resampler and one time-stretcher cover both
knobs.** Rubber Band and SoundTouch each expose `--pitch` and `--tempo` and do
this internally; you will not normally write it — but if you ever reach for
"just play it slower", this is the identity that tells you what you actually
changed.

## Trap 1: resampling is not pitch shifting, and it is not time stretching either

`rambass-live` shipped `warp_samples` as linear interpolation — read the source
at `position * rate` — on the reasonable-sounding grounds that the rates were
within a few percent of 1.0. That argument was about timing and never considered
pitch. Over Manlio's 308 per-beat segments (rates 0.929–1.091) it moved the pitch
**2.78 semitones peak to peak, wobbling once per beat**, in the one file whose
only job was to be listened to.

Here the numbers are not a few percent. **At 50 % speed, naive resampling drops
the whole recording an octave.** This is obvious stated plainly and completely
invisible in a code review that says `source[i * rate]`.

## Trap 2 — the one that does NOT transfer: WSOLA is the wrong algorithm here

`rambass-live/CLAUDE.md` says, correctly for its own job: *do not reach for a
phase vocoder (smears the transients that are the whole signal here)*, and its
`warp_samples` is WSOLA in numpy. **Do not copy that conclusion into this repo.**

The two jobs are different:

| | rambass warp | woodshed practice |
|---|---|---|
| stretch ratio | 0.81–1.21 | **1.4–2.5** (100 % down to 40 %) |
| signal | band minus drums, transients are the point | full mix, dense, cymbals and vocals |
| success test | a backbeat lands where the band played it | it sounds like music you can learn from |

WSOLA copies frames and joins them where they correlate. At ±20 % on
transient-led material that is exactly right. At a ratio of 2 on a dense mix it
has to repeat roughly every other frame, and the audible result is a stutter or
a flutter on sustained material — a held vocal doubles, a cymbal wash pulses.
That is the cost his repo already priced ("WSOLA can displace a transient by up
to its 10 ms search window") paid at ten times the ratio.

**Use Rubber Band.** Its R3 engine is a phase-locked vocoder with explicit
transient detection and per-partial phase handling — the class of algorithm that
holds up at these ratios — and it is what every practice tool worth using is
built on. Offline it is a command-line binary; in the browser it is the WASM
build named below, which has been measured in a worklet rather than assumed.

```
rubberband --time 1.8182 --pitch -1 --formant --fine in.wav out.wav
```

Two flags to know:

* `--formant` preserves formants under a pitch shift. At one semitone it is a
  small effect; leave it on anyway, because it costs nothing and it is the
  difference between "a semitone down" and "a semitone down and slightly
  chipmunked in reverse" on vocals.
* `--fine` / R3 is slower and better. For an offline render of a 30-second
  section, "slower" is a second or two. Take it.

**Licensing, since this repo may go public.** Rubber Band Library is GPLv2+ or
commercial. Calling the `rubberband` CLI as a subprocess is fine for a personal
tool and keeps the boundary clean; linking a GPL WASM build into a page you
publish is a decision to make deliberately. SoundTouch is LGPL and easier that
way, at some quality cost. Note the choice in the repo; do not discover it later.

## The browser build, and what it actually does at ratio 2.0

Measured 2026-09-05 with `tools/rb-probe`, which is a real `AudioWorklet` driven
by a real `AudioContext`, not a bench. Re-run it with
`powershell -ExecutionPolicy Bypass -File .\run.ps1` from that directory; the
recorded runs are in `tools/rb-probe/results/`.

**The build is [`rubberband-wasm@3.3.0`](https://www.npmjs.com/package/rubberband-wasm)**
(Rubber Band 3.3.0, from the official release tarball), and we use `dist/rubberband.wasm`
— 265 101 bytes, sha256 `496d880b…f07c04dc` — **on its own, with no Emscripten JS
glue.** It is a `STANDALONE_WASM` module exporting the whole `rubberband-c.h`
surface and importing nine symbols that this path never reaches, so
`new WebAssembly.Instance` runs synchronously in the processor's constructor with
the `WebAssembly.Module` passed through `processorOptions`. Nothing is fetched
inside the worklet and there is no async gap before the first quantum.

The two rejected alternatives, so nobody re-evaluates them: `rubberband-web`
ships a ready-made worklet but it is push-driven (see below) and so cannot hold
any ratio but 1.0, and it has been untouched since 2022;
`@echogarden/rubberband-wasm` is a live second source but minifies its imports
down to `a.a`…`a.m`, so it needs its own JS glue and that glue is written for
node.

**Stereo R3 at ratio 2.0 costs about 17 % of one core** (Ryzen 9 6900HX, Chrome
152, 44.1 kHz) and holds real time exactly: 30.190 s of stretched output arrived
in 30.182 s of wall clock, with zero underruns across 10 402 quanta and no NaN.
Ratio 2.5 with a −1 semitone shift costs 18 %. R2 is three times cheaper at 6 %,
and we are still not taking it. Changing the ratio mid-stream — the speed slider
being dragged — produced no discontinuity and no crash.

Two numbers from that run belong in the code and not only in this paragraph:

* **The worklet must own the source samples and pull.** A processor that takes a
  quantum from an upstream node and returns a quantum cannot hold a ratio other
  than 1.0: at 2.0 the stretcher emits two frames for every one consumed, so the
  surplus accumulates in its buffer and latency grows without bound; below 1.0 it
  starves. Feed `rb_get_samples_required()` frames from a source the worklet
  holds, until `rb_available()` covers the quantum.
* **The start delay is not zero.** R3 reports `preferredStartPad` 2048 and
  `startDelay` 2048 frames — 46.4 ms at 44.1 kHz, 2170 with a pitch shift on; R2
  reports 1024. Feed the pad, drop the delay, and put both into `clock.py`'s
  source↔playback conversion. Discovering this later looks like every section
  boundary being 46 ms late.

Also: **`performance` does not exist in `AudioWorkletGlobalScope`.** Nothing in
`web/` may call `performance.now()` inside a worklet.

What the probe does *not* establish is that any of it sounds right. Every number
there is throughput and arithmetic on a synthetic signal. That R3 holds up at
1.4–2.5 where WSOLA flutters is a listening judgement, and per the prime
directive this tool does not get to make it — play a real record through the
speed slider at 50 % and listen.

## Trap 3: a real-time stretcher cannot loop seamlessly

This is the one that decides the architecture.

A seamless loop needs the loop point to be **sample-exact and identical every
pass**. Web Audio gives that for free on an `AudioBufferSourceNode`:
`buffer.loop = true`, `loopStart`, `loopEnd`, and the browser splices in the
audio thread with no gap and no drift, for hours.

A real-time stretcher running in an `AudioWorklet` cannot: it is producing output
frames from an internal analysis window with its own phase state, so the loop
seam lands wherever the window happened to be, and it lands somewhere slightly
different on every pass. You hear it as a tick or a hiccup once per loop — the
single most annoying possible defect in a tool whose entire purpose is looping.

### So: two engines, and the seam decides which

| when | engine | why |
|---|---|---|
| **exploring** — dragging the speed slider, scrubbing the song, auditioning a boundary | real-time WASM stretcher in an `AudioWorklet` | instant response matters, seams do not |
| **practising** — a section pinned to a ladder step, looping | **pre-rendered file, decoded to an `AudioBuffer`**, native Web Audio loop | sample-exact seam, zero CPU, zero drift, no glitch under load |

The ladder makes this natural rather than a compromise: **practice speeds are
discrete.** 50, 55, 60, 65… you never practise at 57.3 %. So each
`(section, speed, semitones)` is a small cache file — a 30-second solo at 55 % is
about 55 seconds of audio, a few MB as FLAC — rendered once, in a second or two,
and reused for every rep for the next six months.

Render **ahead**: while you loop at 55 %, the next rung (60 %) renders in the
background. By the time the ladder advances it is already decoded, and the
advance happens at the next loop boundary with no gap.

Never render the whole song at every step. Render sections, on demand.

## Looping, precisely

* **Crossfade the seam.** Even sample-exact, a loop from a musical end back to a
  musical start joins two uncorrelated waveforms and can click. 10 ms
  equal-power crossfade, baked into the rendered file (so the browser just
  loops), and exposed as `loop_crossfade_ms`.
* **Pre-roll is part of the buffer, not a separate source.** Render
  `[start - pre_roll, end]` into the file and set `loopStart` to where the
  section actually begins. First pass plays from sample 0 and includes the
  lead-in; every subsequent pass loops from `loopStart`. One source node, no
  scheduling, no chance of the lead-in drifting.
* **Speed changes at the boundary, never mid-loop.** Queue the new buffer, start
  it at the exact `AudioContext.currentTime` the current loop ends. Two source
  nodes overlap for one crossfade and the change is inaudible except as a change
  of tempo.
* **Never `playbackRate` a stretched buffer.** It is right there and it is the
  wrong knob: it re-introduces trap 1 on top of a correct render.

## The grid under a speed change

The beat grid is stored against the *source* file. Under a render at speed `v`,
every grid position scales by `1/v` and `grid_offset_s` moves with it. Convert in
**one** place — the same rule `rambass-live` enforces for its two clocks
(`bar_beat_to_seconds` vs `audio_time`), where mixing them up shifts everything
by the count-in and the bug is invisible until it is not.

Woodshed has the same two-clock hazard in a different costume:

* **source time** — seconds in the original file. What `song.yaml` stores.
* **playback time** — seconds in the rendered, stretched section, where the
  pre-roll occupies the head. What the waveform and the playhead use.

Write them as two functions with two names and convert at the boundary. Anything
that takes a bare `float` called `t` will eventually take the wrong one.

## Decoding and formats

* **Source files**: MP3, FLAC, WAV, M4A. Decode with `ffmpeg` (via
  `locate_tool`'s override → PATH → discovery order, lifted from `rambass-live`
  — that helper exists because ffmpeg was installed, unfindable, and the error
  message told its author to install what he already had).
* **Cache files**: FLAC. Lossless, half the size of WAV, and
  `decodeAudioData` handles it in every browser that matters. Not MP3 — encoder
  delay makes a gapless loop a gamble you do not need to take.
* **Peaks**: computed server-side into `cache/peaks-*.json`, multi-resolution
  (one bucket per pixel at a few zoom levels). Do not decode a five-minute file
  in the browser to draw a waveform.

## What "good enough" sounds like, and the honest limit

At 70–100 % a Rubber Band render of a full mix is essentially transparent. At
55–70 % it is clearly processed and completely usable — cymbals get a little
watery, reverb tails smear. Below 50 % everything smears and you are learning
against a texture, not a recording; still useful for a fast run, and worth
knowing before you conclude something is broken.

**Do not chase this with settings.** It is the state of the art for a full mix
and no parameter tuning gets around it. If a particular solo needs to be slower
than 50 % and still clear, the answer is a different *source* — an isolated
guitar track, which is what `demucs` in `rambass-live` already does — not a
better stretcher. **As of the plan's Phase 1.5, Group S**, this no longer means
a manual trip to `rambass-live`: a "Guitar only" toggle isolates and caches the
guitar stem per section, in-app, reusing `rambass-live`'s own Demucs choice.
