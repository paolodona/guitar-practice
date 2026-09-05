# Vendored third-party code

## Rubber Band WASM (not yet vendored here — documented ahead of the front end)

The real-time engine (`web/player.js`, work unit D4, gated on prerequisite P1) will
vendor a Rubber Band WASM build into this directory. This README exists now, ahead of
that unit, so the licence story is settled before any front-end code lands.

- **Build**: `rubberband-wasm@3.3.0` — Rubber Band 3.3.0 from the official source
  tarball, built by Daninet's npm package.
- **File**: a single `dist/rubberband.wasm`. It is `STANDALONE_WASM` — it instantiates
  directly from a `WebAssembly.Module` with no Emscripten JS glue required. See
  `tools/rb-probe/` for the working prototype (`rb-worklet.js`) and the measurements
  that confirmed this build runs R3 in real time inside an `AudioWorklet` at ratio 2.0
  (`docs/03-audio-engine.md`).
- **Licence**: GPLv2+ (the same terms as the upstream `rubberband` CLI). Vendoring this
  binary into published `web/` makes the distributed work GPL, which is why the repo
  carries a top-level `LICENSE` (`GPL-2.0-or-later`) — see `../../LICENSE`.
- **Corresponding source (GPLv2 §3)**: a bare `.wasm` blob is not enough on its own.
  When D4 vendors the binary, it must also commit `build.sh` and the C shim beside it
  — `tools/rb-probe/upstream-build.sh` and `tools/rb-probe/upstream-shim.c` are exactly
  those files, already fetched and hash-checked by `tools/rb-probe/fetch-wasm.ps1`.
  D4 copies (or symlinks, if that's cleaner) `rubberband.wasm`, `upstream-build.sh` (as
  `build.sh`), `upstream-shim.c`, and `upstream-LICENSE` into this directory.

**Not done by this unit, deliberately**: the actual binary/build files are not copied
in yet. That is D4's job, and D4 is blocked on prerequisite P1 (the design artboards
must be exported from the published canvas before front-end work starts). This file
just fixes the licence and provenance story ahead of time so nobody has to re-derive
it under a later deadline.

Self-hosted fonts (Archivo, IBM Plex Mono) will also live under `web/vendor/fonts/`
once the front end is built (work unit D1) — not yet present either.
