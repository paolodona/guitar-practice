"""Tests for woodshed.setlist: CRUD over setlists/<slug>.yaml, plus
effective_shift() (CLAUDE.md invariant 4).

The Setlist/SetlistEntry *models* already live in manifest.py (built in
Phase 0's B6, ahead of this unit) -- this module is the operations layer
over them: create/load/save/delete a setlist file, add/remove a song, and
the shift arithmetic itself.
"""

from __future__ import annotations

import pytest

from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import Recording, Setlist, SetlistEntry, Song
from woodshed.setlist import (
    add_song,
    create,
    delete,
    effective_shift,
    list_setlists,
    load,
    remove_song,
    reorder,
    save,
    set_shift,
)


@pytest.fixture
def repo(tmp_path) -> Repo:
    r = Repo(root=tmp_path)
    r.setlists_dir.mkdir(parents=True)
    return r


def _song(slug: str, tuning: str) -> Song:
    return Song(
        slug=slug, title="T", artist="A",
        recording=Recording(file="audio/x.flac", sha256="a" * 64, duration_s=10.0, tuning=tuning),
    )


# ── CRUD ─────────────────────────────────────────────────────────────────


def test_create_then_load_round_trips(repo) -> None:
    setlist = Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug="cant-stop")])
    create(repo, "gig", setlist)
    loaded = load(repo, "gig")
    assert loaded.name == "Gig"
    assert loaded.songs[0].slug == "cant-stop"


def test_create_refuses_an_existing_slug(repo) -> None:
    setlist = Setlist(name="Gig", tuning="E standard")
    create(repo, "gig", setlist)
    with pytest.raises(WoodshedError):
        create(repo, "gig", setlist)


def test_load_unknown_slug_raises(repo) -> None:
    with pytest.raises(WoodshedError):
        load(repo, "no-such-setlist")


def test_list_setlists_returns_every_setlist_loaded(repo) -> None:
    save(repo, "gig", Setlist(name="Gig", tuning="E standard"))
    save(repo, "duo", Setlist(name="Duo", tuning="D standard"))
    names = sorted(s.name for s in list_setlists(repo))
    assert names == ["Duo", "Gig"]


def test_delete_removes_the_file(repo) -> None:
    save(repo, "gig", Setlist(name="Gig", tuning="E standard"))
    delete(repo, "gig")
    with pytest.raises(WoodshedError):
        load(repo, "gig")


def test_delete_unknown_slug_raises(repo) -> None:
    with pytest.raises(WoodshedError):
        delete(repo, "no-such-setlist")


# ── add_song / remove_song (pure, operate on a Setlist in memory) ────────


def test_add_song_appends_an_entry() -> None:
    setlist = Setlist(name="Gig", tuning="E standard")
    updated = add_song(setlist, "cant-stop", shift=-1)
    assert [e.slug for e in updated.songs] == ["cant-stop"]
    assert updated.songs[0].shift == -1
    assert setlist.songs == []  # the input is untouched


def test_add_song_refuses_a_duplicate() -> None:
    setlist = Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="cant-stop")])
    with pytest.raises(WoodshedError):
        add_song(setlist, "cant-stop")


def test_remove_song_drops_the_entry() -> None:
    setlist = Setlist(
        name="Gig", tuning="E standard",
        songs=[SetlistEntry(slug="cant-stop"), SetlistEntry(slug="plush")],
    )
    updated = remove_song(setlist, "plush")
    assert [e.slug for e in updated.songs] == ["cant-stop"]


def test_remove_song_unknown_slug_raises() -> None:
    setlist = Setlist(name="Gig", tuning="E standard")
    with pytest.raises(WoodshedError):
        remove_song(setlist, "no-such-song")


# ── effective_shift() -- invariant 4's arithmetic ────────────────────────


def test_effective_shift_derives_when_entry_has_none() -> None:
    setlist = Setlist(name="Gig", tuning="Eb standard")
    entry = SetlistEntry(slug="cant-stop", shift=None)
    song = _song("cant-stop", "E standard")
    assert effective_shift(setlist, entry, song) == -1


def test_effective_shift_zero_when_already_in_setlist_tuning() -> None:
    """The invariant's whole point: a record already in the band's tuning
    is left untouched, never nudged by a rounding artefact."""
    setlist = Setlist(name="Gig", tuning="Eb standard")
    entry = SetlistEntry(slug="manlio", shift=None)
    song = _song("manlio", "Eb standard")
    assert effective_shift(setlist, entry, song) == 0


def test_effective_shift_explicit_entry_overrides_the_derivation() -> None:
    setlist = Setlist(name="Gig", tuning="Eb standard")
    entry = SetlistEntry(slug="sultans-of-swing", shift=-2)
    song = _song("sultans-of-swing", "E standard")
    assert effective_shift(setlist, entry, song) == -2


def test_effective_shift_is_clamped() -> None:
    setlist = Setlist(name="Gig", tuning="B standard")  # -5
    entry = SetlistEntry(slug="x", shift=None)
    song = _song("x", "E standard")  # derived would be -5, within range already
    assert effective_shift(setlist, entry, song) == -5
    # an explicit out-of-model-range value can't exist (SetlistEntry itself
    # validates +/-6), but effective_shift clamps defensively regardless --
    # exercised directly rather than via a SetlistEntry that can't be built.
    from woodshed.setlist import _clamp_only

    assert _clamp_only(9) == 6
    assert _clamp_only(-9) == -6


# ── set_shift() -- the write path POST /api/shift uses ──────────────────


def test_set_shift_persists_an_override(repo) -> None:
    setlist = Setlist(name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug="cant-stop")])
    create(repo, "gig", setlist)
    updated = set_shift(repo, "gig", "cant-stop", -2)
    assert updated.songs[0].shift == -2
    assert load(repo, "gig").songs[0].shift == -2


def test_set_shift_none_clears_an_override_back_to_derive(repo) -> None:
    setlist = Setlist(
        name="Gig", tuning="Eb standard", songs=[SetlistEntry(slug="cant-stop", shift=-2)]
    )
    create(repo, "gig", setlist)
    updated = set_shift(repo, "gig", "cant-stop", None)
    assert updated.songs[0].shift is None


def test_set_shift_unknown_song_raises(repo) -> None:
    create(repo, "gig", Setlist(name="Gig", tuning="E standard"))
    with pytest.raises(WoodshedError):
        set_shift(repo, "gig", "no-such-song", -1)


def test_set_shift_out_of_range_raises(repo) -> None:
    create(repo, "gig", Setlist(name="Gig", tuning="E standard", songs=[SetlistEntry(slug="x")]))
    with pytest.raises(WoodshedError):
        set_shift(repo, "gig", "x", 7)


# ── reorder (2026-09-06: the dashboard's drag handle needed somewhere to
#    put the result) ───────────────────────────────────────────────────────


def test_reorder_puts_the_songs_in_the_given_order() -> None:
    setlist = Setlist(
        name="The Gig", tuning="E standard",
        songs=[SetlistEntry(slug="a"), SetlistEntry(slug="b"), SetlistEntry(slug="c")],
    )
    reordered = reorder(setlist, ["c", "a", "b"])
    assert [e.slug for e in reordered.songs] == ["c", "a", "b"]


def test_reorder_carries_each_entry_whole_not_just_its_slug() -> None:
    """A running order is the only thing being changed. An entry's own
    shift override has to ride along with it, or reordering a set would
    silently re-derive shifts someone set by hand."""
    setlist = Setlist(
        name="The Gig", tuning="Eb standard",
        songs=[SetlistEntry(slug="a", shift=0), SetlistEntry(slug="b")],
    )
    reordered = reorder(setlist, ["b", "a"])
    assert reordered.songs[1].slug == "a"
    assert reordered.songs[1].shift == 0
    assert reordered.songs[0].shift is None


def test_reorder_is_pure() -> None:
    setlist = Setlist(name="The Gig", tuning="E standard",
                      songs=[SetlistEntry(slug="a"), SetlistEntry(slug="b")])
    reorder(setlist, ["b", "a"])
    assert [e.slug for e in setlist.songs] == ["a", "b"]


def test_reorder_refuses_an_order_that_is_not_a_permutation() -> None:
    """A reorder may not add, drop or duplicate a song. That is what makes
    it safe to send from a browser: the worst a bad drag can do is fail.
    Dropping a song here would be indistinguishable from a rm-song nobody
    asked for, in a file that is a human's own running order."""
    setlist = Setlist(name="The Gig", tuning="E standard",
                      songs=[SetlistEntry(slug="a"), SetlistEntry(slug="b")])
    for bad in (["a"], ["a", "b", "c"], ["a", "a"], []):
        with pytest.raises(WoodshedError, match="running order"):
            reorder(setlist, bad)
