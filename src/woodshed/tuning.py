"""Per-song transpose primitives.

CLAUDE.md invariant 4: transpose is per song, and the setlist only supplies a
default. A band's setlist carries one nominal tuning (e.g. the E-flat band),
but that does not mean every song needs shifting -- some records are already
in the band's tuning. ``default_shift`` computes the derivation
(``pitch_of(setlist_tuning) - pitch_of(recording_tuning)``); the per-song
override itself is not this module's concern -- it lives on the setlist
entry and is composed by the caller (``manifest.py``) out of these pure
primitives.
"""

from __future__ import annotations

import re

from woodshed.errors import WoodshedError

# Semitone offset of each standard tuning's pitch centre from E standard (0).
# Order matters only for the error message below, where it lists tunings from
# highest to lowest, matching the order they were specified in.
TUNINGS: dict[str, int] = {
    "E standard": 0,
    "Eb standard": -1,
    "D standard": -2,
    "C# standard": -3,
    "C standard": -4,
    "B standard": -5,
}

MAX_SHIFT = 6

# Drop tunings are DELIBERATELY not in TUNINGS above -- this is a decision,
# not an omission. "Drop D" lowers a single string; it is not a uniform
# transposition of the whole instrument, so there is no single semitone
# number that describes it, and inventing one would move the entire record
# when only the low string actually moved. A song whose recording.tuning
# reads "Drop D" still has a pitch centre of E standard (the other five
# strings, and everything else on the record, sit exactly where E standard
# sits) -- so drop names resolve to their PARENT standard tuning's shift
# ("Drop D" -> same as "E standard" -> 0, "Drop C#" -> same as "Eb standard"
# -> -1). The arrangement difference -- that one string is a tone lower --
# is recorded elsewhere, on the relevant section's notes field, and is never
# folded into this shift. Someone will be tempted to "fix" this by adding a
# fractional or per-string entry here; don't -- there is nothing a single
# transpose number can say about a drop tuning that is both correct and
# useful, and the notes field already says the true thing.
_DROP_PARENTS: dict[str, str] = {
    "Drop D": "E standard",
    "Drop C#": "Eb standard",
}

_FLAT_NOTE_RE = re.compile(r"^([A-G])b(?=\s|$)")


def pitch_of(name: str) -> int:
    """Semitone offset of ``name``'s pitch centre from E standard.

    Accepts every key in TUNINGS plus the drop-tuning names in
    _DROP_PARENTS, which resolve to their parent standard tuning's offset
    (see the module docstring above for why). Raises WoodshedError, listing
    every recognised name, if ``name`` is none of those.
    """
    parent = _DROP_PARENTS.get(name)
    if parent is not None:
        return TUNINGS[parent]
    try:
        return TUNINGS[name]
    except KeyError:
        known = ", ".join([*TUNINGS, *_DROP_PARENTS])
        raise WoodshedError(f"Unknown tuning {name!r}. Known tunings: {known}") from None


def default_shift(setlist_tuning: str, recording_tuning: str) -> int:
    """The setlist's default transpose for a recording in ``recording_tuning``.

    ``pitch_of(setlist_tuning) - pitch_of(recording_tuning)``: how many
    semitones the recording must move to sit at the setlist's nominal
    tuning. A recording already in the setlist's tuning yields 0 -- that is
    the whole point of the invariant, not a coincidence of the arithmetic.
    This is only the default: a song's own shift, if one is set, overrides
    it entirely and is composed by the caller, not this function.
    """
    return pitch_of(setlist_tuning) - pitch_of(recording_tuning)


def clamp_shift(semitones: int) -> int:
    """Clamp ``semitones`` to the practice view's editable range [-6, +6]."""
    return max(-MAX_SHIFT, min(MAX_SHIFT, semitones))


def tuning_label(name: str, shift: int) -> str:
    """Human-facing label, e.g. "Eb standard  -1" rendered as "E♭ standard  -1".

    Only the flat marker is remapped to the unicode flat character (U+266D);
    sharps already read correctly as "#". ``shift`` is not clamped here --
    it is rendered exactly as given, since clamping is clamp_shift's job.
    """
    label_name = _FLAT_NOTE_RE.sub(f"\\1{chr(0x266D)}", name)
    return f"{label_name}  {shift}"
