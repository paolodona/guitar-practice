"""Tests for woodshed.sources -- Spotify's list, and the library scan
(Phase 3, Group M; docs/04-sources.md).

**No network, and no token.** Every HTTP call in `sources.py` takes an
`opener`, and every test here passes a fake one. That is not only a testing
convenience: it is the shape that lets this module be built and verified on
a machine with no Spotify app registered at all, which is exactly the
machine this was written on.

**No audio either.** The tag reader is exercised against FLAC and ID3
headers synthesised byte-by-byte below -- small enough to write out, and far
more precise about what is being tested than a checked-in binary would be.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest

from woodshed import sources
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import Setlist, load_song


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _opener(payloads):
    """A urlopen stand-in. `payloads` maps a URL substring -> dict body."""
    seen = []

    def opener(request, *args, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        seen.append((url, getattr(request, "data", None)))
        for fragment, body in payloads.items():
            if fragment in url:
                return _FakeResponse(json.dumps(body).encode("utf-8"))
        raise AssertionError(f"no fake payload for {url}")

    opener.seen = seen
    return opener


# ── PKCE ─────────────────────────────────────────────────────────────────


def test_pkce_challenge_is_s256_of_the_verifier() -> None:
    verifier, challenge = sources.new_pkce()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert challenge == expected
    assert "=" not in challenge  # padding stripped, per RFC 7636


def test_pkce_verifier_is_fresh_every_time_and_long_enough() -> None:
    first, _ = sources.new_pkce()
    second, _ = sources.new_pkce()
    assert first != second
    assert 43 <= len(first) <= 128  # RFC 7636's own bounds


def test_authorize_url_asks_for_a_code_with_s256_and_no_secret() -> None:
    url = sources.authorize_url("client-abc", "http://127.0.0.1:8477/callback", "chal")
    assert url.startswith(sources.AUTH_URL)
    assert "response_type=code" in url
    assert "code_challenge_method=S256" in url
    assert "client_secret" not in url  # there is none, by construction


def test_exchange_code_stores_an_absolute_expiry(monkeypatch) -> None:
    opener = _opener({"api/token": {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
    }})
    creds = sources.exchange_code(
        "client-abc", "the-code", "the-verifier", "http://127.0.0.1/cb",
        opener=opener, now=1000.0,
    )
    assert creds.access_token == "at"
    assert creds.expires_at == 4600.0
    assert creds.client_id == "client-abc"
    # The verifier goes in the body, never the URL.
    _url, body = opener.seen[0]
    assert b"code_verifier=the-verifier" in body


def test_credentials_freshness_leaves_a_margin() -> None:
    creds = sources.Credentials("at", "rt", expires_at=1000.0)
    assert creds.is_fresh(now=800.0)
    assert not creds.is_fresh(now=990.0)  # inside the 60s margin: treat as stale


def test_refresh_keeps_the_old_refresh_token_when_spotify_sends_none() -> None:
    """Spotify does not always return a new refresh token. Dropping the old
    one on those responses is the difference between a session that lasts
    and one that silently logs you out."""
    opener = _opener({"api/token": {"access_token": "at2", "expires_in": 3600}})
    creds = sources.Credentials("at", "rt", expires_at=0.0, client_id="client-abc")
    fresh = sources.refresh_credentials(creds, opener=opener, now=0.0)
    assert fresh.access_token == "at2"
    assert fresh.refresh_token == "rt"


def test_refresh_without_a_stored_token_refuses_with_a_next_step() -> None:
    with pytest.raises(WoodshedError, match="connect Spotify again"):
        sources.refresh_credentials(sources.Credentials("at"), opener=_opener({}))


def test_a_token_error_body_is_a_woodshed_error_not_a_key_error() -> None:
    opener = _opener({"api/token": {"error": "invalid_grant",
                                     "error_description": "code expired"}})
    with pytest.raises(WoodshedError, match="code expired"):
        sources.exchange_code("c", "code", "v", "http://127.0.0.1/cb", opener=opener)


# ── credentials on disk ──────────────────────────────────────────────────


def test_credentials_live_outside_the_repo_and_never_in_config_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WOODSHED_HOME", str(tmp_path / "home"))
    path = sources.credentials_path()
    assert path.name == "credentials.json"
    saved = sources.save_credentials(
        sources.Credentials("at", "rt", 4600.0, "client-abc")
    )
    assert saved == path
    loaded = sources.load_credentials()
    assert loaded == sources.Credentials("at", "rt", 4600.0, "client-abc")


def test_missing_or_corrupt_credentials_read_as_not_connected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WOODSHED_HOME", str(tmp_path / "home"))
    assert sources.load_credentials() is None
    path = sources.credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json", encoding="utf-8")
    assert sources.load_credentials() is None


def test_saved_credentials_are_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WOODSHED_HOME", str(tmp_path / "home"))
    path = sources.save_credentials(sources.Credentials("at"))
    mode = path.stat().st_mode & 0o777
    assert mode in (0o600, 0o666)  # 0o666 only where chmod is a no-op (Windows)


# ── references ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp",
         ("track", "3n3Ppam7vgaVa1iaRUc9Lp")),
        ("spotify:album:1DFixLWuPkv3KT3TnV35m3", ("album", "1DFixLWuPkv3KT3TnV35m3")),
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x",
         ("playlist", "37i9dQZF1DXcBWIGoYBM5M")),
    ],
)
def test_parse_ref_handles_urls_uris_and_query_strings(text, expected) -> None:
    assert sources.parse_ref(text) == expected


def test_parse_ref_refuses_anything_else_with_a_next_step() -> None:
    with pytest.raises(WoodshedError, match="Copy link"):
        sources.parse_ref("https://example.com/not-spotify")


# ── search and fetch ─────────────────────────────────────────────────────


def _track_json(name="Can't Stop", ms=268000, album="By the Way"):
    return {
        "id": "abc123", "name": name, "duration_ms": ms, "track_number": 4,
        "artists": [{"name": "Red Hot Chili Peppers"}],
        "album": {"name": album, "images": [{"url": "https://img/1.jpg"}]},
    }


def test_search_returns_tracks_with_seconds_not_milliseconds() -> None:
    opener = _opener({"/search": {"tracks": {"items": [_track_json()]}}})
    creds = sources.Credentials("at")
    [track] = sources.search_tracks("cant stop", creds, opener=opener)
    assert track.title == "Can't Stop"
    assert track.artist == "Red Hot Chili Peppers"
    assert track.duration_s == 268.0
    # `library.slugify` turns an apostrophe into a separator (its own tests
    # fix that, and songs/can-t-stop exists on disk for real) -- see the
    # BACKLOG entry about CLAUDE.md printing `cant-stop` instead.
    assert track.slug == "can-t-stop"


def test_search_sends_the_token_as_a_bearer_header() -> None:
    opener = _opener({"/search": {"tracks": {"items": []}}})
    sources.search_tracks("x", sources.Credentials("secret-token"), opener=opener)
    # The token must never appear in a URL (they get logged; headers don't).
    url, _body = opener.seen[0]
    assert "secret-token" not in url


def test_fetch_album_fills_the_album_back_onto_every_track() -> None:
    """An album's own item list carries no `album` block. Without this,
    every imported album track would come out with album: null."""
    opener = _opener({"/albums/": {
        "name": "By the Way",
        "images": [{"url": "https://img/a.jpg"}],
        "tracks": {"items": [
            {"id": "t1", "name": "By the Way", "duration_ms": 217000,
             "artists": [{"name": "RHCP"}], "track_number": 1},
            {"id": "t2", "name": "Universally Speaking", "duration_ms": 256000,
             "artists": [{"name": "RHCP"}], "track_number": 2},
        ]},
    }})
    name, tracks = sources.fetch_ref("album", "xyz", sources.Credentials("at"), opener=opener)
    assert name == "By the Way"
    assert [t.album for t in tracks] == ["By the Way", "By the Way"]
    assert [t.artwork_url for t in tracks] == ["https://img/a.jpg"] * 2


def test_fetch_playlist_skips_a_removed_track_rather_than_crashing() -> None:
    opener = _opener({"/playlists/": {
        "name": "The set",
        "tracks": {"items": [{"track": _track_json()}, {"track": None}, None]},
    }})
    _name, tracks = sources.fetch_ref("playlist", "p1", sources.Credentials("at"), opener=opener)
    assert len(tracks) == 1


# ── import ───────────────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> Repo:
    for name in ("songs", "setlists", "practice"):
        (tmp_path / name).mkdir()
    return Repo(root=tmp_path)


def _track(title="Can't Stop", spotify_id="abc123"):
    return sources.Track(
        spotify_id=spotify_id, title=title, artist="RHCP",
        album="By the Way", duration_s=268.0,
    )


def test_import_writes_a_needs_audio_song_per_track(tmp_path) -> None:
    repo = _repo(tmp_path)
    result = sources.import_tracks(repo, [_track(), _track("Universally Speaking", "def")])
    assert result.created == ["can-t-stop", "universally-speaking"]

    song = load_song(repo.song_dir("can-t-stop") / "song.yaml")
    assert song.recording.spotify_id == "abc123"
    assert song.recording.duration_s == 268.0
    assert song.recording.sha256 == ""
    # The file it names does NOT exist -- which is exactly what every screen
    # reads as "needs audio" (docs/04-sources.md: a first-class state).
    assert not (repo.song_dir("can-t-stop") / song.recording.file).is_file()


def test_import_never_overwrites_a_song_already_on_disk(tmp_path) -> None:
    """Re-importing a playlist after binding half of it is normal, and must
    not touch the half that is bound."""
    repo = _repo(tmp_path)
    sources.import_tracks(repo, [_track()])
    path = repo.song_dir("can-t-stop") / "song.yaml"
    before = path.read_text(encoding="utf-8")

    result = sources.import_tracks(repo, [_track(spotify_id="different")])
    assert result.created == []
    assert result.already_present == ["can-t-stop"]
    assert path.read_text(encoding="utf-8") == before


def test_import_into_a_setlist_keeps_the_playlist_order(tmp_path) -> None:
    from woodshed import setlist as setlist_module

    repo = _repo(tmp_path)
    setlist_module.save(repo, "gig", Setlist(name="The Gig", tuning="E standard"))
    sources.import_tracks(
        repo,
        [_track("Third", "c"), _track("First", "a"), _track("Second", "b")],
        setlist_slug="gig",
    )
    saved = setlist_module.load(repo, "gig")
    assert [e.slug for e in saved.songs] == ["third", "first", "second"]


# ── tags ─────────────────────────────────────────────────────────────────


def _flac_with_tags(path: Path, **tags: str) -> Path:
    entries = [f"{k.upper()}={v}".encode() for k, v in tags.items()]
    body = (
        len(b"woodshed").to_bytes(4, "little") + b"woodshed"
        + len(entries).to_bytes(4, "little")
        + b"".join(len(e).to_bytes(4, "little") + e for e in entries)
    )
    # One STREAMINFO-shaped block first, so the reader has to actually walk
    # the block list rather than assuming the comment is first.
    stream_info = b"\x00" + (34).to_bytes(3, "big") + b"\x00" * 34
    comment = bytes([0x80 | 4]) + len(body).to_bytes(3, "big") + body
    path.write_bytes(b"fLaC" + stream_info + comment)
    return path


def _mp3_with_id3(path: Path, **tags: str) -> Path:
    frames = b""
    ids = {"title": b"TIT2", "artist": b"TPE1", "album": b"TALB"}
    for key, value in tags.items():
        payload = b"\x03" + value.encode("utf-8")  # 0x03 = UTF-8
        frames += ids[key] + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload
    size = len(frames)
    syncsafe = bytes([
        (size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F,
    ])
    path.write_bytes(b"ID3" + b"\x03\x00" + b"\x00" + syncsafe + frames)
    return path


def test_read_tags_from_flac_walks_past_other_metadata_blocks(tmp_path) -> None:
    path = _flac_with_tags(tmp_path / "x.flac", title="Can't Stop", artist="RHCP")
    assert sources.read_tags(path) == {"title": "Can't Stop", "artist": "RHCP"}


def test_read_tags_from_id3v23(tmp_path) -> None:
    path = _mp3_with_id3(tmp_path / "x.mp3", title="Sultans of Swing", artist="Dire Straits")
    tags = sources.read_tags(path)
    assert tags["title"] == "Sultans of Swing"
    assert tags["artist"] == "Dire Straits"


def test_read_tags_of_something_unreadable_is_empty_not_an_exception(tmp_path) -> None:
    junk = tmp_path / "x.wav"
    junk.write_bytes(b"RIFF....WAVEfmt ")
    assert sources.read_tags(junk) == {}
    assert sources.read_tags(tmp_path / "no-such-file.flac") == {}


# ── the scan ─────────────────────────────────────────────────────────────


def test_scan_prefers_tags_and_says_so(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _flac_with_tags(library / "track01.flac", title="Can't Stop", artist="RHCP")
    [candidate] = sources.scan_library([library], title="Can't Stop", artist="RHCP")
    assert candidate.why == "tags"
    assert candidate.score == 1.0


def test_scan_falls_back_to_the_filename_when_there_are_no_tags(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "RHCP - Cant Stop.mp3").write_bytes(b"not really an mp3")
    [candidate] = sources.scan_library([library], title="Can't Stop", artist="RHCP")
    assert candidate.why == "filename"
    assert candidate.score > 0.5


def test_scan_ranks_the_better_match_first_and_caps_the_list(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "cant stop rhcp.flac").write_bytes(b"x")
    (library / "cant stop the music.flac").write_bytes(b"x")
    (library / "unrelated song.flac").write_bytes(b"x")
    results = sources.scan_library([library], title="Can't Stop", artist="RHCP", limit=2)
    assert len(results) == 2
    assert results[0].path.name == "cant stop rhcp.flac"
    assert all("unrelated" not in c.path.name for c in results)


def test_scan_ignores_non_audio_files_and_missing_paths(tmp_path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "Cant Stop.txt").write_text("not audio", encoding="utf-8")
    results = sources.scan_library(
        [library, tmp_path / "an-unplugged-drive"], title="Can't Stop"
    )
    assert results == []


def test_scan_never_binds_anything(tmp_path) -> None:
    """The whole point of M2's rule: candidates are SHOWN, never applied.
    A fuzzy match that binds itself is how a setlist ends up practising the
    wrong recording without anyone noticing."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "cant stop.flac").write_bytes(b"x")
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    sources.scan_library([library], title="Can't Stop")
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before
