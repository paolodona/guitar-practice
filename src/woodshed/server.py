"""Woodshed's HTTP server: stdlib, local, thin over the Tier 0/1 modules.

Bound to 127.0.0.1 only -- this is a tool on Paolo's own machine, not a
service. Every mutation goes through the same functions the CLI uses
(``ledger.append``, ``manifest.load_song``/``save_song``), so nothing the
browser can do differs from what a terminal can, per CLAUDE.md's "the repo
is the database".

This unit (C2, Phase 0) owned the routes below marked (C2); progress is a
later phase and is deliberately not built here (see the plan's "Endpoint
ownership"). Render (Phase 2, Group I) WAS deliberately not built here at
first, for the same reason -- but was pulled forward into Phase 1.5
2026-09-06 (marked I3 below; see render.py's own module doc for why).
Phase 1's F1 added the setlist-scoped routes and, with them, `?setlist=`
and `readiness` on `/api/song/<slug>` -- practice.py did not exist during
C2's pass, so those were placeholders (0 / omitted) until now:

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
    GET  /api/stem/<slug>/<section>     -> the isolated guitar clip,
                                           RANGE-SERVED, isolating (blocking)
                                           on a miss (Phase 1.5, S3)         (S3)
    GET  /api/click/<slug>/<section>?speed=&mode=lead_in|full
                                        -> a generated click WAV, own gain (G2)
    GET  /api/render/<slug>/<section>?speed=&semitones=&source=mix|guitar
                                        -> the cache file, RANGE-SERVED;
                                           202 + {"rendering": true,
                                           "stage"?: "separating"|
                                           "rendering"} if not yet built
                                           (Phase 2, Group I -- pulled
                                           forward into this phase, see
                                           render.py's own module doc;
                                           ?source= is Group S2)      (I3, S2)
    POST /api/rep                       -> appends ONE ledger line        (C2)
    POST /api/section                   -> create/update/delete a span    (C2)
    POST /api/shift                     -> writes setlist.songs[].shift   (F1)
    POST /api/setlist                   -> create a new setlist       (post-Phase-1)
    POST /api/setlist/<slug>/songs      -> add a song to a setlist    (post-Phase-1)
    POST /api/song/upload               -> bind an uploaded audio file
                                            (multipart/form-data) as a
                                            new song                     (T1)
    POST /api/capture/start             -> arm the default loopback
                                            device, record on a
                                            background thread            (T2)
    GET  /api/capture/status            -> elapsed time, level, overflow
                                            seen, pyaudiowpatch availability
                                                                          (T2)
    POST /api/capture/stop              -> stop the running capture,
                                            wait for it to finish         (T2)
    GET  /api/capture/segments          -> pending segments from the most
                                            recent raw capture recording  (U2)
    GET  /api/capture/segment-audio/<i> -> one pending segment's audio,
                                            RANGE-SERVED, cut on the fly   (U2)
    GET  /api/capture/raw-audio         -> the current session's whole raw
                                            recording, RANGE-SERVED         (U3)
    GET  /api/capture/raw-peaks         -> waveform buckets for the current
                                            session's whole raw recording,
                                            computed on the fly, never
                                            cached                         (U3)
    POST /api/capture/bind              -> bind one pending segment to a
                                            song, existing or new          (U2)
    POST /api/capture/discard           -> drop one pending segment       (U2)
    POST /api/capture/adjust            -> move one pending segment's own
                                            start/end frame               (U2b)
    POST /api/capture/merge             -> merge two adjacent pending
                                            segments into one              (U2b)
    POST /api/capture/split             -> split one pending segment into
                                            two at a frame                 (U2b)
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

import importlib.util
import io
import json
import math
import mimetypes
import os
import re
import shutil
import tempfile
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
from woodshed import render as render_module
from woodshed.capture import (
    Segment,
    bind_segment_as_new_song,
    bind_segment_to_song,
    extract_segment,
)
from woodshed.capture_runner import CaptureRunner
from woodshed.capture_session import (
    STATUS_BOUND,
    STATUS_DISCARDED,
    adjust_boundary,
    current_session,
    merge_segments,
    resolve,
    split_segment,
)
from woodshed.cli import bind_song_file
from woodshed.click import render_click
from woodshed.clock import pre_roll_seconds
from woodshed.config import load_config
from woodshed.errors import WoodshedError
from woodshed.ladder import LadderConfig, LadderState, starting_speed
from woodshed.ledger import Rep
from woodshed.library import Repo, slugify
from woodshed.manifest import Section, Setlist, effective_pre_roll_beats, load_song, save_song
from woodshed.render_runner import RenderRunner
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


_MULTIPART_BOUNDARY_RE = re.compile(r'boundary="?([^";]+)"?')
_MULTIPART_DISPOSITION_RE = re.compile(r'name="([^"]*)"(?:; filename="([^"]*)")?')


def parse_multipart(
    content_type: str, body: bytes
) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """A minimal `multipart/form-data` parser -- stdlib only, no external
    dependency for something this small (`cgi.FieldStorage` is the usual
    answer but is gone as of Python 3.13, so hand-rolling it is the
    forward-compatible choice, not a shortcut). Returns `(fields, files)`:
    `fields` maps a form field's name to its decoded text value, `files`
    maps a file field's name to `(filename, raw bytes)`.

    Used by `POST /api/song/upload` (T1) -- the one route in this server
    that isn't JSON, since a browser's file input has no other shape to
    send.
    """
    match = _MULTIPART_BOUNDARY_RE.search(content_type or "")
    if not match:
        raise WoodshedError("multipart upload needs a Content-Type boundary")
    boundary = ("--" + match.group(1)).encode("utf-8")

    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for raw_part in body.split(boundary)[1:-1]:
        part = raw_part.strip(b"\r\n")
        if not part:
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        disposition = _MULTIPART_DISPOSITION_RE.search(
            header_blob.decode("utf-8", errors="replace")
        )
        if disposition is None:
            continue
        name, filename = disposition.group(1), disposition.group(2)
        if filename is not None:
            files[name] = (filename, content)
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, files


def _totals_json(totals) -> dict:
    """`ledger.Totals` as JSON -- three keys, named as the dataclass names
    them, so nothing downstream has to guess whether "reps" means passes."""
    return {"passes": totals.passes, "cleans": totals.cleans, "minutes": totals.minutes}


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

    def _raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _body(self) -> dict:
        raw = self._raw_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8")) or {}
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
            elif path.startswith("/api/stem/"):
                self._stem(path.removeprefix("/api/stem/"))
            elif path.startswith("/api/click/"):
                self._click(path.removeprefix("/api/click/"), parsed.query)
            elif path.startswith("/api/progress/"):
                self._progress(path.removeprefix("/api/progress/"), parsed.query)
            elif path.startswith("/api/render/"):
                self._render(path.removeprefix("/api/render/"), parsed.query)
            elif path == "/api/capture/segments":
                self._capture_segments()
            elif path.startswith("/api/capture/segment-audio/"):
                self._capture_segment_audio(path.removeprefix("/api/capture/segment-audio/"))
            elif path == "/api/capture/raw-audio":
                self._capture_raw_audio()
            elif path == "/api/capture/raw-peaks":
                self._capture_raw_peaks()
            elif path == "/api/capture/status":
                self._capture_status()
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

    @staticmethod
    def _section_starting_speed(reps: list[Rep], song, section: Section) -> float:
        """Where practice.js/song.js should start the speed dial for this
        section, next time it's opened -- found live 2026-09-06, Paolo:
        "not all songs or sections will be practiced from 50%".

        `full_song` (manifest.Section.full_song's own docstring: a rep
        counter, not a ladder target) resumes at whatever speed the most
        recent pass actually used (`ledger.last_speed`, clean or not) --
        there is no rung to have earned. Every other section resumes at
        the highest rung with `reps_to_advance` clean reps already banked
        (`ladder.starting_speed`, unchanged, just wired in here for the
        first time) -- exploring a speed without banking the clean reps to
        earn it must not move next session's starting point.
        """
        if section.full_song:
            last = ledger.last_speed(reps, song.slug, section.id)
            return song.practice.start_speed if last is None else last
        cfg = LadderConfig(
            start_speed=(
                section.start_speed if section.start_speed is not None
                else song.practice.start_speed
            ),
            ladder_step=(
                section.ladder_step if section.ladder_step is not None
                else song.practice.ladder_step
            ),
            reps_to_advance=(
                section.reps_to_advance if section.reps_to_advance is not None
                else song.practice.reps_to_advance
            ),
            target_speed=section.target_speed,
        )
        clean_counts = ledger.clean_by_speed(reps, song.slug, section.id)
        return starting_speed(clean_counts, cfg)

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

        from woodshed.separate import demucs_available

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
            # Phase 1.5, S3: screens/practice.js's "Guitar only" toggle
            # disables itself (with a message naming the fix) when this is
            # false, the same require_module-backed degrade doctor.py's own
            # demucs check already reports -- never a hard failure just
            # because the optional isolation dependency is missing.
            "demucs_available": demucs_available(),
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
                    "starting_speed_pct": self._section_starting_speed(reps, song, section),
                }
                for section in song.sections
            ],
        })

    def _progress(self, raw_slug: str, query: str = "") -> None:
        """`GET /api/progress/<slug>?weeks=` -- per-section series from the
        ledger (Phase 2, K2). Everything here is derived per request: there
        is no progress cache and there must not be one (CLAUDE.md invariant
        6 -- the moment a count lives in two files they can disagree and
        nothing says which is right).

        `?weeks=` is the readiness chart's window; `weeks=all` (or any
        value <= 0) spans back to the first rep in the ledger, floored at
        one week so an empty ledger still has an axis to draw.
        """
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return
        song = load_song(self.repo.song_dir(slug) / "song.yaml")
        reps = list(ledger.read(self.repo))
        result = practice.progress(song, reps, weeks=self._progress_weeks(query, reps))
        self._json(
            {
                "slug": result.slug,
                "title": result.title,
                "artist": result.artist,
                "weeks": result.weeks,
                "readiness_series": [
                    [day.isoformat(), value] for day, value in result.readiness_series
                ],
                "totals": _totals_json(result.totals),
                "week": _totals_json(result.week),
                "rungs_gained": result.rungs_gained,
                "sections": [
                    {
                        "id": row.id,
                        "name": row.name,
                        "target_speed": row.target_speed,
                        "reps": row.reps,
                        "cleans": row.cleans,
                        "minutes": row.minutes,
                        "best_sustained": row.best_sustained,
                        "reached": row.reached,
                        "last_speed": row.last_speed,
                        "last_practised": (
                            None if row.last_practised is None
                            else row.last_practised.isoformat()
                        ),
                        "days_since": row.days_since,
                        "cold": row.cold,
                        "series": [[day.isoformat(), speed] for day, speed in row.series],
                    }
                    for row in result.sections
                ],
            }
        )

    @staticmethod
    def _progress_weeks(query: str, reps: list) -> int:
        """The readiness window in weeks. An unparseable value falls back to
        the default rather than 400ing a read-only screen over a query
        string; "all" measures back to the oldest rep actually on disk,
        which is the only honest reading of "all"."""
        raw = parse_qs(query).get("weeks", [""])[0]
        try:
            weeks = int(float(raw))
        except ValueError:
            weeks = 0 if raw.strip().lower() == "all" else practice.PROGRESS_WEEKS
        if weeks > 0:
            return weeks
        oldest = min((r.t for r in reps), default=None)
        if oldest is None:
            return practice.PROGRESS_WEEKS
        days = (datetime.now(UTC) - datetime.fromisoformat(oldest.replace("Z", "+00:00"))).days
        return max(1, math.ceil(days / 7))

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
        try:
            from woodshed import peaks as peaks_module
        except ImportError:
            # woodshed.peaks (Tier 2) is not built yet in this pass. A 404
            # with a small explanatory body rather than a 500: the caller's
            # documented choice for a cache that is not built yet.
            self._error(404, f"peaks for {slug!r} are not built yet")
            return
        # Found live 2026-09-06: `song.js`/`practice.js` both fetch
        # `peaks_url` bare, with no `?level=` -- there is no zoom feature
        # yet for either to pick one from. A missing/blank param defaults
        # to peaks.py's own DEFAULT_LEVEL (its coarsest, whole-song-overview
        # resolution); an explicit `?level=` still selects a specific one.
        raw_level = parse_qs(query).get("level", [None])[0]
        level = int(raw_level) if raw_level else peaks_module.DEFAULT_LEVEL
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

    def _stem(self, rest: str) -> None:
        """`GET /api/stem/<slug>/<section>` -- the isolated guitar clip for
        *section* (Phase 1.5, S3), RANGE-SERVED like `_audio`. Unlike
        `/api/render`, this is NOT the offline (speed/pitch-baked) cache --
        `screens/practice.js`'s "Guitar only" toggle points the SAME
        real-time WASM engine at this clip instead of the mix, so the
        stretching still happens live; only the SOURCE differs. See
        `player.js`'s `computeSliceFrames` for the coordinate conversion
        this implies (the clip does not start at the recording's own t=0).

        Blocking, on purpose: `separate.isolate_guitar` runs Demucs
        synchronously the first time (genuinely slow on CPU -- doctor.py's
        S4 check already says so) and this request simply waits for it,
        same as any cache-miss file read; there is no 202/poll dance here
        the way `/api/render` needs, because there is no second
        (rubberband) stage after it -- isolation IS the whole job.
        `WoodshedError` (missing demucs, missing source audio) surfaces as
        400 through `do_GET`'s own handler, same as every other route.
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

        from woodshed.separate import isolate_guitar

        pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
        clip_path = isolate_guitar(self.repo, song, section, pre_roll_s=pre_roll_s)
        self._send_file(clip_path, "audio/flac")

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

    def _render(self, rest: str, query: str) -> None:
        """`GET /api/render/<slug>/<section>?speed=&semitones=&source=` --
        the cache file, RANGE-SERVED, or 202 `{"rendering": true}` if it is
        not built yet (Phase 2, Group I3 -- pulled forward into Phase 1.5;
        see `render.py`'s own module doc for why).

        A cache hit ALSO kicks off `render.plan_ahead` for the ladder rung
        above the requested speed, on the same `render_runner`,
        fire-and-forget: this response never waits on it, and a failure
        there is silent (there is no persisted ladder state to read yet --
        Phase 2, Group K1 -- so `state`/`cfg` are synthesised straight from
        this request; a real ladder position, once K1 exists, can only do
        better than this guess, never worse). `plan_ahead` always pre-
        renders the MIX's own next rung regardless of `source` -- its own
        signature (fixed in the plan's module map) carries no `source`
        parameter; look-ahead for a guitar-only practice session is not
        this pass's scope.

        `?source=guitar` (Group S2, default `mix`) isolates the guitar
        first -- see `render.render_section`'s own docstring. The 202 body
        gains a coarse `"stage": "separating" | "rendering"` in that case
        -- two HONEST stages (Demucs has no fine-grained progress readout
        to report; inventing a smooth percentage is the exact failure mode
        CLAUDE.md's "the tool never judges... inventing a measurement it
        cannot make" already names for a different measurement), derived
        from whether the isolated stem is cached yet, not from progress
        polling inside the render itself.
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
            speed_pct = float(params.get("speed", ["100"])[0])
        except ValueError:
            speed_pct = 100.0
        try:
            semitones = int(float(params.get("semitones", ["0"])[0]))
        except ValueError:
            semitones = 0
        source = params.get("source", ["mix"])[0]
        if source not in ("mix", "guitar"):
            self._error(400, f"unknown source {source!r} -- expected 'mix' or 'guitar'")
            return

        crossfade_ms = song.practice.loop_crossfade_ms
        pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
        fp = render_module.span_fingerprint(
            song, section, pre_roll_s=pre_roll_s, crossfade_ms=crossfade_ms
        )
        dest = render_module.cache_path(
            self.repo, slug, section.id, speed_pct, semitones, fp, source=source
        )

        if dest.is_file():
            self._send_file(dest, "audio/flac")
            cfg = LadderConfig(
                start_speed=(
                    section.start_speed if section.start_speed is not None
                    else song.practice.start_speed
                ),
                ladder_step=(
                    section.ladder_step if section.ladder_step is not None
                    else song.practice.ladder_step
                ),
                reps_to_advance=(
                    section.reps_to_advance if section.reps_to_advance is not None
                    else song.practice.reps_to_advance
                ),
                target_speed=section.target_speed,
            )
            state = LadderState(speed=speed_pct, clean_at_speed=0)
            self.render_runner.ensure_started(
                f"ahead:{dest}",
                lambda: render_module.plan_ahead(self.repo, song, section, state, cfg, semitones),
            )
            return

        key = str(dest)
        self.render_runner.ensure_started(
            key,
            lambda: render_module.render_section(
                self.repo, song, section, speed_pct, semitones,
                crossfade_ms=crossfade_ms, pre_roll_s=pre_roll_s, source=source,
            ),
        )
        error = self.render_runner.error(key)
        if error:
            self._error(500, f"render failed: {error}")
            return

        body = {"rendering": True}
        if source == "guitar":
            from woodshed.separate import stem_cache_path, stem_fingerprint

            stem_fp = stem_fingerprint(song, section, pre_roll_s=pre_roll_s)
            stem_path = stem_cache_path(self.repo, slug, section.id, stem_fp)
            body["stage"] = "rendering" if stem_path.is_file() else "separating"
        self._json(body, status=202)

    # ── POST ────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802 -- stdlib naming
        path = urlsplit(self.path).path
        if not self._security_check():
            return
        try:
            # Not JSON -- a browser's file input has no other shape to send
            # -- so this one route reads the raw body itself, BEFORE the
            # generic `self._body()` JSON read below would consume it.
            if path == "/api/song/upload":
                self._post_song_upload(self.headers.get("Content-Type", ""), self._raw_body())
                return
            body = self._body()
            if path == "/api/rep":
                self._post_rep(body)
            elif path == "/api/section":
                self._post_section(body)
            elif path == "/api/song/delete":
                self._post_song_delete(body)
            elif path == "/api/shift":
                self._post_shift(body)
            elif path == "/api/setlist":
                self._post_setlist(body)
            elif path.startswith("/api/setlist/") and path.endswith("/songs"):
                setlist_slug = path.removeprefix("/api/setlist/").removesuffix("/songs")
                self._post_setlist_songs(setlist_slug, body)
            elif path == "/api/capture/bind":
                self._post_capture_bind(body)
            elif path == "/api/capture/discard":
                self._post_capture_discard(body)
            elif path == "/api/capture/adjust":
                self._post_capture_adjust(body)
            elif path == "/api/capture/merge":
                self._post_capture_merge(body)
            elif path == "/api/capture/split":
                self._post_capture_split(body)
            elif path == "/api/capture/start":
                self._post_capture_start(body)
            elif path == "/api/capture/stop":
                self._post_capture_stop(body)
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
                "start_speed": body.get("start_speed"),
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

    def _post_song_delete(self, body: dict) -> None:
        """`POST /api/song/delete`, body `{song: <slug>}` -- irreversibly
        removes a song: its whole `songs/<slug>/` tree (song.yaml, the
        bound audio, and its nested `cache/` -- library.Repo.cache_dir is
        `song_dir(slug)/"cache"`, so one `rmtree` clears all three), and
        the slug from every setlist that names it (`setlist.songs[]` --
        left in place, a deleted song would otherwise linger forever as a
        `needs_audio` placeholder no one asked for and nothing can rebind,
        since `needs_audio` is derived purely from song.yaml's absence,
        not a stored flag).

        Deliberately does NOT touch `practice/reps.jsonl`: CLAUDE.md
        invariant 5, the ledger is append-only and the only irreplaceable
        file. The song's past reps stay on disk forever, keyed to a slug
        that no longer resolves to a song -- the same shape a retraction
        already uses (the ledger never rewrites, only accretes), and
        exactly why every ledger reader takes reps as raw input rather
        than assuming the song they name still exists.

        No confirmation step here -- this is a route, not a UI. The actual
        friction against a misclick belongs to whoever calls this
        (screens/song.js's own confirm() dialog); the endpoint's job is
        only to refuse an unknown slug, not to have a change of heart.
        """
        raw_slug = str(body.get("song", ""))
        slug = self._resolve_slug(raw_slug)
        if slug is None:
            self._error(404, f"no such song: {raw_slug!r}")
            return

        for setlist_slug in self.repo.list_setlists():
            setlist = load_setlist(self.repo, setlist_slug)
            if any(e.slug == slug for e in setlist.songs):
                setlist.songs = [e for e in setlist.songs if e.slug != slug]
                save_setlist(self.repo, setlist_slug, setlist)

        shutil.rmtree(self.repo.song_dir(slug))
        self._json({"deleted": slug})

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

    # ── add a song for real (Phase 1.5, Group T, T1) ────────────────────────

    def _post_song_upload(self, content_type: str, raw: bytes) -> None:
        """`POST /api/song/upload` (`multipart/form-data`): a real file
        picker, reachable from a browser for the first time -- replaces
        `dashboard.js`'s old "Add song" text field, which only ever
        produced a `needs_audio` placeholder row and never actually bound
        a file. Fields: `title`, `artist?`, `album?`, `tuning`, `slug?`,
        plus one `file` part. Calls the same `bind_song_file` `cmd_add`
        uses (CLAUDE.md's "one action table" instinct extended to "one
        binding function") -- not a second copy that can drift.

        The uploaded bytes are written to a system-temp file first (never
        under the repo -- this temp file isn't one of CLAUDE.md's four
        write categories, it's a staging area `bind_song_file` copies
        FROM), with the browser's own filename kept only as `dest_filename`
        -- and even then run through `Path(...).name` first, so a
        maliciously crafted filename can't smuggle a directory component
        into `songs/<slug>/audio/`.
        """
        fields, files = parse_multipart(content_type, raw)
        if "file" not in files:
            raise WoodshedError("upload needs a 'file' part")
        filename, content = files["file"]
        title = fields.get("title", "").strip()
        if not title:
            raise WoodshedError("upload needs a 'title'")
        tuning = fields.get("tuning", "").strip()
        if not tuning:
            raise WoodshedError("upload needs a 'tuning'")
        slug = fields.get("slug", "").strip() or None
        dest_filename = Path(filename).name or "upload"

        suffix = Path(dest_filename).suffix or ".wav"
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_path = Path(handle.name)
        try:
            handle.write(content)
            handle.close()
            song = bind_song_file(
                self.repo, tmp_path,
                title=title, artist=fields.get("artist", ""),
                album=fields.get("album") or None, tuning=tuning, slug=slug,
                dest_filename=dest_filename,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        self._json({"slug": song.slug})

    # ── live capture, start/status/stop (Phase 1.5, Group T, T2) ───────────

    def _post_capture_start(self, body: dict) -> None:
        """`POST /api/capture/start` -- arm the default loopback device and
        start recording on a background thread (`capture_runner.py`'s
        `CaptureRunner`, one shared instance per server -- see
        `make_server`). Refuses (400, via the usual `WoodshedError` ->
        `_error` path) if one is already running, or if `pyaudiowpatch`
        itself is missing -- `default_device()`'s own `require_module`
        names the installer."""
        self.capture_runner.start(self.repo)
        self._json({"started": True})

    def _capture_status(self) -> None:
        """`GET /api/capture/status` -- elapsed time, current level,
        whether an overflow has been seen so far, and (once one finishes)
        how many segments it produced -- derived from the runner's own
        in-memory state, not disk (see `capture_runner.py`'s module doc
        for why a live-in-progress recording isn't one of CLAUDE.md's four
        write categories). `available` is a cheap, non-importing check
        (`importlib.util.find_spec`, the same technique `doctor.py`'s own
        loopback check already uses) -- `screens/capture.js` reads it
        BEFORE ever offering the live-arm UI, rather than only discovering
        `pyaudiowpatch` is missing on the first failed `start`."""
        status = self.capture_runner.status()
        status["available"] = importlib.util.find_spec("pyaudiowpatch") is not None
        self._json(status)

    def _post_capture_stop(self, body: dict) -> None:
        """`POST /api/capture/stop` -- signal the running capture to stop
        and wait for its thread to actually finish, so the response can
        honestly say the resulting segments are already visible via
        `GET /api/capture/segments`. Refuses (400) if nothing is
        running."""
        self._json(self.capture_runner.stop())

    # ── capture-first: split now, name later (Phase 1.5, Group U) ──────────

    def _pending_capture_entry(self, index: int):
        """The current session's pending entry at *index*, or None -- the
        one lookup every capture/segment endpoint below needs first."""
        session = current_session(self.repo)
        if session is None:
            return None, None
        entry = next((e for e in session.entries if e.index == index), None)
        if entry is None or entry.status != "pending":
            return session, None
        return session, entry

    def _capture_segments(self) -> None:
        """The still-unresolved segments from the most recent raw capture
        recording -- `[]` once every segment from it is resolved (bound or
        discarded), same as `current_session` returning `None` then.

        `start_frame`/`end_frame`/`sample_rate` (added for U3) are what
        `screens/capture.js`'s segment-review UI needs to position each
        segment against the whole-pass waveform strip (`/api/capture/
        raw-peaks`, below) and to build the frame-valued bodies `POST
        /api/capture/adjust|merge|split` already require -- additive
        fields, nothing existing that only read `index`/`duration_s`/
        `overflowed` is affected."""
        session = current_session(self.repo)
        entries = [] if session is None else session.pending
        self._json([
            {
                "index": e.index,
                "duration_s": e.duration_s,
                "overflowed": e.overflowed,
                "start_frame": e.start_frame,
                "end_frame": e.end_frame,
                "sample_rate": e.sample_rate,
            }
            for e in entries
        ])

    def _capture_raw_audio(self) -> None:
        """`GET /api/capture/raw-audio` (U3) -- the CURRENT session's whole
        raw recording, RANGE-SERVED (`_send_file`, same as `_audio`) so
        `player.js`'s `RealtimeEngine` (`loop: false`, R1) can fetch/decode
        it and `seek()` (P1) around it for the whole-pass scrub strip --
        the same "audition, no rep" mechanism already built, pointed at the
        raw pass instead of a bound song's `/api/audio/<slug>`. 404 when
        there is no current session (nothing captured yet, or every
        segment from the last one has already resolved)."""
        session = current_session(self.repo)
        if session is None:
            self._error(404, "no current capture session")
            return
        self._send_file(session.raw_path, "audio/wav")

    def _capture_raw_peaks(self) -> None:
        """`GET /api/capture/raw-peaks` (U3) -- waveform buckets for the
        CURRENT session's whole raw recording, computed on the fly from
        the WAV already on disk (never cached under `cache/`: this is
        `capture/`, ephemeral by CLAUDE.md's own lifecycle rule, gone the
        moment every segment resolves) via `woodshed.peaks` (Tier 2, numpy
        only -- same lazy, defensive import `_peaks` already uses). Shape
        mirrors a bound song's peaks payload plus `duration_s`/
        `sample_rate`, which a bound song's own `song.yaml` already
        supplies some other way but a raw capture session has nowhere else
        to read from. 404 when there is no current session."""
        session = current_session(self.repo)
        if session is None:
            self._error(404, "no current capture session")
            return
        try:
            from woodshed import peaks as peaks_module
        except ImportError:
            self._error(404, "peaks are not built yet")
            return
        samples, sample_rate = self._read_wav_mono(session.raw_path)
        buckets = peaks_module.compute_peaks(samples, peaks_module.DEFAULT_LEVEL)
        self._json({
            "level": peaks_module.DEFAULT_LEVEL,
            "peaks": [list(pair) for pair in buckets],
            "duration_s": len(samples) / sample_rate if sample_rate else 0.0,
            "sample_rate": sample_rate,
        })

    @staticmethod
    def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
        """The inverse of `_encode_wav_mono` below -- a mono 16-bit PCM WAV
        (what `capture.py`'s `_write_wav_mono_16bit` always writes for a
        raw capture recording) back to float32 samples in [-1, 1] plus its
        sample rate, stdlib + numpy only."""
        with wave_module.open(str(path), "rb") as reader:
            sample_rate = reader.getframerate()
            raw = reader.readframes(reader.getnframes())
        pcm16 = np.frombuffer(raw, dtype="<i2")
        return pcm16.astype(np.float32) / 32768.0, sample_rate

    def _capture_segment_audio(self, raw_index: str) -> None:
        """Range-served preview audio for one still-pending segment, cut
        from the raw recording on the fly into a system-temp file (never
        under the repo -- this is a throwaway preview extract, not one of
        CLAUDE.md's four write categories) and deleted again once served."""
        try:
            index = int(raw_index)
        except ValueError:
            self._error(404, f"no such capture segment: {raw_index!r}")
            return
        session, entry = self._pending_capture_entry(index)
        if entry is None:
            self._error(404, f"no such pending capture segment: {index}")
            return
        segment = Segment(
            start_frame=entry.start_frame, end_frame=entry.end_frame,
            sample_rate=entry.sample_rate, overflowed=entry.overflowed,
        )
        handle = tempfile.NamedTemporaryFile(suffix=".flac", delete=False)
        handle.close()
        tmp_path = Path(handle.name)
        try:
            extract_segment(session.raw_path, segment, tmp_path)
            self._send_file(tmp_path, "audio/flac")
        finally:
            tmp_path.unlink(missing_ok=True)

    def _post_capture_bind(self, body: dict) -> None:
        """Bind one pending segment to a song. Body: `{index, mode:
        "existing"|"new", slug?, title?, artist?, tuning, setlist?}`.

        `"existing"` calls `bind_segment_to_song` -- also how T2's own
        single-song live capture finishes, the same code path, not a
        second one. `"new"` calls `bind_segment_as_new_song` and, when
        `setlist` is given, adds the new song to it (mirrors
        `POST /api/setlist/<slug>/songs`'s existing add-by-slug
        behaviour). Either way, `resolve()` removes the segment from the
        pending list and deletes the raw file once none remain.
        """
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            raise WoodshedError("bind needs an integer 'index'") from None
        mode = str(body.get("mode", ""))
        if mode not in ("existing", "new"):
            raise WoodshedError("'mode' must be 'existing' or 'new'")
        tuning = str(body.get("tuning", "")).strip()
        if not tuning:
            raise WoodshedError("bind needs a 'tuning'")

        session, entry = self._pending_capture_entry(index)
        if entry is None:
            raise WoodshedError(f"no such pending capture segment: {index}")
        segment = Segment(
            start_frame=entry.start_frame, end_frame=entry.end_frame,
            sample_rate=entry.sample_rate, overflowed=entry.overflowed,
        )

        if mode == "existing":
            slug = str(body.get("slug", "")).strip()
            if not slug:
                raise WoodshedError("mode 'existing' needs a 'slug'")
            bind_segment_to_song(self.repo, slug, session.raw_path, segment, tuning=tuning)
        else:
            title = str(body.get("title", "")).strip()
            if not title:
                raise WoodshedError("mode 'new' needs a 'title'")
            artist = str(body.get("artist", "")).strip()
            slug = bind_segment_as_new_song(
                self.repo, session.raw_path, segment,
                title=title, artist=artist, tuning=tuning,
            )
            setlist_slug = str(body.get("setlist") or "").strip()
            if setlist_slug:
                updated = add_song(load_setlist(self.repo, setlist_slug), slug)
                save_setlist(self.repo, setlist_slug, updated)

        resolve(self.repo, index, STATUS_BOUND)
        self._json({"index": index, "slug": slug})

    def _post_capture_discard(self, body: dict) -> None:
        """Drop a pending segment (a false positive from noise, say)
        without creating anything. Same cleanup-when-none-remain rule as
        binding."""
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            raise WoodshedError("discard needs an integer 'index'") from None
        _session, entry = self._pending_capture_entry(index)
        if entry is None:
            raise WoodshedError(f"no such pending capture segment: {index}")
        resolve(self.repo, index, STATUS_DISCARDED)
        self._json({"index": index, "discarded": True})

    @staticmethod
    def _entry_json(entry) -> dict:
        return {
            "index": entry.index,
            "start_frame": entry.start_frame,
            "end_frame": entry.end_frame,
            "duration_s": entry.duration_s,
            "overflowed": entry.overflowed,
        }

    def _post_capture_adjust(self, body: dict) -> None:
        """Move one pending segment's own start/end frame (U2b). Body:
        `{index, start_frame?, end_frame?}` -- either bound may be omitted
        to leave it where it is. `adjust_boundary` itself refuses an
        inverted or overlapping result; this is routing only."""
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            raise WoodshedError("adjust needs an integer 'index'") from None
        raw_start = body.get("start_frame")
        raw_end = body.get("end_frame")
        start_frame = None if raw_start is None else int(raw_start)
        end_frame = None if raw_end is None else int(raw_end)
        entry = adjust_boundary(self.repo, index, start_frame=start_frame, end_frame=end_frame)
        self._json(self._entry_json(entry))

    def _post_capture_merge(self, body: dict) -> None:
        """Merge two adjacent pending segments into one (U2b). Body:
        `{first_index, second_index}` -- `merge_segments` itself refuses a
        non-adjacent pair; this is routing only."""
        try:
            first_index = int(body.get("first_index"))
            second_index = int(body.get("second_index"))
        except (TypeError, ValueError):
            raise WoodshedError(
                "merge needs integer 'first_index' and 'second_index'"
            ) from None
        entry = merge_segments(self.repo, first_index, second_index)
        self._json(self._entry_json(entry))

    def _post_capture_split(self, body: dict) -> None:
        """Split one pending segment into two at a frame (U2b). Body:
        `{index, at_frame}` -- `split_segment` itself refuses a boundary at
        or past either end; this is routing only."""
        try:
            index = int(body.get("index"))
            at_frame = int(body.get("at_frame"))
        except (TypeError, ValueError):
            raise WoodshedError("split needs an integer 'index' and 'at_frame'") from None
        first, second = split_segment(self.repo, index, at_frame)
        self._json({"first": self._entry_json(first), "second": self._entry_json(second)})

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
    """A configured server, not yet serving. Tests bind port 0.

    `capture_runner` is ONE `CaptureRunner` shared across every request
    (a class attribute, same binding trick as `repo`) -- there is exactly
    one background capture thread per running server, matching
    `CaptureRunner`'s own "one capture at a time" contract.
    """
    handler = type(
        "BoundWoodshedHandler", (WoodshedHandler,),
        {"repo": repo, "capture_runner": CaptureRunner(), "render_runner": RenderRunner()},
    )
    return WoodshedServer(("127.0.0.1", port), handler)
