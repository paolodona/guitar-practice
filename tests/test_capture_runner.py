"""Tests for woodshed.capture_runner (Phase 1.5, Group T, T2): the
background wrapper around capture.py's capture() generator.

Test contract (the plan's own words): "a second `start` while one is
running is refused, not queued"; "status reports elapsed time, current
level, and whether an overflow was seen"; "the background wrapper is
tested against a mocked `capture()` generator -- never a real device in
this suite."

**2026-09-25**: `capture()` now runs in its own OS process in production
(see capture_runner.py's own module doc -- three native crashes in one
evening took the whole server down with it before this). Every test here
still runs `_capture_worker` (the real `multiprocessing.Process` target)
against a monkeypatched `capture_runner_module.capture`, but via
`_ThreadProcess` -- a stand-in for `multiprocessing.Process` with the same
start/is_alive/join/terminate/exitcode shape, that runs the worker on a
plain thread in THIS process instead of a real subprocess. A real spawned
process re-imports this module fresh on Windows, so a patch applied here
would never reach it; `process_factory` is the seam that keeps the fake
`capture()` in play while still exercising `CaptureRunner`'s own
orchestration -- the actual thing this module owns -- exactly as before.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

import woodshed.capture_runner as capture_runner_module
from woodshed.capture import SCRATCH_FILENAME, Device, Segment
from woodshed.capture_runner import CaptureRunner
from woodshed.capture_session import current_session
from woodshed.errors import WoodshedError
from woodshed.library import Repo

FAKE_DEVICE = Device(index=0, name="Fake Loopback", sample_rate=1000, channels=1)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(root=tmp_path)


class _ThreadProcess:
    """Stand-in for `multiprocessing.Process`, injected via `_runner`'s own
    `process_factory` below: runs *target* on a plain thread in THIS
    process rather than a real OS process, so `capture_runner.capture`
    stays monkeypatchable (see this file's own module doc for why a real
    subprocess can't be patched this way) while `CaptureRunner._run`'s
    actual orchestration -- start, live level/overflow, the stop-then-
    terminate timeout -- runs completely unchanged from production.

    `terminate()` can only ever mark itself dead: an ordinary Python
    thread cannot be force-killed from outside the way a real OS process
    can, so a "wedged" fake that never checks its own stop condition
    keeps running in the background after `terminate()` (harmless -- it's
    a daemon thread, and it never reports a result once cut loose here).
    That is also exactly why an actual native CRASH is not something this
    double can simulate: unlike `capture.py`'s own device-facing half, no
    fake in this suite can prove that path true, only that the recovery
    it shares with the wedge case behaves correctly once triggered.
    """

    def __init__(self, target, args=(), kwargs=None, daemon=None) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._thread: threading.Thread | None = None
        self._exitcode: int | None = None
        self._terminated = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._target(*self._args, **self._kwargs)
            self._exitcode = 0
        except Exception:  # noqa: BLE001 -- mirrors a real process's own exit code, not a raise
            self._exitcode = 1

    def is_alive(self) -> bool:
        return not self._terminated and self._thread is not None and self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def terminate(self) -> None:
        self._terminated = True
        if self._exitcode is None:
            self._exitcode = -9  # matches a real killed process's negative-signal exit code

    @property
    def exitcode(self) -> int | None:
        return self._exitcode if self._terminated or not self.is_alive() else None


def _runner(**kwargs) -> CaptureRunner:
    """Every test's own `CaptureRunner`, always wired to `_ThreadProcess`
    unless a test overrides `process_factory` itself -- see this file's
    module doc for why."""
    kwargs.setdefault("process_factory", _ThreadProcess)
    return CaptureRunner(**kwargs)


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


def _wedged_fake_capture(scratch_samples: np.ndarray):
    """A fake `capture()` that IGNORES `stop_event` entirely -- as if
    genuinely wedged inside a real blocked `stream.read()` (found live
    2026-09-06) -- and never returns. There is no stream handle to
    force-close from out here any more (that was the old in-thread
    mechanism); the only thing that can free a worker like this now is
    `CaptureRunner._run` killing its own process outright once
    `join_timeout_s` has passed, same as a genuine native crash would look
    like from the parent's side. Writes *scratch_samples* to the same
    scratch file `capture()` itself streams into, so the recovery path
    that follows a `terminate()` has something real to recover."""

    def fake(device, out_dir, *, on_level=None, on_overflow=None, raw_path=None,
             stop_event=None, **_ignored):
        scratch_path = Path(out_dir) / SCRATCH_FILENAME
        scratch_path.parent.mkdir(parents=True, exist_ok=True)
        scratch_path.write_bytes(scratch_samples.tobytes())
        while True:  # deliberately NOT stop_event.is_set()
            time.sleep(0.005)
        yield  # pragma: no cover -- makes this a generator function; never reached

    return fake


def test_status_is_idle_before_any_capture_starts() -> None:
    runner = _runner()
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
    runner = _runner()
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
    runner = _runner()
    runner.start(repo, device=FAKE_DEVICE)

    with pytest.raises(WoodshedError, match="already running"):
        runner.start(repo, device=FAKE_DEVICE)

    runner.stop()


def test_stop_refuses_when_nothing_is_running() -> None:
    runner = _runner()
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
    runner = _runner()
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
    runner = _runner()
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
    runner = _runner()
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
    runner = _runner()
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
    runner = _runner()
    runner.start(repo, device=FAKE_DEVICE)
    deadline = time.monotonic() + 2.0
    while runner.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runner.status()["error"] == "boom"

    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner.start(repo, device=FAKE_DEVICE)  # does not raise "already running"
    assert runner.status()["running"] is True
    runner.stop()


def test_stop_terminates_a_worker_wedged_past_stop_event_and_recovers_the_scratch_file(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found live 2026-09-06: a real capture stuck forever inside
    `stream.read()`, never noticing `stop_event` -- `stop()`'s join alone
    cannot recover it. Since 2026-09-25, the fix is one level blunter (kill
    the worker's own process outright) but the OUTCOME is the same as
    before: whatever it had captured still shows up as a pending session,
    now via `recover_scratch` reading the ring-buffer file straight off
    disk rather than the old generator finishing gracefully. `join_timeout_s`
    is injected small here so the test doesn't have to wait out the real
    2s production default."""
    scratch_samples = np.full(200, 0.5, dtype=np.float32)  # well above the -50dB floor
    monkeypatch.setattr(capture_runner_module, "capture", _wedged_fake_capture(scratch_samples))
    runner = _runner(join_timeout_s=0.05)
    runner.start(repo, device=FAKE_DEVICE)
    time.sleep(0.02)

    status = runner.stop()

    assert status["running"] is False
    assert status["segment_count"] == 1
    session = current_session(repo)
    assert session is not None
    assert session.entries[0].duration_s == pytest.approx(0.2)  # 200 frames @ 1000Hz


def test_stop_without_a_wedge_never_needs_the_force_close_path(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case (found live 2026-09-06's FIRST fix, unchanged):
    a capture that notices `stop_event` promptly stops on the first join,
    with no force-close involved at all."""
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = _runner(join_timeout_s=0.05)
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
    runner = _runner(join_timeout_s=0.5)
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
    runner = _runner(join_timeout_s=0.5)

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
    runner = _runner(join_timeout_s=0.5)
    runner.start(repo, device=FAKE_DEVICE, monitor=True)
    _wait_until(lambda: "dir" in seen)
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])

    assert not seen["dir"].exists()


def test_a_real_capture_still_reports_monitor_false(repo: Repo, monkeypatch) -> None:
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = _runner(join_timeout_s=0.5)
    runner.start(repo, device=FAKE_DEVICE)
    assert runner.status()["monitor"] is False
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])


def test_monitoring_and_capturing_cannot_run_at_once(repo: Repo, monkeypatch) -> None:
    """One device, one reader -- the same refusal a second capture gets."""
    monkeypatch.setattr(capture_runner_module, "capture", _blocking_fake_capture([]))
    runner = _runner(join_timeout_s=0.5)
    runner.start(repo, device=FAKE_DEVICE, monitor=True)
    with pytest.raises(WoodshedError, match="already running"):
        runner.start(repo, device=FAKE_DEVICE)
    runner.stop()
    _wait_until(lambda: not runner.status()["running"])
