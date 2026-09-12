"""Tests for woodshed.render_runner (Phase 2, Group I3): the background-
thread wrapper GET /api/render's 202 path uses.

Test contract (the module's own docstring): tested against a fake
zero-argument callable, never a real render -- see `test_capture_runner.py`
for the identical discipline applied to `capture()`.
"""

from __future__ import annotations

import threading
import time

import pytest

from woodshed.render_runner import RenderRunner


def test_ensure_started_runs_the_callable_and_reports_no_error() -> None:
    runner = RenderRunner()
    done = threading.Event()

    def fn() -> None:
        done.set()

    started = runner.ensure_started("key-1", fn)

    assert started is True
    assert done.wait(timeout=2.0)
    _wait_until_idle(runner, "key-1")
    assert runner.in_progress("key-1") is False
    assert runner.error("key-1") is None


def test_ensure_started_is_a_noop_for_a_key_already_in_flight() -> None:
    runner = RenderRunner()
    release = threading.Event()
    calls = []

    def fn() -> None:
        calls.append(1)
        release.wait(timeout=2.0)

    first = runner.ensure_started("key-1", fn)
    second = runner.ensure_started("key-1", fn)  # while the first is still blocked in fn()

    assert first is True
    assert second is False
    release.set()
    _wait_until_idle(runner, "key-1")
    assert len(calls) == 1


def test_two_different_keys_run_one_at_a_time() -> None:
    """FOUND LIVE 2026-09-11, Paolo, practising tutti-in-fila/full-solo.

    This runner used to start a thread per key and say so in its own
    docstring: "renders are independent per cache key and may run
    concurrently without stepping on each other". They are independent, and
    they do step on each other -- not through shared state but through the
    CPU. Six rapid speed presses commissioned six `rubberband --fine`
    processes over the same 88-second span at once, and each one then took
    six times as long as it would have alone, which is what the status bar
    was reporting when it flapped between percentages for minutes.

    Renders are serialised from here. The client only asks for one at a
    time now (screens/practice.js debounces a burst of presses into a
    single target), so the queue is normally one deep; this is what keeps
    it honest when it is not.
    """
    runner = RenderRunner()
    concurrent = []
    live = {"n": 0}
    lock = threading.Lock()
    done = threading.Event()

    def fn() -> None:
        with lock:
            live["n"] += 1
            concurrent.append(live["n"])
        time.sleep(0.02)
        with lock:
            live["n"] -= 1
            if len(concurrent) == 2:
                done.set()

    runner.ensure_started("a", fn)
    runner.ensure_started("b", fn)

    assert done.wait(timeout=2.0), "both renders should have run"
    _wait_until_idle(runner, "a")
    _wait_until_idle(runner, "b")
    assert concurrent == [1, 1], f"renders overlapped: {concurrent}"


def test_a_queued_render_is_reported_as_in_progress_before_it_starts() -> None:
    """A caller polling `in_progress` must not be told "not running" about a
    render that is queued -- `GET /api/render`'s 202 path would otherwise
    start it a second time."""
    runner = RenderRunner()
    release = threading.Event()

    runner.ensure_started("slow", lambda: release.wait(timeout=2.0))
    runner.ensure_started("queued", lambda: None)

    assert runner.in_progress("queued") is True
    release.set()
    _wait_until_idle(runner, "slow")
    _wait_until_idle(runner, "queued")


def test_a_user_render_jumps_the_queue_ahead_of_a_look_ahead() -> None:
    """`GET /api/render`'s cache-hit path fires `render.plan_ahead` for the
    next rung, fire-and-forget. With one worker that look-ahead would sit in
    front of the render someone is actually waiting to hear, so priority
    exists for exactly this: the press you made outranks the guess the
    server made about your next one."""
    runner = RenderRunner()
    order = []
    release = threading.Event()

    runner.ensure_started("blocking", lambda: release.wait(timeout=2.0))
    runner.ensure_started("ahead", lambda: order.append("ahead"), priority=1)
    runner.ensure_started("wanted", lambda: order.append("wanted"), priority=0)

    release.set()
    _wait_until_idle(runner, "blocking")
    _wait_until_idle(runner, "ahead")
    _wait_until_idle(runner, "wanted")

    assert order == ["wanted", "ahead"]


def test_error_is_captured_and_surfaced() -> None:
    runner = RenderRunner()

    def fn() -> None:
        raise ValueError("boom")

    runner.ensure_started("key-1", fn)
    _wait_until_idle(runner, "key-1")

    assert runner.error("key-1") == "boom"


def test_a_fresh_run_clears_a_previous_error() -> None:
    runner = RenderRunner()
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")

    runner.ensure_started("key-1", flaky)
    _wait_until_idle(runner, "key-1")
    assert runner.error("key-1") == "boom"

    runner.ensure_started("key-1", flaky)
    _wait_until_idle(runner, "key-1")
    assert runner.error("key-1") is None


def _wait_until_idle(runner: RenderRunner, key: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while runner.in_progress(key):
        if time.monotonic() > deadline:
            pytest.fail(f"{key!r} never finished")
        time.sleep(0.01)
