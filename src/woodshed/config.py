"""Config model and loader for config.yaml.

Pydantic v2 is used here as the same parsing-boundary exception CLAUDE.md's
"Layering" section grants ``manifest.py`` and ``setlist.py``: a schema is a
Pydantic model in the module that owns it, and config.yaml's validation --
coercion of numbers, the midi CC map's int keys, sane field defaults -- is
exactly what it does well.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, Field

from woodshed.library import Repo


class Defaults(BaseModel):
    """Song-level defaults, used when a song.yaml doesn't override them."""

    start_speed: float = 50.0
    ladder_step: float = 5.0
    reps_to_advance: int = 3
    pre_roll_beats: float = 4.0
    auto_confirm: bool = False

    # next_up() scoring weights, read by a later phase's scoring function.
    # docs/02-data-model.md's config.yaml example doesn't print values for
    # these -- there is nothing to be faithful to, so all three default to
    # 1.0: an unweighted score until a human decides otherwise.
    weight_gap: float = 1.0
    weight_cold: float = 1.0
    weight_gig: float = 1.0


class Midi(BaseModel):
    """Foot-controller mapping. See docs/05-foot-control.md."""

    input: str = ""
    channel: int = 1
    # CC number -> action name. YAML integer keys parse as Python ints
    # already; pydantic keeps them as int rather than silently stringifying.
    map: dict[int, str] = Field(default_factory=dict)


class Spotify(BaseModel):
    """Never a secret in this file -- config.yaml is tracked. See docs/04."""

    client_id: str | None = None


class Gx100(BaseModel):
    """Auto-applying a GX-100 patch change on section/song load (#3, N2).

    `path` (the sibling `gx100` repo cross-reference, Phase 3 N1) is gone:
    that mechanism resolved a section's now-removed `patch:` field, and
    "one song-level timeline of program changes replaces N-per-section
    free text" retired the whole thing. The local, human-maintained patch
    list now lives at `config/gx100.yaml` (`library.Repo.gx100_config_path`,
    `gx100.load_patch_names`) instead.
    """

    #: Sending Program Changes alters the pedal you are about to play a gig
    #: on, so it is off unless explicitly turned on -- CLAUDE.md's own
    #: "Don't" and the same instinct as gx100's edit-buffer-only rule.
    send_program_changes: bool = False
    #: Case-insensitive substring against a Web MIDI output port's own
    #: `name` -- same convention as `Midi.input` above. Empty matches the
    #: first available output port, the ordinary case with one pedal
    #: attached.
    midi_output: str = ""


class RenderConfig(BaseModel):
    engine: str = "rubberband"
    formant_preserve: bool = True
    cache_max_gb: float = 20.0


class Config(BaseModel):
    library_paths: list[str] = Field(default_factory=list)
    defaults: Defaults = Field(default_factory=Defaults)
    midi: Midi = Field(default_factory=Midi)
    spotify: Spotify = Field(default_factory=Spotify)
    gx100: Gx100 = Field(default_factory=Gx100)
    render: RenderConfig = Field(default_factory=RenderConfig)


def load_config(repo: Repo) -> Config:
    """Load config.yaml from *repo*, or field defaults if it is missing.

    A missing config.yaml is a "degrade, don't refuse" path (CLAUDE.md): it
    yields a Config built entirely from field defaults, never an error.
    """
    path = repo.config_path
    if not path.is_file():
        return Config()

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return Config.model_validate(data)
