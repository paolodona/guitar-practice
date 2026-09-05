"""Sections as spans, not tiles (CLAUDE.md invariant 2).

"Full solo" and "solo, first part, tapping" are both practice targets and
the second lives inside the first. Containment and lane assignment are
DERIVED from the spans here -- nothing about the nesting is stored on disk,
so a dragged boundary can never leave a stale parent behind.

Tier 0: pure functions, stdlib + numpy only. No pydantic, no pyyaml, no
import of woodshed.manifest -- this module is lower-tier than manifest.py
and takes the structural Span protocol below instead of a concrete Section,
so manifest.Section can satisfy it without sections.py knowing it exists.

Use start_s / end_s / duration EVERYWHERE below. Never .start / .end.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from woodshed.errors import WoodshedError

_VALID_SNAP_MODES = ("beat", "bar", "free")


@runtime_checkable
class Span(Protocol):
    """The structural shape every function in this module operates on.

    Deliberately minimal: id, start_s, end_s and the derived duration.
    counts_toward_readiness (used by coverage_readiness's caller-side
    filtering, see that function's docstring) lives on manifest.Section, a
    higher tier, and is NOT part of this protocol.
    """

    id: str
    start_s: float
    end_s: float

    @property
    def duration(self) -> float: ...  # end_s - start_s


def contains(a: Span, b: Span) -> bool:
    """True if span a fully contains span b (shared endpoints count), a != b."""
    return a.start_s <= b.start_s and b.end_s <= a.end_s and a is not b


def order(spans: Sequence[Span]) -> list[Span]:
    """Spans sorted by (start_s, -duration): earliest first, longest first on ties.

    This is the one ordering used throughout the module -- assign_lanes
    relies on it to put containers on lane 0, ancestors relies on it to
    return outermost-first, and coverage_readiness relies on it for its
    tie-break rule. Does not mutate the input.
    """
    return sorted(spans, key=lambda s: (s.start_s, -s.duration))


def _overlaps(a: Span, b: Span) -> bool:
    """True if a and b share any open interval of time (touching endpoints don't)."""
    return a.start_s < b.end_s and b.start_s < a.end_s


def assign_lanes(spans: Sequence[Span]) -> dict[str, int]:
    """Assign each span the first lane with no overlap against spans already there.

    order() first (longest-first on a tied start), so a container is always
    considered before what it contains and ends up on lane 0; whatever it
    contains overlaps it and is pushed to a later lane. Two spans that don't
    overlap each other (e.g. siblings, or touching endpoints) can share a
    lane even if neither is a container.
    """
    ordered = order(spans)
    lanes: list[list[Span]] = []
    result: dict[str, int] = {}
    for span in ordered:
        for lane_index, occupants in enumerate(lanes):
            if not any(_overlaps(span, other) for other in occupants):
                occupants.append(span)
                result[span.id] = lane_index
                break
        else:
            lanes.append([span])
            result[span.id] = len(lanes) - 1
    return result


def ancestors(spans: Sequence[Span], span_id: str) -> list[Span]:
    """Spans that CONTAIN span_id, outermost first.

    Returns [] both when span_id is unknown and when it names a top-level
    span with no containing ancestor -- callers that need to distinguish
    "unknown id" from "no ancestors" must check membership themselves.
    """
    target = next((s for s in spans if s.id == span_id), None)
    if target is None:
        return []
    containers = [s for s in spans if contains(s, target)]
    return order(containers)


def validate(spans: Sequence[Span], duration_s: float | None) -> None:
    """Refuse malformed spans; degrade rather than refuse when audio isn't bound yet.

    Raises WoodshedError for: start_s >= end_s; a span outside [0, duration_s];
    a duplicate id; an EXACT duplicate span (identical start_s AND end_s to
    another span -- the only thing refused as a duplicate; overlap and
    nesting between different spans is legal and expected).

    duration_s is None or <= 0 for a needs-audio song whose audio is not
    bound yet -- in that case the bounds check is skipped, and ONLY the
    bounds check: start_s < end_s, id uniqueness and exact-duplicate
    rejection all still apply. Refusing every span on an unbound song would
    break the degrade-do-not-refuse rule the data model requires.
    """
    check_bounds = duration_s is not None and duration_s > 0
    seen_ids: set[str] = set()
    seen_bounds: set[tuple[float, float]] = set()
    for span in spans:
        if span.start_s >= span.end_s:
            raise WoodshedError(
                f"section '{span.id}': start_s ({span.start_s}) must be before "
                f"end_s ({span.end_s})"
            )
        if check_bounds and (span.start_s < 0 or span.end_s > duration_s):
            raise WoodshedError(
                f"section '{span.id}' ({span.start_s}-{span.end_s}s) falls outside "
                f"the song's duration (0-{duration_s}s)"
            )
        if span.id in seen_ids:
            raise WoodshedError(f"duplicate section id '{span.id}'")
        seen_ids.add(span.id)

        bounds = (span.start_s, span.end_s)
        if bounds in seen_bounds:
            raise WoodshedError(
                f"section '{span.id}' is an exact duplicate of another section "
                f"({span.start_s}-{span.end_s}s) -- overlapping or nested spans are "
                "fine, but two spans with identical bounds are not"
            )
        seen_bounds.add(bounds)


def snap(t: float, grid: Sequence[float], mode: str, tolerance_s: float = 0.12) -> float:
    """Snap t to the nearest value in grid within tolerance_s, else return t unchanged.

    mode is one of "beat" | "bar" | "free". Returns t unchanged when
    mode == "free" or grid is empty, without even validating mode in that
    case (a "free" caller may pass an empty or irrelevant grid).
    """
    if mode == "free" or not grid:
        return t
    if mode not in _VALID_SNAP_MODES:
        raise WoodshedError(f"unknown snap mode '{mode}' (expected one of {_VALID_SNAP_MODES})")
    nearest = min(grid, key=lambda g: abs(g - t))
    if abs(nearest - t) <= tolerance_s:
        return nearest
    return t


@dataclass(frozen=True)
class CoverageInterval:
    """One elementary interval of covered song time and the span used for it."""

    start_s: float
    end_s: float
    span_id: str  # the longest span covering this interval
    reached: float  # reached[span_id] (or 0.0 if absent), the value used


@dataclass(frozen=True)
class CoverageResult:
    """The result of coverage_readiness: an overall ratio plus its breakdown."""

    ratio: float
    intervals: list[CoverageInterval]


def coverage_readiness(spans: Sequence[Span], reached: Mapping[str, float]) -> CoverageResult:
    """Length-weighted readiness over covered song time, longest-covering-span-wins.

    Filtering by counts_toward_readiness is the CALLER's job: that field
    lives on manifest.Section, a higher tier, and is deliberately not part
    of the Span protocol in this module -- pass in only the spans that
    should count.

    Algorithm (fixed, see CLAUDE.md invariant 2 and the plan):
      1. (caller has already dropped non-counting spans)
      2. Collect every remaining span's start_s/end_s into sorted boundaries.
      3. For each elementary interval between consecutive boundaries, find
         every input span covering it in full. An interval with no covering
         span is OUTSIDE the denominator entirely (not numerator, not
         denominator) -- gaps between sections must not dilute the ratio.
         Otherwise the interval contributes its length to the denominator,
         and length * reached[longest covering span] to the numerator.
         "Longest" is why a drill can never lift its containing solo's
         contribution: everywhere the drill also covers, its container is
         longer, so the container's own reached is what's used -- a
         length-weighted mean over sections would double-count a subdivided
         solo, and this rule is exactly what avoids that.
      4. ratio = numerator / denominator, 0.0 if denominator is 0.

    Ties on covering length break by order() (earlier start_s, then
    -duration): mid_covering below is filtered out of an already-order()'d
    sequence, so the first span at the max duration is order()'s pick.
    """
    if not spans:
        return CoverageResult(ratio=0.0, intervals=[])

    boundaries = sorted({s.start_s for s in spans} | {s.end_s for s in spans})
    ordered_spans = order(spans)

    numerator = 0.0
    denominator = 0.0
    intervals: list[CoverageInterval] = []

    for lo, hi in zip(boundaries, boundaries[1:], strict=False):
        if hi <= lo:
            continue
        covering = [s for s in ordered_spans if s.start_s <= lo and hi <= s.end_s]
        if not covering:
            continue
        length = hi - lo
        max_duration = max(s.duration for s in covering)
        chosen = next(s for s in covering if s.duration == max_duration)
        r = reached.get(chosen.id, 0.0)
        numerator += length * r
        denominator += length
        intervals.append(CoverageInterval(start_s=lo, end_s=hi, span_id=chosen.id, reached=r))

    ratio = numerator / denominator if denominator > 0 else 0.0
    return CoverageResult(ratio=ratio, intervals=intervals)
