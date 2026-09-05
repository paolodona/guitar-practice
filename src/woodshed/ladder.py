"""The speed ladder: a pure state machine for the discrete practice-speed rungs.

Pure, stdlib-only (see CLAUDE.md's "Layering" -- this module may not import
pydantic, numpy, or anything heavier).

Two counters, and do not conflate them (see CLAUDE.md and the plan's ladder
section): ``clean_at_speed`` is progress toward the NEXT rung -- it lives in
``0 .. reps_to_advance`` and resets to 0 on advance. The total historical
count of clean reps at a given speed is a different number and belongs to
``ledger.clean_by_speed`` (a different module) -- this module never holds
that number and must not be asked to represent it.

Speed is a PERCENT throughout this module (50.0 means 50%), per the plan's
"Types and units" convention.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class LadderConfig:
    """Immutable configuration for one song/section's speed ladder."""

    start_speed: float = 50.0
    ladder_step: float = 5.0
    reps_to_advance: int = 3
    target_speed: float = 100.0


@dataclass(frozen=True)
class LadderState:
    """The ladder's current position: which rung, and progress toward the next."""

    speed: float
    clean_at_speed: int

    def remaining(self, cfg: LadderConfig) -> int:
        """Reps still needed to advance off this rung, floored at 0."""
        return max(0, cfg.reps_to_advance - self.clean_at_speed)

    def hint(self, cfg: LadderConfig) -> str:
        """A short human-readable hint, e.g. "-> 60% after 1 more".

        Names no further rung when already at (or past) target_speed.
        """
        next_rung = _next_rung(self.speed, cfg)
        remaining = self.remaining(cfg)
        if next_rung is None:
            return f"-> target after {remaining} more"
        return f"-> {_fmt(next_rung)}% after {remaining} more"


def rungs(cfg: LadderConfig) -> list[float]:
    """start_speed, start_speed+ladder_step, ... up to and including target_speed."""
    result: list[float] = []
    rung = cfg.start_speed
    # Guard against a zero/negative step producing an infinite loop -- not a
    # documented case, but cheap to make safe.
    if cfg.ladder_step <= 0:
        return [cfg.start_speed]
    while rung < cfg.target_speed:
        result.append(rung)
        rung += cfg.ladder_step
    result.append(cfg.target_speed)
    return result


def starting_speed(clean_by_speed: Mapping[float, int], cfg: LadderConfig) -> float:
    """The rung ABOVE the highest rung with >= reps_to_advance cleans recorded,
    capped at target_speed; else cfg.start_speed.

    Returning the earned rung itself would force re-earning work already
    advanced past -- do not do that.
    """
    all_rungs = rungs(cfg)
    highest_qualifying_index: int | None = None
    for index, rung in enumerate(all_rungs):
        if clean_by_speed.get(rung, 0) >= cfg.reps_to_advance:
            highest_qualifying_index = index
    if highest_qualifying_index is None:
        return cfg.start_speed
    next_index = min(highest_qualifying_index + 1, len(all_rungs) - 1)
    return all_rungs[next_index]


def on_clean(state: LadderState, cfg: LadderConfig) -> LadderState:
    """Record a clean rep: increment clean_at_speed; advance a rung and reset
    the counter to 0 once it reaches reps_to_advance.

    Never advances past target_speed, even if clean_at_speed would
    mathematically allow it.
    """
    clean_at_speed = state.clean_at_speed + 1
    if clean_at_speed < cfg.reps_to_advance:
        return LadderState(speed=state.speed, clean_at_speed=clean_at_speed)
    next_rung = _next_rung(state.speed, cfg)
    new_speed = state.speed if next_rung is None else next_rung
    return LadderState(speed=new_speed, clean_at_speed=0)


def on_retract(state: LadderState, cfg: LadderConfig) -> LadderState:
    """Record a retraction: drop clean_at_speed by one, floored at 0.

    Never resets the counter to zero outright, and never changes speed --
    a retraction punishes the last rep's progress, not the whole rung.
    """
    del cfg  # unused, but kept for signature symmetry with on_clean
    clean_at_speed = max(0, state.clean_at_speed - 1)
    return LadderState(speed=state.speed, clean_at_speed=clean_at_speed)


def _next_rung(speed: float, cfg: LadderConfig) -> float | None:
    """The rung above `speed`, capped at target_speed; None if already there."""
    if speed >= cfg.target_speed:
        return None
    all_rungs = rungs(cfg)
    for rung in all_rungs:
        if rung > speed:
            return rung
    return cfg.target_speed


def _fmt(speed: float) -> str:
    """Render a whole-number speed without a trailing ".0"."""
    if speed == int(speed):
        return str(int(speed))
    return str(speed)
