"""Woodshed's HTTP server: stdlib, local, thin over the Tier 0/1 modules.

Bound to 127.0.0.1 only -- this is a tool on Paolo's own machine, not a
service. Every mutation goes through the same functions the CLI uses
(``ledger.append``, ``manifest.load_song``/``save_song``), so nothing the
browser can do differs from what a terminal can, per CLAUDE.md's "the repo
is the database".

This unit (C2, Phase 0) owned the routes below marked (C2); render/progress
are later phases and are deliberately not built here (see the plan's
"Endpoint ownership"). Phase 1's F1 added the setlist-scoped routes and, with
them, `?setlist=` and `readiness` on `/api/song/<slug>` -- practice.py did
not exist during C2's pass, so those were placeholders (0 / omitted) until
now:

    GET  /                              -> web/index.html                 (C2)
    GET  /web/*                         -> static files under web/        (C2)
    GET  /api/config                    -> config.load_config(repo)       (C2)
    GET  /api/setlists                  -> [{slug, name, tuning, date,
                                             song_count}]                 (F1)
    GET  /api/setlist/<slug>            -> dashboard payload: rows,
                                            next_up, needs_audio_count,
                                            weeks_to_gig                  (F1)
    GET  /api/song/<slug>?setlist=      -> song page payload (song,
                                            sections with lanes +
                                            ancestors, tempo, peaks url,
                                            shift, readiness)        (C2, F1)
    GET  /api/peaks/<slug>              -> cached peaks json, 404 if
                                            not built                     (C2)
    GET  /api/audio/<slug>              -> the source file, RANGE-SERVED  (C2)
    GET  /api/click/<slug>/<section>?speed=&mode=lead_in|full
                                        -> a generated click WAV, own gain (G2)
    POST /api/rep                       -> appends ONE ledger line        (C2)
    POST /api/section                   -> create/update/delete a span    (C2)
    POST /api/shift                     -> writes setlist.songs[].shift   (F1)
    POST /api/setlist                   -> create a new setlist       (post-Phase-1)
    POST /api/setlist/<slug>/songs      -> add a song to a setlist    (post-Phase-1)
    POST /api/shutdown                  -> stops the server               (C2)

`/api/peaks/<slug>` still answers 404 with a small body when woodshed.peaks
cannot be imported, rather than failing to import at server start -- that
part of C2's reasoning is unchanged.

``parse_byte_range``, the ``_send``/``_json``/``_error``/``_body``/
``_send_file`` plumbing, ``ConsoleServer``'s ``allow_reuse_address = False``
and ``make_server`` are lifted from
``rambass-live/src/rambass/console.py`` (verbatim where the logic is
project-agnostic), with ``ProjectError`` renamed to
:class:`woodshed.errors.WoodshedError` and the console's per-song rebuild
lock (not needed here -- this unit renders nothing) dropped.
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import threading
import uuid
import wave as wave_module
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np
from pydantic import ValidationError

from woodshed import ledger, practice, sections
from woodshed.click import render_click
from woodshed.clock import pre_roll_seconds
from woodshed.config import load_config
from woodshed.errors import WoodshedError
from woodshed.ledger import Rep
from woodshed.library import Repo, slugify
from woodshed.manifest import Section, Setlist, effective_pre_roll_beats, load_song, save_song
from woodshed.setlist import add_song, effective_shift, set_shift
from woodshed.setlist import create as create_setlist
from woodshed.setlist import load as load_setlist
from woodshed.setlist import save as save_setlist

#: Arbitrary and unregistered; --port overrides it. Not load-bearing.
DEFAULT_PORT = 8420


def parse_byte_range(header: str, size: int):
    """``(start, stop)`` for a ``Range:`` header over a *size*-byte file.

    Lifted verbatim from rambass-live/src/rambass/console.py:58.

    ``None`` means "no usable range, send the whole thing" -- which RFC 9110
    requires for a header this cannot parse, rather than an error: a clip that
    refuses to serve is a clip that does not play at all. ``()`` means the
    range is well formed but unsatisfiable, which *is* an error (416), because
    that is the answer a player needs in order to correct itself.

    Only the single-range forms a media element actually sends: ``bytes=N-M``,
    ``bytes=N-`` to resume, and ``bytes=-N`` for a trailer.
    """
    units, _, spec = (header or "").partition("=")
    if units.strip().lower() != "bytes" or "," in spec:
        return None
    first, sep, last = spec.strip().partition("-")
    if not sep:
        return None
    try:
        if not first:
            if not last:
                return None
            start, stop = max(0, size - int(last)), size
        else:
            start = int(first)
            stop = size if not last else min(size, int(last) + 1)
    except ValueError:
        return None
    if start >= size:
        return ()          # well formed, past the end: 416
    if stop <= start:
        return None        # e.g. bytes=5-2 -- nonsense, so ignore it
    return start, stop


class WoodshedHandler(BaseHTTPRequestHandler):
    """Routes only. All judgement lives in the Tier 0/1 modules."""

    server_version = "woodshed-server"
    # Seeking a rendered/source file is a stream of range requests, and
    # HTTP/1.0 closes the connection after every one of them. Safe here
    # because every reply goes through `_send` / `_send_file` / `_json`,
    # all of which set an accurate Content-Length, which is what keep-alive
    # needs. Lifted from rambass-live/src/rambass/console.py:110.
    protocol_version = "HTTP/1.1"
    repo: Repo = None  # set by make_server

    def log_message(self, *args) -> None:  # noqa: D102 -- silence per request
        pass

    # ── plumbing, lifted verbatim from
    #    rambass-live/src/rambass/console.py:126-188 ─────────────────────────
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        """Serve *path*, honouring ``Range``. What makes a clip seekable.

        A browser will not let you seek in a media resource that does not
        advertise byte ranges: `element.seekable` stays empty and assigning
        `currentTime` snaps back to the start of what is buffered.

        Only the requested slice is read, rather than the whole file per
        request.
        """
        size = path.stat().st_size
        wanted = parse_byte_range(self.headers.get("Range", ""), size)
        if wanted == ():
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, stop = wanted or (0, size)
        with path.open("rb") as handle:
            handle.seek(start)
            body = handle.read(stop - start)
        self.send_response(206 if wanted else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        # Never cached: a render can be re-cut with the same URL (a changed
        # span misses the fingerprinted cache key instead, but the SOURCE
        # file at /api/audio/<slug> has no such key), so a browser holding
        # old bytes would go on playing stale audio -- the one lie this
        # server must not tell.
        self.send_header("Cache-Control", "no-store")
        if wanted:
            self.send_header("Content-Range", f"bytes {start}-{stop - 1}/{size}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # ── path resolution: allow-list, never concatenate ──────────────────────
    def _resolve_slug(self, raw: str) -> str | None:
        """Decode *raw* and check it against ``repo.list_songs()``.

        A slug that decodes to something containing a path separator (a
        traversal attempt, e.g. ``..%2f..%2fetc``) can never equal a real
        slug -- slugs are lowercase-kebab, no slashes -- so this allow-list
        check is the whole of the defence: it never opens a file to find
        out, it just refuses to recognise the name.
        """
        slug = unquote(raw)
        if slug in self.repo.list_songs():
            return slug
        return None

    def _resolve_under(self, base: Path, rest: str) -> Path | None:
        """Resolve *rest* under *base*; ``None`` if it would escape *base*.

        Every path segment is resolved and checked with ``is_relative_to``
        before anything is opened -- a traversal attempt is a 404 from this
        check, never a file read outside *base*.
        """
        base = base.resolve()
        candidate = (base / unquote(rest)).resolve()
        if not candidate.is_relative_to(base):
            return None
        return candidate

    # ── GET ─────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 -- stdlib naming
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path == "/":
                self._index()
            elif path.startswith("/web/"):
                self._web_static(path.removeprefix("/web/"))
            elif path == "/api/config":
                self._json(load_config(self.repo).model_dump(mode="json"))
            elif path == "/api/setlists":
                self._setlists()
            elif path.startswith("/api/setlist/"):
                self._setlist(path.removeprefix("/api/setlist/"))
            elif path.startswith("/api/song/"):
                self._song(path.removeprefix("/api/song/"), parsed.query)
            elif path.startswith("/api/peaks/"):
                self._peaks(path.removeprefix("/api/peaks/"), parsed.query)
            elif path.startswith("/api/audio/"):
                self._audio(path.removeprefix("/api/audio/"))
            elif path.startswith("/api/click/"):
                self._click(path.removeprefix("/api/click/"), parsed.query)
            else:
                self._error(404, f"no such page: {path}")
        except (WoodshedError, ValidationError) as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass

    def _index(self) -> None:
        target = self.repo.web_dir / "index.html"
        if not target.is_file():
            self._error(404, "web/index.html is missing")
            return
        self._send(200, target.read_bytes(), "text/html; charset=utf-8")

    def _web_static(self, rest: str) -> None:
        target = self._resolve_under(self.repo.web_dir, rest)
        if target is None or not target.is_file():
            self._error(404, f"no such file: {rest}")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), content_type)

    def _song(self, raw_slug: str, query: str = "") -> None:
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        song = load_song(self.repo.song_dir(slug) / "song.yaml")
        # manifest.Section satisfies sections.Span directly (it carries a
        # `duration` property) -- no adapter needed.
        spans = song.sections
        lanes = sections.assign_lanes(spans)

        # `?setlist=` names WHICH setlist's shift applies (CLAUDE.md's
        # "transpose is per song, the setlist only supplies a default" --
        # there is no shift to derive without one). An unknown setlist slug,
        # or a setlist this song isn't a member of, degrades to 0 rather
        # than 404ing the whole song page over a stale query param.
        shift = 0
        setlist_slug = parse_qs(query).get("setlist", [None])[0]
        if setlist_slug:
            try:
                setlist = load_setlist(self.repo, setlist_slug)
            except WoodshedError:
                setlist = None
            if setlist is not None:
                entry = next((e for e in setlist.songs if e.slug == slug), None)
                if entry is not None:
                    shift = effective_shift(setlist, entry, song)

        reps = list(ledger.read(self.repo))
        readiness = practice.song_readiness(song, reps, song.practice)

        self._json({
            "slug": song.slug,
            "title": song.title,
            "artist": song.artist,
            "album": song.album,
            "recording": song.recording.model_dump(mode="json"),
            "tempo": song.tempo.model_dump(mode="json"),
            "practice": song.practice.model_dump(mode="json"),
            "shift": shift,
            "peaks_url": f"/api/peaks/{slug}",
            "readiness": {
                "ratio": readiness.ratio,
                "intervals": [
                    {
                        "start_s": interval.start_s,
                        "end_s": interval.end_s,
                        "span_id": interval.span_id,
                        "reached": interval.reached,
                    }
                    for interval in readiness.intervals
                ],
            },
            "sections": [
                {
                    **section.model_dump(mode="json"),
                    "lane": lanes[section.id],
                    "ancestors": [a.id for a in sections.ancestors(spans, section.id)],
                }
                for section in song.sections
            ],
        })

    def _setlists(self) -> None:
        result = []
        for slug in self.repo.list_setlists():
            try:
                setlist = load_setlist(self.repo, slug)
            except (WoodshedError, ValidationError):
                # A malformed setlists/*.yaml shouldn't 500 the whole list --
                # skip it; it will still fail loudly if opened directly via
                # GET /api/setlist/<slug>.
                continue
            result.append({
                "slug": slug,
                "name": setlist.name,
                "tuning": setlist.tuning,
                "date": setlist.date.isoformat() if setlist.date else None,
                "song_count": len(setlist.songs),
            })
        self._json(result)

    def _setlist(self, raw_slug: str) -> None:
        """The dashboard's whole payload for one setlist: one row per song,
        the next-up pick, and the three headline numbers docs/00-spec.md's
        Dashboard section names (weeks to the gig, songs at target, needs
        audio)."""
        slug = raw_slug
        if slug not in self.repo.list_setlists():
            self._error(404, f"no such setlist: {raw_slug!r}")
            return
        setlist = load_setlist(self.repo, slug)
        reps = list(ledger.read(self.repo))
        now = datetime.now(UTC)

        rows = []
        songs_at_target = 0
        needs_audio_count = 0
        for entry in setlist.songs:
            song_path = self.repo.song_dir(entry.slug) / "song.yaml"
            if not song_path.is_file():
                needs_audio_count += 1
                rows.append({
                    "slug": entry.slug, "title": entry.slug, "artist": None,
                    "needs_audio": True, "readiness": None, "section_count": 0,
                    "sections_under_target": 0, "last_practised": None,
                    "is_cold": False, "shift": None,
                })
                continue

            song = load_song(song_path)
            needs_audio = not (self.repo.song_dir(song.slug) / song.recording.file).is_file()
            if needs_audio:
                needs_audio_count += 1

            readiness = practice.song_readiness(song, reps, song.practice)
            # Same filter practice.song_readiness applies internally (a
            # full_song section is never "under target" material either --
            # it's a rep counter, not a practice target) -- kept in sync
            # by hand since this is a display-only count, not itself a
            # coverage_readiness input.
            counting = [s for s in song.sections if s.counts_toward_readiness and not s.full_song]
            under_target = sum(
                1 for s in counting if practice.reached(reps, song, s, song.practice) < 1.0
            )
            if counting and under_target == 0:
                songs_at_target += 1

            last = ledger.last_practised(reps, song.slug)
            is_cold = (
                last is not None
                and (now - last).total_seconds() / 86400.0 > practice.COLD_DAYS
                and readiness.ratio > practice.COLD_REACHED_THRESHOLD
            )

            rows.append({
                "slug": song.slug,
                "title": song.title,
                "artist": song.artist,
                "needs_audio": needs_audio,
                "readiness": readiness.ratio,
                # Excludes full_song sections from the count shown here --
                # it's not a practice target the way the rest of this row
                # is about (see manifest.Section.full_song).
                "section_count": sum(1 for s in song.sections if not s.full_song),
                "sections_under_target": under_target,
                "last_practised": last.isoformat() if last else None,
                "is_cold": is_cold,
                "shift": effective_shift(setlist, entry, song),
            })

        cfg = load_config(self.repo).defaults
        ranked = practice.next_up(self.repo, setlist, reps, cfg, now=now)
        next_up_payload = None
        if ranked:
            top = ranked[0]
            top_song = load_song(self.repo.song_dir(top.song_slug) / "song.yaml")
            top_section = next(s for s in top_song.sections if s.id == top.section_id)
            next_up_payload = {
                "song_slug": top.song_slug,
                "song_title": top_song.title,
                "section_id": top.section_id,
                "section_name": top_section.name,
                "target_speed": top_section.target_speed,
                "score": top.score,
                "gap": top.gap,
                "cold": top.cold,
                "gig": top.gig,
                "reached": top.reached,
            }

        weeks_to_gig = None
        if setlist.date is not None:
            weeks_to_gig = max(0, (setlist.date - now.date()).days // 7)

        self._json({
            "slug": slug,
            "name": setlist.name,
            "tuning": setlist.tuning,
            "date": setlist.date.isoformat() if setlist.date else None,
            "venue": setlist.venue,
            "weeks_to_gig": weeks_to_gig,
            "song_count": len(setlist.songs),
            "songs_at_target": songs_at_target,
            "needs_audio_count": needs_audio_count,
            "next_up": next_up_payload,
            "rows": rows,
        })

    def _peaks(self, raw_slug: str, query: str) -> None:
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        level = parse_qs(query).get("level", [None])[0]
        try:
            from woodshed import peaks as peaks_module
        except ImportError:
            # woodshed.peaks (Tier 2) is not built yet in this pass. A 404
            # with a small explanatory body rather than a 500: the caller's
            # documented choice for a cache that is not built yet.
            self._error(404, f"peaks for {slug!r} are not built yet")
            return
        data = peaks_module.read_peaks(self.repo, slug, level)
        if data is None:
            self._error(404, f"no cached peaks for {slug!r} at level {level!r}")
            return
        self._json(data)

    def _audio(self, raw_slug: str) -> None:
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        song_path = self.repo.song_dir(slug) / "song.yaml"
        if not song_path.is_file():
            self._error(404, f"no such song: {slug!r}")
            return
        song = load_song(song_path)
        audio_dir = self.repo.audio_dir(slug).resolve()
        candidate = (self.repo.song_dir(slug) / song.recording.file).resolve()
        if not candidate.is_relative_to(audio_dir) or not candidate.is_file():
            self._error(404, f"no audio file for {slug!r}")
            return
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self._send_file(candidate, content_type)

    def _click(self, rest: str, query: str) -> None:
        """A generated click WAV, in PLAYBACK seconds at the requested
        *speed* -- `click.render_click`'s own docstring assigns "mixing it
        in" to this unit (G2); this is that mixing.

        `mode=lead_in` (the default) covers just the lead-in
        (`effective_pre_roll_beats` converted to seconds via
        `clock.pre_roll_seconds`, then divided by *speed* the same way
        every other playback-seconds quantity is -- CLAUDE.md invariant 3).
        `mode=full` covers the lead-in PLUS one full loop, meant to be
        played with `loop=true` for the practice.click:"always" setting --
        a plain click buffer looped natively is already the sample-exact
        loop invariant 9 asks for, unlike the stretched music itself.

        Generated FRESH at `bpm * speed` with `grid_offset_s=0` (beat 1 at
        the window's own t=0), not sliced from a whole-song click -- this
        assumes the section's own boundary already lands on a downbeat
        (a grid-snapped section, G1). A `snapped: 'free'` section's click
        will not agree with the music's actual beats; that is an inherent
        limit of generating the click relative to the window rather than
        the whole song's `grid_offset_s`, named here rather than silently
        wrong.

        `bpm <= 0` (no tempo yet) answers a near-silent single-sample WAV
        rather than 404ing -- CLAUDE.md's degrade rule: no tempo means no
        click, not an error the caller has to special-case.
        """
        raw_slug, _, raw_section = rest.partition("/")
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        song = load_song(self.repo.song_dir(slug) / "song.yaml")
        section = next((s for s in song.sections if s.id == raw_section), None)
        if section is None:
            self._error(404, f"no such section: {raw_section!r}")
            return

        params = parse_qs(query)
        try:
            speed = float(params.get("speed", ["1.0"])[0])
        except ValueError:
            speed = 1.0
        speed = max(0.1, speed)
        mode = params.get("mode", ["lead_in"])[0]

        bpm = song.tempo.bpm
        if bpm <= 0:
            self._send(200, self._encode_wav_mono(np.zeros(1, dtype=np.float32)), "audio/wav")
            return

        pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), bpm) / speed
        if mode == "full":
            duration_s = pre_roll_s + (section.end_s - section.start_s) / speed
        else:
            duration_s = pre_roll_s

        pcm = render_click(bpm * speed, 0.0, song.tempo.time_signature, duration_s)
        self._send(200, self._encode_wav_mono(pcm), "audio/wav")

    @staticmethod
    def _encode_wav_mono(pcm: np.ndarray, sample_rate: int = 48000) -> bytes:
        """*pcm* (float32, [-1, 1]) as a 16-bit mono PCM WAV, stdlib only --
        no soundfile/scipy, matching CLAUDE.md's "numpy and nothing else"
        for this call site."""
        clamped = np.clip(pcm, -1.0, 1.0)
        pcm16 = (clamped * 32767.0).astype("<i2")
        buffer = io.BytesIO()
        with wave_module.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(pcm16.tobytes())
        return buffer.getvalue()

    # ── POST ────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802 -- stdlib naming
        path = urlsplit(self.path).path
        if not self._security_check():
            return
        body = self._body()
        try:
            if path == "/api/rep":
                self._post_rep(body)
            elif path == "/api/section":
                self._post_section(body)
            elif path == "/api/shift":
                self._post_shift(body)
            elif path == "/api/setlist":
                self._post_setlist(body)
            elif path.startswith("/api/setlist/") and path.endswith("/songs"):
                setlist_slug = path.removeprefix("/api/setlist/").removesuffix("/songs")
                self._post_setlist_songs(setlist_slug, body)
            elif path == "/api/shutdown":
                self._shutdown()
            else:
                self._error(404, f"no such action: {path}")
        except (WoodshedError, ValidationError) as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass

    def _security_check(self) -> bool:
        """Host + Origin checks against DNS rebinding and CSRF.

        Binding to 127.0.0.1 keeps the network out but not the browser: any
        page open in the same browser can POST to 127.0.0.1:<port>. A POST
        whose Host is not exactly 127.0.0.1|localhost:<port> is refused
        (defeats DNS rebinding); a POST whose Origin is present and is not
        this server's own origin is refused (defeats CSRF) -- but a POST
        with NO Origin header at all (curl, the CLI, some same-origin
        fetches) is allowed. No tokens, no nonces: this is a single-user
        tool on a loopback socket and those would be theatre.
        """
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._error(400, f"refusing a request with Host {host!r}")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://127.0.0.1:{port}":
            self._error(403, f"refusing a request with Origin {origin!r}")
            return False
        return True

    def _post_rep(self, body: dict) -> None:
        """Append one ledger line. Invariant 5: append-only, never rewritten."""
        song = str(body.get("song", ""))
        section = str(body.get("section", ""))
        if not song or not section:
            raise WoodshedError("a rep needs both 'song' and 'section'")
        rep = Rep(
            id=uuid.uuid4().hex,
            t=str(body.get("t") or datetime.now(UTC).isoformat().replace("+00:00", "Z")),
            song=song,
            section=section,
            speed=float(body.get("speed", 0.0)),
            semitones=int(body.get("semitones", 0)),
            # "pass" is the on-disk/wire key (docs/02-data-model.md); "passed"
            # accepted too since it is the Python-side attribute name.
            passed=bool(body.get("pass", body.get("passed", False))),
            clean=bool(body.get("clean", False)),
            loop_s=float(body.get("loop_s", 0.0)),
            setlist=body.get("setlist"),
            source=body.get("source", "ui"),
            retracted=bool(body.get("retracted", False)),
            retracts=body.get("retracts"),
        )
        ledger.append(self.repo, rep)
        self._json({"id": rep.id})

    def _post_section(self, body: dict) -> None:
        """Create, update or delete one span in `song.yaml`.

        Only for a song that already exists (`song.yaml` on disk): creating
        a brand-new song needs a bound `recording` (file, sha256,
        duration_s, tuning) that a section POST has no way to invent, and
        manifest.Song.recording is a required field -- so "create the song's
        first section" cannot mean "create the song" here. That is a
        different, larger operation (binding audio, detecting tempo) owned
        by a later unit; this endpoint 404s on an unknown slug the same way
        the GET routes do.
        """
        raw_slug = str(body.get("song", ""))
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        song_path = self.repo.song_dir(slug) / "song.yaml"
        song = load_song(song_path)

        action = str(body.get("action", "upsert"))
        if action == "delete":
            section_id = str(body.get("id", ""))
            if section_id not in {s.id for s in song.sections}:
                raise WoodshedError(f"no such section: {section_id!r}")
            song.sections = [s for s in song.sections if s.id != section_id]
        else:
            section_id = str(body.get("id") or uuid.uuid4().hex[:8])
            new_section = Section.model_validate({
                "id": section_id,
                "name": body.get("name", section_id),
                "start_s": float(body["start_s"]),
                "end_s": float(body["end_s"]),
                "snapped": body.get("snapped", "free"),
                "target_speed": float(body.get("target_speed", 100.0)),
                "ladder_step": body.get("ladder_step"),
                "reps_to_advance": body.get("reps_to_advance"),
                "notes": body.get("notes"),
                "patch": body.get("patch"),
                "counts_toward_readiness": bool(body.get("counts_toward_readiness", True)),
                # FOUND while wiring full_song through: lead_in_beats (G2)
                # was added to the model but never reached this explicit
                # field list, so a section's own lead-in override could
                # never actually be set via the UI/API -- fixed alongside.
                "lead_in_beats": body.get("lead_in_beats"),
                "full_song": bool(body.get("full_song", False)),
            })
            song.sections = [s for s in song.sections if s.id != section_id] + [new_section]

        sections.validate(song.sections, song.recording.duration_s)
        save_song(song, song_path)
        # Same lane/ancestors shape _song() returns -- invariant 2 says containment
        # and lane assignment are derived, never stored, and a caller that redraws
        # from this response (rather than re-fetching GET /api/song) needs the
        # derived fields here too, or every section silently collapses onto lane 0
        # after the first edit. Found while building the front end (Group D, D3).
        spans = song.sections
        lanes = sections.assign_lanes(spans)
        self._json({
            "sections": [
                {
                    **s.model_dump(mode="json"),
                    "lane": lanes[s.id],
                    "ancestors": [a.id for a in sections.ancestors(spans, s.id)],
                }
                for s in song.sections
            ]
        })

    def _post_shift(self, body: dict) -> None:
        """Write `setlist.songs[].shift` for one (setlist, song) pair.

        `shift: null` (or the key absent) clears an override back to
        "derive from setlist.tuning" -- the same "None means derive, 0 means
        explicitly zero" distinction `SetlistEntry` itself carries (see
        manifest.py and CLAUDE.md invariant 4). Range validation happens
        inside `set_shift` -> `SetlistEntry`, not here.
        """
        setlist_slug = str(body.get("setlist", ""))
        song_slug = str(body.get("song", ""))
        if not setlist_slug or not song_slug:
            raise WoodshedError("a shift needs both 'setlist' and 'song'")
        raw_shift = body.get("shift")
        shift = None if raw_shift is None else int(raw_shift)

        updated = set_shift(self.repo, setlist_slug, song_slug, shift)
        entry = next(e for e in updated.songs if e.slug == song_slug)

        song_path = self.repo.song_dir(song_slug) / "song.yaml"
        effective = shift
        if song_path.is_file():
            song = load_song(song_path)
            effective = effective_shift(updated, entry, song)

        self._json({
            "setlist": setlist_slug,
            "song": song_slug,
            "shift": effective,
            "raw": entry.shift,
        })

    def _post_setlist(self, body: dict) -> None:
        """Create a new setlist. Body: `{name, tuning, slug?, date?, venue?}`
        -- `slug` derived via `slugify(name)` when omitted, the same
        pattern `woodshed add`/`woodshed capture` use so the dashboard's
        "New setlist" form only ever has to ask for a name. Refuses (400)
        rather than overwriting if that slug already names a setlist --
        `setlist.create`'s own guard.
        """
        name = str(body.get("name", "")).strip()
        tuning = str(body.get("tuning", "")).strip()
        if not name or not tuning:
            raise WoodshedError("a setlist needs both 'name' and 'tuning'")
        slug = str(body.get("slug") or "").strip() or slugify(name)
        if not slug:
            raise WoodshedError(f"{name!r} does not slugify to anything usable -- pass 'slug'")

        setlist = Setlist(
            name=name, tuning=tuning,
            date=body.get("date") or None, venue=str(body.get("venue") or ""),
        )
        create_setlist(self.repo, slug, setlist)
        self._json({
            "slug": slug, "name": setlist.name, "tuning": setlist.tuning,
            "date": setlist.date.isoformat() if setlist.date else None,
            "song_count": 0,
        })

    def _post_setlist_songs(self, setlist_slug: str, body: dict) -> None:
        """Add a song to a setlist's running order. Body: `{song: <slug-or-
        title>, shift?}`.

        `song` is resolved the same way the CLI's `--slug`-less commands
        resolve a title (`Repo.find_song`: exact slug, or an unambiguous
        fuzzy match); a needle matching nothing is `slugify`'d and added
        as-is rather than refused -- adding a song to a setlist before its
        audio exists is exactly docs/00-spec.md's needs-audio state, not
        an error. `setlist.add_song` itself still refuses a slug already
        in this setlist's running order.
        """
        needle = str(body.get("song", "")).strip()
        if not needle:
            raise WoodshedError("a song needs a title or slug")
        try:
            song_slug = self.repo.find_song(needle)
        except WoodshedError:
            song_slug = slugify(needle)
            if not song_slug:
                raise WoodshedError(f"{needle!r} does not slugify to anything usable") from None

        raw_shift = body.get("shift")
        shift = None if raw_shift is None else int(raw_shift)
        updated = add_song(load_setlist(self.repo, setlist_slug), song_slug, shift=shift)
        save_setlist(self.repo, setlist_slug, updated)
        self._json({"setlist": setlist_slug, "song": song_slug})

    def _shutdown(self) -> None:
        """Stand down. No build queue in this unit, so nothing to refuse for."""
        self._json({"stopping": True, "pid": os.getpid()})
        # After the reply, and from another thread: `shutdown` blocks until
        # the serve loop exits, and this handler *is* that loop's current job.
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class WoodshedServer(ThreadingHTTPServer):
    """The server, which refuses to share its address.

    Lifted from rambass-live/src/rambass/console.py:742 (there named
    ``ConsoleServer``). ``allow_reuse_address`` is 1 on
    :class:`~http.server.HTTPServer` by default, and on Windows
    ``SO_REUSEADDR`` lets a *second* socket bind an address that is already
    being listened on. On a loopback dev server, silently rebinding to a
    leftover socket masks the fact that a previous instance never released
    it -- a bind that fails loudly is the whole point here.

    Safe to turn off for a listener: only accepted connections go to
    TIME_WAIT, so a server that has just been stopped does not block the
    next one.
    """

    allow_reuse_address = False


def make_server(repo: Repo, *, port: int = DEFAULT_PORT) -> WoodshedServer:
    """A configured server, not yet serving. Tests bind port 0."""
    handler = type("BoundWoodshedHandler", (WoodshedHandler,), {"repo": repo})
    return WoodshedServer(("127.0.0.1", port), handler)
