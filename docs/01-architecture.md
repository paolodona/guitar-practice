# Architecture

## The shape

A **local web app**: a small Python server that owns the library and the files,
and a browser front end that owns the audio and the drawing. Same shape as
`gx100 console` and `rambass review serve`, so all three tools launch the same
way and feel like one toolset.

Why not a desktop app: the audio work has to happen in the browser anyway (Web
Audio gives sample-accurate looping and a real-time stretcher for free), the
drawing is canvas either way, and Electron/Tauri would add a build step and a
packaging story for exactly zero capability you do not already have. If it ever
needs to be an app icon rather than a URL, wrap it then — the boundary is clean.

Why not a pure static page: because the library, the analysis, the cached
renders and the rep ledger are files on disk, and a browser cannot own files on
disk in a way you would still trust in a year.

```
  browser                                   python
  ────────────────────────────────────      ──────────────────────────────
  player.js    Web Audio graph              server.py   JSON + range-served audio
  wave.js      canvas waveform + grid       library.py  songs, setlists, slugs
  sections.js  drawing / dragging           manifest.py song.yaml load/save/validate
  ladder.js    speed + rep state machine    ledger.py   append-only rep log
  midi.js      Web MIDI in                  analyze.py  tempo, beat grid, peaks
  ui           the five screens             render.py   offline stretch/shift cache
                                            practice.py readiness, next-up, stats
```

## Layering, and the rule that keeps it cheap

Copy `rambass-live`'s discipline exactly, because it is the reason that repo can
still run its core commands on a laptop with nothing installed:

| module | job | dependencies |
|---|---|---|
| `library.py` | repo layout, slugs, song lookup | — |
| `manifest.py` | `song.yaml` / `setlist.yaml` load, save, validate | pyyaml, pydantic |
| `sections.py` | spans, snapping, containment, lane assignment, coverage — **pure functions** | — |
| `ladder.py` | speed steps, rep counting, advance rules — **pure functions** | — |
| `ledger.py` | append-only rep log, read and aggregate | — |
| `practice.py` | readiness over covered song time, cold list, next-up ranking | — |
| `server.py` | the HTTP server, JSON endpoints, range-served media | stdlib |
| `sources.py` | Spotify search/import, local folder scan, file binding | urllib |
| `peaks.py` | multi-resolution waveform peaks | numpy |
| `capture.py` | loopback recording, silence splitting, duration matching | **pyaudiowpatch** (optional) |
| `analyze.py` | tempo refinement, beat grid, onset detection | **librosa** (optional) |
| `render.py` | offline time-stretch / pitch-shift into the cache | **rubberband** (optional) |

**Never import librosa, open an audio device, or call the rubberband binary above
the `capture.py` / `analyze.py` / `render.py` line.** Everything else must run from a
fresh clone with `pyyaml`, `numpy` and `pydantic` and nothing more — which also means
the whole test suite runs that way. Those three are the core, and none of them is
heavy: pydantic is a wheel with no system dependency, so it costs nothing that the
rule exists to prevent.

Use a `require_module()`-style helper so a missing extra prints an install hint
naming the installer that actually exists, not an `ImportError` traceback. Both
other repos already have that helper; lift it.

**Pydantic is scoped to the parsing boundary.** Only the modules that read YAML —
`manifest.py` and `setlist.py` — import it. `sections`, `ladder`, `ledger`, `clock`
and `tuning` import stdlib and numpy and nothing else, which keeps the pure maths
testable with the minimum installed and independent of the schema library.

## Stack

* **Python 3.12+, `uv`, `ruff`, `pytest`.** Same as both other repos.
* **Server: the standard library.** `ThreadingHTTPServer`, JSON endpoints, one
  static directory. This app's server does three things — serve JSON, serve
  audio with `Range:` support, write a line to a log — and
  `rambass-live/src/rambass/console.py` already implements two of them well.
  Lift `parse_byte_range` and the handler shape verbatim; it is tested, and its
  docstring explains the RFC 9110 corner (an unparseable range sends the whole
  file; a well-formed unsatisfiable one is a 416) that a media element depends
  on.
* **Front end: vanilla ES modules, no bundler, no framework.** Five screens and a
  canvas do not need React, and a build step is a thing that breaks between you
  and playing guitar. Serve the modules directly.
* **Audio: Web Audio API**, `AudioBufferSourceNode` for cached/decoded material,
  an `AudioWorklet` running a WASM time-stretcher for live speed changes.
* **WASM stretcher: vendor it, do not CDN it.** `@soundtouchjs/audio-worklet` or
  a Rubber Band WASM build, committed under `web/vendor/` with its licence and
  its version in a `README`. This tool has to work on a laptop with no network.

## The one design rule, inherited

**The repo is the database.** Every screen is derived from files on each
request; the only things the server *writes* are:

1. `song.yaml` / `setlist.yaml` edits you made deliberately in the UI,
2. one appended line per rep in the ledger,
3. cache files under `cache/`, which are always safe to delete,
4. (Phase 1.5, Group U) a raw capture recording under `capture/` — **not**
   cache, since it is real, unrepeatable audio kept until every segment split
   from it has been bound to a song or explicitly discarded.

So editing a YAML by hand, running the CLI, or letting a Claude session write a
finding all show up on the next refresh, with no sync step. If the UI and the
files disagree, the files are right and the UI has a bug.

## A CLI, and why it exists first

`woodshed <command>`, Typer or argparse. Not because you will practise from a
terminal, but because every UI action should be a thin call onto a function the
CLI also calls — the rule that keeps `rambass-live`'s console honest ("nothing
the browser can do differs from what a terminal can"). It also makes the whole
thing testable without a browser.

```
woodshed add <file> [--title --artist --spotify <id>]
woodshed analyze <slug>              # tempo, grid offset, peaks
woodshed section <slug> add "Full solo" 178.4 262.9
woodshed section <slug> add "Solo — tapping" 178.4 201.15   # overlaps, deliberately
woodshed setlist new <name> --tuning Eb
woodshed capture --queue <setlist>     # arm loopback, split a playlist on the gaps
woodshed render <slug> --section solo --speed 60 --semitones -1
woodshed status [--setlist <name>]   # the dashboard, as text
woodshed log <slug> <section> --speed 60 --clean   # a rep, from anywhere
woodshed serve [--port 8477]
woodshed doctor                      # ffmpeg, rubberband, librosa, MIDI, files
```

`doctor` is not optional garnish. Both other repos have one and both earn it —
`rambass-live`'s exists because ffmpeg was installed and unfindable, and the
error message sent its author to install something he already had. This app has
five external things that can each be absent (ffmpeg, rubberband, a WASM
stretcher, a MIDI device, a loopback-capable audio device) and `doctor` is where that is said out loud, naming
the route it took to find each one.

## What to lift from the other two repos

Concrete, in rough order of value:

| from | what | why |
|---|---|---|
| `rambass-live/src/rambass/console.py` | `parse_byte_range` + the `ThreadingHTTPServer` handler | range-served audio, already correct and tested |
| `rambass-live/src/rambass/analyze.py` | `refine_tempo` (comb/DFT fit) and `pulse_wander` | pure numpy, and it exists precisely because librosa returns a **tempogram bin centre, not a measurement** — near 117 BPM the only values it can return are ~112.4, 117.5, 123.1. Your grid depends on the real number |
| `rambass-live/src/rambass/click.py` | click generation from a tempo map | the lead-in metronome |
| `rambass-live/src/rambass/audio.py` | `locate_tool` (override → PATH → discovery, in that order) and `require_module` | so `ffmpeg`/`rubberband` are found or *explained*, never silently missing |
| `rambass-live/src/rambass/console.html` | the waveform/ruler/playhead stack: one gutter, one `mark.frac * width` mapping, one playhead across all lanes | four alignment bugs are already documented and fixed there. Read that section before drawing a single pixel |
| `rambass-live/src/rambass/project.py` | `slugify` (folds accents) and `parse_position` | Italian titles, and `bar.beat` notation |
| `gx100/src/gx100lab/data/workflow.yaml` | the pattern of *sequencing as data, not code* | if this tool ever grows a guided flow |
| `gx100` `songs/<slug>/song.yaml` | `patches:` and `structure:` blocks | a Woodshed section can name a `gx100` patch and show it |

## Where it lives on disk

```
woodshed/
  songs/<slug>/song.yaml           # tracked
  songs/<slug>/audio/              # gitignored — the bytes
  songs/<slug>/cache/              # gitignored — rendered variants, peaks
  setlists/<slug>.yaml             # tracked
  practice/reps.jsonl              # tracked. append-only. the one irreplaceable file
  capture/<timestamp>.<ext>        # gitignored — raw, unrepeatable; kept until
                                    # every segment split from it is resolved
  config.yaml                      # tracked — library paths, defaults, MIDI map
  src/woodshed/                    # generic. no song names, no personal paths
  web/                             # the front end
  tests/
```

**`src/` is generic**; song-specific anything lives in `songs/` or `setlists/`.
Audio bytes never enter git — and unlike the other two repos, here that is not
only a size rule: these are commercial recordings you own a licence to listen
to, not to publish.
