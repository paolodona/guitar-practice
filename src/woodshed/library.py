"""Repo layout and slugs.

``find_root`` and ``slugify`` are lifted from
``rambass-live/src/rambass/project.py`` (``find_root`` around line 67,
``slugify`` around line 48) and adapted so refusals raise
:class:`woodshed.errors.WoodshedError` instead of that repo's
``ProjectError``.

Stdlib only, no third-party imports here -- accent folding is a plain
string-translation table, not a library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from woodshed.errors import WoodshedError

#: Marker files that identify the repository root.
ROOT_MARKERS = ("pyproject.toml", "woodshed.toml", ".git")


def slugify(text: str) -> str:
    """Turn a song or setlist title into a filesystem-safe slug.

    Lifted from rambass-live/src/rambass/project.py:48. Accented characters
    are folded down to ASCII rather than dropped: ``"Perché No"`` ->
    ``"perche-no"``.
    """
    folds = {
        "à": "a", "á": "a", "â": "a", "ä": "a", "è": "e", "é": "e", "ê": "e",
        "ë": "e", "ì": "i", "í": "i", "î": "i", "ï": "i", "ò": "o", "ó": "o",
        "ô": "o", "ö": "o", "ù": "u", "ú": "u", "û": "u", "ü": "u", "ç": "c",
        "ñ": "n", "'": " ", "’": " ",
    }
    out = text.strip().lower()
    for src, dst in folds.items():
        out = out.replace(src, dst)
    out = re.sub(r"[^a-z0-9]+", "-", out)
    return out.strip("-")


def find_root(start: Path | None = None) -> Path:
    """Walk upwards from *start* looking for the repository root.

    Lifted from rambass-live/src/rambass/project.py:67, adapted to raise
    WoodshedError and to this repo's ROOT_MARKERS.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    raise WoodshedError(
        f"could not find the woodshed repository root above {here}. "
        "Run the command from inside the checkout."
    )


@dataclass(frozen=True)
class Repo:
    """Resolved paths for one checkout of the repository."""

    root: Path

    # ── top-level directories ────────────────────────────────────────────
    @property
    def songs_dir(self) -> Path:
        return self.root / "songs"

    @property
    def setlists_dir(self) -> Path:
        return self.root / "setlists"

    @property
    def practice_dir(self) -> Path:
        return self.root / "practice"

    @property
    def capture_dir(self) -> Path:
        # Phase 1.5, Group U: CLAUDE.md's fourth server-write category --
        # raw, unrepeatable capture recordings, kept until every segment
        # split from one has been bound to a song or discarded. Not cache;
        # never evicted the way songs/*/cache/ is.
        return self.root / "capture"

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def web_dir(self) -> Path:
        return self.root / "web"

    # ── per-song directories ─────────────────────────────────────────────
    def song_dir(self, slug: str) -> Path:
        return self.songs_dir / slug

    def audio_dir(self, slug: str) -> Path:
        return self.song_dir(slug) / "audio"

    def cache_dir(self, slug: str) -> Path:
        return self.song_dir(slug) / "cache"

    # ── the ledger ────────────────────────────────────────────────────────
    def ledger_path(self) -> Path:
        return self.practice_dir / "reps.jsonl"

    # ── listing and lookup ───────────────────────────────────────────────
    def list_songs(self) -> list[str]:
        """Slugs of every song directory under songs_dir, sorted.

        A directory counts as a song only once it has a song.yaml -- a bare
        folder (mid-import, or the scratch scaffold for a future song) is
        not yet a song the rest of the tool can see.
        """
        if not self.songs_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self.songs_dir.iterdir()
            if p.is_dir() and not p.name.startswith((".", "_"))
            and (p / "song.yaml").is_file()
        )

    def list_setlists(self) -> list[str]:
        """Slugs (filenames without extension) of every setlists/*.yaml."""
        if not self.setlists_dir.is_dir():
            return []
        return sorted(
            p.stem
            for p in self.setlists_dir.iterdir()
            if p.is_file() and p.suffix == ".yaml"
        )

    def find_song(self, needle: str) -> str:
        """Resolve *needle* to a song slug.

        *needle* may be an exact slug, or a title-ish string that slugifies
        to one -- case-insensitive, accent-folded, punctuation-insensitive.
        Raises WoodshedError naming the candidates when more than one song
        matches, or when none does.
        """
        slugs = self.list_songs()
        if needle in slugs:
            return needle

        wanted = slugify(needle)
        if wanted in slugs:
            return wanted

        candidates = sorted(
            s for s in slugs if wanted and (wanted in s or s in wanted)
        )
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise WoodshedError(
                f"{needle!r} is ambiguous -- matches: {', '.join(candidates)}"
            )
        known = ", ".join(slugs) if slugs else "(none)"
        raise WoodshedError(f"no song matching {needle!r}. Known songs: {known}")
