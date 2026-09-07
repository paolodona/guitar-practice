"""The `gx100` cross-reference: a section names a patch, and this resolves it
(plan Phase 3, N1; docs/01-architecture.md:150, docs/05-foot-control.md).

`gx100` is the sibling repo where the patches are designed. A Woodshed
section may carry `patch: lead`, and the thing that says what "lead" IS --
its profile and which memory slot it lives in -- is
`gx100/songs/<slug>/song.yaml`'s own `patches:` block.

Three rules, all of them load-bearing:

**Read by path from config, never imported.** CLAUDE.md's rule across all
three repos is cross-reference by slug, never by copying content. A path
read honours that without creating a dependency: nothing here imports
`gx100`, nothing here vendors its data, and the sibling can be at any
version or absent entirely.

**Absent degrades to "not shown", never to an error.** The sibling repo is
missing on any machine but Paolo's own, its file may be mid-edit, and the
`patches:` block may be a shape this module has never seen. None of that may
take down a practice screen, so every failure here answers "no patches".

**No slot -> Program Change arithmetic lives here, deliberately.**
docs/05-foot-control.md and `rambass-live/docs/gx100.md` both spell out the
gotcha: a PC number does not name a memory, it names a SLOT, and the pedal's
own PROGRAM MAP decides which of the 300 memories that slot points at. The
mapping lives in `rambass-live`'s `config/gx100.yaml` and must not be
assumed -- so this module carries none of it, and a test asserts that it
carries none of it. Showing `U02-3` because the sibling said `U02-3` is a
quotation; computing it would be a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["Patch", "repo_path", "load_patches", "resolve"]


@dataclass(frozen=True)
class Patch:
    """One patch as the `gx100` repo describes it. Quoted, never derived."""

    id: str
    profile: str | None = None
    #: The memory slot as the sibling writes it, e.g. "U02-3". A STRING,
    #: on purpose -- see the module docstring.
    slot: str | None = None


def repo_path(config) -> Path | None:
    """Where the `gx100` checkout is, per `config.gx100.path` -- or `None`.

    `None` covers both "not configured" and "configured at somewhere that is
    not there", because the caller does the same thing in both cases: show
    nothing.
    """
    raw = getattr(getattr(config, "gx100", None), "path", None)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def load_patches(gx100_root: Path | str | None, slug: str) -> dict[str, Patch]:
    """`{patch_id: Patch}` from `<gx100_root>/songs/<slug>/song.yaml`.

    `{}` for every kind of absence: no root, no such song, no `patches:`
    block, a malformed file, or a shape this module does not recognise.
    """
    if gx100_root is None:
        return {}
    path = Path(gx100_root) / "songs" / slug / "song.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        # UnicodeDecodeError belongs here for the same reason as the other
        # two: a file in ANOTHER repo can be in any encoding it likes, and
        # this module's one contract is that absence degrades rather than
        # taking a practice screen down (found by review 2026-09-07).
        return {}
    if not isinstance(data, dict):
        return {}
    return _parse_patches(data.get("patches"))


def _parse_patches(raw) -> dict[str, Patch]:
    """Accept both plausible YAML shapes for the same idea.

    The sibling's file is not this repo's to fix, and a list of entries with
    an `id` and a mapping keyed by id are both reasonable ways to write it.
    Reading either costs six lines; depending on which one it happens to use
    today would cost a broken cross-reference the first time it changed.
    """
    patches: dict[str, Patch] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            patch_id = entry.get("id")
            if not patch_id:
                continue
            patches[str(patch_id)] = Patch(
                id=str(patch_id),
                profile=_str_or_none(entry.get("profile")),
                slot=_str_or_none(entry.get("slot")),
            )
    elif isinstance(raw, dict):
        for patch_id, entry in raw.items():
            entry = entry if isinstance(entry, dict) else {}
            patches[str(patch_id)] = Patch(
                id=str(patch_id),
                profile=_str_or_none(entry.get("profile")),
                slot=_str_or_none(entry.get("slot")),
            )
    return patches


def _str_or_none(value) -> str | None:
    return None if value is None else str(value)


def resolve(gx100_root: Path | str | None, slug: str, patch_id: str | None) -> Patch | None:
    """The `Patch` a section's `patch:` names, or `None` when it cannot be
    resolved -- including when the section names no patch at all."""
    if not patch_id:
        return None
    return load_patches(gx100_root, slug).get(patch_id)
