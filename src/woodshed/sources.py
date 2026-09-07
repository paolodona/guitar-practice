"""Where songs come from before there is any audio: Spotify's *list*, and
the local library scan (`docs/04-sources.md`, plan Phase 3 Group M).

Two separate questions, kept apart here exactly as that document keeps them
apart:

1. **Which song is this?** — title, artist, album, duration, order in a
   playlist. Spotify answers this well; `import_tracks` turns the answer
   into songs and (optionally) a setlist, every one of them badged
   **needs-audio**.
2. **Where do the samples come from?** — Spotify cannot answer this, ever,
   and nothing in this module tries. There is no download path here, no
   Web Playback SDK, no DRM anything. `capture.py` records an audio DEVICE
   and knows nothing about any service; the library scan below finds files
   that are already yours.

**Layering.** stdlib + `manifest`/`library`/`setlist` only: `urllib.request`
for HTTP, `hashlib`/`base64`/`secrets` for PKCE, a small pure-Python tag
reader for FLAC and MP3. No `requests`, no `spotipy`, no `mutagen`, and no
subprocess — CLAUDE.md names `analyze.py`, `render.py` and `separate.py` as
the only modules allowed a heavy or external dependency, and this is not one
of them.

**The token is never in `config.yaml`.** That file is tracked in git.
`config.spotify.client_id` is a public identifier and lives there; the
access and refresh tokens live in `~/.woodshed/credentials.json`, written
0600. PKCE (RFC 7636) is what lets a desktop app do this with no client
secret at all — there is no secret to leak because there never was one.

**Every network call takes an `opener`.** Default `urllib.request.urlopen`;
the tests pass a fake. Nothing in this module's test suite touches the
network, and nothing in it requires a real token to be exercised.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from woodshed.errors import WoodshedError
from woodshed.library import Repo, slugify
from woodshed.manifest import Recording, Song, save_song

__all__ = [
    "AUTH_URL",
    "TOKEN_URL",
    "API_BASE",
    "Credentials",
    "Track",
    "Candidate",
    "credentials_path",
    "load_credentials",
    "save_credentials",
    "new_pkce",
    "authorize_url",
    "exchange_code",
    "refresh_credentials",
    "parse_ref",
    "search_tracks",
    "fetch_ref",
    "song_from_track",
    "import_tracks",
    "read_tags",
    "scan_library",
]

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

#: The only scope any of this needs. Reading a playlist you can see is the
#: whole job; nothing here modifies anything in a Spotify account.
DEFAULT_SCOPES = ("playlist-read-private",)

#: Audio extensions the scan will offer as candidates -- docs/04-sources.md's
#: own list of what a source file may be.
AUDIO_SUFFIXES = (".flac", ".wav", ".mp3", ".m4a", ".aiff", ".aif", ".ogg", ".opus")


# ── credentials ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Credentials:
    """A Spotify token pair. Never written to `config.yaml`."""

    access_token: str
    refresh_token: str | None = None
    #: Unix seconds. Compared against `time.time()`, never against a
    #: formatted string -- a clock is a number here.
    expires_at: float = 0.0
    client_id: str | None = None

    def is_fresh(self, *, now: float | None = None, margin_s: float = 60.0) -> bool:
        """Whether the access token is still usable, with a minute of slack
        so a request never leaves on a token that expires mid-flight."""
        return (now if now is not None else time.time()) + margin_s < self.expires_at


def credentials_path() -> Path:
    """`~/.woodshed/credentials.json`, or `$WOODSHED_HOME/credentials.json`.

    The env override exists so a test never writes to a real home directory
    -- not as a feature anyone is expected to use.
    """
    home = os.environ.get("WOODSHED_HOME")
    base = Path(home) if home else Path.home() / ".woodshed"
    return base / "credentials.json"


def load_credentials(path: Path | None = None) -> Credentials | None:
    """The stored credentials, or `None` when there are none.

    A corrupt file is `None` too, not an exception: the answer to "am I
    connected to Spotify" is no, and the fix (connect again) is the same
    either way.
    """
    path = path or credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return Credentials(
        access_token=str(data["access_token"]),
        refresh_token=data.get("refresh_token"),
        expires_at=float(data.get("expires_at", 0.0)),
        client_id=data.get("client_id"),
    )


def save_credentials(creds: Credentials, path: Path | None = None) -> Path:
    """Write the token pair, owner-readable only."""
    path = path or credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": creds.access_token,
                "refresh_token": creds.refresh_token,
                "expires_at": creds.expires_at,
                "client_id": creds.client_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        # Windows and some filesystems have no such mode; the file is still
        # written, and saying so loudly here would be noise.
        pass
    return path


# ── PKCE (RFC 7636) ───────────────────────────────────────────────────────


def new_pkce() -> tuple[str, str]:
    """A fresh `(verifier, challenge)` pair.

    The verifier is 64 URL-safe characters of real entropy; the challenge is
    `base64url(sha256(verifier))` with the padding stripped, which is what
    S256 means. This is the whole reason a desktop app needs no client
    secret: the secret is generated per authorisation and never stored.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorize_url(
    client_id: str,
    redirect_uri: str,
    challenge: str,
    *,
    scopes: Sequence[str] = DEFAULT_SCOPES,
    state: str = "",
) -> str:
    """The URL a human opens in a browser to authorise this app."""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": " ".join(scopes),
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    opener=None,
    now: float | None = None,
) -> Credentials:
    """Trade an authorisation code for a token pair."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    return _token_request(payload, client_id=client_id, opener=opener, now=now)


def refresh_credentials(
    creds: Credentials, *, opener=None, now: float | None = None
) -> Credentials:
    """A new access token from the refresh token.

    Spotify does not always return a new refresh token; when it does not,
    the existing one is kept rather than dropped, which is the difference
    between a session that lasts and one that silently logs you out.
    """
    if not creds.refresh_token or not creds.client_id:
        raise WoodshedError(
            "no refresh token stored -- connect Spotify again "
            "(`woodshed import --connect`)"
        )
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
    }
    fresh = _token_request(payload, client_id=creds.client_id, opener=opener, now=now)
    if fresh.refresh_token:
        return fresh
    return Credentials(
        access_token=fresh.access_token,
        refresh_token=creds.refresh_token,
        expires_at=fresh.expires_at,
        client_id=creds.client_id,
    )


def _token_request(payload: dict, *, client_id: str, opener=None, now: float | None = None):
    body = urllib.parse.urlencode(payload).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    data = _read_json(request, opener=opener, what="the Spotify token endpoint")
    if "access_token" not in data:
        raise WoodshedError(
            f"Spotify refused the token request: {data.get('error_description') or data}"
        )
    issued = now if now is not None else time.time()
    return Credentials(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=issued + float(data.get("expires_in", 3600)),
        client_id=client_id,
    )


def _read_json(request, *, opener=None, what: str):
    opener = opener or urllib.request.urlopen
    try:
        with opener(request) as response:
            raw = response.read()
    except OSError as exc:
        # Every network failure reads the same to a human: it did not work,
        # and here is what was being asked for.
        raise WoodshedError(f"could not reach {what}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise WoodshedError(f"{what} answered something that is not JSON") from exc


# ── references, search, fetch ─────────────────────────────────────────────

_REF_RE = re.compile(
    r"(?:spotify[:/])?(?P<kind>track|album|playlist)[:/](?P<id>[A-Za-z0-9]{10,})"
)


def parse_ref(text: str) -> tuple[str, str]:
    """`("track" | "album" | "playlist", id)` from a URL, a URI or a bare
    `kind:id` — the three shapes a person actually has in the clipboard."""
    match = _REF_RE.search(text.strip())
    if match is None:
        raise WoodshedError(
            f"not a Spotify track/album/playlist link: {text!r}\n"
            "  paste the 'Copy link to ...' URL, or a spotify:track:... URI"
        )
    return match.group("kind"), match.group("id")


@dataclass(frozen=True)
class Track:
    """One song as Spotify describes it. No audio, ever — see the module doc."""

    spotify_id: str
    title: str
    artist: str
    album: str | None
    duration_s: float
    track_number: int | None = None
    artwork_url: str | None = None

    @property
    def slug(self) -> str:
        return slugify(self.title)


def _api_get(path: str, creds: Credentials, *, opener=None, params: dict | None = None):
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {creds.access_token}"}
    )
    return _read_json(request, opener=opener, what=f"the Spotify API ({path})")


def _track_from_json(data: dict) -> Track:
    artists = data.get("artists") or []
    album = data.get("album") or {}
    images = album.get("images") or []
    return Track(
        spotify_id=data.get("id") or "",
        title=data.get("name") or "",
        artist=", ".join(a.get("name", "") for a in artists if a.get("name")),
        album=album.get("name"),
        duration_s=float(data.get("duration_ms") or 0) / 1000.0,
        track_number=data.get("track_number"),
        artwork_url=images[0]["url"] if images and images[0].get("url") else None,
    )


def search_tracks(query: str, creds: Credentials, *, limit: int = 10, opener=None) -> list[Track]:
    """Search Spotify's catalogue for tracks matching *query*."""
    data = _api_get(
        "/search", creds, opener=opener,
        params={"q": query, "type": "track", "limit": limit},
    )
    items = ((data.get("tracks") or {}).get("items")) or []
    return [_track_from_json(item) for item in items if item]


def fetch_ref(
    kind: str, spotify_id: str, creds: Credentials, *, opener=None
) -> tuple[str, list[Track]]:
    """`(name, tracks)` for a track, album or playlist reference.

    An album's own items carry no `album` block (it would be the album you
    just asked for), so it is filled back in here — otherwise every imported
    album track would come out with `album: null`, which is exactly the kind
    of quietly-missing metadata this import exists to avoid.
    """
    if kind == "track":
        data = _api_get(f"/tracks/{spotify_id}", creds, opener=opener)
        track = _track_from_json(data)
        return track.title, [track]

    if kind == "album":
        album = _api_get(f"/albums/{spotify_id}", creds, opener=opener)
        items = ((album.get("tracks") or {}).get("items")) or []
        tracks = []
        for item in items:
            merged = {**item, "album": {"name": album.get("name"),
                                        "images": album.get("images") or []}}
            tracks.append(_track_from_json(merged))
        return album.get("name") or spotify_id, tracks

    if kind == "playlist":
        playlist = _api_get(f"/playlists/{spotify_id}", creds, opener=opener)
        items = ((playlist.get("tracks") or {}).get("items")) or []
        tracks = [
            _track_from_json(item["track"])
            for item in items
            if item and item.get("track")
        ]
        return playlist.get("name") or spotify_id, tracks

    raise WoodshedError(f"unknown Spotify reference kind: {kind!r}")


# ── import: tracks -> songs (badged needs-audio) ──────────────────────────


def song_from_track(track: Track, *, tuning: str = "E standard", slug: str | None = None) -> Song:
    """A `Song` for a track with no audio yet.

    `recording.file` names where the audio WILL live and `sha256` is empty,
    which is precisely what "needs-audio" means on disk: the song exists,
    the file does not, and every screen that asks
    `(song_dir / recording.file).is_file()` gets a truthful `False`.
    docs/04-sources.md is explicit that this is a first-class state and must
    be visible, not an error — "silence about a gap is how a setlist quietly
    turns out to be half practisable the week before the gig".
    """
    slug = slug or track.slug
    return Song(
        slug=slug,
        title=track.title,
        artist=track.artist,
        album=track.album,
        recording=Recording(
            file=f"audio/{slug}.flac",
            sha256="",
            duration_s=track.duration_s,
            tuning=tuning,
            spotify_id=track.spotify_id or None,
            source="spotify (metadata only -- no audio bound yet)",
        ),
    )


@dataclass
class ImportResult:
    """What an import actually did, so a caller can say so precisely."""

    created: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    setlist: str | None = None


def import_tracks(
    repo: Repo,
    tracks: Iterable[Track],
    *,
    tuning: str = "E standard",
    setlist_slug: str | None = None,
) -> ImportResult:
    """Write one needs-audio `song.yaml` per track, and optionally add them
    all to a setlist, in order.

    A slug that already exists is left completely alone — never overwritten,
    never suffixed. Re-importing the same playlist after binding half of it
    is a normal thing to do, and it must not touch the half that is bound.
    """
    from woodshed import setlist as setlist_module

    result = ImportResult(setlist=setlist_slug)
    for track in tracks:
        if not track.title:
            continue
        slug = track.slug
        song_path = repo.song_dir(slug) / "song.yaml"
        if song_path.is_file():
            result.already_present.append(slug)
            continue
        song = song_from_track(track, tuning=tuning, slug=slug)
        song_path.parent.mkdir(parents=True, exist_ok=True)
        save_song(song, song_path)
        result.created.append(slug)

    if setlist_slug:
        current = setlist_module.load(repo, setlist_slug)
        for slug in [*result.created, *result.already_present]:
            if any(entry.slug == slug for entry in current.songs):
                continue
            current = setlist_module.add_song(current, slug)
        setlist_module.save(repo, setlist_slug, current)
    return result


# ── M2: the library scan ──────────────────────────────────────────────────
#
# "Scan `config.library_paths` (tags first, then filename, candidates shown
# -- never bind automatically on a fuzzy match)" -- docs/04-sources.md.
#
# The tag reader below is deliberately small and pure stdlib: FLAC's Vorbis
# comment block and MP3's ID3v2 text frames, which between them cover the
# formats a ripped or bought library is actually in. Anything else answers
# `{}` and the scan falls back to the filename, which is the honest
# degrade -- adding `mutagen` for the remainder would put a dependency
# below CLAUDE.md's line for a match that is only ever a SUGGESTION anyway.


def read_tags(path: Path) -> dict[str, str]:
    """`{"title": ..., "artist": ..., "album": ...}` where readable, else `{}`.

    Never raises: an unreadable or unrecognised file is "no tags", and the
    caller falls back to the filename. A scan over a few thousand files
    must not die on one truncated download.
    """
    try:
        with path.open("rb") as f:
            head = f.read(4)
            if head == b"fLaC":
                return _flac_tags(f)
            if head[:3] == b"ID3":
                return _id3_tags(f, head)
    except OSError:
        return {}
    return {}


def _flac_tags(f) -> dict[str, str]:
    """Vorbis comments out of a FLAC metadata block (type 4)."""
    tags: dict[str, str] = {}
    while True:
        header = f.read(4)
        if len(header) < 4:
            return tags
        last = bool(header[0] & 0x80)
        block_type = header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        payload = f.read(length)
        if block_type == 4:
            tags.update(_parse_vorbis_comment(payload))
            return tags
        if last:
            return tags


def _parse_vorbis_comment(payload: bytes) -> dict[str, str]:
    tags: dict[str, str] = {}
    try:
        offset = 0
        vendor_len = int.from_bytes(payload[offset:offset + 4], "little")
        offset += 4 + vendor_len
        count = int.from_bytes(payload[offset:offset + 4], "little")
        offset += 4
        for _ in range(count):
            # Bounded by the payload, not by the count: that count is a
            # 32-bit field read straight off the file, and an out-of-range
            # slice below returns empty bytes rather than raising -- so a
            # corrupt or truncated FLAC claiming four billion comments span
            # four billion times and the scan looked like it had hung
            # (found by review 2026-09-07).
            if offset + 4 > len(payload):
                break
            size = int.from_bytes(payload[offset:offset + 4], "little")
            offset += 4
            entry = payload[offset:offset + size].decode("utf-8", "replace")
            offset += size
            key, _, value = entry.partition("=")
            key = key.strip().lower()
            if key in ("title", "artist", "album") and value:
                tags.setdefault(key, value)
    except (IndexError, ValueError):
        return tags
    return tags


#: ID3v2 text frames worth reading -- title, artist, album, and nothing else.
_ID3_FRAMES = {b"TIT2": "title", b"TPE1": "artist", b"TALB": "album"}


def _id3_tags(f, head: bytes) -> dict[str, str]:
    rest = f.read(6)
    if len(rest) < 6:
        return {}
    version = head[3] if len(head) > 3 else 0
    size = _syncsafe(rest[2:6])
    body = f.read(size)
    tags: dict[str, str] = {}
    offset = 0
    while offset + 10 <= len(body):
        frame_id = body[offset:offset + 4]
        if not frame_id.strip(b"\x00"):
            break
        raw_size = body[offset + 4:offset + 8]
        # v2.4 sizes are syncsafe; v2.3's are plain big-endian. Getting this
        # wrong walks off the end of the frame table and silently returns
        # nothing, so the version byte is read rather than assumed.
        frame_size = _syncsafe(raw_size) if version >= 4 else int.from_bytes(raw_size, "big")
        offset += 10
        payload = body[offset:offset + frame_size]
        offset += frame_size
        name = _ID3_FRAMES.get(frame_id)
        if name and payload:
            tags.setdefault(name, _decode_id3_text(payload))
    return {k: v for k, v in tags.items() if v}


def _syncsafe(raw: bytes) -> int:
    value = 0
    for byte in raw:
        value = (value << 7) | (byte & 0x7F)
    return value


def _decode_id3_text(payload: bytes) -> str:
    encodings = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    encoding = encodings.get(payload[0], "latin-1")
    return payload[1:].decode(encoding, "replace").strip("\x00").strip()


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Word tokens for MATCHING -- not a slug.

    Apostrophes are removed here rather than turned into a separator the way
    `library.slugify` turns them (deliberately: see that function and its
    tests). For matching, "Can\'t Stop" and a ripped file called "Cant Stop"
    must produce the same tokens, or the one file you actually own scores
    0.4 against the song it IS. Slug policy is untouched by this; only what
    the scan compares.
    """
    cleaned = (text or "").replace("'", "").replace("\u2019", "")
    return set(_TOKEN_RE.findall(slugify(cleaned).replace("-", " ")))


def _similarity(wanted: set[str], found: set[str]) -> float:
    """Jaccard over word tokens: 1.0 for the same words in any order.

    Deliberately crude, and deliberately NOT a fuzzy character-level score.
    Every result of this function is shown to a human as a suggestion; a
    cleverer number would only make a wrong match look more convincing.
    """
    if not wanted or not found:
        return 0.0
    return len(wanted & found) / len(wanted | found)


@dataclass(frozen=True)
class Candidate:
    """One file that might be a song's audio. A suggestion, never a binding."""

    path: Path
    score: float
    #: "tags" or "filename" -- which evidence produced the score, so the UI
    #: can say why this file is being offered.
    why: str
    tags: dict[str, str] = field(default_factory=dict)


def scan_library(
    paths: Iterable[str | Path],
    *,
    title: str,
    artist: str = "",
    limit: int = 5,
    min_score: float = 0.34,
) -> list[Candidate]:
    """Rank audio files under *paths* against `title`/`artist`.

    **Tags first, then filename** (docs/04-sources.md): a file whose tags
    match scores on its tags and says so; one with no readable tags is
    scored on its filename and stem path instead. The result is a ranked
    list of candidates and nothing else — **this function never binds
    anything**, because a fuzzy match that binds itself is how a setlist
    ends up practising the wrong recording without anyone noticing.
    """
    wanted = _tokens(f"{title} {artist}")
    results: list[Candidate] = []
    for root in paths:
        root_path = Path(root).expanduser()
        if not root_path.is_dir():
            # A library path that has gone away (an unplugged drive) is not
            # an error -- the other paths still scan.
            continue
        for path in sorted(root_path.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            tags = read_tags(path)
            tag_score = _similarity(
                wanted, _tokens(f"{tags.get('title', '')} {tags.get('artist', '')}")
            )
            name_score = _similarity(wanted, _tokens(path.stem))
            if tag_score >= name_score and tag_score > 0:
                results.append(Candidate(path=path, score=tag_score, why="tags", tags=tags))
            elif name_score > 0:
                results.append(Candidate(path=path, score=name_score, why="filename", tags=tags))
    results = [c for c in results if c.score >= min_score]
    results.sort(key=lambda c: (-c.score, str(c.path)))
    return results[:limit]
