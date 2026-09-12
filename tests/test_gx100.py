"""Tests for woodshed.gx100 -- the GX-100 patch-change lane's server half
(#3 / Group N2), replacing Phase 3's N1 entirely.

N1 resolved a section's free-text `patch:` id against the SIBLING gx100
repo's own song.yaml, deliberately carrying no slot->Program Change
arithmetic at all -- correct at the time, because docs/08-unification.md's
finding was CONTESTED (rambass-live's inference from a 2023 gig project said
a PC names a slot resolved through the pedal's PROGRAM MAP; gx100's own
hands-on unit test said otherwise). That contest is now RESOLVED
(docs/05-foot-control.md): a bare Program Change selects a memory directly,
0-127, and Bank Select must never be sent to this pedal. `Section.patch` and
the whole per-section mechanism are gone (#3's own ask -- "one song-level
timeline replaces N-per-section free text"), so this module now owns the
arithmetic that was correctly missing before, and the local patch-name list
that replaces the sibling-repo cross-reference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from woodshed import gx100
from woodshed.errors import WoodshedError

# ---- memory_to_index / index_to_memory ------------------------------------


@pytest.mark.parametrize(
    "memory,index",
    [
        ("U01-1", 0),
        ("U01-2", 1),
        ("U01-4", 3),
        ("U02-1", 4),
        ("U32-4", 127),
        ("u01-1", 0),  # case-insensitive, matches the pedal's own printed labels
        (" U01-1 ", 0),  # whitespace-tolerant, same as a hand-typed config entry
    ],
)
def test_memory_to_index_matches_the_hardware_verified_table(memory: str, index: int) -> None:
    # docs/05-foot-control.md's table: PC n loads memory n directly, and a
    # bare Program Change is 7-bit -- U01-1..U32-4 is the whole reachable
    # range, nothing past it.
    assert gx100.memory_to_index(memory) == index


def test_index_to_memory_is_the_exact_inverse() -> None:
    for index in range(gx100.MAX_PC + 1):
        memory = gx100.index_to_memory(index)
        assert gx100.memory_to_index(memory) == index


@pytest.mark.parametrize("bad", ["U33-1", "U50-4", "P01-1", "not-a-memory", "U01-5", "U00-1", ""])
def test_memory_to_index_refuses_anything_a_bare_program_change_cannot_reach(bad: str) -> None:
    # U33-1 onward and every P-bank preset need Bank Select or SysEx to
    # reach -- and this repo must never send Bank Select to this pedal
    # (docs/05-foot-control.md). Refusing here, loudly, is what keeps that
    # true: there is no code path that could silently clamp or wrap into
    # the unsafe range.
    with pytest.raises(WoodshedError):
        gx100.memory_to_index(bad)


@pytest.mark.parametrize("bad", [-1, 128, 300])
def test_index_to_memory_refuses_outside_0_127(bad: int) -> None:
    with pytest.raises(WoodshedError):
        gx100.index_to_memory(bad)


# ---- the local patch-name list (config/gx100.yaml) ------------------------


def test_load_patch_names_reads_the_local_file(tmp_path: Path) -> None:
    path = tmp_path / "gx100.yaml"
    path.write_text(
        "channel: 2\n"
        "patches:\n"
        "  - {memory: U01-1, name: Clean}\n"
        "  - {memory: U01-2, name: Lead crunch}\n",
        encoding="utf-8",
    )
    names = gx100.load_patch_names(path)
    assert names.channel == 2
    assert [p.memory for p in names.patches] == ["U01-1", "U01-2"]
    assert [p.name for p in names.patches] == ["Clean", "Lead crunch"]


def test_load_patch_names_degrades_to_empty_when_the_file_does_not_exist(tmp_path: Path) -> None:
    # CLAUDE.md's degrade rule: a song with no local patch-name file yet
    # simply has nothing to suggest, never an error -- the ordinary case on
    # any machine before someone has typed their real patches in.
    names = gx100.load_patch_names(tmp_path / "does-not-exist.yaml")
    assert names.channel == 1
    assert names.patches == []


def test_load_patch_names_degrades_on_an_empty_file_too(tmp_path: Path) -> None:
    path = tmp_path / "gx100.yaml"
    path.write_text("", encoding="utf-8")
    names = gx100.load_patch_names(path)
    assert names.patches == []


def test_save_patch_names_round_trips_through_load(tmp_path: Path) -> None:
    path = tmp_path / "gx100.yaml"
    written = gx100.PatchNames(
        channel=3,
        patches=[gx100.PatchName(memory="U01-1", name="CLEAN"),
                 gx100.PatchName(memory="U01-2", name="LEAD CRUNCH")],
    )
    gx100.save_patch_names(path, written)
    read_back = gx100.load_patch_names(path)
    assert read_back.channel == 3
    assert [(p.memory, p.name) for p in read_back.patches] == [
        ("U01-1", "CLEAN"), ("U01-2", "LEAD CRUNCH"),
    ]


def test_save_patch_names_replaces_whatever_was_there_before(tmp_path: Path) -> None:
    # A sync overwrites the file wholesale -- it is a cache of the pedal now,
    # not a hand-typed list a sync should merge into.
    path = tmp_path / "gx100.yaml"
    path.write_text("channel: 1\npatches:\n  - {memory: U09-9, name: Stale}\n", encoding="utf-8")
    gx100.save_patch_names(path, gx100.PatchNames(
        channel=1, patches=[gx100.PatchName(memory="U01-1", name="Fresh")],
    ))
    read_back = gx100.load_patch_names(path)
    assert [p.memory for p in read_back.patches] == ["U01-1"]


def test_save_patch_names_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "config" / "gx100.yaml"
    gx100.save_patch_names(path, gx100.PatchNames())
    assert path.is_file()
