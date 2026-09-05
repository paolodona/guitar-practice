"""Woodshed's HTTP server: stdlib, local, thin over the Tier 0/1 modules.

Bound to 127.0.0.1 only -- this is a tool on Paolo's own machine, not a
service. Every mutation goes through the same functions the CLI uses
(``ledger.append``, ``manifest.load_song``/``save_song``), so nothing the
browser can do differs from what a terminal can, per CLAUDE.md's "the repo
is the database".

This unit (C2) owns exactly these routes -- render/progress/setlist
endpoints are later phases and are deliberately not built here (see the
plan's "Endpoint ownership"):

    GET  /                    -> web/index.html
    GET  /web/*               -> static files under web/
    GET  /api/config          -> woodshed.config.load_config(repo), as JSON
    GET  /api/song/<slug>     -> song page payload (song, sections with
                                 lanes + ancestors, tempo, a peaks url, shift)
    GET  /api/peaks/<slug>    -> cached peaks json, or a 404 saying not built
    GET  /api/audio/<slug>    -> the source file, RANGE-SERVED
    POST /api/rep             -> appends ONE ledger line
    POST /api/section         -> create/update/delete a span in song.yaml
    POST /api/shutdown        -> stops the server

Two things this unit deliberately omits, both because the modules they need
do not exist yet in this pass (see the C2 report for the full reasoning):
readiness is left out of the /api/song payload entirely (song_readiness /
practice.py is not built yet), and /api/peaks/<slug> answers 404 with a
small body when woodshed.peaks cannot be imported, rather than failing to
import at server start.

``parse_byte_range``, the ``_send``/``_json``/``_error``/``_body``/
``_send_file`` plumbing, ``ConsoleServer``'s ``allow_reuse_address = False``
and ``make_server`` are lifted from
``rambass-live/src/rambass/console.py`` (verbatim where the logic is
project-agnostic), with ``ProjectError`` renamed to
:class:`woodshed.errors.WoodshedError` and the console's per-song rebuild
lock (not needed here -- this unit renders nothing) dropped.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from woodshed import ledger, sections
from woodshed.config import load_config
from woodshed.errors import WoodshedError
from woodshed.ledger import Rep
from woodshed.library import Repo
from woodshed.manifest import Section, load_song, save_song

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
            elif path.startswith("/api/song/"):
                self._song(path.removeprefix("/api/song/"))
            elif path.startswith("/api/peaks/"):
                self._peaks(path.removeprefix("/api/peaks/"), parsed.query)
            elif path.startswith("/api/audio/"):
                self._audio(path.removeprefix("/api/audio/"))
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

    def _song(self, raw_slug: str) -> None:
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        song = load_song(self.repo.song_dir(slug) / "song.yaml")
        # manifest.Section satisfies sections.Span directly (it carries a
        # `duration` property) -- no adapter needed.
        spans = song.sections
        lanes = sections.assign_lanes(spans)
        self._json({
            "slug": song.slug,
            "title": song.title,
            "artist": song.artist,
            "album": song.album,
            "recording": song.recording.model_dump(mode="json"),
            "tempo": song.tempo.model_dump(mode="json"),
            "practice": song.practice.model_dump(mode="json"),
            # No setlist context reaches this endpoint (that is a later
            # unit's `?setlist=` query param -- see CLAUDE.md's "Transpose
            # is per song" invariant), so there is nothing to derive a
            # shift FROM yet. 0 is "unshifted", the honest default until
            # a setlist is plumbed in; documented in the C2 report.
            "shift": 0,
            "peaks_url": f"/api/peaks/{slug}",
            # readiness is deliberately omitted: song_readiness lives in
            # practice.py, which does not exist yet in this pass -- see
            # the module docstring and the C2 report.
            "sections": [
                {
                    **section.model_dump(mode="json"),
                    "lane": lanes[section.id],
                    "ancestors": [a.id for a in sections.ancestors(spans, section.id)],
                }
                for section in song.sections
            ],
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
