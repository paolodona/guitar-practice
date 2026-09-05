"""Tests for woodshed.sections (CLAUDE.md invariant 2: spans, not tiles).

Written before the implementation (test-first, per the plan's ground rules).
Uses a minimal local dataclass satisfying the Span protocol (id, start_s,
end_s, duration) rather than importing any higher-tier concrete Section,
since sections.py must not depend on manifest.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from woodshed.errors import WoodshedError
from woodshed.sections import (
    ancestors,
    assign_lanes,
    contains,
    coverage_readiness,
    order,
    snap,
    validate,
)


@dataclass(frozen=True)
class S:
    """A minimal Span for tests: id/start_s/end_s/duration, nothing else."""

    id: str
    start_s: float
    end_s: float

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s


# ---------------------------------------------------------------------------
# contains
# ---------------------------------------------------------------------------


def test_contains_true_for_strict_containment():
    outer = S("outer", 0.0, 100.0)
    inner = S("inner", 10.0, 20.0)
    assert contains(outer, inner) is True


def test_contains_true_when_bounds_shared_on_one_side():
    outer = S("outer", 0.0, 100.0)
    inner = S("inner", 0.0, 50.0)
    assert contains(outer, inner) is True


def test_contains_false_for_a_span_that_is_not_inside():
    a = S("a", 0.0, 10.0)
    b = S("b", 5.0, 15.0)
    assert contains(a, b) is False
    assert contains(b, a) is False


def test_contains_false_for_identical_span_object():
    a = S("a", 0.0, 10.0)
    assert contains(a, a) is False


def test_contains_true_both_ways_for_two_distinct_spans_with_identical_bounds():
    # Per the fixed formula (a.start_s <= b.start_s and b.end_s <= a.end_s
    # and a is not b), object identity is what's checked, not id equality --
    # two distinct objects with identical bounds satisfy containment in
    # both directions. This case is a different concern (an exact-duplicate
    # span) refused separately by validate(), not by contains().
    a = S("a", 0.0, 10.0)
    b = S("b", 0.0, 10.0)
    assert contains(a, b) is True
    assert contains(b, a) is True


# ---------------------------------------------------------------------------
# order
# ---------------------------------------------------------------------------


def test_order_sorts_by_start_then_by_longest_duration_first():
    early_long = S("early_long", 0.0, 100.0)
    early_short = S("early_short", 0.0, 10.0)
    late = S("late", 50.0, 60.0)
    result = order([late, early_short, early_long])
    assert [s.id for s in result] == ["early_long", "early_short", "late"]


def test_order_does_not_mutate_input():
    spans = [S("b", 5.0, 6.0), S("a", 0.0, 1.0)]
    original = list(spans)
    order(spans)
    assert spans == original


# ---------------------------------------------------------------------------
# assign_lanes
# ---------------------------------------------------------------------------


def test_assign_lanes_puts_container_on_lane_zero():
    solo = S("solo", 0.0, 100.0)
    drill = S("drill", 10.0, 20.0)
    lanes = assign_lanes([drill, solo])
    assert lanes["solo"] == 0
    assert lanes["drill"] == 1


def test_assign_lanes_shares_a_lane_for_non_overlapping_siblings():
    solo = S("solo", 0.0, 100.0)
    drill_a = S("drill_a", 0.0, 40.0)
    drill_b = S("drill_b", 40.0, 100.0)
    lanes = assign_lanes([solo, drill_a, drill_b])
    assert lanes["solo"] == 0
    # non-overlapping siblings (touching at 40.0, not overlapping) share lane 1
    assert lanes["drill_a"] == 1
    assert lanes["drill_b"] == 1


def test_assign_lanes_gives_overlapping_siblings_different_lanes():
    solo = S("solo", 0.0, 100.0)
    drill_a = S("drill_a", 0.0, 50.0)
    drill_b = S("drill_b", 30.0, 80.0)  # overlaps drill_a
    lanes = assign_lanes([solo, drill_a, drill_b])
    assert lanes["solo"] == 0
    assert lanes["drill_a"] != lanes["drill_b"]
    assert lanes["drill_a"] != 0
    assert lanes["drill_b"] != 0


def test_assign_lanes_touching_endpoints_do_not_overlap():
    a = S("a", 0.0, 10.0)
    b = S("b", 10.0, 20.0)
    lanes = assign_lanes([a, b])
    assert lanes["a"] == lanes["b"] == 0


def test_assign_lanes_deterministic_regardless_of_input_order():
    solo = S("solo", 0.0, 100.0)
    part1 = S("part1", 0.0, 50.0)
    part2 = S("part2", 50.0, 100.0)
    tap = S("tap", 10.0, 20.0)  # nested inside part1

    spans = [solo, part1, part2, tap]
    result_a = assign_lanes(spans)
    result_b = assign_lanes(list(reversed(spans)))
    result_c = assign_lanes([tap, part2, solo, part1])

    assert result_a == result_b == result_c


# ---------------------------------------------------------------------------
# ancestors
# ---------------------------------------------------------------------------


def test_ancestors_returns_containers_outermost_first():
    solo = S("solo", 0.0, 100.0)
    part = S("part", 0.0, 50.0)
    tap = S("tap", 10.0, 20.0)
    spans = [solo, part, tap]
    result = ancestors(spans, "tap")
    assert [s.id for s in result] == ["solo", "part"]


def test_ancestors_empty_for_a_top_level_span():
    solo = S("solo", 0.0, 100.0)
    other = S("other", 200.0, 250.0)
    result = ancestors([solo, other], "solo")
    assert result == []


def test_ancestors_excludes_non_containing_overlaps():
    a = S("a", 0.0, 10.0)
    b = S("b", 5.0, 15.0)  # overlaps a but does not contain or get contained
    result = ancestors([a, b], "b")
    assert result == []


def test_ancestors_unknown_span_id_returns_empty():
    solo = S("solo", 0.0, 100.0)
    assert ancestors([solo], "does-not-exist") == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_accepts_well_formed_spans():
    spans = [S("a", 0.0, 10.0), S("b", 5.0, 20.0)]
    validate(spans, duration_s=100.0)  # must not raise


def test_validate_rejects_start_not_before_end():
    spans = [S("a", 10.0, 10.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=100.0)


def test_validate_rejects_start_after_end():
    spans = [S("a", 20.0, 10.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=100.0)


def test_validate_rejects_span_outside_duration():
    spans = [S("a", 0.0, 200.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=100.0)


def test_validate_rejects_negative_start():
    spans = [S("a", -5.0, 10.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=100.0)


def test_validate_rejects_duplicate_id():
    spans = [S("a", 0.0, 10.0), S("a", 20.0, 30.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=100.0)


def test_validate_rejects_exact_duplicate_span():
    spans = [S("a", 0.0, 10.0), S("b", 0.0, 10.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=100.0)


def test_validate_accepts_overlapping_but_not_identical_spans():
    # Overlap and nesting between DIFFERENT spans is legal and expected --
    # only an EXACT (start_s, end_s) duplicate is refused.
    spans = [S("a", 0.0, 10.0), S("b", 5.0, 15.0), S("c", 0.0, 100.0)]
    validate(spans, duration_s=100.0)  # must not raise


def test_validate_with_duration_none_skips_bounds_check_only():
    # A needs-audio song with no bound audio yet: degrade, don't refuse.
    spans = [S("a", 0.0, 200.0)]  # would be out of bounds if duration_s=100
    validate(spans, duration_s=None)  # must not raise


def test_validate_with_duration_zero_skips_bounds_check_only():
    spans = [S("a", 0.0, 200.0)]
    validate(spans, duration_s=0.0)  # must not raise


def test_validate_with_duration_none_still_rejects_start_after_end():
    spans = [S("a", 20.0, 10.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=None)


def test_validate_with_duration_none_still_rejects_exact_duplicate():
    spans = [S("a", 0.0, 10.0), S("b", 0.0, 10.0)]
    with pytest.raises(WoodshedError):
        validate(spans, duration_s=None)


# ---------------------------------------------------------------------------
# snap
# ---------------------------------------------------------------------------


def test_snap_free_mode_returns_input_unchanged():
    assert snap(12.345, [10.0, 12.0, 14.0], "free") == 12.345


def test_snap_empty_grid_returns_input_unchanged():
    assert snap(12.345, [], "beat") == 12.345


def test_snap_snaps_to_nearest_within_tolerance():
    assert snap(12.05, [10.0, 12.0, 14.0], "beat", tolerance_s=0.12) == 12.0


def test_snap_returns_unchanged_when_outside_tolerance():
    assert snap(12.5, [10.0, 12.0, 14.0], "beat", tolerance_s=0.12) == 12.5


def test_snap_picks_the_nearest_of_several_candidates():
    assert snap(13.0, [10.0, 12.0, 14.0], "bar", tolerance_s=1.5) == 12.0


def test_snap_boundary_tolerance_is_inclusive():
    assert snap(12.12, [12.0], "beat", tolerance_s=0.12) == 12.0


def test_snap_rejects_unknown_mode():
    with pytest.raises(WoodshedError):
        snap(12.0, [10.0, 12.0], "nonsense-mode")


# ---------------------------------------------------------------------------
# coverage_readiness
# ---------------------------------------------------------------------------


def test_coverage_readiness_single_span():
    solo = S("solo", 0.0, 100.0)
    result = coverage_readiness([solo], {"solo": 0.5})
    assert result.ratio == pytest.approx(0.5)


def test_coverage_readiness_empty_spans_is_zero():
    result = coverage_readiness([], {})
    assert result.ratio == 0.0
    assert result.intervals == []


def test_coverage_readiness_subdividing_a_solo_does_not_change_song_readiness():
    # THE double-count test: a solo split into three drills, each further
    # along than the solo's own reached, must not move the song's readiness
    # for that stretch of time above what the solo's own reached implies --
    # a length-weighted mean over sections would double-count a subdivided
    # solo, and this is exactly the bug CLAUDE.md calls out.
    solo = S("solo", 0.0, 100.0)
    drill1 = S("drill1", 0.0, 40.0)
    drill2 = S("drill2", 40.0, 70.0)
    drill3 = S("drill3", 70.0, 100.0)

    undivided = coverage_readiness([solo], {"solo": 0.5})
    subdivided = coverage_readiness(
        [solo, drill1, drill2, drill3],
        {"solo": 0.5, "drill1": 1.0, "drill2": 1.0, "drill3": 1.0},
    )
    assert subdivided.ratio == pytest.approx(undivided.ratio)
    assert subdivided.ratio == pytest.approx(0.5)


def test_coverage_readiness_a_fully_reached_drill_does_not_lift_its_solo():
    # A drill at 100% inside a solo at 50%: the drill can only ever
    # contribute by being the LONGEST covering span for an interval, and
    # inside the solo it never is -- the solo is longer everywhere the
    # drill also covers, so the drill's own reached never surfaces here.
    solo = S("solo", 0.0, 100.0)
    drill = S("drill", 20.0, 50.0)
    result = coverage_readiness([solo, drill], {"solo": 0.5, "drill": 1.0})
    assert result.ratio == pytest.approx(0.5)


def test_coverage_readiness_ties_broken_by_order():
    # Two equal-length spans covering the exact same interval: order()
    # (start_s, -duration) picks the earlier start_s as the "longest".
    a = S("a", 0.0, 10.0)
    b = S("b", 0.0, 10.0)  # would be refused by validate(), but
    # coverage_readiness itself does not call validate -- exercise the
    # tie-break directly.
    result = coverage_readiness([a, b], {"a": 1.0, "b": 0.0})
    assert result.ratio == pytest.approx(1.0)
    assert result.intervals[0].span_id == "a"


def test_coverage_readiness_uncovered_gaps_are_excluded_from_denominator():
    # Gaps between sections must not dilute the ratio -- only covered
    # seconds count toward the denominator at all.
    a = S("a", 0.0, 10.0)
    b = S("b", 20.0, 30.0)  # gap from 10..20 is uncovered
    result = coverage_readiness([a, b], {"a": 1.0, "b": 0.5})
    # weighted mean over the COVERED 20s only: (10*1.0 + 10*0.5) / 20 = 0.75
    assert result.ratio == pytest.approx(0.75)


def test_coverage_readiness_missing_reached_entry_defaults_to_zero():
    solo = S("solo", 0.0, 10.0)
    result = coverage_readiness([solo], {})
    assert result.ratio == pytest.approx(0.0)


def test_coverage_readiness_breakdown_lengths_sum_to_denominator():
    a = S("a", 0.0, 10.0)
    b = S("b", 20.0, 35.0)
    result = coverage_readiness([a, b], {"a": 1.0, "b": 1.0})
    total_length = sum(iv.end_s - iv.start_s for iv in result.intervals)
    assert total_length == pytest.approx(25.0)
    assert result.ratio == pytest.approx(1.0)
