# Where the audio comes from

## The short answer about Spotify

**You cannot practise against Spotify audio, and no amount of engineering
changes that.** Better to design around it now than to discover it in week three.

Two independent blocks:

1. **There is no audio in the Web API.** It returns metadata — titles, artists,
   albums, artwork, durations, ids. It has never returned a full track.
2. **The Web Playback SDK plays audio you cannot touch.** It streams into the
   browser through Encrypted Media Extensions. The decoded samples are inside
   the DRM path: you cannot read them, cannot route them through Web Audio,
   cannot time-stretch them, cannot pitch-shift them, and cannot even set
   `playbackRate`. It is a remote control for a player, not an audio source.

And capturing the stream anyway — a virtual audio cable, a stream-ripper — is a
straightforward breach of Spotify's terms, so this repo will not contain it.

### What Spotify *is* good for here, and it is genuinely useful

**Identity and import.** Almost all the tedium of setting this tool up is typing
song titles and building setlists. Spotify removes exactly that:

* **Search** — type three words, pick the right track, and the song entry is
  created with title, artist, album, artwork, duration and a stable `spotify_id`.
* **Paste a URL** — track, album or playlist.
* **A playlist becomes a setlist.** This is the feature worth building. Your band
  has a rehearsal playlist; one paste and the whole running order exists, in
  order, with artwork, every song marked `needs-audio`.
* **A stable id** to hang everything else off, and to re-find a song after you
  rename a file.

So the import flow is: **Spotify gives you the list. You supply the bytes.**

### The endpoints that are gone, and how to be sure

Spotify restricted several Web API endpoints for **newly created apps** in
November 2024 — among them Audio Features and Audio Analysis (which returned
tempo, key, and a beat grid), Recommendations, Related Artists, and the
30-second `preview_url` in most responses. Apps that already had extended access
kept it; a fresh app registered today almost certainly does not.

Assume they are unavailable. If you were counting on Spotify for tempo and key:
**do not** — `analyze.refine_tempo` in `rambass-live` gets you a better number
anyway, because it fits the actual file rather than a bin centre, and it works
offline on the audio you will actually be playing.

**Verify this at registration rather than trusting this paragraph.** Create the
app, call `/audio-features/{id}` once, and write the result — 200 or 403 — into
this document with the date. That is the house rule from `gx100/CLAUDE.md`
applied to an API instead of a pedal: *a document is `probable`, and the unit
wins*. Here the unit is the API response.

### Auth, concretely

* Register an app at `developer.spotify.com/dashboard`, personal, non-commercial.
* **Authorization Code with PKCE.** No client secret in the repo, and none
  needed — PKCE exists for exactly this shape of app.
* Redirect URI: a loopback address on the app's own port, e.g.
  `http://127.0.0.1:8477/callback`. Spotify has tightened what it accepts here
  (explicit loopback IP rather than the hostname `localhost`); use the IP form.
* The refresh token goes in `~/.woodshed/credentials.json`, mode 600 — **not**
  in `config.yaml`, which is tracked. `config.yaml` holds the client id only,
  which is not a secret.
* The whole integration is optional. `woodshed doctor` reports it as configured
  or not, and every other feature works with it absent.

---

## Where the bytes actually come from

In rough order of how good they are:

| source | notes |
|---|---|
| **Your own files** | MP3, FLAC, WAV, M4A you already have. The main case, and the one to make effortless |
| **Your CDs** | Rip to FLAC. Best quality available and unambiguously yours |
| **Buy the track** | Bandcamp, Qobuz, 7digital, Amazon MP3, iTunes Store. A few euros per song, DRM-free, one click, and it is the answer for the songs you do not have |
| **The band's own recordings** | For originals, the masters are in `rambass-live` already — and its `render/` output *is* the practice track for those |
| **Isolated stems** | `demucs` in `rambass-live` separates a mix. A guitar-only stem is the right source when a solo needs to go below 50 % and stay legible (see `docs/03-audio-engine.md`) |

Streaming-service downloads (Spotify offline, Apple Music, YouTube Music
Premium) are DRM-encrypted files on disk and are not usable as sources.

## Binding files to songs

**The `needs-audio` state is a first-class state, not an error.** A song imported
from a playlist with no file bound is a perfectly good song entry — it shows on
the dashboard with a "bind audio" button and it is *visible*, which is the whole
point. Silence about a gap is how a setlist quietly turns out to be half
practisable the week before the gig. Same instinct as `rambass-live`'s status
board and `gx100`'s `unknowns.md`: an unknown is a fine answer, and it goes
somewhere a human will find it.

Three ways to bind:

1. **Drop a file** on the song row.
2. **Scan** the folders in `config.library_paths`, matching on tags first
   (`artist` + `title`), then on a fuzzy filename match. Show candidates and let
   a human choose — never bind automatically on a fuzzy match.
3. **Fingerprint** (optional, later): Chromaprint/AcoustID gives a real match
   rather than a string match, and is worth it if the library is large and the
   tags are bad. `fpcalc` as a subprocess, an optional extra, absent by default.

On binding: hash the file, read its duration, run tempo detection, compute peaks.
Roughly a few seconds per song, once, in the background, with a visible spinner
on the row rather than a modal that blocks the app.
