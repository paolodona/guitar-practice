"""Tests for woodshed.gx100 -- the cross-reference (Phase 3, N1).

The sibling repo is **not** present on the machine this was built on, and
these tests never require it to be: every case below builds a
`gx100/songs/<slug>/song.yaml`-shaped tree in `tmp_path`, which is also
exactly what the "absent repo degrades to not-shown" rule needs in order to
be tested at all. Nothing here imports anything from `gx100` -- CLAUDE.md's
rule for all three repos is cross-reference by slug, never by copying
content, and reading a file by a configured path is the version of that
which does not create a dependency.
"""

from __future__ import annotations

from pathlib import Path

from woodshed import gx100


def _gx100_song(root: Path, slug: str, body: str) -> Path:
    path = root / "songs" / slug / "song.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_patches_are_read_by_path_from_the_sibling_repo(tmp_path: Path) -> None:
    _gx100_song(tmp_path, "cant-stop", """
title: Can't Stop
patches:
  - id: rhythm
    profile: funk-clean
    slot: U01-1
  - id: lead
    profile: solo-boost
    slot: U02-3
""")
    patches = gx100.load_patches(tmp_path, "cant-stop")
    assert set(patches) == {"rhythm", "lead"}
    assert patches["lead"].profile == "solo-boost"
    assert patches["lead"].slot == "U02-3"


def test_a_patches_mapping_is_read_as_well_as_a_list(tmp_path: Path) -> None:
    """The sibling's own file is not this repo's to fix, and both shapes are
    plausible YAML for the same idea -- accept either rather than making a
    cross-repo read depend on which one it happens to use today."""
    _gx100_song(tmp_path, "manlio", """
patches:
  rhythm: {profile: clean, slot: U01-2}
""")
    patches = gx100.load_patches(tmp_path, "manlio")
    assert patches["rhythm"].slot == "U01-2"


def test_an_absent_repo_is_not_shown_rather_than_an_error(tmp_path: Path) -> None:
    assert gx100.load_patches(tmp_path / "no-such-repo", "cant-stop") == {}
    assert gx100.load_patches(None, "cant-stop") == {}


def test_a_song_the_sibling_does_not_have_is_empty_not_an_error(tmp_path: Path) -> None:
    _gx100_song(tmp_path, "other-song", "patches: []\n")
    assert gx100.load_patches(tmp_path, "cant-stop") == {}


def test_a_malformed_sibling_file_degrades_instead_of_taking_the_page_down(
    tmp_path: Path,
) -> None:
    """A file in ANOTHER repo can be mid-edit, broken, or a shape this one
    has never seen. It may not be able to 500 a practice screen."""
    _gx100_song(tmp_path, "cant-stop", "patches: [this: is, : broken\n")
    assert gx100.load_patches(tmp_path, "cant-stop") == {}
    _gx100_song(tmp_path, "cant-stop", "patches: 12\n")
    assert gx100.load_patches(tmp_path, "cant-stop") == {}


def test_patch_entries_missing_a_field_still_resolve_what_they_do_have(
    tmp_path: Path,
) -> None:
    _gx100_song(tmp_path, "cant-stop", "patches:\n  - id: rhythm\n")
    patch = gx100.load_patches(tmp_path, "cant-stop")["rhythm"]
    assert patch.profile is None
    assert patch.slot is None


def test_resolve_looks_a_section_patch_up_and_answers_none_when_unknown(
    tmp_path: Path,
) -> None:
    _gx100_song(tmp_path, "cant-stop", "patches:\n  - {id: lead, slot: U02-3}\n")
    assert gx100.resolve(tmp_path, "cant-stop", "lead").slot == "U02-3"
    assert gx100.resolve(tmp_path, "cant-stop", "no-such-patch") is None
    assert gx100.resolve(tmp_path, "cant-stop", None) is None


def test_repo_path_comes_from_config_and_expands_a_user_path(tmp_path, monkeypatch) -> None:
    from woodshed.config import Config

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "gx100").mkdir()
    config = Config.model_validate({"gx100": {"path": "~/gx100"}})
    assert gx100.repo_path(config) == tmp_path / "gx100"

    # Unconfigured, or configured at somewhere that isn't there: None, and
    # the caller shows nothing. Never an error -- the sibling repo being
    # absent is the ordinary case on any machine but Paolo's own.
    assert gx100.repo_path(Config()) is None
    assert gx100.repo_path(Config.model_validate({"gx100": {"path": "/nope"}})) is None


def test_slot_is_never_turned_into_a_program_change_number() -> None:
    """docs/05-foot-control.md's gotcha, made structural: a PC number names
    a SLOT, and the pedal's own PROGRAM MAP decides which of the 300
    memories that slot points at. The mapping lives in `rambass-live`'s
    config/gx100.yaml and must not be assumed -- so this module carries no
    slot->PC arithmetic at all, and there is nothing here to get wrong.
    """
    assert not any(
        name for name in dir(gx100)
        if "program" in name.lower() or name.lower().startswith("send")
    )
