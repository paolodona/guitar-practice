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


def test_two_different_keys_run_concurrently() -> None:
    runner = RenderRunner()
    barrier = threading.Barrier(2, timeout=2.0)

    def fn() -> None:
        barrier.wait()  # only passes if BOTH threads reach it -- proves concurrency

    runner.ensure_started("a", fn)
    runner.ensure_started("b", fn)

    _wait_until_idle(runner, "a")
    _wait_until_idle(runner, "b")


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
