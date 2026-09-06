# Build order

Four phases. **Phase 0 is a usable tool on its own** — that is the test of
whether the order is right. Nothing here is scheduled; they are dependency
layers, and the gig is a year out.

## Phase 0 — a looper that counts (the minimum that is worth using)

* `song.yaml`, `library.py`, `manifest.py`, slugs, the repo layout.
* `woodshed add <file>` — bind an audio file, hash it, read its duration.
* The local server: JSON endpoints + range-served audio, lifted from
  `rambass-live/src/rambass/console.py`.
* Waveform peaks (`peaks.py`, numpy) and a canvas that draws them.
* Draw, name and save sections by dragging on the waveform — overlapping and
  nested from the start, with derived lanes. Retrofitting overlap onto a tiling
  model would touch the lanes, the ordering and the readiness maths at once.
* **Real-time playback with a speed slider** — WASM stretcher in an
  `AudioWorklet`. No transpose yet, no cache, no ladder.
* Loop a section. Count passes. `practice/reps.jsonl`.
* A practice view with the two big numbers.

At the end of Phase 0 you can already slow a solo to 60 % and loop it while the
app counts. Everything after this is making that better, not making it work.

## Phase 1 — in tune, on the grid

* **Transpose**, per song — derived from the setlist's tuning as a default and
  overridable from the practice view at any time.
* Tempo detection: `analyze.refine_tempo` from `rambass-live` — refined against
  the file, not a librosa tempogram bin — plus a **tap-tempo** fallback and a
  manual override, each recording which it was.
* Beat grid, bar ruler, boundary snapping, millisecond nudge.
* Lead-in bars and a generated click (`click.py`, also from `rambass-live`).
* Setlists, per-song transpose with a `−`/`+` stepper, and the dashboard.
* **Capture** — loopback recording, silence splitting, duration matching to an
  imported tracklist. Promoted out of phase 3 on 2026-09-05: without source audio
  there is nothing to practise against, so it is not an extra.

## Phase 2 — the ladder, and a seam you cannot hear

* **The offline render cache** — Rubber Band, one file per
  `(section, speed, semitones)`, rendered ahead of the next rung. Practice
  playback moves onto decoded `AudioBuffer`s with a native, sample-exact loop.
* The speed ladder: `reps_to_advance`, `ladder_step`, advance at the loop
  boundary, one-step-back on a retraction.
* Clean-rep confirmation and retraction.
* The progress screen and the "cold" list.
* `woodshed doctor`.

## Phase 3 — feet, and the rest of the toolset

* **Web MIDI foot control.** Six actions, one action table shared with the
  keyboard. This is the requirement that started the project, and it is late in
  the order only because it needs something worth controlling.
* Spotify import: search, track/album/playlist → songs and setlists, all badged
  `needs-audio` and feeding the capture queue.
* Library scan and file binding.
* The `gx100` cross-reference: a section names a patch; show it, optionally send
  the Program Change, optionally follow the pedal's own.

## Later, if it earns it

* Chromaprint fingerprint matching for the library scan.
* ~~Practising against a `demucs` stem when a solo needs to go below 50 %.~~
  Moved into the active plan 2026-09-06 — see
  `.agent_session/001_woodshed-implementation_plan.md`, Phase 1.5 Group S.
* Export a practice log to a chart, or to the band.
* A "session" concept — a plan for tonight, twenty minutes, four sections.

## What would make this go wrong

Written down now so it is recognisable later:

* **Building the dashboard before the looper.** The dashboard is the most fun to
  build and the least useful. Phase 0 has no dashboard on purpose.
* **Perfecting the stretch quality.** It is a solved problem with a known
  ceiling (`docs/03-audio-engine.md`). Use Rubber Band, accept what it sounds
  like at 50 %, move on.
* **Adding a seventh foot action.** Six is memorable with a guitar on. Seven is
  a lookup.
* **Storing a summary in `song.yaml`.** The ledger is the truth; the moment a
  count lives in two files they will disagree.
* **Practising the tool instead of the guitar.** The honest failure mode for a
  developer with a gig in a year. Phase 0 is deliberately small so that there is
  something to practise *with* within a weekend.
