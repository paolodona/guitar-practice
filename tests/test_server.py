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
import urllib.error
import urllib.request
import wave as wave_module
from pathlib import Path
from urllib.parse import urlsplit

import pytest

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
    save_setlist,
    save_song,
)
from woodshed.server import WoodshedServer, make_server, parse_byte_range

POLL = 0.01  # server.serve_forever's poll interval; small so shutdown is fast in tests

# 12 bytes, chosen (like the sibling's fixture) so the range arithmetic below
# has a size that isn't a round number.
AUDIO_BYTES = b"WOODSHEDAUD1"
assert len(AUDIO_BYTES) == 12


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


def test_server_writes_nothing_else(served):
    base, repo, slug = served
    save_setlist(
        Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug=slug)]),
        repo.setlists_dir / "gig.yaml",
    )
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
        ), f"unexpected write to {path}"
    assert changed, "the test exercised nothing that writes -- assertion would be vacuous"


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
