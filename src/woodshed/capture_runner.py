"""Background-thread wrapper around `capture.py`'s `capture()` generator,
for `POST /api/capture/start|status|stop` (Phase 1.5, Group T, T2). One
capture at a time -- a loopback device has one reader, so a second
`start()` while one is already running is refused outright, never queued.

Bridges into Group U's own bookkeeping the moment a capture stops: the
finished raw recording and its split segments are handed to
`capture_session.start_session` -- the SAME pending-segment structure
`GET /api/capture/segments` already serves, so a live start/stop here and
`woodshed capture --split` (U4, CLI parity, lower priority) both land in
one on-disk shape, not two competing schemes. A capture that produces
ZERO segments (stopped instantly, or a silent recording) has nothing for
that bookkeeping to track -- its raw file is deleted rather than left as
permanent, uncatalogued debris under `capture/`.

**Runtime-only state** (is a capture running, its elapsed time, its live
level, whether an overflow has been seen so far) is deliberately NOT one
of CLAUDE.md's four server-write categories: none of it is meaningful to
persist across a server restart (the in-progress audio itself is gone
with the process along with it), so it lives in memory on one
`CaptureRunner` instance the server owns, not on disk.

Test contract (the plan's own words): "the background-thread wrapper is
tested against a mocked `capture()` generator -- never a real device in
this suite." Tests monkeypatch this module's own `capture` reference with
a synchronous fake generator function; the actual PyAudioWPatch-facing
control flow (`stop_event`/`on_overflow`) is `capture.py`'s own concern --
see its module doc and `test_capture.py`'s fake-stream tests for T2's
addition there.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from woodshed import capture_session
from woodshed.capture import Device, capture, default_device
from woodshed.errors import WoodshedError
from woodshed.library import Repo

__all__ = ["CaptureRunner"]


@dataclass
class _State:
    running: bool = False
    started_at: float = 0.0
    level: float = 0.0
    overflowed: bool = False
    error: str | None = None
    segment_count: int = 0


class CaptureRunner:
    """Owns at most one background capture thread at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state = _State()

    def start(self, repo: Repo, *, device: Device | None = None) -> None:
        """Arm *device* (default: `default_device()`) and start recording
        on a background thread. Refuses outright if one is already
        running -- a loopback device has one reader, not a queue."""
        with self._lock:
            if self._state.running:
                raise WoodshedError("a capture is already running")
            resolved_device = device if device is not None else default_device()
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            raw_path = repo.capture_dir / f"{timestamp}.wav"
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._state = _State(running=True, started_at=time.monotonic())
            thread = threading.Thread(
                target=self._run,
                args=(repo, resolved_device, raw_path, stop_event),
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def _run(
        self, repo: Repo, device: Device, raw_path: Path, stop_event: threading.Event
    ) -> None:
        state = self._state  # this run's own state -- fixed for its whole lifetime

        def on_level(level: float) -> None:
            state.level = level

        def on_overflow() -> None:
            state.overflowed = True

        try:
            segments = list(capture(
                device, raw_path.parent, on_level=on_level, on_overflow=on_overflow,
                raw_path=raw_path, stop_event=stop_event,
            ))
            if segments:
                capture_session.start_session(repo, raw_path, segments)
                state.segment_count = len(segments)
            else:
                raw_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 -- surfaced via status(), never crashes silently
            state.error = str(exc)
        finally:
            state.running = False

    def status(self) -> dict:
        """`{running, elapsed_s, level, overflowed, error, segment_count}`
        -- derived from in-memory state, not disk (see module doc)."""
        state = self._state
        elapsed = time.monotonic() - state.started_at if state.running else 0.0
        return {
            "running": state.running,
            "elapsed_s": elapsed,
            "level": state.level,
            "overflowed": state.overflowed,
            "error": state.error,
            "segment_count": state.segment_count,
        }

    def stop(self) -> dict:
        """Signal the running capture to stop and wait for its thread to
        actually finish -- so the response can honestly say the resulting
        segments are already on disk, not "still finishing up". Refuses if
        nothing is running."""
        with self._lock:
            if not self._state.running:
                raise WoodshedError("no capture is running")
            thread = self._thread
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=30.0)
        return self.status()
