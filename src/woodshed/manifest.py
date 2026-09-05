"""Pydantic models and load/save for `song.yaml` and `setlist.yaml`.

This is the parsing boundary named in CLAUDE.md's "Layering" section: pydantic
is scoped to this module (and the later `setlist.py`) precisely because the
validators that matter here -- a bare-string or mapping setlist entry, the
shift range, `end_s > start_s` -- are exactly what it does well. Nothing above
this module in the dependency graph should need to import pydantic.

`song.yaml` and `setlist.yaml` are *declarations* (see docs/02-data-model.md):
what is true about a song and what you intend, never a derived or measured
value. Every model here is therefore ``extra="allow"`` -- a hand-added field
that a future version of the app will read must not be silently dropped by
an older version's round trip -- except for the two places where a wrong
value would be silently harmful: a section whose `end_s` does not exceed its
`start_s` (a zero- or negative-length loop), and a setlist entry's `shift`
outside the +/-`tuning.MAX_SHIFT` range. Those two are validated at the model
itself, not left to a caller, because a range enforced in only one of several
write paths is not enforced.
"""

from __future__ import annotations

import hashlib
from datetime import date as _date
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from woodshed.errors import WoodshedError
from woodshed.tuning import MAX_SHIFT

if TYPE_CHECKING:
    # Only for type hints -- library.py (B1) owns Repo, and importing it for
    # real here would create a dependency this module does not otherwise
    # need. Never evaluated at runtime.
    from woodshed.library import Repo

__all__ = [
    "Recording",
    "Tempo",
    "PracticeDefaults",
    "Section",
    "Song",
    "SetlistEntry",
    "Setlist",
    "load_song",
    "save_song",
    "load_setlist",
    "save_setlist",
    "hash_file",
    "check_binding",
    "effective_pre_roll_beats",
]


class Recording(BaseModel):
    """The commercial recording a song's sections are measured against."""

    model_config = ConfigDict(extra="allow")

    file: str  # relative to the song dir, e.g. "audio/cant-stop.flac"
    sha256: str
    duration_s: float
    tuning: str  # what the RECORD is in -- not what you play it in
    spotify_id: str | None = None
    source: str | None = None  # free text: where the bytes came from


class Tempo(BaseModel):
    """How source seconds map onto bars for display. Never load-bearing for
    where a section actually starts -- see CLAUDE.md's seconds-not-bars
    invariant.

    Every field defaults, so a song no analysis has ever touched -- a
    hand-authored song.yaml, a future library-scan import -- parses to the
    degrade state docs/02-data-model.md documents explicitly: "if tempo.bpm
    is 0 or absent, the app still works: no grid, no click, no bar ruler,
    free-dragged boundaries." ``bpm <= 0`` is what every grid consumer
    (`analyze.beat_grid`, eventually `click.py`, the front end's bar ruler)
    checks -- 0 and "the key was never there" collapse to the same value
    here rather than needing two separate checks everywhere else.
    """

    model_config = ConfigDict(extra="allow")

    bpm: float = 0.0
    source: Literal["detected", "refined", "tapped", "manual"] = "manual"
    grid_offset_s: float = 0.0  # where bar 1 beat 1 lands in the FILE
    time_signature: str = "4/4"
    confidence: float | None = None  # null when typed by hand


class PracticeDefaults(BaseModel):
    """Song-level defaults for the practice view. Every field has a sane
    fallback so a song written before a field existed still loads."""

    model_config = ConfigDict(extra="allow")

    start_speed: float = 50.0
    ladder_step: float = 5.0
    reps_to_advance: int = 3
    pre_roll_beats: float = 4.0
    pre_roll_every_pass: bool = False
    click: Literal["off", "lead-in", "always"] = "off"
    loop_crossfade_ms: float = 10.0


class Section(BaseModel):
    """A practice target: an arbitrary span of the recording, not a tile.

    Sections may overlap and nest to any depth -- see docs/02-data-model.md.
    Containment, lanes and order are all derived (by `sections.py`) from the
    spans; nothing about the nesting is stored here.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    start_s: float
    end_s: float
    snapped: Literal["beat", "bar", "free"]
    target_speed: float
    ladder_step: float | None = None  # per-section override of the song default
    reps_to_advance: int | None = None
    notes: str | None = None
    patch: str | None = None  # optional: a patch id in gx100 songs/<slug>/song.yaml
    counts_toward_readiness: bool = True
    # Phase 1, G2: docs/00-spec.md:98 lists this as a per-section field; an
    # earlier draft of the model omitted it. None means "use the song's
    # own practice.pre_roll_beats" -- see effective_pre_roll_beats below,
    # the one place that precedence is resolved.
    lead_in_beats: int | None = None

    @property
    def duration(self) -> float:
        """end_s - start_s. Required for Section to satisfy sections.Span structurally
        (see the plan's "Types and units": Span is a Protocol with id/start_s/end_s/
        duration, and Section must match it by attribute name, never .start/.end)."""
        return self.end_s - self.start_s

    @model_validator(mode="after")
    def _check_span(self) -> Section:
        # Refuse on load, don't silently accept -- a zero- or negative-length
        # loop is not a smaller section, it is a broken one. WoodshedError is
        # a plain RuntimeError subclass, so pydantic does not catch and wrap
        # it into a ValidationError: it propagates to the caller unchanged.
        if self.end_s <= self.start_s:
            raise WoodshedError(
                f"section '{self.id}': end_s ({self.end_s}) must be greater "
                f"than start_s ({self.start_s})"
            )
        return self


class Song(BaseModel):
    """The whole of `songs/<slug>/song.yaml`."""

    model_config = ConfigDict(extra="allow")

    slug: str
    title: str
    artist: str
    album: str | None = None
    recording: Recording
    tempo: Tempo = Field(default_factory=Tempo)
    practice: PracticeDefaults = Field(default_factory=PracticeDefaults)
    sections: list[Section] = Field(default_factory=list)


class SetlistEntry(BaseModel):
    """One song in a setlist: a slug, plus an optional shift override.

    Accepts either a bare string (`- cant-stop`, takes the derived shift) or
    a mapping (`- {slug: manlio, shift: 0}`). `shift: None` means "derive it
    from setlist.tuning and the song's recording.tuning"; `shift: 0` means
    "explicitly zero, don't derive" -- the two must never collapse into each
    other, which is why `shift` stays `int | None` all the way through
    instead of defaulting to `0`.
    """

    model_config = ConfigDict(extra="allow")

    slug: str
    shift: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_string(cls, data: object) -> object:
        if isinstance(data, str):
            return {"slug": data}
        return data

    @model_validator(mode="after")
    def _check_shift_range(self) -> SetlistEntry:
        # Enforced here, at the model, so POST /api/shift and a hand-edited
        # setlist.yaml go through the identical check -- a range enforced in
        # only one of several write paths is not enforced.
        if self.shift is not None and abs(self.shift) > MAX_SHIFT:
            raise WoodshedError(
                f"setlist entry '{self.slug}': shift {self.shift:+d} is outside "
                f"the +/-{MAX_SHIFT} semitone range"
            )
        return self


class Setlist(BaseModel):
    """The whole of `setlists/<slug>.yaml`."""

    model_config = ConfigDict(extra="allow")

    name: str
    tuning: str  # THE BAND'S tuning -- what makes the transpose
    date: _date | None = None  # optional. Drives the countdown.
    venue: str = ""
    songs: list[SetlistEntry] = Field(default_factory=list)
    notes: str | None = None


def load_song(path: str | Path) -> Song:
    """Load and validate a `song.yaml` file. Raises WoodshedError (via
    Section's span check) if a section has end_s <= start_s; any other
    structural problem raises pydantic's ValidationError."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Song.model_validate(data)


def save_song(song: Song, path: str | Path) -> None:
    """Write `song.yaml` with `yaml.safe_dump(sort_keys=False)`, preserving
    the model's field order.

    This round-trips stably for files THIS APP wrote: load-then-save with no
    edit reproduces the same bytes. It does **not** preserve comments,
    quoting style or scalar style from a hand-edited file -- `safe_dump`
    cannot do that, full stop. `song.yaml` is tracked in git, so a rewrite
    that drops comments is a visible diff and one `git checkout` away from
    being reverted; that is the accepted trade (see docs/02-data-model.md and
    the plan's context file), not a bug to route around with `ruamel.yaml`.
    Durable prose about a section belongs in `notes:`, a real field this
    function does round-trip -- not in a YAML comment.
    """
    data = song.model_dump(mode="python", exclude_none=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def load_setlist(path: str | Path) -> Setlist:
    """Load and validate a `setlists/<slug>.yaml` file."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Setlist.model_validate(data)


def save_setlist(setlist: Setlist, path: str | Path) -> None:
    """Write `setlists/<slug>.yaml`. Same round-trip guarantee and the same
    limits as `save_song` -- see that docstring."""
    data = setlist.model_dump(mode="python", exclude_none=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def hash_file(path: str | Path) -> str:
    """Streamed sha256 of a file -- never reads the whole thing into memory,
    because these are multi-hundred-megabyte lossless rips."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_binding(song: Song, repo: Repo) -> str | None:
    """Check that `song.recording` still describes a real file on disk.

    Returns None if the binding holds; otherwise a sentence naming the drift
    (missing file, or a sha256 mismatch meaning a re-rip or remaster has
    replaced the bytes `song.yaml`'s sections were measured against). Cheap
    to compute once and it is the only thing standing between a practice
    session and a silently wrong loop -- see docs/02-data-model.md.
    """
    audio_path = repo.song_dir(song.slug) / song.recording.file
    if not audio_path.exists():
        return (
            f"'{song.slug}': recording file '{song.recording.file}' is missing "
            f"-- song.yaml expects it at {audio_path}"
        )
    actual = hash_file(audio_path)
    if actual != song.recording.sha256:
        return (
            f"'{song.slug}': recording file '{song.recording.file}' has changed "
            f"since song.yaml was written (sha256 {actual[:12]}… vs "
            f"recorded {song.recording.sha256[:12]}…) -- sections may no "
            f"longer line up; re-detect the grid"
        )
    return None


def effective_pre_roll_beats(song: Song, section: Section) -> float:
    """The lead-in, in BEATS: *section*'s own `lead_in_beats` if it declares
    one, else *song*'s `practice.pre_roll_beats` default.

    Precedence only -- turning the result into seconds is
    `clock.pre_roll_seconds`'s separate job. That split keeps this function
    ignorant of `bpm`/tempo entirely: it only ever decides WHICH beats
    count wins, never what a beat is worth in seconds.
    """
    if section.lead_in_beats is not None:
        return section.lead_in_beats
    return song.practice.pre_roll_beats
