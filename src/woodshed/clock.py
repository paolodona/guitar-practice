"""The two clocks (CLAUDE.md invariant 3), isolated so mixing them up is loud.

Source time is seconds in the original commercial recording -- what
song.yaml stores. Playback time is seconds in the rendered, stretched
section, where the pre-roll occupies the head. The two are related by a
single affine transform per Render (a fixed offset and a fixed speed
ratio), and this module is the only place that transform is written down.

Everything above the render boundary (UI, ledger, ladder) must call
to_playback/to_source rather than reimplement the arithmetic -- that is
what keeps a source-seconds value from ever being compared against a
playback-seconds value by accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NewType

SourceSeconds = NewType("SourceSeconds", float)  # position in the original file
PlaybackSeconds = NewType("PlaybackSeconds", float)  # position in the rendered, stretched section


@dataclass(frozen=True)
class Render:
    """Describes one rendered section: what was cut, and at what speed.

    ffmpeg cuts exactly [start_s - pre_roll_s, end_s] from the source and
    rubberband stretches that cut by 1/speed. Nothing past end_s is
    rendered, so the equal-power crossfade at loop time folds the
    section's final crossfade_ms of *playback* audio over the head of the
    loop region rather than fading in from a nonexistent tail -- see
    loop_end.
    """

    start_s: float  # source seconds, section start (NOT including pre-roll)
    end_s: float  # source seconds, section end
    pre_roll_s: float  # source seconds of lead-in rendered ahead of start_s
    speed: float  # 0.40 .. 1.00, a FRACTION -- never a percent in this one field
    crossfade_ms: float = 10.0
    # Phase 1, G2: docs/02-data-model.md:47's practice.pre_roll_every_pass,
    # made real. False (the default) is Phase 0's only behaviour: the
    # lead-in plays once, on load/restart, and every subsequent pass loops
    # from AFTER it. True replays the lead-in on every pass instead --
    # loop_start moves to the render's very start; loop_end and total are
    # untouched, since how long a lap lasts doesn't change, only where it
    # begins.
    pre_roll_every_pass: bool = False

    @property
    def loop_start(self) -> PlaybackSeconds:
        if self.pre_roll_every_pass:
            return PlaybackSeconds(0.0)
        return PlaybackSeconds(self.pre_roll_s / self.speed)

    @property
    def loop_end(self) -> PlaybackSeconds:
        # crossfade_ms is already-stretched playback milliseconds, so it is
        # NOT divided by speed -- see the module and class docstrings for
        # why this term exists at all.
        return PlaybackSeconds(
            self.loop_start
            + (self.end_s - self.start_s) / self.speed
            - self.crossfade_ms / 1000
        )

    @property
    def total(self) -> PlaybackSeconds:
        return PlaybackSeconds((self.pre_roll_s + (self.end_s - self.start_s)) / self.speed)


def to_playback(t: SourceSeconds, r: Render) -> PlaybackSeconds:
    """Convert a position in the original file to a position in the render."""
    return PlaybackSeconds((t - (r.start_s - r.pre_roll_s)) / r.speed)


def to_source(t: PlaybackSeconds, r: Render) -> SourceSeconds:
    """Inverse of to_playback: a position in the render back to the original file."""
    return SourceSeconds(t * r.speed + (r.start_s - r.pre_roll_s))


def grid_to_playback(grid_s: Sequence[float], r: Render) -> list[PlaybackSeconds]:
    """Map a whole grid of source-second positions through to_playback, in order."""
    return [to_playback(g, r) for g in grid_s]


def pre_roll_seconds(beats: float, bpm: float) -> float:
    """``beats`` at ``bpm`` -> SOURCE seconds (``beats * 60 / bpm``).

    The beats -> seconds half of Phase 1, G2's lead-in conversion --
    ``render_section(pre_roll_s=...)`` (Phase 2) wants seconds, but both
    ``Section.lead_in_beats`` and ``PracticeDefaults.pre_roll_beats`` are in
    beats, and nothing before this converted them. ``bpm <= 0`` (0 or
    absent, docs/02-data-model.md:160's degrade rule) answers 0.0 rather
    than dividing by zero -- no tempo means no pre-roll, not an exception.
    WHICH beats value wins (a section's own override, or the song's
    default) is ``manifest.effective_pre_roll_beats``'s job, one tier up;
    this function only ever does the arithmetic.
    """
    if bpm <= 0:
        return 0.0
    return beats * 60.0 / bpm
