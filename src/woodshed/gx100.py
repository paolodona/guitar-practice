"""The GX-100 patch-change lane's server half (#3, Group N2).

Replaces Phase 3's N1 entirely -- that module resolved a section's
free-text `patch:` id against the SIBLING gx100 repo's own song.yaml,
carrying no slot->Program Change arithmetic at all, because
docs/08-unification.md's finding was CONTESTED: rambass-live's model (a PC
number names a *slot*, resolved through the pedal's own PROGRAM MAP) versus
the gx100 repo's own hands-on unit test. That contest is now RESOLVED --
see docs/05-foot-control.md for the full story -- and the per-section
`patch:` field is gone (#3's own ask: "one song-level timeline of program
changes replaces N-per-section free text").

**The hardware-verified protocol this module assumes throughout**: a bare
Program Change (`0xC0`, no Bank Select) selects a memory *directly* --
`PC n` loads memory `n`, the identity, confirmed on the physical unit
2026-09-06. Two consequences:

- **Bank Select must never be sent.** `CC#0`/`CC#32` left the unit
  unresponsive to SysEx until its power was pulled. There is no bank
  arithmetic anywhere in this module, on purpose -- nothing here could
  construct that pair even by accident.
- **Only memories 0-127 (`U01-1`..`U32-4`) are reachable this way.**
  `U33-1` onward and every `P`-bank preset need SysEx, which this module
  does not send (`web/midi.js` deliberately has no SysEx access today).
  `memory_to_index` refuses anything outside that range, loudly, rather
  than silently clamping or wrapping into a value nobody asked for.

The actual MIDI send lives client-side (`web/gx100.js`, the mirror of the
arithmetic below) -- the pedal is attached to the same machine the browser
runs on, so Web MIDI reaches it directly and no server-side MIDI library is
needed. This module's own job is the arithmetic (shared, so a server-side
write can validate a patch name before it is ever stored) and reading the
local, human-maintained patch-name list.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from woodshed.errors import WoodshedError

__all__ = [
    "MAX_PC",
    "DEFAULT_MEMORY",
    "memory_to_index",
    "index_to_memory",
    "PatchName",
    "PatchNames",
    "load_patch_names",
]

#: A bare Program Change is 7-bit, and this pedal never receives Bank
#: Select (see the module doc) -- 0-127 is the whole reachable range.
MAX_PC = 127

#: What a section plays through when its song has no patch_changes entry
#: covering it yet -- U01-1, the pedal's own bank-0/slot-0 default.
DEFAULT_MEMORY = "U01-1"


def memory_to_index(memory: str) -> int:
    """``"U01-1"`` -> 0 ... ``"U32-4"`` -> 127 -- the Program Change number
    that selects *memory* directly (see the module doc: no PROGRAM MAP
    indirection, no Bank Select). Case- and whitespace-tolerant, matching
    how the pedal itself prints these labels.

    Raises :class:`WoodshedError`, naming the reachable range, for a
    P-bank preset or a U-bank memory past U32-4: a bare Program Change
    cannot reach either, and reaching them needs SysEx (out of scope here)
    or Bank Select (unsafe on this unit -- never sent).
    """
    text = memory.strip().upper()
    if len(text) < 4 or text[0] != "U" or "-" not in text:
        raise WoodshedError(
            f"not a reachable GX-100 memory: {memory!r} -- expected U01-1..U32-4 "
            "(a bare Program Change cannot reach a P-bank preset, or SysEx would be needed)"
        )
    bank_text, _, slot_text = text[1:].partition("-")
    if not bank_text.isdigit() or not slot_text.isdigit():
        raise WoodshedError(f"not a GX-100 memory name: {memory!r} -- expected e.g. U03-2")
    bank, slot = int(bank_text), int(slot_text)
    if not 1 <= bank <= 32 or not 1 <= slot <= 4:
        raise WoodshedError(
            f"{memory!r} is outside the range a bare Program Change can reach (U01-1..U32-4) -- "
            "anything past it needs Bank Select, which this pedal does not survive "
            "(docs/05-foot-control.md)"
        )
    return (bank - 1) * 4 + (slot - 1)


def index_to_memory(index: int) -> str:
    """Inverse of :func:`memory_to_index`."""
    if not 0 <= index <= MAX_PC:
        raise WoodshedError(f"program change {index} outside 0-{MAX_PC}")
    return f"U{index // 4 + 1:02d}-{index % 4 + 1}"


class PatchName(BaseModel):
    """One row of ``config/gx100.yaml``'s own ``patches:`` list -- a
    human-typed label for a memory, checked against the pedal by hand
    (the same discipline rambass-live's own config/gx100.yaml asks for:
    "confirm against the pedal before relying on it")."""

    model_config = ConfigDict(extra="allow")

    memory: str
    name: str


class PatchNames(BaseModel):
    """``config/gx100.yaml`` in full: the MIDI channel Woodshed sends
    Program Changes on (must match the pedal's own RX CHANNEL,
    docs/05-foot-control.md), and the local patch list the song screen's
    patch-lane popup offers as suggestions."""

    model_config = ConfigDict(extra="allow")

    channel: int = 1
    patches: list[PatchName] = Field(default_factory=list)


def load_patch_names(path: Path) -> PatchNames:
    """``config/gx100.yaml``, or field defaults when it does not exist yet
    -- degrade, don't refuse (CLAUDE.md): before anyone has typed their
    real patches in, the popup simply has nothing to suggest beyond typing
    a raw memory name by hand."""
    if not path.is_file():
        return PatchNames()
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return PatchNames.model_validate(data)
