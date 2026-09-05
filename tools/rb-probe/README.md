# `rb-probe` — does the Rubber Band WASM build survive a ratio of 2.0 in a worklet?

The plan parked this as an open question: *"Which Rubber Band WASM build, and has it
been tested in an `AudioWorklet` at ratio 2.0? Not verified during planning. Phase 0's
D4 depends on it."* This directory is the verification, so that the answer lives in the
repo rather than in a transcript.

**Answer: `rubberband-wasm@3.3.0`, and yes.** Measured 2026-09-05.

## The build

| | |
|---|---|
| package | [`rubberband-wasm@3.3.0`](https://www.npmjs.com/package/rubberband-wasm) (Daninet), npm |
| Rubber Band | 3.3.0, from the official `breakfastquay.com` release tarball — the version is pinned by the package's own `build.sh`, not inferred |
| file we use | `dist/rubberband.wasm`, 265 101 bytes, sha256 `496d880b…f07c04dc` |
| tarball | sha256 `dc7f4141…1d2f2cbc` |
| licence | GPLv2+ (Rubber Band Library) |
| built with | `emcc … -s STANDALONE_WASM=1 -s MODULARIZE=1 -s ALLOW_MEMORY_GROWTH=1` over `RubberBandSingle.cpp` |

**The .wasm is used on its own — the Emscripten JS glue is not needed.** It is a
standalone module: 35 exports covering the whole `rubberband-c.h` surface
(`rb_new`, `rb_set_time_ratio`, `rb_process`, `rb_available`, `rb_retrieve`,
`rb_get_start_delay`, …), and nine imports — one `emscripten_notify_memory_growth`
plus eight WASI stubs that are never reached on this path. `new WebAssembly.Instance`
runs synchronously inside `AudioWorkletProcessor`'s constructor, with the
`WebAssembly.Module` handed over through `processorOptions` (it is structured
cloneable, so no `fetch` in the worklet and no async gap before the first quantum).

The binary is **not committed**: it is GPLv2+ and this repo does not distribute it
until the licence question in the plan's Decision 1 is confirmed and A2 vendors it
into `web/vendor/rubberband/`. `fetch-wasm.ps1` downloads and hash-checks it, and
also keeps `build.sh` and the C shim next to it — those are the corresponding source
for the binary, which is the part of GPLv2 §3 that a vendored blob otherwise fails.

## Running it

```powershell
cd tools\rb-probe
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

`run.ps1` fetches the wasm if it is missing, serves this directory, drives headless
Chrome through both suites and prints the runs. `-Suite offline` or `-Suite realtime`
narrows it. The realtime suite takes about 70 s of wall clock because it is measuring
whether the worklet keeps up in real time, and there is no way to hurry that.

* `offline.html` — an `OfflineAudioContext` sweep: ratios 1.0 / 1.4 / 2.0 / 2.5, R3 vs
  R2, stereo vs mono, a −1 semitone shift at ratio 2.0, and a mid-stream ratio change
  standing in for a dragged speed slider.
* `realtime.html` — a real `AudioContext`, which is the only suite that can fail for
  the reason we care about: 30 s of stretched output has to arrive in 30 s of wall
  clock or the engine is not viable.
* `rb-worklet.js` — the processor. Also the working prototype for D4's `player.js`.

Results from the run on record are in `results/`.

## What it measured

Machine: AMD Ryzen 9 6900HX, 16 logical cores, Windows 11, Chrome 152, 44.1 kHz.

**Real-time, stereo, R3, `--formant` equivalent** (`results/2026-09-05-realtime.json`):

| run | audio out | wall clock | underruns | NaN | CPU (one core) |
|---|---|---|---|---|---|
| ratio 2.0, 15 s in | 30.190 s | 30.182 s | 0 / 10 402 quanta | 0 | 17.2 % |
| ratio 2.5 + pitch −1, 8 s in | 20.231 s | 20.232 s | 0 / 6 971 | 0 | 17.9 % |
| ratio 2.0, R2 for comparison | 20.046 s | 20.044 s | 0 / 6 907 | 0 | 5.9 % |

So R3 at the ratios this tool actually uses costs about a sixth of one core and holds
real time with room to spare. R2 is roughly three times cheaper and we are not taking
it — `docs/03-audio-engine.md` already priced that trade.

One honest caveat on the per-quantum tail. Of 10 402 quanta at ratio 2.0, nine
measured 3 ms or more against a nominal 2.9 ms budget, and one measured 37 ms — but
that one is the final quantum, where the probe drains the stretcher after the source
runs out, and eight of the other nine sit exactly on the 1 ms resolution floor of
`Date.now`. Chrome renders a whole device buffer per callback (`baseLatency` 10 ms,
three or four quanta) rather than one quantum at a time, so the budget that matters
is per callback, and the run produced 30.19 s of audio in 30.18 s of wall clock with
no underrun. The number to trust here is the wall clock, not the histogram.

**Offline sweep** (`results/2026-09-05-offline.json`): every run produced 0 underruns,
0 NaN samples, and a measured output/input frame ratio within 1.3 % of the requested
one (the excess is the start pad and the tail). The 440 Hz component came back at
440 Hz — or at 415.30 Hz when a −1 semitone shift was asked for — with the octave
below it a thousand times quieter, which is the check for invariant 8's failure mode.
Changing the time ratio mid-stream from 1.0 to 2.0 to 1.4 produced no NaN, no
discontinuity and no crash.

## Three things this turned up that D4 has to know

1. **The worklet must own the source and pull.** A processor that takes 128 frames
   from an upstream node and returns 128 frames per quantum cannot hold any ratio but
   1.0: at 2.0 the stretcher emits two frames per frame consumed, so the surplus
   accumulates and latency grows without bound; below 1.0 it starves. `rb-worklet.js`
   feeds `rb_get_samples_required()` frames from a source it holds, until
   `rb_available()` can cover the quantum. This is why we do not use the published
   `rubberband-web` worklet, whose processor is written the other way round.

2. **There is a start delay and it is not zero.** R3 reports
   `preferredStartPad` 2048 and `startDelay` 2048 frames — 46.4 ms at 44.1 kHz, and
   2170 when a pitch shift is on; R2 reports 1024. Feed the pad, drop the delay, and
   put both numbers into `clock.py`'s source↔playback conversion rather than
   discovering them as a 46 ms offset in the section boundaries later.

3. **`performance` does not exist in `AudioWorkletGlobalScope`.** The per-quantum
   histogram in `rb-worklet.js` falls back to `Date.now`, which is why it is reported
   as a coarse 1 ms histogram and not as a mean. Nothing in `web/` should assume
   `performance.now()` inside a worklet.

## What this does not prove

It does not prove the stretch *sounds* right. Every number here is throughput and
arithmetic on a synthetic chord-plus-clicks signal; the claim in
`docs/03-audio-engine.md` that R3 holds up at 1.4–2.5 where WSOLA flutters is a
listening judgement, and per prime directive 12 this tool does not get to make it.
The remaining check is a human one: play a real record through the D4 slider at 50 %
and listen.
