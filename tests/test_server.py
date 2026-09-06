"""Tests for woodshed.server: the HTTP server (unit C2).

Test contract (from the C2 unit prompt):
- the range-serving tests lifted from rambass-live/src/rambass/console.py
  (parse_byte_range, 14 parametrised cases -- see the citation on the test
  itself for why 14 and not the plan's remembered "15") all pass
- POST /api/rep appends exactly one line and the ledger file's line count
  grows by exactly one
- POST /api/section with a span that exactly duplicates an existing span's
  (start_s, end_s) is rejected with a 400 whose JSON body has an "error" key
- a traversal attempt on each of /web/*, /api/song/<slug> and
  /api/audio/<slug> returns 404, never a file read outside the intended
  directory
- a POST with a foreign Host header is refused
- a POST with a foreign Origin header is refused
- a POST with NO Origin header at all is allowed
- test_server_writes_nothing_else: hashes every file in a tmp repo before
  and after exercising every mutating endpoint, and asserts the only paths
  whose hash changed are song.yaml, the ledger file, or files under cache/
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.request
import wave as wave_module
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

import woodshed.capture_runner as capture_runner_module
from woodshed.capture import Device, Segment
from woodshed.capture_session import start_session
from woodshed.errors import WoodshedError
from woodshed.ledger import read as read_ledger
from woodshed.library import Repo
from woodshed.manifest import (
    Recording,
    Section,
    Setlist,
    SetlistEntry,
    Song,
    Tempo,
    hash_file,
    load_song,
    save_setlist,
    save_song,
)
from woodshed.server import WoodshedServer, make_server, parse_byte_range, parse_multipart

POLL = 0.01  # server.serve_forever's poll interval; small so shutdown is fast in tests

# 12 bytes, chosen (like the sibling's fixture) so the range arithmetic below
# has a size that isn't a round number.
AUDIO_BYTES = b"WOODSHEDAUD1"
assert len(AUDIO_BYTES) == 12


def _wav_bytes(*, seconds: float = 1.0, rate: int = 44100) -> bytes:
    """A real, playable mono WAV -- for POST /api/song/upload's success
    path, which (unlike `_make_song`'s hand-built `Song`) reads its
    duration back out through `wave.open` via `bind_song_file`."""
    frames = int(seconds * rate)
    buffer = io.BytesIO()
    with wave_module.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


# ── building a real repo on disk ─────────────────────────────────────────


def _make_song(repo: Repo, slug: str) -> Song:
    """A song with one audio file and two nested sections, saved for real."""
    song_dir = repo.song_dir(slug)
    audio_dir = song_dir / "audio"
    audio_dir.mkdir(parents=True)
    audio_path = audio_dir / "track.wav"
    audio_path.write_bytes(AUDIO_BYTES)

    song = Song(
        slug=slug,
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        recording=Recording(
            file="audio/track.wav",
            sha256=hash_file(audio_path),
            duration_s=200.0,
            tuning="E standard",
        ),
        tempo=Tempo(bpm=120.0, source="manual", grid_offset_s=0.0, time_signature="4/4"),
        sections=[
            Section(
                id="solo-full", name="Solo (full)", start_s=0.0, end_s=60.0,
                snapped="free", target_speed=100.0,
            ),
            Section(
                id="solo-part", name="Solo (tapping)", start_s=10.0, end_s=30.0,
                snapped="free", target_speed=80.0,
            ),
        ],
    )
    save_song(song, song_dir / "song.yaml")
    return song


@pytest.fixture(autouse=True)
def _no_auto_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """`bind_song_file`/`bind_segment_to_song`/`bind_segment_as_new_song`
    now call `cli.analyze_after_bind` automatically (found live
    2026-09-06) -- a no-op here, same reasoning as `test_capture.py`'s own
    identically-named fixture (most of the capture tests below use fake
    placeholder audio bytes a real ffmpeg decode would reject, and the
    upload tests don't need real peaks/tempo coverage here -- that lives
    in `test_cli.py`'s own dedicated `analyze_after_bind` tests)."""
    monkeypatch.setattr("woodshed.cli.analyze_after_bind", lambda *a, **k: None)


@pytest.fixture
def real_repo(tmp_path: Path) -> Repo:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    repo.setlists_dir.mkdir()
    repo.practice_dir.mkdir()
    repo.config_path.write_text("", encoding="utf-8")
    repo.web_dir.mkdir()
    (repo.web_dir / "index.html").write_text(
        "<!doctype html><title>Woodshed</title><body>hello</body>", encoding="utf-8"
    )
    (repo.web_dir / "app.js").write_text("console.log('hi');", encoding="utf-8")
    _make_song(repo, "test-song")
    return repo


def _serve(server: WoodshedServer) -> None:
    threading.Thread(target=lambda: server.serve_forever(POLL), daemon=True).start()


@pytest.fixture
def served(real_repo: Repo):
    server = make_server(real_repo, port=0)
    _serve(server)
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}", real_repo, "test-song"
    server.shutdown()
    server.server_close()


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path) as response:
        return response.status, response.read()


def _get_json(base: str, path: str):
    status, body = _get(base, path)
    return status, json.loads(body.decode("utf-8"))


def _post(base: str, path: str, payload: dict, headers: dict | None = None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _multipart_post(
    base: str, path: str, fields: dict[str, str], *, file_field: str,
    filename: str, content: bytes,
) -> tuple[int, dict]:
    """Build a real multipart/form-data body by hand (stdlib has no client-
    side helper for this) and POST it -- the same shape a browser's
    `FormData`/`fetch` sends for a file input."""
    boundary = "----woodshedtestboundary"
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        .encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    request = urllib.request.Request(
        base + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _post_raw(base: str, path: str, body: bytes, headers: dict) -> tuple[int, bytes]:
    """A POST with a literal, possibly-wrong Host header -- urllib always
    derives Host from the URL, so a raw http.client connection is the only
    way to send one that doesn't match."""
    parsed = urlsplit(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port)
    try:
        conn.putrequest("POST", path, skip_host=True)
        full_headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        full_headers.update(headers)
        for key, value in full_headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


# ── parse_byte_range, lifted verbatim from
#    rambass-live/tests/test_console.py:1173-1198 (14 parametrised cases;
#    the plan text names 15, but the sibling file itself -- read per this
#    unit's own instructions -- has 14) ────────────────────────────────────


@pytest.mark.parametrize(
    "header,expected",
    [
        ("bytes=0-9", (0, 10)),
        ("bytes=4-7", (4, 8)),
        ("bytes=6-", (6, 12)),
        ("bytes=-3", (9, 12)),
        ("bytes=-99", (0, 12)),  # a trailer longer than the file is the file
        ("bytes=11-99", (11, 12)),  # clipped to the end, not refused
        ("bytes=12-", ()),  # well formed, past the end: 416
        ("bytes=5-2", None),  # nonsense: ignore it, send the whole file
        ("bytes=x-y", None),
        ("bytes=", None),
        ("bytes=0-4,8-9", None),  # multipart: not what a player sends
        ("items=0-1", None),
        ("bananas", None),
        ("", None),
    ],
)
def test_the_range_parser_only_answers_what_a_player_asks(header, expected):
    assert parse_byte_range(header, 12) == expected


# ── the server object itself ─────────────────────────────────────────────


def test_allow_reuse_address_is_false():
    """A bind to an address still held by a leftover instance must fail
    loudly, not silently rebind -- see the class docstring."""
    assert WoodshedServer.allow_reuse_address is False


# ── GET / and GET /web/* ──────────────────────────────────────────────────


def test_the_index_page_is_served(served):
    base, _, _ = served
    status, body = _get(base, "/")
    assert status == 200
    assert b"hello" in body


def test_a_static_web_file_is_served(served):
    base, _, _ = served
    status, body = _get(base, "/web/app.js")
    assert status == 200
    assert b"console.log" in body


def test_web_static_traversal_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/web/..%2f..%2f..%2fconfig.yaml")
    assert caught.value.code == 404


def test_a_missing_web_file_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/web/no-such-file.js")
    assert caught.value.code == 404


# ── GET /api/config ────────────────────────────────────────────────────────


def test_api_config_returns_defaults_when_config_yaml_is_empty(served):
    base, _, _ = served
    status, data = _get_json(base, "/api/config")
    assert status == 200
    assert data["defaults"]["start_speed"] == 50.0
    assert data["render"]["engine"] == "rubberband"


# ── GET /api/song/<slug> ────────────────────────────────────────────────────


def test_api_song_payload_has_lanes_and_ancestors(served):
    base, _, slug = served
    status, data = _get_json(base, f"/api/song/{slug}")
    assert status == 200
    assert data["slug"] == slug
    assert data["tempo"]["bpm"] == 120.0
    assert data["peaks_url"] == f"/api/peaks/{slug}"
    assert data["shift"] == 0  # no ?setlist= on this request -- unshifted
    # readiness is real as of F1 (practice.py); no reps yet, so the two
    # sections' shared time is entirely unreached.
    assert data["readiness"]["ratio"] == 0.0
    assert data["readiness"]["intervals"]

    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["solo-full"]["lane"] == 0
    assert by_id["solo-part"]["lane"] == 1  # nested inside solo-full
    assert by_id["solo-part"]["ancestors"] == ["solo-full"]
    assert by_id["solo-full"]["ancestors"] == []


def test_api_song_starting_speed_pct_is_the_earned_ladder_rung(served):
    """Found live 2026-09-06, Paolo: "not all songs or sections will be
    practiced from 50%". An ordinary section resumes at the highest rung
    with reps_to_advance (default 3) clean reps already banked --
    ladder.starting_speed, wired in via server.py's own
    `_section_starting_speed`."""
    base, _, slug = served
    payload = {
        "song": slug, "section": "solo-full", "semitones": 0,
        "pass": True, "clean": True, "loop_s": 12.3, "setlist": None, "source": "ui",
    }
    for _ in range(3):
        _post(base, "/api/rep", {**payload, "speed": 55.0})

    _, data = _get_json(base, f"/api/song/{slug}")
    by_id = {s["id"]: s for s in data["sections"]}
    # song.practice.start_speed=50, ladder_step=5 -- 3 clean reps at 55 earns
    # the rung above it, 60, not 55 itself (re-earning would be punitive).
    assert by_id["solo-full"]["starting_speed_pct"] == 60.0
    # solo-part has no reps at all -- falls back to the song's start_speed.
    assert by_id["solo-part"]["starting_speed_pct"] == 50.0


def test_api_song_starting_speed_pct_honours_a_per_section_start_speed_override(served):
    """Found live 2026-09-06, Paolo: a per-section START override (same shape
    as ladder_step/reps_to_advance) -- "not all songs OR SECTIONS will be
    practiced from 50%" applies to the starting rung itself, not just the
    step/reps-to-advance around it. Before this, `_section_starting_speed`
    always fed `song.practice.start_speed` into LadderConfig regardless of
    what an individual section declared."""
    base, repo, slug = served
    song_path = repo.song_dir(slug) / "song.yaml"
    song = load_song(song_path)
    solo_part = next(s for s in song.sections if s.id == "solo-part")
    solo_part.start_speed = 70.0
    save_song(song, song_path)

    # Never practiced -- falls back to ITS OWN start_speed override (70), not
    # the song's flat default (50), and not the earned-rung math either
    # (there is nothing earned yet).
    _, data = _get_json(base, f"/api/song/{slug}")
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["solo-part"]["starting_speed_pct"] == 70.0
    # solo-full still falls back to the song default, unaffected.
    assert by_id["solo-full"]["starting_speed_pct"] == 50.0


def test_api_song_starting_speed_pct_is_last_practiced_for_full_song(served):
    """A `full_song` section is a rep counter, not a ladder target
    (manifest.Section.full_song's own docstring) -- it resumes at whatever
    speed the last pass actually used, clean or not, never an earned rung."""
    base, repo, slug = served
    song_path = repo.song_dir(slug) / "song.yaml"
    song = load_song(song_path)
    song.sections.append(
        Section(
            id="whole-song", name="Whole song", start_s=0.0, end_s=200.0,
            snapped="free", target_speed=100.0, full_song=True,
        )
    )
    save_song(song, song_path)

    # Never practiced yet -- falls back to the song's flat start_speed.
    _, data = _get_json(base, f"/api/song/{slug}")
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["whole-song"]["starting_speed_pct"] == 50.0

    _post(base, "/api/rep", {
        "song": slug, "section": "whole-song", "speed": 92.0, "semitones": 0,
        "pass": True, "clean": False, "loop_s": 180.0, "setlist": None, "source": "ui",
    })

    _, data = _get_json(base, f"/api/song/{slug}")
    by_id = {s["id"]: s for s in data["sections"]}
    # 92 -- exactly the last pass's speed, unclean and not a rung, unlike
    # solo-full's ladder-quantized result above.
    assert by_id["whole-song"]["starting_speed_pct"] == 92.0


def test_api_song_setlist_query_param_derives_the_shift(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
    # test-song's recording.tuning is "E standard" (see _make_song) -- an
    # Eb-standard setlist derives -1.
    status, data = _get_json(base, f"/api/song/{slug}?setlist=gig")
    assert status == 200
    assert data["shift"] == -1


def test_api_song_unknown_setlist_query_param_degrades_to_zero(served):
    base, _, slug = served
    status, data = _get_json(base, f"/api/song/{slug}?setlist=no-such-setlist")
    assert status == 200
    assert data["shift"] == 0


def test_api_song_unknown_slug_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/song/no-such-song")
    assert caught.value.code == 404


def test_api_song_traversal_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/song/..%2f..%2fetc%2fpasswd")
    assert caught.value.code == 404


# ── GET /api/peaks/<slug> ──────────────────────────────────────────────────


def test_api_peaks_not_built_yet_is_404(served):
    """woodshed.peaks does not exist in this run -- documented choice: a
    missing cache (or a missing module) answers 404 with a small body
    rather than a 500 or a crash."""
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/peaks/{slug}?level=1024")
    assert caught.value.code == 404


def test_api_peaks_unknown_slug_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/peaks/no-such-song")
    assert caught.value.code == 404


def test_api_peaks_defaults_to_the_coarsest_level_when_none_given(served):
    """Found live 2026-09-06: song.js/practice.js both fetch payload's
    peaks_url bare, with no ?level= at all (there is no zoom feature yet
    for either to pick one from) -- this must serve the cached peaks, not
    404 on a param neither caller ever sends."""
    from woodshed import peaks as peaks_module

    base, repo, slug = served
    peaks_module.write_peaks(repo, slug, {1024: [(-0.5, 0.5)], 4096: [(-0.2, 0.2)]})

    status, data = _get_json(base, f"/api/peaks/{slug}")

    assert status == 200
    assert data["level"] == 1024


def test_api_peaks_explicit_level_still_selects_that_level(served):
    from woodshed import peaks as peaks_module

    base, repo, slug = served
    peaks_module.write_peaks(repo, slug, {1024: [(-0.5, 0.5)], 4096: [(-0.2, 0.2)]})

    status, data = _get_json(base, f"/api/peaks/{slug}?level=4096")

    assert status == 200
    assert data["level"] == 4096


# ── GET /api/audio/<slug> ───────────────────────────────────────────────────


def test_audio_is_served_whole_with_range_support_advertised(served):
    base, _, slug = served
    with urllib.request.urlopen(base + f"/api/audio/{slug}") as response:
        assert response.status == 200
        assert response.headers.get("Accept-Ranges") == "bytes"
        body = response.read()
    assert body == AUDIO_BYTES


def test_audio_honours_a_byte_range(served):
    base, _, slug = served
    request = urllib.request.Request(
        base + f"/api/audio/{slug}", headers={"Range": "bytes=4-7"}
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == f"bytes 4-7/{len(AUDIO_BYTES)}"
        body = response.read()
    assert body == AUDIO_BYTES[4:8]


def test_audio_unknown_slug_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/audio/no-such-song")
    assert caught.value.code == 404


def test_audio_traversal_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/audio/..%2f..%2fconfig.yaml")
    assert caught.value.code == 404


# ── GET /api/stem/<slug>/<section> (Phase 1.5, S3) ──────────────────────────


def test_stem_isolates_and_serves_the_clip_range_served(served, monkeypatch):
    import woodshed.separate as separate_module

    base, repo, slug = served
    calls = []

    def fake_isolate(repo_, song, section, *, pre_roll_s=0.0, force=False):
        calls.append((song.slug, section.id, pre_roll_s))
        dest = repo_.cache_dir(song.slug) / "stems" / f"{section.id}-guitar-fake.flac"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"ISOLATED GUITAR BYTES")
        return dest

    monkeypatch.setattr(separate_module, "isolate_guitar", fake_isolate)

    with urllib.request.urlopen(base + f"/api/stem/{slug}/solo-full") as response:
        assert response.status == 200
        assert response.headers.get("Accept-Ranges") == "bytes"
        assert response.read() == b"ISOLATED GUITAR BYTES"
    assert len(calls) == 1
    assert calls[0][0] == slug
    assert calls[0][1] == "solo-full"


def test_stem_unknown_section_is_404(served):
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/stem/{slug}/no-such-section")
    assert caught.value.code == 404


def test_stem_unknown_song_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/stem/no-such-song/solo-full")
    assert caught.value.code == 404


def test_stem_missing_demucs_surfaces_as_400(served, monkeypatch):
    import woodshed.separate as separate_module
    from woodshed.errors import WoodshedError

    base, _, slug = served

    def fake_isolate(*a, **kw):
        raise WoodshedError("this command needs the 'demucs' package.")

    monkeypatch.setattr(separate_module, "isolate_guitar", fake_isolate)

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/stem/{slug}/solo-full")
    assert caught.value.code == 400


# ── GET /api/song/<slug>'s demucs_available field (Phase 1.5, S3) ──────────


def test_song_payload_reports_demucs_availability(served, monkeypatch):
    import woodshed.separate as separate_module

    base, _, slug = served
    monkeypatch.setattr(separate_module, "demucs_available", lambda: True)
    status, data = _get_json(base, f"/api/song/{slug}")
    assert status == 200
    assert data["demucs_available"] is True

    monkeypatch.setattr(separate_module, "demucs_available", lambda: False)
    status, data = _get_json(base, f"/api/song/{slug}")
    assert data["demucs_available"] is False


# ── GET /api/click/<slug>/<section> ─────────────────────────────────────────


def _wav_duration_s(body: bytes) -> float:
    with wave_module.open(io.BytesIO(body), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


def test_click_lead_in_covers_the_pre_roll(served):
    # _make_song's tempo is 120bpm; PracticeDefaults.pre_roll_beats
    # defaults to 4.0 -- 4 beats at 120bpm is 2.0s at speed 1.0.
    base, _, slug = served
    status, body = _get(base, f"/api/click/{slug}/solo-full")
    assert status == 200
    assert _wav_duration_s(body) == pytest.approx(2.0, abs=0.05)


def test_click_speed_scales_the_duration(served):
    base, _, slug = served
    status, body = _get(base, f"/api/click/{slug}/solo-full?speed=0.5")
    assert status == 200
    assert _wav_duration_s(body) == pytest.approx(4.0, abs=0.05)  # half speed, twice as long


def test_click_full_mode_covers_lead_in_plus_the_whole_loop(served):
    base, _, slug = served
    status, body = _get(base, f"/api/click/{slug}/solo-full?mode=full")
    assert status == 200
    # solo-full is 0.0-60.0s -- 2.0s lead-in + 60.0s loop at speed 1.0.
    assert _wav_duration_s(body) == pytest.approx(62.0, abs=0.05)


def test_click_unknown_section_is_404(served):
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/click/{slug}/no-such-section")
    assert caught.value.code == 404


def test_click_unknown_song_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/click/no-such-song/solo-full")
    assert caught.value.code == 404


# ── GET /api/render/<slug>/<section> (Phase 2, Group I3 -- pulled forward
#    into Phase 1.5; see render.py's own module doc) ─────────────────────────


def _render_cache_path(
    repo: Repo, slug: str, section: Section, speed_pct: float, semitones: int
) -> Path:
    """The exact path `_render` will look for -- computed the same way the
    endpoint itself does, from the test song's own tempo/practice
    defaults (120bpm, 4-beat/2.0s pre-roll, 10ms crossfade)."""
    from woodshed.clock import pre_roll_seconds
    from woodshed.manifest import effective_pre_roll_beats
    from woodshed.render import cache_path, span_fingerprint

    song = load_song(repo.song_dir(slug) / "song.yaml")
    pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
    fp = span_fingerprint(
        song, section, pre_roll_s=pre_roll_s, crossfade_ms=song.practice.loop_crossfade_ms
    )
    return cache_path(repo, slug, section.id, speed_pct, semitones, fp)


def test_api_render_serves_the_cached_file_range_served(served, monkeypatch):
    import woodshed.render as render_module

    base, repo, slug = served
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "solo-full")
    dest = _render_cache_path(repo, slug, section, 60.0, 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"FAKE FLAC BYTES")
    # plan_ahead fires in the background on a cache HIT too -- keep it inert
    # rather than shelling out to a real rubberband for the next rung.
    monkeypatch.setattr(render_module, "render_section", lambda *a, **kw: Path("unused"))

    url = base + f"/api/render/{slug}/solo-full?speed=60&semitones=0"
    with urllib.request.urlopen(url) as response:
        assert response.status == 200
        assert response.headers.get("Accept-Ranges") == "bytes"
        assert response.read() == b"FAKE FLAC BYTES"


def test_api_render_kicks_off_plan_ahead_for_the_rung_above_on_a_cache_hit(served, monkeypatch):
    import woodshed.render as render_module

    base, repo, slug = served
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "solo-full")
    dest = _render_cache_path(repo, slug, section, 60.0, 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"cached")

    seen = []
    monkeypatch.setattr(
        render_module, "render_section",
        lambda repo_, s, sec, speed_pct, semitones, **kw: seen.append(speed_pct) or Path("x"),
    )

    _get(base, f"/api/render/{slug}/solo-full?speed=60&semitones=0")

    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    # song.practice defaults: start_speed=50, ladder_step=5 -- the rung
    # above the requested 60 is 65.
    assert seen == [65.0]


def test_api_render_returns_202_while_not_yet_cached(served, monkeypatch):
    import woodshed.render as render_module

    base, _, slug = served
    release = threading.Event()

    def fake_render_section(repo_, song, section, speed_pct, semitones, **kw):
        release.wait(timeout=2.0)
        return Path("fake.flac")

    monkeypatch.setattr(render_module, "render_section", fake_render_section)

    status, data = _get_json(base, f"/api/render/{slug}/solo-full?speed=60&semitones=0")

    assert status == 202
    assert data == {"rendering": True}
    release.set()  # let the background thread finish before the fixture tears down


def test_api_render_unknown_section_is_404(served):
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/render/{slug}/no-such-section?speed=60")
    assert caught.value.code == 404


def test_api_render_unknown_song_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/render/no-such-song/solo-full?speed=60")
    assert caught.value.code == 404


def test_api_render_unknown_source_is_400(served):
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/render/{slug}/solo-full?speed=60&source=bass")
    assert caught.value.code == 400


# ── GET /api/render?source=guitar (Group S2) ────────────────────────────────


def test_api_render_guitar_source_reports_separating_before_the_stem_is_cached(
    served, monkeypatch
):
    import woodshed.render as render_module

    base, _, slug = served
    release = threading.Event()
    monkeypatch.setattr(
        render_module, "render_section",
        lambda *a, **kw: release.wait(timeout=2.0) or Path("fake.flac"),
    )

    status, data = _get_json(
        base, f"/api/render/{slug}/solo-full?speed=60&semitones=0&source=guitar"
    )

    assert status == 202
    assert data == {"rendering": True, "stage": "separating"}
    release.set()


def test_api_render_guitar_source_reports_rendering_once_the_stem_is_cached(
    served, monkeypatch
):
    import woodshed.render as render_module
    from woodshed.clock import pre_roll_seconds
    from woodshed.manifest import effective_pre_roll_beats
    from woodshed.separate import stem_cache_path, stem_fingerprint

    base, repo, slug = served
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "solo-full")
    pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
    stem_fp = stem_fingerprint(song, section, pre_roll_s=pre_roll_s)
    stem_path = stem_cache_path(repo, slug, section.id, stem_fp)
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    stem_path.write_bytes(b"already isolated")

    release = threading.Event()
    monkeypatch.setattr(
        render_module, "render_section",
        lambda *a, **kw: release.wait(timeout=2.0) or Path("fake.flac"),
    )

    status, data = _get_json(
        base, f"/api/render/{slug}/solo-full?speed=60&semitones=0&source=guitar"
    )

    assert status == 202
    assert data == {"rendering": True, "stage": "rendering"}
    release.set()


def test_api_render_guitar_source_never_collides_with_the_mix_cache(served, monkeypatch):
    import woodshed.render as render_module

    base, repo, slug = served
    song = load_song(repo.song_dir(slug) / "song.yaml")
    section = next(s for s in song.sections if s.id == "solo-full")
    mix_dest = _render_cache_path(repo, slug, section, 60.0, 0)
    mix_dest.parent.mkdir(parents=True, exist_ok=True)
    mix_dest.write_bytes(b"MIX BYTES")
    monkeypatch.setattr(render_module, "render_section", lambda *a, **kw: Path("unused"))

    status, data = _get_json(
        base, f"/api/render/{slug}/solo-full?speed=60&semitones=0&source=guitar"
    )

    assert status == 202  # the guitar variant is NOT cached, even though the mix is
    assert data["stage"] == "separating"


# ── GET /api/setlists, GET /api/setlist/<slug> ──────────────────────────────


def test_api_setlists_lists_every_setlist(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
    status, data = _get_json(base, "/api/setlists")
    assert status == 200
    assert data == [
        {"slug": "gig", "name": "Gig", "tuning": "Eb standard", "date": None, "song_count": 1}
    ]


def test_api_setlist_payload_has_a_row_per_song_and_a_next_up(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
    status, data = _get_json(base, "/api/setlist/gig")
    assert status == 200
    assert data["name"] == "Gig"
    assert data["song_count"] == 1
    assert data["needs_audio_count"] == 0
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["slug"] == slug
    assert row["shift"] == -1  # E standard recording, Eb standard setlist
    assert row["section_count"] == 2
    assert data["next_up"]["song_slug"] == slug


def test_api_setlist_row_section_count_excludes_full_song(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
    _post(
        base, "/api/section",
        {"song": slug, "id": "whole", "name": "Whole song", "start_s": 0.0,
         "end_s": 200.0, "snapped": "free", "target_speed": 100.0, "full_song": True},
    )
    _, data = _get_json(base, "/api/setlist/gig")
    row = data["rows"][0]
    # _make_song's two ordinary sections, NOT the full_song one just added.
    assert row["section_count"] == 2


def test_api_setlist_row_flags_needs_audio_for_an_unbound_song(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="ghost")]),
        repo.setlists_dir / "gig.yaml",
    )
    status, data = _get_json(base, "/api/setlist/gig")
    assert status == 200
    assert data["needs_audio_count"] == 1
    assert data["rows"][0] == {
        "slug": "ghost", "title": "ghost", "artist": None, "needs_audio": True,
        "readiness": None, "section_count": 0, "sections_under_target": 0,
        "last_practised": None, "is_cold": False, "shift": None,
    }


def test_api_setlist_unknown_slug_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/setlist/no-such-setlist")
    assert caught.value.code == 404


# ── POST /api/shift ──────────────────────────────────────────────────────


def test_post_shift_writes_and_returns_the_effective_value(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
    status, data = _post(base, "/api/shift", {"setlist": "gig", "song": slug, "shift": -3})
    assert status == 200
    assert data == {"setlist": "gig", "song": slug, "shift": -3, "raw": -3}

    from woodshed.setlist import load as load_setlist
    assert load_setlist(repo, "gig").songs[0].shift == -3


def test_post_shift_null_clears_back_to_the_derived_default(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug=slug, shift=-3)]),
        repo.setlists_dir / "gig.yaml",
    )
    status, data = _post(base, "/api/shift", {"setlist": "gig", "song": slug, "shift": None})
    assert status == 200
    assert data["raw"] is None
    assert data["shift"] == -1  # derived: Eb standard setlist, E standard recording


def test_post_shift_out_of_range_is_400(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/shift", {"setlist": "gig", "song": slug, "shift": 9})
    assert caught.value.code == 400


def test_post_shift_missing_fields_is_400(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/shift", {"setlist": "gig"})
    assert caught.value.code == 400


# ── POST /api/setlist, POST /api/setlist/<slug>/songs ───────────────────


def test_post_setlist_creates_one_derived_slug(served):
    base, repo, _ = served
    status, data = _post(base, "/api/setlist", {"name": "The Gig", "tuning": "Eb standard"})
    assert status == 200
    assert data == {"slug": "the-gig", "name": "The Gig", "tuning": "Eb standard",
                     "date": None, "song_count": 0}

    from woodshed.setlist import load
    assert load(repo, "the-gig").name == "The Gig"


def test_post_setlist_explicit_slug_overrides_the_derived_one(served):
    base, repo, _ = served
    status, data = _post(
        base, "/api/setlist",
        {"name": "The Gig", "tuning": "E standard", "slug": "gig", "date": "2027-04-17"},
    )
    assert status == 200
    assert data["slug"] == "gig"
    assert data["date"] == "2027-04-17"
    from woodshed.setlist import load
    assert load(repo, "gig").date.isoformat() == "2027-04-17"


def test_post_setlist_missing_fields_is_400(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/setlist", {"name": "The Gig"})
    assert caught.value.code == 400


def test_post_setlist_refuses_an_existing_slug(served):
    base, _, _ = served
    _post(base, "/api/setlist", {"name": "The Gig", "tuning": "E standard"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/setlist", {"name": "The Gig", "tuning": "Eb standard"})
    assert caught.value.code == 400


def test_post_setlist_songs_resolves_an_existing_song_by_fuzzy_title(served):
    base, repo, slug = served
    _post(base, "/api/setlist", {"name": "Gig", "tuning": "E standard", "slug": "gig"})
    status, data = _post(base, "/api/setlist/gig/songs", {"song": "Test Song"})
    assert status == 200
    assert data == {"setlist": "gig", "song": slug}

    from woodshed.setlist import load
    assert [e.slug for e in load(repo, "gig").songs] == [slug]


def test_post_setlist_songs_adds_a_needs_audio_placeholder(served):
    base, repo, _ = served
    _post(base, "/api/setlist", {"name": "Gig", "tuning": "E standard", "slug": "gig"})
    status, data = _post(base, "/api/setlist/gig/songs", {"song": "Mother Sacher"})
    assert status == 200
    assert data["song"] == "mother-sacher"

    # No song.yaml exists for it -- the dashboard's own needs-audio path.
    status, dashboard = _get_json(base, "/api/setlist/gig")
    assert status == 200
    row = next(r for r in dashboard["rows"] if r["slug"] == "mother-sacher")
    assert row["needs_audio"] is True


def test_post_setlist_songs_missing_song_is_400(served):
    base, _, _ = served
    _post(base, "/api/setlist", {"name": "Gig", "tuning": "E standard", "slug": "gig"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/setlist/gig/songs", {})
    assert caught.value.code == 400


# ── POST /api/rep ────────────────────────────────────────────────────────


def test_post_rep_appends_exactly_one_line(served):
    base, repo, slug = served
    ledger_path = repo.ledger_path()
    before = len(list(read_ledger(repo))) if ledger_path.exists() else 0

    status, data = _post(
        base,
        "/api/rep",
        {
            "song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
            "pass": True, "clean": True, "loop_s": 12.3, "setlist": None,
            "source": "ui",
        },
    )
    assert status == 200
    assert "id" in data

    after = list(read_ledger(repo))
    assert len(after) == before + 1
    assert after[-1].song == slug
    assert after[-1].passed is True
    assert after[-1].clean is True


def test_post_rep_twice_grows_the_ledger_by_two_lines(served):
    base, repo, slug = served
    payload = {
        "song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
        "pass": True, "clean": False, "loop_s": 1.0, "setlist": None, "source": "ui",
    }
    _post(base, "/api/rep", payload)
    _post(base, "/api/rep", payload)
    assert len(list(read_ledger(repo))) == 2


# ── POST /api/section ───────────────────────────────────────────────────


def test_post_section_creates_a_new_span(served):
    base, repo, slug = served
    status, data = _post(
        base,
        "/api/section",
        {
            "song": slug, "id": "new-bit", "name": "New bit",
            "start_s": 70.0, "end_s": 90.0, "snapped": "free",
            "target_speed": 100.0,
        },
    )
    assert status == 200
    ids = [s["id"] for s in data["sections"]]
    assert "new-bit" in ids


def test_post_section_round_trips_full_song_and_lead_in_beats(served):
    base, _, slug = served
    status, data = _post(
        base,
        "/api/section",
        {
            "song": slug, "id": "whole", "name": "Whole song",
            "start_s": 0.0, "end_s": 200.0, "snapped": "free",
            "target_speed": 100.0, "full_song": True, "lead_in_beats": 8,
        },
    )
    assert status == 200
    whole = next(s for s in data["sections"] if s["id"] == "whole")
    assert whole["full_song"] is True
    assert whole["lead_in_beats"] == 8


def test_post_section_round_trips_start_speed(served):
    base, _, slug = served
    status, data = _post(
        base,
        "/api/section",
        {
            "song": slug, "id": "whole", "name": "Whole song",
            "start_s": 0.0, "end_s": 200.0, "snapped": "free",
            "target_speed": 100.0, "start_speed": 65.0,
        },
    )
    assert status == 200
    whole = next(s for s in data["sections"] if s["id"] == "whole")
    assert whole["start_speed"] == 65.0


def test_post_section_updates_an_existing_span(served):
    base, repo, slug = served
    status, data = _post(
        base,
        "/api/section",
        {
            "song": slug, "id": "solo-part", "name": "Renamed",
            "start_s": 10.0, "end_s": 35.0, "snapped": "free",
            "target_speed": 90.0,
        },
    )
    assert status == 200
    updated = next(s for s in data["sections"] if s["id"] == "solo-part")
    assert updated["end_s"] == 35.0
    assert updated["name"] == "Renamed"
    # exactly one span with this id survives -- an update, not an add
    assert sum(1 for s in data["sections"] if s["id"] == "solo-part") == 1


def test_post_section_response_carries_lane_and_ancestors(served):
    """GET /api/song computes `lane`/`ancestors` per invariant 2 (derived, never
    stored); POST /api/section must return the same shape. Found building the front
    end (Group D, D3): a caller that redraws lanes from this response rather than
    re-fetching GET /api/song needs these fields too, or every section collapses onto
    lane 0 after the first edit.
    """
    base, repo, slug = served
    status, data = _post(
        base,
        "/api/section",
        {
            "song": slug, "id": "new-bit", "name": "New bit",
            "start_s": 70.0, "end_s": 90.0, "snapped": "free",
            "target_speed": 100.0,
        },
    )
    assert status == 200
    for section in data["sections"]:
        assert "lane" in section
        assert "ancestors" in section


def test_post_section_deletes_a_span(served):
    base, repo, slug = served
    status, data = _post(
        base, "/api/section", {"song": slug, "action": "delete", "id": "solo-part"}
    )
    assert status == 200
    assert "solo-part" not in [s["id"] for s in data["sections"]]
    assert "solo-full" in [s["id"] for s in data["sections"]]


def test_post_section_exact_duplicate_is_400_with_error_key(served):
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(
            base,
            "/api/section",
            {
                "song": slug, "id": "duplicate-of-solo-full",
                "name": "Dup", "start_s": 0.0, "end_s": 60.0,
                "snapped": "free", "target_speed": 100.0,
            },
        )
    assert caught.value.code == 400
    body = json.loads(caught.value.read().decode("utf-8"))
    assert "error" in body


def test_post_section_unknown_song_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(
            base, "/api/section",
            {"song": "no-such-song", "id": "x", "start_s": 0.0, "end_s": 1.0,
             "snapped": "free", "target_speed": 100.0},
        )
    assert caught.value.code == 404


# ── POST /api/song/delete ────────────────────────────────────────────────


def test_post_song_delete_removes_the_song_directory(served):
    base, repo, slug = served
    song_dir = repo.song_dir(slug)
    assert song_dir.is_dir()

    status, data = _post(base, "/api/song/delete", {"song": slug})
    assert status == 200
    assert data == {"deleted": slug}
    assert not song_dir.exists()


def test_post_song_delete_removes_the_slug_from_every_setlist(served):
    from woodshed.setlist import load as load_setlist

    base, repo, slug = served
    save_setlist(
        Setlist(
            name="Gig", tuning="E standard",
            songs=[SetlistEntry(slug=slug), SetlistEntry(slug="other-song")],
        ),
        repo.setlists_dir / "gig.yaml",
    )

    _post(base, "/api/song/delete", {"song": slug})

    updated = load_setlist(repo, "gig")
    assert [e.slug for e in updated.songs] == ["other-song"]


def test_post_song_delete_never_touches_the_ledger(served):
    """CLAUDE.md invariant 5: the ledger is append-only and the only
    irreplaceable file. Deleting the song it names must not touch it."""
    base, repo, slug = served
    _post(base, "/api/rep", {
        "song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
        "pass": True, "clean": True, "loop_s": 12.3, "setlist": None, "source": "ui",
    })
    before = list(read_ledger(repo))

    _post(base, "/api/song/delete", {"song": slug})

    assert list(read_ledger(repo)) == before


def test_post_song_delete_unknown_slug_is_404(served):
    base, _, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/song/delete", {"song": "no-such-song"})
    assert caught.value.code == 404


# ── Host / Origin hardening ────────────────────────────────────────────────


def test_post_with_foreign_host_is_refused(served):
    base, _, slug = served
    body = json.dumps({
        "song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
        "pass": True, "clean": True, "loop_s": 1.0, "setlist": None, "source": "ui",
    }).encode("utf-8")
    status, _ = _post_raw(base, "/api/rep", body, {"Host": "evil.example:9999"})
    assert status in (400, 403)


def test_post_with_foreign_origin_is_refused(served):
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(
            base, "/api/rep",
            {"song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
             "pass": True, "clean": True, "loop_s": 1.0, "setlist": None,
             "source": "ui"},
            headers={"Origin": "http://evil.example"},
        )
    assert caught.value.code in (400, 403)


def test_post_with_no_origin_header_is_allowed(served):
    """Easy to get backwards: curl, the CLI and some same-origin fetches send
    no Origin header at all, and that must be let through -- only a
    *foreign* Origin is refused."""
    base, repo, slug = served
    status, data = _post(
        base, "/api/rep",
        {"song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
         "pass": True, "clean": True, "loop_s": 1.0, "setlist": None,
         "source": "ui"},
    )
    assert status == 200
    assert "id" in data


def test_post_with_matching_origin_is_allowed(served):
    base, _, slug = served
    status, data = _post(
        base, "/api/rep",
        {"song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
         "pass": True, "clean": True, "loop_s": 1.0, "setlist": None,
         "source": "ui"},
        headers={"Origin": base},
    )
    assert status == 200


# ── POST /api/shutdown ──────────────────────────────────────────────────────


def test_post_shutdown_stops_the_server(real_repo):
    server = make_server(real_repo, port=0)
    _serve(server)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"

    status, data = _post(base, "/api/shutdown", {})
    assert status == 200
    assert data["stopping"] is True

    server_thread_done = threading.Event()

    def _wait():
        server.server_close()
        server_thread_done.set()

    # give the server's own shutdown thread a moment to actually stop the loop
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(base + "/api/config", timeout=0.2)
            time.sleep(0.05)
        except (urllib.error.URLError, ConnectionError, OSError):
            break
    else:
        pytest.fail("server never stood down after /api/shutdown")


# ── the three-writes test ────────────────────────────────────────────────


def _hash_tree(root: Path) -> dict[Path, str]:
    hashes: dict[Path, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            hashes[path.relative_to(root)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def test_server_writes_nothing_else(served, monkeypatch):
    base, repo, slug = served
    save_setlist(
        Setlist(
            name="Gig", tuning="Eb standard",
            songs=[SetlistEntry(slug=slug), SetlistEntry(slug="needs-audio-tune")],
        ),
        repo.setlists_dir / "gig.yaml",
    )
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000),
        Segment(start_frame=96_000, end_frame=144_000, sample_rate=48_000),
        Segment(start_frame=144_000, end_frame=192_000, sample_rate=48_000),
        Segment(start_frame=192_000, end_frame=240_000, sample_rate=48_000),
    ])
    _fake_extract(monkeypatch)
    before = _hash_tree(repo.root)

    # every GET this unit implements -- harmless, but exercised so a stray
    # write in a read path would be caught too
    _get(base, "/api/setlist/gig")
    _get(base, f"/api/click/{slug}/solo-full")
    _get(base, "/")
    _get(base, "/web/app.js")
    _get(base, "/api/config")
    _get(base, f"/api/song/{slug}")
    _get(base, f"/api/audio/{slug}")
    try:
        _get(base, f"/api/peaks/{slug}")
    except urllib.error.HTTPError:
        pass

    # every mutating endpoint this unit implements
    # (start/stop with zero segments -- so it never disturbs the 5-segment
    # session already seeded above, which the capture/adjust|merge|split|bind
    # calls below still need to find as the CURRENT session)
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    monkeypatch.setattr(
        capture_runner_module, "default_device",
        lambda: Device(index=0, name="Fake", sample_rate=48_000, channels=1),
    )
    _post(base, "/api/capture/start", {})
    _get(base, "/api/capture/status")
    _post(base, "/api/capture/stop", {})
    _post(
        base, "/api/rep",
        {"song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
         "pass": True, "clean": True, "loop_s": 3.0, "setlist": None,
         "source": "ui"},
    )
    _post(
        base, "/api/section",
        {"song": slug, "id": "another-bit", "name": "Another",
         "start_s": 100.0, "end_s": 120.0, "snapped": "free",
         "target_speed": 100.0},
    )
    _post(base, "/api/section", {"song": slug, "action": "delete", "id": "another-bit"})
    _post(base, "/api/shift", {"setlist": "gig", "song": slug, "shift": -2})
    _post(base, "/api/setlist", {"name": "Duo", "tuning": "E standard", "slug": "duo"})
    _post(base, "/api/setlist/duo/songs", {"song": slug})
    _multipart_post(
        base, "/api/song/upload", {"title": "Uploaded In Writes Test", "tuning": "E standard"},
        file_field="file", filename="upload.wav", content=_wav_bytes(seconds=1.0),
    )
    _get(base, "/api/capture/segments")
    _get(base, "/api/capture/segment-audio/0")
    _post(base, "/api/capture/adjust", {"index": 4, "end_frame": 230_000})
    _post(base, "/api/capture/merge", {"first_index": 1, "second_index": 2})
    _post(base, "/api/capture/split", {"index": 3, "at_frame": 168_000})
    _post(
        base, "/api/capture/bind",
        {"index": 0, "mode": "existing", "slug": "needs-audio-tune", "tuning": "E standard"},
    )

    after = _hash_tree(repo.root)
    all_paths = set(before) | set(after)
    changed = {p for p in all_paths if before.get(p) != after.get(p)}

    ledger_rel = repo.ledger_path().relative_to(repo.root)
    for path in changed:
        # setlists/<slug>.yaml is CLAUDE.md's "setlist.yaml" -- the slug
        # names the file, not the literal word "setlist".
        assert (
            path.name == "song.yaml"
            or path.parts[0] == "setlists"
            or path == ledger_rel
            or "cache" in path.parts
            or path.parts[0] == "capture"
            # binding a segment writes the song's own audio bytes alongside
            # its song.yaml -- one deliberate action, same as `woodshed add`/
            # `woodshed capture` always did from the CLI; U2's bind is the
            # first thing that does it from the SERVER, not a new category.
            or "audio" in path.parts
        ), f"unexpected write to {path}"
    assert changed, "the test exercised nothing that writes -- assertion would be vacuous"


# ── capture-first: split now, name later (Phase 1.5, Group U) ──────────────


def _start_capture(repo: Repo, segments: list[Segment]) -> Path:
    raw_path = repo.capture_dir / "20260906-120000.wav"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(AUDIO_BYTES)
    start_session(repo, raw_path, segments)
    return raw_path


def _fake_extract(monkeypatch: pytest.MonkeyPatch, content: bytes = b"fake-flac-bytes") -> None:
    """`extract_segment` is imported by NAME into two separate module
    namespaces -- `woodshed.server` (the segment-audio GET's own direct
    call) and `woodshed.capture` (where `bind_segment_to_song`/
    `bind_segment_as_new_song` call it internally) -- so both copies of
    the reference need patching, not just one."""
    import woodshed.capture as capture_module
    import woodshed.server as server_module

    def fake(raw_audio_path, segment, dest_path):
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(content)

    monkeypatch.setattr(server_module, "extract_segment", fake)
    monkeypatch.setattr(capture_module, "extract_segment", fake)


def test_capture_segments_empty_when_nothing_captured(served) -> None:
    base, _repo, _slug = served
    status, body = _get_json(base, "/api/capture/segments")
    assert status == 200
    assert body == []


def test_capture_segments_lists_pending_entries(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000, overflowed=True),
    ])

    status, body = _get_json(base, "/api/capture/segments")

    assert status == 200
    assert body == [
        {
            "index": 0, "duration_s": 1.0, "overflowed": False,
            "start_frame": 0, "end_frame": 48_000, "sample_rate": 48_000,
        },
        {
            "index": 1, "duration_s": 1.0, "overflowed": True,
            "start_frame": 48_000, "end_frame": 96_000, "sample_rate": 48_000,
        },
    ]


def test_capture_raw_audio_404_with_no_current_session(served) -> None:
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/capture/raw-audio")
    assert caught.value.code == 404


def test_capture_raw_audio_range_serves_the_raw_file(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])

    status, body = _get(base, "/api/capture/raw-audio")

    assert status == 200
    assert body == AUDIO_BYTES


def test_capture_raw_peaks_404_with_no_current_session(served) -> None:
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/capture/raw-peaks")
    assert caught.value.code == 404


def test_capture_raw_peaks_computed_from_the_real_wav_on_disk(served) -> None:
    base, repo, _slug = served
    raw_path = repo.capture_dir / "20260906-120000.wav"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(_wav_bytes(seconds=1.0, rate=48_000))
    start_session(repo, raw_path, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])

    status, body = _get_json(base, "/api/capture/raw-peaks")

    assert status == 200
    assert body["sample_rate"] == 48_000
    assert body["duration_s"] == pytest.approx(1.0)
    assert body["level"] == 1024
    assert len(body["peaks"]) == 1024


def test_capture_segment_audio_serves_the_extracted_bytes(served, monkeypatch) -> None:
    base, repo, _slug = served
    _start_capture(repo, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])
    _fake_extract(monkeypatch, b"preview-bytes")

    status, body = _get(base, "/api/capture/segment-audio/0")

    assert status == 200
    assert body == b"preview-bytes"


def test_capture_segment_audio_404_for_an_unknown_index(served) -> None:
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/capture/segment-audio/0")
    assert caught.value.code == 404


def test_post_capture_bind_existing_binds_to_a_needs_audio_slug(served, monkeypatch) -> None:
    base, repo, _slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug="new-tune")]),
        repo.setlists_dir / "gig.yaml",
    )
    _start_capture(repo, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])
    _fake_extract(monkeypatch)

    status, body = _post(
        base, "/api/capture/bind",
        {"index": 0, "mode": "existing", "slug": "new-tune", "tuning": "Eb standard"},
    )

    assert status == 200
    assert body["slug"] == "new-tune"
    assert (repo.song_dir("new-tune") / "song.yaml").is_file()
    # the lone segment resolved -> the raw file and sidecar are both gone
    _status, remaining = _get_json(base, "/api/capture/segments")
    assert remaining == []


def test_post_capture_bind_new_creates_a_song_and_adds_it_to_a_setlist(served, monkeypatch) -> None:
    base, repo, _slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[]),
        repo.setlists_dir / "gig.yaml",
    )
    _start_capture(repo, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])
    _fake_extract(monkeypatch)

    status, body = _post(
        base, "/api/capture/bind",
        {
            "index": 0, "mode": "new", "title": "Brand New Tune", "artist": "Someone",
            "tuning": "E standard", "setlist": "gig",
        },
    )

    assert status == 200
    assert body["slug"] == "brand-new-tune"
    assert (repo.song_dir("brand-new-tune") / "song.yaml").is_file()
    updated_gig = Setlist.model_validate(
        yaml.safe_load((repo.setlists_dir / "gig.yaml").read_text(encoding="utf-8"))
    )
    assert [s.slug for s in updated_gig.songs] == ["brand-new-tune"]


def test_post_capture_bind_refuses_an_already_resolved_index(served, monkeypatch) -> None:
    base, repo, _slug = served
    _start_capture(repo, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])
    _fake_extract(monkeypatch)
    _post(
        base, "/api/capture/bind",
        {"index": 0, "mode": "new", "title": "First Bind", "tuning": "E standard"},
    )

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(
            base, "/api/capture/bind",
            {"index": 0, "mode": "new", "title": "Second Bind", "tuning": "E standard"},
        )
    assert caught.value.code == 400


def test_post_capture_discard_drops_the_segment_without_creating_anything(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)])
    songs_before = set(repo.list_songs())

    status, body = _post(base, "/api/capture/discard", {"index": 0})

    assert status == 200
    assert body == {"index": 0, "discarded": True}
    assert set(repo.list_songs()) == songs_before
    _status, remaining = _get_json(base, "/api/capture/segments")
    assert remaining == []


def test_post_capture_discard_then_bind_the_same_index_refuses(served) -> None:
    """Plan's own named contract."""
    base, repo, _slug = served
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000),
    ])
    _post(base, "/api/capture/discard", {"index": 0})

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(
            base, "/api/capture/bind",
            {"index": 0, "mode": "new", "title": "Too Late", "tuning": "E standard"},
        )
    assert caught.value.code == 400


# ── U2b: /api/capture/adjust|merge|split ─────────────────────────────────


def test_post_capture_adjust_moves_the_boundary(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000),
    ])

    status, body = _post(base, "/api/capture/adjust", {"index": 1, "start_frame": 50_000})

    assert status == 200
    assert body["index"] == 1
    assert body["start_frame"] == 50_000
    assert body["end_frame"] == 96_000


def test_post_capture_adjust_refuses_overlapping_a_neighbour(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000),
    ])

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/capture/adjust", {"index": 1, "start_frame": 10_000})
    assert caught.value.code == 400


def test_post_capture_merge_combines_two_adjacent_segments(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000),
    ])

    status, body = _post(
        base, "/api/capture/merge", {"first_index": 0, "second_index": 1}
    )

    assert status == 200
    assert body["index"] == 0
    assert body["start_frame"] == 0
    assert body["end_frame"] == 96_000
    _status, remaining = _get_json(base, "/api/capture/segments")
    assert [e["index"] for e in remaining] == [0]


def test_post_capture_merge_refuses_non_adjacent_indices(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000),
        Segment(start_frame=96_000, end_frame=144_000, sample_rate=48_000),
    ])

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/capture/merge", {"first_index": 0, "second_index": 2})
    assert caught.value.code == 400


def test_post_capture_split_creates_a_second_entry_with_a_fresh_index(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [Segment(start_frame=0, end_frame=96_000, sample_rate=48_000)])

    status, body = _post(base, "/api/capture/split", {"index": 0, "at_frame": 48_000})

    assert status == 200
    assert body["first"]["index"] == 0
    assert body["first"]["end_frame"] == 48_000
    assert body["second"]["index"] == 1
    assert body["second"]["start_frame"] == 48_000
    _status, remaining = _get_json(base, "/api/capture/segments")
    assert sorted(e["index"] for e in remaining) == [0, 1]


def test_post_capture_split_refuses_a_boundary_outside_the_segment(served) -> None:
    base, repo, _slug = served
    _start_capture(repo, [Segment(start_frame=0, end_frame=96_000, sample_rate=48_000)])

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/capture/split", {"index": 0, "at_frame": 96_000})
    assert caught.value.code == 400


# ── parse_multipart, POST /api/song/upload (Phase 1.5, Group T, T1) ────────


def test_parse_multipart_extracts_fields_and_the_file(served) -> None:
    boundary = "boundary123"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\n'
        f"My Song\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="track.wav"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode() + b"\x00\x01binarydata" + f"\r\n--{boundary}--\r\n".encode()

    fields, files = parse_multipart(f"multipart/form-data; boundary={boundary}", body)

    assert fields == {"title": "My Song"}
    assert files["file"] == ("track.wav", b"\x00\x01binarydata")


def test_parse_multipart_refuses_a_content_type_with_no_boundary() -> None:
    with pytest.raises(WoodshedError, match="boundary"):
        parse_multipart("multipart/form-data", b"whatever")


def test_post_song_upload_binds_a_new_song(served) -> None:
    base, repo, _slug = served
    wav_bytes = _wav_bytes(seconds=2.0)
    status, body = _multipart_post(
        base, "/api/song/upload",
        {"title": "Uploaded Tune", "artist": "Band", "tuning": "Eb standard"},
        file_field="file", filename="original-name.wav", content=wav_bytes,
    )

    assert status == 200
    assert body["slug"] == "uploaded-tune"
    song_path = repo.song_dir("uploaded-tune") / "song.yaml"
    assert song_path.is_file()
    song = load_song(song_path)
    assert song.artist == "Band"
    assert song.recording.tuning == "Eb standard"
    assert song.recording.file == "audio/original-name.wav"
    assert song.recording.duration_s == pytest.approx(2.0, abs=0.05)
    assert (repo.song_dir("uploaded-tune") / song.recording.file).read_bytes() == wav_bytes
    # A default "Whole song" section -- Practice is reachable immediately,
    # with no section drawn by hand first.
    assert len(song.sections) == 1
    assert song.sections[0].full_song is True


def test_post_song_upload_refuses_a_missing_title(served) -> None:
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _multipart_post(
            base, "/api/song/upload", {"tuning": "E standard"},
            file_field="file", filename="a.wav", content=AUDIO_BYTES,
        )
    assert caught.value.code == 400


def test_post_song_upload_refuses_an_existing_slug(served) -> None:
    base, _repo, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _multipart_post(
            base, "/api/song/upload", {"title": "Test Song", "tuning": "E standard"},
            file_field="file", filename="a.wav", content=AUDIO_BYTES,
        )
    assert caught.value.code == 400


# ── POST /api/capture/start|status|stop (Phase 1.5, Group T, T2) ──────────


def _blocking_fake_capture(segments_after_stop: list[Segment]):
    """Same technique as test_capture_runner.py's own fake: blocks on the
    REAL stop_event the runner created, so POST /api/capture/stop is what
    actually unblocks it."""

    def fake(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
             stop_event=None, **_ignored):
        if on_level is not None:
            on_level(0.25)
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(b"fake raw audio")
        while not stop_event.is_set():
            time.sleep(0.005)
        yield from segments_after_stop

    return fake


def test_capture_status_is_idle_before_any_capture_starts(served) -> None:
    base, _repo, _slug = served
    status, body = _get_json(base, "/api/capture/status")
    assert status == 200
    assert body["running"] is False
    assert body["segment_count"] == 0
    assert "available" in body  # a real bool either way -- pyaudiowpatch may not be installed


def test_post_capture_start_then_stop_creates_a_pending_session(
    served, monkeypatch
) -> None:
    base, repo, _slug = served
    segments = [Segment(start_frame=0, end_frame=48_000, sample_rate=48_000)]
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture(segments))
    monkeypatch.setattr(
        capture_runner_module, "default_device",
        lambda: Device(index=0, name="Fake", sample_rate=48_000, channels=1),
    )

    status, body = _post(base, "/api/capture/start", {})
    assert status == 200
    assert body == {"started": True}

    _status, running_body = _get_json(base, "/api/capture/status")
    assert running_body["running"] is True

    status, stop_body = _post(base, "/api/capture/stop", {})
    assert status == 200
    assert stop_body["running"] is False
    assert stop_body["segment_count"] == 1
    _status, remaining = _get_json(base, "/api/capture/segments")
    assert len(remaining) == 1


def test_post_capture_start_refuses_while_one_is_already_running(
    served, monkeypatch
) -> None:
    base, _repo, _slug = served
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    monkeypatch.setattr(
        capture_runner_module, "default_device",
        lambda: Device(index=0, name="Fake", sample_rate=48_000, channels=1),
    )
    _post(base, "/api/capture/start", {})

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/capture/start", {})
    assert caught.value.code == 400

    _post(base, "/api/capture/stop", {})


def test_post_capture_stop_refuses_when_nothing_is_running(served) -> None:
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/capture/stop", {})
    assert caught.value.code == 400


# ── WoodshedError surfaces as 400 ───────────────────────────────────────────


def test_a_woodshed_error_from_a_route_is_a_400(served):
    """sections.validate raising for a malformed span (end_s <= start_s)
    reaches the client as 400, not a traceback."""
    base, _, slug = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(
            base, "/api/section",
            {"song": slug, "id": "backwards", "name": "Backwards",
             "start_s": 50.0, "end_s": 10.0, "snapped": "free",
             "target_speed": 100.0},
        )
    assert caught.value.code == 400


# ── GET /api/progress/<slug> (Phase 2, K2) ───────────────────────────────


def test_progress_payload_has_a_row_per_section_and_a_readiness_series(served):
    base, repo, slug = served
    _post(base, "/api/rep", {
        "song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
        "pass": True, "clean": True, "loop_s": 30.0, "source": "ui",
    })
    status, data = _get_json(base, f"/api/progress/{slug}")
    assert status == 200
    assert data["slug"] == slug
    assert {row["id"] for row in data["sections"]} == {"solo-full", "solo-part"}
    assert len(data["readiness_series"]) >= 2
    assert data["totals"]["passes"] == 1
    row = next(r for r in data["sections"] if r["id"] == "solo-full")
    assert row["reps"] == 1
    assert row["series"][0][1] == 60.0


def test_progress_on_an_unpractised_song_is_zeroes_not_an_error(served):
    base, _repo, slug = served
    status, data = _get_json(base, f"/api/progress/{slug}")
    assert status == 200
    assert data["totals"]["passes"] == 0
    assert data["rungs_gained"] == 0
    assert all(row["series"] == [] for row in data["sections"])


def test_progress_unknown_song_is_a_404(served):
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(base, "/api/progress/no-such-song")
    assert excinfo.value.code == 404


def test_progress_traversal_attempt_is_a_404(served):
    base, _repo, _slug = served
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(base, "/api/progress/..%2f..%2fetc%2fpasswd")
    assert excinfo.value.code == 404


def test_progress_writes_nothing(served):
    """The progress screen is a pure read of the ledger -- CLAUDE.md's "the
    server writes exactly four things" does not include a progress cache."""
    base, repo, slug = served
    before = _hash_tree(repo.root)
    _get_json(base, f"/api/progress/{slug}")
    assert _hash_tree(repo.root) == before


def test_progress_weeks_query_narrows_the_readiness_window(served):
    base, _repo, slug = served
    _status, twelve = _get_json(base, f"/api/progress/{slug}?weeks=12")
    assert twelve["weeks"] == 12
    _status, default = _get_json(base, f"/api/progress/{slug}")
    assert default["weeks"] == 26


def test_progress_weeks_all_spans_back_to_the_oldest_rep(served):
    base, _repo, slug = served
    _post(base, "/api/rep", {
        "song": slug, "section": "solo-full", "speed": 60.0, "semitones": 0,
        "pass": True, "clean": True, "loop_s": 30.0, "source": "ui",
    })
    _status, data = _get_json(base, f"/api/progress/{slug}?weeks=all")
    # One rep, appended a moment ago -- "all" is one week, not 26, and
    # certainly not zero (a chart with no axis).
    assert data["weeks"] == 1


def test_progress_weeks_garbage_falls_back_rather_than_400ing(served):
    base, _repo, slug = served
    status, data = _get_json(base, f"/api/progress/{slug}?weeks=banana")
    assert status == 200
    assert data["weeks"] == 26
