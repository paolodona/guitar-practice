"""CRUD over `setlists/<slug>.yaml`, plus `effective_shift` -- CLAUDE.md
invariant 4: transpose is per song, the setlist only supplies a default.

The `Setlist`/`SetlistEntry` *models* and `load_setlist`/`save_setlist`
already live in `manifest.py` (built in Phase 0's B6, ahead of this unit --
the plan's "Before starting" note for this phase said to expect exactly
this kind of drift). This module is the operations layer above them: it
owns creating/listing/deleting a setlist file, adding or removing a song
from one, and the shift arithmetic itself -- everything `POST /api/shift`
and `GET /api/setlist/<slug>` (both this unit, F1) need and nothing
`manifest.py` already provides.
"""

from __future__ import annotations

from pathlib import Path

from woodshed import tuning
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import Setlist, SetlistEntry, load_setlist, save_setlist

__all__ = [
    "list_setlists",
    "load",
    "save",
    "create",
    "delete",
    "add_song",
    "remove_song",
    "effective_shift",
    "set_shift",
]


def _path(repo: Repo, slug: str) -> Path:
    return repo.setlists_dir / f"{slug}.yaml"


def load(repo: Repo, slug: str) -> Setlist:
    """Load one setlist by slug. Raises WoodshedError if it doesn't exist."""
    path = _path(repo, slug)
    if not path.is_file():
        raise WoodshedError(f"no such setlist: {slug!r}")
    return load_setlist(path)


def save(repo: Repo, slug: str, setlist: Setlist) -> None:
    """Write *setlist* to `setlists/<slug>.yaml`, creating the directory if
    this is the first setlist. Overwrites an existing file at that slug --
    callers wanting "must not already exist" use `create` instead."""
    repo.setlists_dir.mkdir(parents=True, exist_ok=True)
    save_setlist(setlist, _path(repo, slug))


def create(repo: Repo, slug: str, setlist: Setlist) -> Setlist:
    """Like `save`, but refuses to overwrite an existing setlist file."""
    if _path(repo, slug).exists():
        raise WoodshedError(f"setlist {slug!r} already exists")
    save(repo, slug, setlist)
    return setlist


def delete(repo: Repo, slug: str) -> None:
    """Remove a setlist file. Raises WoodshedError if it doesn't exist."""
    path = _path(repo, slug)
    if not path.is_file():
        raise WoodshedError(f"no such setlist: {slug!r}")
    path.unlink()


def list_setlists(repo: Repo) -> list[Setlist]:
    """Every setlist under `repo.setlists_dir`, loaded, slug-sorted."""
    return [load(repo, slug) for slug in repo.list_setlists()]


def add_song(setlist: Setlist, song_slug: str, shift: int | None = None) -> Setlist:
    """A new Setlist with *song_slug* appended. Pure -- *setlist* is
    untouched; the caller still owns writing the result to disk (via
    `save`). Refuses a slug already present rather than silently
    duplicating a song in its own running order."""
    if any(entry.slug == song_slug for entry in setlist.songs):
        raise WoodshedError(f"{song_slug!r} is already in this setlist")
    entry = SetlistEntry(slug=song_slug, shift=shift)
    return setlist.model_copy(update={"songs": [*setlist.songs, entry]})


def remove_song(setlist: Setlist, song_slug: str) -> Setlist:
    """A new Setlist with *song_slug*'s entry dropped. Pure; raises if the
    song isn't in this setlist -- silently no-op-ing a typo'd slug would
    hide the mistake."""
    if not any(entry.slug == song_slug for entry in setlist.songs):
        raise WoodshedError(f"{song_slug!r} is not in this setlist")
    return setlist.model_copy(update={
        "songs": [entry for entry in setlist.songs if entry.slug != song_slug]
    })


def effective_shift(setlist: Setlist, entry: SetlistEntry, song) -> int:
    """`entry.shift` if the entry declares one, else the derived default
    (`pitch_of(setlist.tuning) - pitch_of(song.recording.tuning)`); always
    clamped to +/-`tuning.MAX_SHIFT`.

    *song* needs only `.recording.tuning` -- typed loosely (not
    `manifest.Song`) so a caller building a lightweight stand-in for a
    needs-audio song doesn't need a full model.
    """
    if entry.shift is not None:
        return _clamp_only(entry.shift)
    return _clamp_only(tuning.default_shift(setlist.tuning, song.recording.tuning))


def _clamp_only(semitones: int) -> int:
    """`tuning.clamp_shift` returns a float-safe int range; re-exposed here
    (still just that function) so effective_shift's docstring above doesn't
    have to explain two module names for the same clamp."""
    return tuning.clamp_shift(semitones)


def set_shift(repo: Repo, setlist_slug: str, song_slug: str, shift: int | None) -> Setlist:
    """Write `setlist.songs[].shift` for *song_slug* within *setlist_slug*'s
    file. `shift=None` clears an override back to "derive". The write path
    `POST /api/shift` uses; raises WoodshedError if *song_slug* is not in
    this setlist. Range validation (`+/-MAX_SHIFT`) happens inside
    `SetlistEntry` itself when the updated setlist is constructed below --
    the same validator a hand-edited `setlist.yaml` goes through, per
    manifest.py's "a range enforced in only one of several write paths is
    not enforced."
    """
    setlist = load(repo, setlist_slug)
    entries = list(setlist.songs)
    for index, entry in enumerate(entries):
        if entry.slug == song_slug:
            entries[index] = SetlistEntry(slug=song_slug, shift=shift)
            break
    else:
        raise WoodshedError(f"{song_slug!r} is not in setlist {setlist_slug!r}")
    updated = setlist.model_copy(update={"songs": entries})
    save(repo, setlist_slug, updated)
    return updated
