"""Tests for woodshed.capture_runner (Phase 1.5, Group T, T2): the
background-thread wrapper around capture.py's capture() generator.

Test contract (the plan's own words): "a second `start` while one is
running is refused, not queued"; "status reports elapsed time, current
level, and whether an overflow was seen"; "the background-thread wrapper
is tested against a mocked `capture()` generator -- never a real device
in this suite."
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import woodshed.capture_runner as capture_runner_module
from woodshed.capture import Device, Segment
from woodshed.capture_runner import CaptureRunner
from woodshed.capture_session import current_session
from woodshed.errors import WoodshedError
from woodshed.library import Repo

FAKE_DEVICE = Device(index=0, name="Fake Loopback", sample_rate=1000, channels=1)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(root=tmp_path)


def _blocking_fake_capture(segments_after_stop: list[Segment], *, level: float = 0.3):
    """A fake `capture()`: reports one level, writes a stand-in raw file,
    blocks on the REAL `stop_event` the runner created (so `runner.stop()`
    is what actually unblocks it, exactly like the real generator), then
    yields *segments_after_stop*."""

    def fake(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
             stop_event=None, **_ignored):
        if on_level is not None:
            on_level(level)
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(b"fake raw audio")
        while not stop_event.is_set():
            time.sleep(0.005)
        yield from segments_after_stop

    return fake


class _FakeStream:
    """Stands in for a real PyAudio stream `on_stream_ready` would hand
    over -- `.close()` is the one thing `CaptureRunner.stop()`'s force-close
    path calls on it."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _wedged_fake_capture(segments_after_stop: list[Segment]):
    """A fake `capture()` that IGNORES `stop_event` entirely -- as if
    genuinely wedged inside a real blocked `stream.read()` (found live
    2026-09-06; see `CaptureRunner.stop()`'s own doc) -- and only ever
    stops once its own stream has been force-closed. Exercises the ONE
    thing that can unwedge a capture like this: nothing here ever checks
    `stop_event`, on purpose."""

    def fake(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
             stop_event=None, on_stream_ready=None, **_ignored):
        stream = _FakeStream()
        if on_stream_ready is not None:
            on_stream_ready(stream)
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(b"fake raw audio")
        while not stream.closed:  # deliberately NOT stop_event.is_set()
            time.sleep(0.005)
        yield from segments_after_stop

    return fake


def test_status_is_idle_before_any_capture_starts() -> None:
    runner = CaptureRunner()
    status = runner.status()
    assert status["running"] is False
    assert status["elapsed_s"] == 0.0
    assert status["level"] == 0.0
    assert status["overflowed"] is False
    assert status["error"] is None
    assert status["segment_count"] == 0


def test_start_then_status_reports_running_with_elapsed_and_level(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        capture_runner_module, "capture", _blocking_fake_capture([], level=0.42)
    )
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)
    time.sleep(0.02)  # let the background thread report its level

    status = runner.status()

    assert status["running"] is True
    assert status["elapsed_s"] > 0.0
    assert status["level"] == pytest.approx(0.42)

    runner.stop()  # let the thread finish so the test doesn't leak one


def test_start_refuses_while_one_is_already_running(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)

    with pytest.raises(WoodshedError, match="already running"):
        runner.start(repo, device=FAKE_DEVICE)

    runner.stop()


def test_stop_refuses_when_nothing_is_running() -> None:
    runner = CaptureRunner()
    with pytest.raises(WoodshedError, match="no capture is running"):
        runner.stop()


def test_stop_waits_for_the_thread_and_starts_a_pending_capture_session(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    segments = [
        Segment(start_frame=0, end_frame=48_000, sample_rate=48_000),
        Segment(start_frame=48_000, end_frame=96_000, sample_rate=48_000, overflowed=True),
    ]
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture(segments))
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)

    status = runner.stop()

    assert status["running"] is False
    assert status["segment_count"] == 2
    session = current_session(repo)
    assert session is not None
    assert [e.status for e in session.entries] == ["pending", "pending"]
    assert session.raw_path.is_file()


def test_a_capture_with_zero_segments_deletes_its_own_raw_file(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)

    runner.stop()

    assert current_session(repo) is None
    assert list(repo.capture_dir.glob("*.wav")) == []


def test_an_overflow_is_reflected_in_status_before_stop(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
             stop_event=None, **_ignored):
        if on_overflow is not None:
            on_overflow()
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(b"x")
        while not stop_event.is_set():
            time.sleep(0.005)
        return
        yield  # pragma: no cover -- makes this a generator function

    monkeypatch.setattr(capture_runner_module, "capture", fake)
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)
    time.sleep(0.02)

    assert runner.status()["overflowed"] is True

    runner.stop()


def test_an_exception_in_the_background_thread_surfaces_via_status_not_a_crash(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
             stop_event=None, **_ignored):
        raise RuntimeError("simulated device failure")
        yield  # pragma: no cover -- makes this a generator function

    monkeypatch.setattr(capture_runner_module, "capture", fake)
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)

    deadline = time.monotonic() + 2.0
    while runner.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.005)

    status = runner.status()
    assert status["running"] is False
    assert status["error"] == "simulated device failure"


def test_a_fresh_start_succeeds_after_a_previous_run_errored(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
                stop_event=None, **_ignored):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(capture_runner_module, "capture", failing)
    runner = CaptureRunner()
    runner.start(repo, device=FAKE_DEVICE)
    deadline = time.monotonic() + 2.0
    while runner.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runner.status()["error"] == "boom"

    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner.start(repo, device=FAKE_DEVICE)  # does not raise "already running"
    assert runner.status()["running"] is True
    runner.stop()


def test_stop_force_closes_a_stream_wedged_past_stop_event(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found live 2026-09-06: a real capture thread stuck forever inside
    `stream.read()`, never noticing `_stop_event` -- `stop()`'s join alone
    cannot recover it; only force-closing the stream `on_stream_ready`
    handed over does. `join_timeout_s` is injected small here so the test
    doesn't have to wait out the real 2s production default."""
    segments = [Segment(start_frame=0, end_frame=100, sample_rate=1000)]
    monkeypatch.setattr(capture_runner_module, "capture", _wedged_fake_capture(segments))
    runner = CaptureRunner(join_timeout_s=0.05)
    runner.start(repo, device=FAKE_DEVICE)
    time.sleep(0.02)

    status = runner.stop()

    assert status["running"] is False
    assert status["segment_count"] == 1
    session = current_session(repo)
    assert session is not None


def test_stop_without_a_wedge_never_needs_the_force_close_path(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case (found live 2026-09-06's FIRST fix, unchanged):
    a capture that notices `stop_event` promptly stops on the first join,
    with no force-close involved at all."""
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = CaptureRunner(join_timeout_s=0.05)
    runner.start(repo, device=FAKE_DEVICE)

    status = runner.stop()

    assert status["running"] is False


# ── monitor mode (2026-09-06): judging the level BEFORE committing ───────


def _wait_until(predicate, timeout_s: float = 2.0) -> None:
    """Poll *predicate* rather than sleeping a guessed interval -- these
    tests drive a real background thread, and a fixed sleep is either slow
    or flaky depending on the machine."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition never became true")


def test_monitor_never_writes_into_the_repo(repo: Repo, monkeypatch) -> None:
    """Paolo's own pain: input level is impossible to judge before pressing
    start, so arming/stopping/re-arming was the only way to find out.
    Monitoring runs the SAME capture path -- no second device code to get
    wrong -- but into a scratch directory outside the repo, and throws it
    away. `capture/` stays what CLAUDE.md says it is: real, unrepeatable
    audio, never a by-product of looking at a meter."""
    monkeypatch.setattr(
        capture_runner_module, "capture", _blocking_fake_capture([], level=0.42)
    )
    runner = CaptureRunner(join_timeout_s=0.5)
    before = sorted(p for p in repo.root.rglob("*"))

    runner.start(repo, device=FAKE_DEVICE, monitor=True)
    _wait_until(lambda: runner.status()["level"] > 0)
    assert runner.status()["monitor"] is True
    assert runner.status()["level"] == pytest.approx(0.42)
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])

    assert sorted(p for p in repo.root.rglob("*")) == before


def test_monitor_leaves_no_session_and_no_segments(repo: Repo, monkeypatch) -> None:
    """Even if the fake hands back segments, a monitor discards them: it
    was never a recording, and half-becoming one on stop would be the
    worst of both."""
    segment = Segment(start_frame=0, end_frame=1000, sample_rate=1000)
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([segment]))
    runner = CaptureRunner(join_timeout_s=0.5)

    runner.start(repo, device=FAKE_DEVICE, monitor=True)
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])

    assert runner.status()["segment_count"] == 0
    assert current_session(repo) is None


def test_monitor_cleans_up_its_scratch_directory(repo: Repo, monkeypatch) -> None:
    seen = {}

    def fake(device, out_dir, *, on_level=None, raw_path=None, stop_event=None, **_ignored):
        seen["dir"] = Path(raw_path).parent
        Path(raw_path).write_bytes(b"fake raw audio")
        while not stop_event.is_set():
            time.sleep(0.005)
        return iter(())

    monkeypatch.setattr(capture_runner_module, "capture", fake)
    runner = CaptureRunner(join_timeout_s=0.5)
    runner.start(repo, device=FAKE_DEVICE, monitor=True)
    _wait_until(lambda: "dir" in seen)
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])

    assert not seen["dir"].exists()


def test_a_real_capture_still_reports_monitor_false(repo: Repo, monkeypatch) -> None:
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = CaptureRunner(join_timeout_s=0.5)
    runner.start(repo, device=FAKE_DEVICE)
    assert runner.status()["monitor"] is False
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])


def test_monitoring_and_capturing_cannot_run_at_once(repo: Repo, monkeypatch) -> None:
    """One device, one reader -- the same refusal a second capture gets."""
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = CaptureRunner(join_timeout_s=0.5)
    runner.start(repo, device=FAKE_DEVICE, monitor=True)
    with pytest.raises(WoodshedError, match="already running"):
        runner.start(repo, device=FAKE_DEVICE)
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])
