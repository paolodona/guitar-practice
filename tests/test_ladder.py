"""Tests for woodshed.ladder: the speed ladder's pure state machine.

Two counters matter here and must not be conflated (see CLAUDE.md and
ladder.py's module docstring): `clean_at_speed` is progress toward the next
rung (0..reps_to_advance, resets to 0 on advance); the ledger's total
historical cleans at a speed is a different number and belongs to
ledger.clean_by_speed, not to this module.
"""

from __future__ import annotations

from woodshed.ladder import LadderConfig, LadderState, on_clean, on_retract, rungs, starting_speed


def test_rungs_default_config_50_to_100_step_5() -> None:
    cfg = LadderConfig()
    assert rungs(cfg) == [50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0]


def test_rungs_includes_target_speed_exactly() -> None:
    cfg = LadderConfig(start_speed=90.0, ladder_step=5.0, target_speed=100.0)
    assert rungs(cfg) == [90.0, 95.0, 100.0]


def test_rungs_single_rung_when_start_equals_target() -> None:
    cfg = LadderConfig(start_speed=100.0, target_speed=100.0)
    assert rungs(cfg) == [100.0]


def test_remaining_is_reps_to_advance_minus_clean_at_speed() -> None:
    cfg = LadderConfig(reps_to_advance=3)
    state = LadderState(speed=50.0, clean_at_speed=1)
    assert state.remaining(cfg) == 2


def test_remaining_floors_at_zero() -> None:
    # clean_at_speed cannot exceed reps_to_advance in practice (on_clean advances
    # and resets before that happens), but remaining must never go negative if it
    # somehow did.
    cfg = LadderConfig(reps_to_advance=3)
    state = LadderState(speed=50.0, clean_at_speed=5)
    assert state.remaining(cfg) == 0


def test_hint_names_next_rung_and_reps_needed() -> None:
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3)
    state = LadderState(speed=55.0, clean_at_speed=2)
    assert state.hint(cfg) == "-> 60% after 1 more"


def test_hint_at_target_speed_names_no_further_rung() -> None:
    cfg = LadderConfig(target_speed=100.0)
    state = LadderState(speed=100.0, clean_at_speed=1)
    hint = state.hint(cfg)
    # No further rung exists; the hint must not claim one at 105%.
    assert "105" not in hint


def test_starting_speed_with_no_history_is_start_speed() -> None:
    cfg = LadderConfig(start_speed=50.0)
    assert starting_speed({}, cfg) == 50.0


def test_starting_speed_three_cleans_at_55_returns_60_not_55() -> None:
    # Named per the test contract: returning the earned rung itself would force
    # re-earning work already advanced past.
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3)
    clean_by_speed = {55.0: 3}
    assert starting_speed(clean_by_speed, cfg) == 60.0


def test_starting_speed_ignores_rung_with_insufficient_cleans() -> None:
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3)
    clean_by_speed = {55.0: 2}
    assert starting_speed(clean_by_speed, cfg) == 50.0


def test_starting_speed_uses_highest_qualifying_rung() -> None:
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3)
    clean_by_speed = {55.0: 3, 60.0: 3, 65.0: 1}
    assert starting_speed(clean_by_speed, cfg) == 65.0


def test_starting_speed_capped_at_target_speed() -> None:
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3, target_speed=100.0)
    clean_by_speed = {100.0: 3}
    assert starting_speed(clean_by_speed, cfg) == 100.0


def test_on_clean_increments_without_reaching_threshold() -> None:
    cfg = LadderConfig(reps_to_advance=3)
    state = LadderState(speed=50.0, clean_at_speed=0)
    next_state = on_clean(state, cfg)
    assert next_state.speed == 50.0
    assert next_state.clean_at_speed == 1


def test_on_clean_advances_rung_and_resets_counter_at_threshold() -> None:
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3)
    state = LadderState(speed=50.0, clean_at_speed=2)
    next_state = on_clean(state, cfg)
    assert next_state.speed == 55.0
    assert next_state.clean_at_speed == 0


def test_on_clean_never_advances_past_target_speed() -> None:
    # Named per the test contract: assert this explicitly starting one step below
    # target, so a threshold reached at the top rung does not overshoot.
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3, target_speed=100.0)
    state = LadderState(speed=100.0, clean_at_speed=2)
    next_state = on_clean(state, cfg)
    assert next_state.speed == 100.0
    assert next_state.clean_at_speed == 0


def test_on_retract_at_zero_leaves_speed_unchanged_and_does_not_go_negative() -> None:
    cfg = LadderConfig()
    state = LadderState(speed=55.0, clean_at_speed=0)
    next_state = on_retract(state, cfg)
    assert next_state.speed == 55.0
    assert next_state.clean_at_speed == 0


def test_on_retract_drops_by_one_not_to_zero() -> None:
    # Named for the punishment this avoids: one bad rep should not erase all
    # progress toward the rung.
    cfg = LadderConfig()
    state = LadderState(speed=55.0, clean_at_speed=2)
    next_state = on_retract(state, cfg)
    assert next_state.speed == 55.0
    assert next_state.clean_at_speed == 1


def test_on_retract_never_changes_speed() -> None:
    cfg = LadderConfig()
    state = LadderState(speed=55.0, clean_at_speed=1)
    next_state = on_retract(state, cfg)
    assert next_state.speed == 55.0
