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
    # 0.40 .. 1.10, a FRACTION -- never a percent in this one field.
    #
    # The ceiling is 1.10, not 1.00, and that was a real decision (settled
    # 2026-09-06, BACKLOG): Paolo asked to be able to push a section FASTER
    # than the recording on purpose -- get comfortable ahead of 100%, then
    # come back down -- so `web/player.js` allows a manual speed up to 110%.
    # The practice loop plays from the render CACHE, so a cache capped at
    # 1.00 would mean the one speed range he asked for is the one range that
    # silently falls back to the real-time stretcher. The cache follows the
    # slider.
    #
    # What does NOT follow: the ladder. `ladder.rungs()` still stops at
    # `target_speed` and `on_clean` still never advances past it, so nothing
    # auto-climbs above 100% -- going there is always a deliberate press.
    speed: float
    crossfade_ms: float = 10.0
    # Phase 1, G2: docs/02-data-model.md:47's practice.pre_roll_every_pass,
    # made real. False (the default) is Phase 0's only behaviour: the
    # lead-in plays once, on load/restart, and every subsequent pass loops
    # from AFTER it. True replays the lead-in on every pass instead --
    # loop_start moves to the render's very start; loop_end and total are
    # untouched: total because it describes what was rendered, loop_end
    # because it is the section's own end position. The LAP therefore gets
    # longer by exactly the lead-in -- which is the point of the setting.
    pre_roll_every_pass: bool = False

    @property
    def effective_pre_roll_s(self) -> float:
        """The lead-in that actually EXISTS in the rendered file.

        `render_section` cuts `[max(0, start_s - pre_roll_s), end_s]`, so a
        section 0.5 s into a recording cannot have a 2 s lead-in however
        many beats the song asks for -- there is only 0.5 s of recording in
        front of it. Clamped here, in the one place both clocks read
        (CLAUDE.md's two-clock rule: convert at the boundary and nowhere
        else), rather than in each consumer.

        FOUND BY REVIEW 2026-09-07, and it was silent in the worst way:
        `loop_start` and `total` came off the unclamped value while the
        file came off the clamped cut, so every lap of such a section
        started `pre_roll_s - start_s` playback-seconds late, `loop_end`
        pointed past the end of the file, and `_bake_crossfade` folded the
        seam that far INSIDE the section instead of at its head.
        """
        return max(0.0, min(self.pre_roll_s, self.start_s))

    @property
    def loop_start(self) -> PlaybackSeconds:
        if self.pre_roll_every_pass:
            return PlaybackSeconds(0.0)
        return PlaybackSeconds(self.effective_pre_roll_s / self.speed)

    @property
    def loop_end(self) -> PlaybackSeconds:
        # crossfade_ms is already-stretched playback milliseconds, so it is
        # NOT divided by speed -- see the module and class docstrings for
        # why this term exists at all.
        #
        # Measured from the SECTION's own end (pre-roll + section, less the
        # crossfade), never from loop_start: where a lap begins has nothing
        # to say about where the section ends. Defining it relative to
        # loop_start -- as this property did until Group J -- dragged the
        # loop end back by pre_roll_s/speed whenever pre_roll_every_pass
        # moved loop_start to 0, so a native loop stopped that far short of
        # the section's real end and render.py trimmed those samples off
        # the cache file. See tests/test_clock.py for the named test.
        return PlaybackSeconds(
            (self.effective_pre_roll_s + (self.end_s - self.start_s)) / self.speed
            - self.crossfade_ms / 1000
        )

    @property
    def total(self) -> PlaybackSeconds:
        return PlaybackSeconds(
            (self.effective_pre_roll_s + (self.end_s - self.start_s)) / self.speed
        )


def to_playback(t: SourceSeconds, r: Render) -> PlaybackSeconds:
    """Convert a position in the original file to a position in the render.

    The render's own t=0 is `start_s - effective_pre_roll_s`, not
    `start_s - pre_roll_s`: what was cut is what exists (see
    `Render.effective_pre_roll_s`).
    """
    return PlaybackSeconds((t - (r.start_s - r.effective_pre_roll_s)) / r.speed)


def to_source(t: PlaybackSeconds, r: Render) -> SourceSeconds:
    """Inverse of to_playback: a position in the render back to the original file."""
    return SourceSeconds(t * r.speed + (r.start_s - r.effective_pre_roll_s))


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
