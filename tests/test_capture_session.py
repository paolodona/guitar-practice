"""Tests for woodshed.capture_session (Phase 1.5, Group U): the on-disk
sidecar bookkeeping GET /api/capture/segments and friends read.

Test contract (plan's U2 unit): binding an already-resolved index refuses;
discarding then binding the same index refuses; the raw file is deleted
exactly when the last pending segment resolves, asserted against a temp
dir, never the real repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from woodshed.capture import Segment
from woodshed.capture_session import (
    STATUS_BOUND,
    STATUS_DISCARDED,
    STATUS_PENDING,
    adjust_boundary,
    current_session,
    merge_segments,
    resolve,
    split_segment,
    start_session,
)
from woodshed.errors import WoodshedError
from woodshed.library import Repo


def _seg(start: int, end: int, *, overflowed: bool = False) -> Segment:
    return Segment(start_frame=start, end_frame=end, sample_rate=1000, overflowed=overflowed)


def test_current_session_is_none_when_nothing_has_ever_been_captured(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    assert current_session(repo) is None


def test_start_session_then_current_session_round_trips(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")

    start_session(repo, raw_path, [_seg(0, 100), _seg(100, 250, overflowed=True)])
    session = current_session(repo)

    assert session is not None
    assert session.raw_path == raw_path
    assert [e.status for e in session.entries] == [STATUS_PENDING, STATUS_PENDING]
    assert session.entries[1].overflowed is True
    assert [e.index for e in session.pending] == [0, 1]


def test_start_session_refuses_a_raw_path_that_already_has_a_sidecar(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, [_seg(0, 100)])

    with pytest.raises(WoodshedError, match="already exists"):
        start_session(repo, raw_path, [_seg(0, 100)])


def test_current_session_picks_the_most_recent_raw_file(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.capture_dir.mkdir(parents=True)
    older = repo.capture_dir / "20260101-000000.wav"
    newer = repo.capture_dir / "20260906-120000.wav"
    older.write_bytes(b"x")
    newer.write_bytes(b"x")
    start_session(repo, older, [_seg(0, 100)])
    start_session(repo, newer, [_seg(0, 200)])

    session = current_session(repo)

    assert session is not None
    assert session.raw_path == newer


def test_resolve_marks_the_entry_bound_and_keeps_the_raw_file_when_others_are_pending(
    tmp_path: Path,
) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, [_seg(0, 100), _seg(100, 200)])

    resolved = resolve(repo, 0, STATUS_BOUND)

    assert resolved.status == STATUS_BOUND
    assert raw_path.is_file()
    session = current_session(repo)
    assert [e.status for e in session.entries] == [STATUS_BOUND, STATUS_PENDING]


def test_resolve_deletes_the_sidecar_and_raw_file_once_every_entry_resolves(
    tmp_path: Path,
) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    sidecar_path = raw_path.with_suffix(".json")
    start_session(repo, raw_path, [_seg(0, 100), _seg(100, 200)])

    resolve(repo, 0, STATUS_BOUND)
    assert raw_path.is_file()  # one still pending
    resolve(repo, 1, STATUS_DISCARDED)

    assert not raw_path.is_file()
    assert not sidecar_path.is_file()
    assert current_session(repo) is None


def test_resolve_refuses_an_unknown_index(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, [_seg(0, 100)])

    with pytest.raises(WoodshedError, match="no such capture segment"):
        resolve(repo, 5, STATUS_BOUND)


def test_resolve_refuses_binding_an_already_bound_index(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, [_seg(0, 100), _seg(100, 200)])
    resolve(repo, 0, STATUS_BOUND)

    with pytest.raises(WoodshedError, match="already bound"):
        resolve(repo, 0, STATUS_BOUND)


def test_resolve_refuses_binding_an_already_discarded_index(tmp_path: Path) -> None:
    """Plan's own named contract: discard then bind the same index refuses."""
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, [_seg(0, 100), _seg(100, 200)])
    resolve(repo, 0, STATUS_DISCARDED)

    with pytest.raises(WoodshedError, match="already discarded"):
        resolve(repo, 0, STATUS_BOUND)


def test_current_session_is_none_once_a_lone_segment_resolves(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, [_seg(0, 100)])

    resolve(repo, 0, STATUS_DISCARDED)

    assert current_session(repo) is None


# ── U2b: adjust_boundary / merge_segments / split_segment ───────────────────


def _start(tmp_path: Path, segments: list[Segment]) -> Repo:
    repo = Repo(root=tmp_path)
    raw_path = repo.capture_dir / "one.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"x")
    start_session(repo, raw_path, segments)
    return repo


# adjust_boundary ─────────────────────────────────────────────────────────


def test_adjust_boundary_moves_start_and_end(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200), _seg(200, 300)])

    updated = adjust_boundary(repo, 1, start_frame=110, end_frame=190)

    assert updated.start_frame == 110
    assert updated.end_frame == 190
    session = current_session(repo)
    middle = next(e for e in session.entries if e.index == 1)
    assert (middle.start_frame, middle.end_frame) == (110, 190)


def test_adjust_boundary_leaves_an_unspecified_bound_unchanged(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])

    updated = adjust_boundary(repo, 1, end_frame=180)

    assert updated.start_frame == 100
    assert updated.end_frame == 180


def test_adjust_boundary_refuses_start_at_or_past_end(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100)])

    with pytest.raises(WoodshedError, match="must be before"):
        adjust_boundary(repo, 0, start_frame=100)


def test_adjust_boundary_allows_touching_exactly_at_a_neighbours_boundary(
    tmp_path: Path,
) -> None:
    """Touching, not overlapping -- [0,100)+[100,200) share the point 100."""
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200), _seg(200, 300)])

    updated = adjust_boundary(repo, 1, start_frame=100, end_frame=200)

    assert (updated.start_frame, updated.end_frame) == (100, 200)


def test_adjust_boundary_refuses_overlapping_the_previous_neighbour(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])

    with pytest.raises(WoodshedError, match="overlap segment 0"):
        adjust_boundary(repo, 1, start_frame=50)


def test_adjust_boundary_refuses_overlapping_the_next_neighbour(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200), _seg(200, 300)])

    with pytest.raises(WoodshedError, match="overlap segment 2"):
        adjust_boundary(repo, 1, end_frame=250)


def test_adjust_boundary_refuses_an_unknown_index(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100)])

    with pytest.raises(WoodshedError, match="no such capture segment"):
        adjust_boundary(repo, 5, start_frame=10)


def test_adjust_boundary_refuses_a_non_pending_index(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])
    resolve(repo, 0, STATUS_BOUND)

    with pytest.raises(WoodshedError, match="not pending"):
        adjust_boundary(repo, 0, start_frame=10)


# merge_segments ──────────────────────────────────────────────────────────


def test_merge_segments_combines_adjacent_entries_at_the_first_index(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200), _seg(200, 300)])

    merged = merge_segments(repo, 0, 1)

    assert (merged.index, merged.start_frame, merged.end_frame) == (0, 0, 200)
    session = current_session(repo)
    assert [e.index for e in session.entries] == [0, 2]


def test_merge_segments_works_regardless_of_argument_order(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])

    merged = merge_segments(repo, 1, 0)

    assert (merged.index, merged.start_frame, merged.end_frame) == (0, 0, 200)


def test_merge_segments_ors_overflowed(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100, overflowed=True), _seg(100, 200)])

    merged = merge_segments(repo, 0, 1)

    assert merged.overflowed is True


def test_merge_segments_refuses_non_adjacent_indices(tmp_path: Path) -> None:
    repo = _start(
        tmp_path, [_seg(0, 100), _seg(100, 200), _seg(200, 300), _seg(300, 400)]
    )

    with pytest.raises(WoodshedError, match="not adjacent"):
        merge_segments(repo, 0, 2)


def test_merge_segments_refuses_a_non_pending_index(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])
    resolve(repo, 1, STATUS_DISCARDED)

    with pytest.raises(WoodshedError, match="not pending"):
        merge_segments(repo, 0, 1)


# split_segment ───────────────────────────────────────────────────────────


def test_split_segment_creates_two_entries_the_second_with_a_fresh_index(
    tmp_path: Path,
) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])

    first, second = split_segment(repo, 1, 150)

    assert (first.index, first.start_frame, first.end_frame) == (1, 100, 150)
    assert (second.index, second.start_frame, second.end_frame) == (2, 150, 200)
    session = current_session(repo)
    assert sorted(e.index for e in session.entries) == [0, 1, 2]


def test_split_segment_never_reuses_an_index_already_in_the_session(
    tmp_path: Path,
) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200), _seg(200, 300)])
    resolve(repo, 2, STATUS_BOUND)  # index 2 still on disk, just not pending

    _first, second = split_segment(repo, 1, 150)

    assert second.index == 3  # not 2, which is already in use


def test_split_segment_refuses_a_boundary_at_the_start(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])

    with pytest.raises(WoodshedError, match="strictly inside"):
        split_segment(repo, 1, 100)


def test_split_segment_refuses_a_boundary_at_the_end(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])

    with pytest.raises(WoodshedError, match="strictly inside"):
        split_segment(repo, 1, 200)


def test_split_segment_refuses_a_non_pending_index(tmp_path: Path) -> None:
    repo = _start(tmp_path, [_seg(0, 100), _seg(100, 200)])
    resolve(repo, 0, STATUS_BOUND)

    with pytest.raises(WoodshedError, match="not pending"):
        split_segment(repo, 0, 50)
