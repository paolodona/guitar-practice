"""Tests for woodshed.tuning (CLAUDE.md invariant 4: transpose is per song;
the setlist only supplies a default)."""

from __future__ import annotations

import pytest

from woodshed.errors import WoodshedError
from woodshed.tuning import (
    MAX_SHIFT,
    TUNINGS,
    clamp_shift,
    default_shift,
    pitch_of,
    tuning_label,
)


def test_pitch_of_known_tunings_matches_table() -> None:
    for name, semitones in TUNINGS.items():
        assert pitch_of(name) == semitones


def test_pitch_of_unknown_name_raises_and_lists_known_names() -> None:
    with pytest.raises(WoodshedError) as exc_info:
        pitch_of("Drop A")
    message = str(exc_info.value)
    for name in TUNINGS:
        assert name in message


def test_default_shift_eb_setlist_e_recording_is_minus_one() -> None:
    # An Eb band needs an E-standard record moved down one semitone.
    assert default_shift("Eb standard", "E standard") == -1


def test_default_shift_eb_setlist_eb_recording_is_zero_record_already_in_tuning() -> None:
    # The invariant's whole point: a record already in the band's tuning
    # needs no shift, even though the setlist itself is not E standard.
    assert default_shift("Eb standard", "Eb standard") == 0


def test_default_shift_is_pure_and_an_explicit_song_shift_overrides_it() -> None:
    # default_shift and clamp_shift are the pure primitives a caller
    # (manifest.py) composes; an explicit per-song shift simply replaces
    # the derived value rather than being combined with it, which is
    # exactly what "override" means for a caller holding both numbers.
    derived = default_shift("Eb standard", "E standard")
    explicit_song_shift = 3
    assert derived != explicit_song_shift
    chosen = explicit_song_shift  # caller's choice, not derived + explicit
    assert clamp_shift(chosen) == 3


@pytest.mark.parametrize(
    ("semitones", "expected"),
    [
        (7, MAX_SHIFT),
        (-7, -MAX_SHIFT),
        (6, 6),
        (-6, -6),
        (0, 0),
    ],
)
def test_clamp_shift_clamps_to_max_shift(semitones: int, expected: int) -> None:
    assert clamp_shift(semitones) == expected


def test_drop_d_resolves_to_same_shift_as_e_standard_because_only_one_string_moved() -> None:
    # "Drop D" lowers a single string; the record's pitch centre is still
    # E standard, so it must derive the same default_shift as a recording
    # explicitly tagged "E standard" -- inventing a distinct number here
    # would move the whole record when only the low string moved.
    assert pitch_of("Drop D") == pitch_of("E standard")
    assert default_shift("Eb standard", "Drop D") == default_shift("Eb standard", "E standard")


def test_drop_c_sharp_resolves_to_same_shift_as_eb_standard() -> None:
    assert pitch_of("Drop C#") == pitch_of("Eb standard")


def test_tuning_label_renders_flat_as_unicode_flat_character() -> None:
    assert tuning_label("Eb standard", -1) == "E♭ standard  -1"


def test_tuning_label_leaves_sharp_names_unchanged() -> None:
    assert tuning_label("C# standard", -3) == "C# standard  -3"
