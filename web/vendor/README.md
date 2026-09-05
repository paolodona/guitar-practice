# Vendored third-party code

## Rubber Band WASM

`web/vendor/rubberband/` holds the vendored build, copied in by work unit D4
(2026-09-05) from `tools/rb-probe/`:
`rubberband.wasm`, `build.sh` (renamed from `upstream-build.sh`), `upstream-shim.c`,
`upstream-LICENSE`, plus `worklet.js` — the `AudioWorkletProcessor` D4 adapted from
`tools/rb-probe/rb-worklet.js` for looping, play/pause gating and start-pad discard.

- **Build**: `rubberband-wasm@3.3.0` — Rubber Band 3.3.0 from the official source
  tarball, built by Daninet's npm package.
- **File**: a single `rubberband.wasm`. It is `STANDALONE_WASM` — it instantiates
  directly from a `WebAssembly.Module` with no Emscripten JS glue required, and D4
  confirmed the copied file's sha256 (`496d880b…f07c04dc`) matches what
  `tools/rb-probe/fetch-wasm.ps1` originally hash-checked.
- **Licence**: GPLv2+ (the same terms as the upstream `rubberband` CLI). Vendoring this
  binary into published `web/` makes the distributed work GPL, which is why the repo
  carries a top-level `LICENSE` (`GPL-2.0-or-later`) — see `../../LICENSE`.
- **Corresponding source (GPLv2 §3)**: `build.sh` and `upstream-shim.c` are committed
  beside the binary for exactly this reason — a bare `.wasm` blob is not enough on its
  own.

## Self-hosted fonts

`web/vendor/fonts/` holds Archivo (one variable woff2 covering weights 400-700 — Google
serves it as a single file, so there is nothing to duplicate per weight) and IBM Plex
Mono (three static woff2 files, 400/500/600 — no variable build exists for this family),
both fetched from Google Fonts by work unit D1 (2026-09-05). Both are SIL Open Font
License 1.1; see `web/vendor/fonts/README.md` for the exact source URLs and weights.
