# Self-hosted fonts

Vendored so the app works on a laptop with no network at runtime (CLAUDE.md's whole
reason this repo exists). Fetched once, from Google Fonts, during this build step
only — nothing here is fetched again by the running app.

- **Source**: `https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap`,
  requested with a desktop-Chrome User-Agent (Google serves woff2 only to modern
  UAs). Only the `latin` unicode-range block was kept for each weight —
  vietnamese/latin-ext/cyrillic/cyrillic-ext were dropped; this app has no need for
  them and it keeps this directory small.
- **Archivo** (400/500/600/700): Google serves Archivo as a single **variable**
  woff2 — the four weight blocks in the fetched CSS all point at the identical
  URL, one file whose weight axis a browser resolves per `@font-face`'s declared
  `font-weight`. So there is exactly one file, `archivo-variable.woff2`, reused by
  four `@font-face` rules in `web/index.html`.
- **IBM Plex Mono** (400/500/600): not variable — three distinct static files,
  `ibmplexmono-400.woff2` / `-500.woff2` / `-600.woff2`.
- **Licence**: both families are **SIL Open Font License 1.1**. Archivo is
  designed by Omnibus-Type; IBM Plex Mono by IBM under the same licence. The OFL
  permits bundling and redistribution (including in a GPL-licensed work — see
  `../README.md`'s note on `rubberband.wasm`) provided the font name isn't used to
  market a derivative; neither restriction is triggered by embedding these files
  unmodified in this app. Full licence text: <https://openfontlicense.org/>.

| File | Family | Weight(s) |
|---|---|---|
| `archivo-variable.woff2` | Archivo | 400, 500, 600, 700 (variable) |
| `ibmplexmono-400.woff2` | IBM Plex Mono | 400 |
| `ibmplexmono-500.woff2` | IBM Plex Mono | 500 |
| `ibmplexmono-600.woff2` | IBM Plex Mono | 600 |
