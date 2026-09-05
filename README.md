# Woodshed

*The repo is `guitar-practice`; Woodshed is the tool's working name.*

A practice tool for one guitarist. Load a song, slow it down without changing its
pitch, drop it a half step where the record needs it, carve it into named sections, and loop the hard one until it is not hard any more — counting every
pass and every speed step, hands never leaving the guitar.

It is the third of three:

| repo | job |
|---|---|
| [`gx100`](../gx100) | makes the **sound** — tone matching, patch library, GX-100 protocol |
| [`rambass-live`](../rambass-live) | makes the **backing track** — drums, click, video, Reaper |
| **`guitar-practice`** | makes the **player** — practice the parts, track the progress |

They share a house style (Python + uv, YAML on disk, a local web console, the repo
*is* the database) and two of them share real data with this one: a setlist here
can name a `gx100` song slug and show which patch a section wants, and the
GX-100's own footswitches drive this app over MIDI.

## Read in this order

1. [`docs/00-spec.md`](docs/00-spec.md) — what it does, screen by screen. Start here.
2. [`docs/01-architecture.md`](docs/01-architecture.md) — layering, stack, what to lift from the other two repos.
3. [`docs/02-data-model.md`](docs/02-data-model.md) — `song.yaml`, `setlist.yaml`, the rep ledger.
4. [`docs/03-audio-engine.md`](docs/03-audio-engine.md) — time-stretch, pitch shift, seamless looping. The load-bearing part.
5. [`docs/04-sources.md`](docs/04-sources.md) — capture, local files, and exactly what Spotify can and cannot give you.
6. [`docs/05-foot-control.md`](docs/05-foot-control.md) — hands-free, from the GX-100.
7. [`docs/06-design-brief.md`](docs/06-design-brief.md) — the brief to paste into Claude Design.
8. [`docs/07-roadmap.md`](docs/07-roadmap.md) — build order. Phase 0 is usable on its own.

## The one sentence

**Practice is reps at a speed, and both numbers are the product.** Everything
else — the waveform, the sections, the setlists, the dashboard — exists to get
you to the next rep without touching the computer.
