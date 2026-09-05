# Where the audio comes from

**Revised 2026-09-05.** The first draft treated "I don't own this song" as an
import problem. It is a capture problem, and capture is a first-class feature —
without source audio there is nothing to practise against.

## Two separate questions

1. **Which song is this?** — title, artist, album, artwork, duration, order in a
   playlist. Spotify answers this well and it is worth using.
2. **Where do the samples come from?** — Spotify cannot answer this, ever. The
   machine's own output can, and does.

Keep them apart in the code as they are kept apart here.

---

## What Spotify can and cannot do

**It cannot give you audio, and no engineering changes that.** The Web API has
never returned a track. The Web Playback SDK streams through Encrypted Media
Extensions, so the decoded samples live inside the DRM path: unreadable from
script, unroutable through Web Audio, unstretchable, and `playbackRate` does not
even apply. There is no clever way round this from inside a browser page.

**What it is for here** is the list. Search, or paste a track/album/playlist URL,
and the song entries appear with title, artist, album, artwork, duration and a
stable `spotify_id` — a whole setlist in one action, in order. That removes the
only real tedium in setting the tool up, and it is what feeds the capture
queue below.

Do not count on Audio Features or Audio Analysis for tempo and key — those were
restricted for new apps in late 2024. Fit the tempo off the file instead; it is
better anyway, because it measures the audio you will actually play against.
Verify the 200 or the 403 at registration and write the date in this file.

---

## Capture: recording what the machine plays

The general answer, and the one that works for every source at once. Windows
exposes **WASAPI loopback**: a capture stream fed by the render device's own mix.
It is a *digital* copy of what the output device receives — bit-exact, no cable,
no analogue stage, no quality loss against the stream itself. Whatever is
playing — a browser, a streaming client, a media player, a DAW — lands in a
file.

That is deliberately source-agnostic. The tool records an audio device; it does
not know or care which application produced the sound, and it contains no
integration with any service. **Two things it will not contain**, for reasons
worth stating once and then not repeating: anything that decrypts a
DRM-protected stream, and any bundled downloader for a service whose terms
forbid downloading. Recording a protected stream is against Spotify's and
YouTube's terms of use whatever the mechanism; that is a judgement about what
you point the recorder at, and it is yours to make. If you would rather fetch a
file with a tool of your own, the **Drop a file** path already takes whatever
you produce.

### How it works

```
woodshed capture --queue ramba-live-2027      # arm, then press play over there
```

* **Device**: the default render device, or a named one. 48 kHz, 32-bit float.
* **Detect, don't guess**: recording starts on the first sample above the noise
  floor and a **segment ends after ≥1.2 s below it**. So a whole playlist is one
  pass and comes out as one file per track.
* **Match by duration, in order.** Each segment is bound to the next unclaimed
  track in the imported tracklist. A segment within ±1.5 s of the expected
  duration binds silently; anything further out stops and asks. Never bind on a
  guess — the failure mode is a whole album shifted by one, and it is invisible
  until you practise the wrong song.
* **Then the usual**: trim the leading and trailing silence, write FLAC, hash it,
  detect the tempo, compute the peaks, mark the song `bound`.
* **Real time, once.** A four-minute song takes four minutes; a 23-song set is
  an hour and a half of the machine playing to itself. It happens **once per
  song, ever**, which is why the queue matters and a per-song capture button is
  the fallback rather than the main road.

### What will bite

* **It records everything the device plays.** Notifications, a video in another
  tab, a Teams call. Mute notifications and play one thing at a time; the
  capture screen says so.
* **Exclusive-mode applications** (some ASIO paths, a DAW holding the device)
  bypass the shared mix and produce silence. `doctor` checks the loopback opens
  and reports it.
* **The system volume does not matter** — loopback taps the mix before the
  endpoint volume on most drivers — but a per-application volume does. Play at
  100 % in the app.
* **A dropout is silent.** Log the callback's overflow flag per segment and
  refuse to bind a segment that reported one.

### Implementation

`capture.py`, its own optional extra, importable only from there — the same
layering rule as `analyze.py` and `render.py`. On Windows the reliable route is
**PyAudioWPatch** (a PyAudio fork carrying WASAPI loopback) or `sounddevice`
with `WasapiSettings(loopback=True)`. On macOS there is no OS loopback: it needs
a virtual device (BlackHole, Loopback) and `doctor` should say so rather than
failing obscurely. Ring buffer to disk, never to memory: an hour of stereo
float32 at 48 kHz is 1.4 GB.

---

## The other routes, still worth having

| source | notes |
|---|---|
| **Your own files** | MP3, FLAC, WAV, M4A you already have. Instant, lossless, no capture pass |
| **Your CDs** | Rip to FLAC. Best quality available and unambiguously yours |
| **Buy the track** | Bandcamp, Qobuz, 7digital, Amazon MP3. DRM-free, a few euros, and no hour of real-time capture |
| **The band's own recordings** | For originals the masters are already in `rambass-live`; `render/` output *is* the practice track for those. The library scan should look there |
| **Isolated stems** | `demucs` in `rambass-live` separates a mix. A guitar-only stem is the right source when a solo needs to go below 50 % and stay legible |

Streaming-service offline downloads (Spotify, Apple Music, YouTube Music) are
DRM-encrypted files on disk and are not usable as sources — which is exactly why
capture exists.

## Binding, and the visible gap

**`needs-audio` is a first-class state, not an error.** A song imported from a
playlist with no file yet shows on the dashboard with a **Capture** button and a
**Bind file** button, and it is *visible*. Silence about a gap is how a setlist
quietly turns out to be half practisable the week before the gig.

Three ways to bind: drop a file, scan `config.library_paths` (tags first, then
filename, candidates shown — never bind automatically on a fuzzy match), or
capture. Optionally, later, Chromaprint/AcoustID for a real fingerprint match
instead of a string match.
