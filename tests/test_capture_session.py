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
    current_session,
    resolve,
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
