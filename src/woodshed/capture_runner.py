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

import shutil
import tempfile
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
    #: True while this run is a MONITOR: the same capture, into a scratch
    #: directory outside the repo, thrown away on stop. See `start`.
    monitor: bool = False


class CaptureRunner:
    """Owns at most one background capture thread at a time."""

    def __init__(self, *, join_timeout_s: float = 2.0) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state = _State()
        #: The raw PyAudio stream `capture()`'s own `on_stream_ready` most
        #: recently handed us -- `stop()` reaches for this if the capture
        #: thread never notices `_stop_event` in time. Injectable so tests
        #: don't have to wait out the real production timeout to exercise
        #: the force-close path (see `stop()`'s own doc).
        self._stream: object | None = None
        self._join_timeout_s = join_timeout_s

    def start(self, repo: Repo, *, device: Device | None = None, monitor: bool = False) -> None:
        """Arm *device* (default: `default_device()`) and start recording
        on a background thread. Refuses outright if one is already
        running -- a loopback device has one reader, not a queue.

        **`monitor=True` (2026-09-06)**: run the meter without committing
        to a recording. Found live -- input level is impossible to judge
        before pressing start, so the only way to check it was to arm,
        look, stop, and re-arm, repeatedly, before every real capture.

        A monitor is deliberately the SAME capture, pointed at a scratch
        directory outside the repo and deleted on stop, rather than a
        second device path. Two reasons: the device half of `capture.py`
        is the one part of this repo that no test can cover (there is no
        loopback device in CI, and its own history is a list of things
        only a real WASAPI stream revealed), so a parallel implementation
        would be a second thing that can only be debugged live; and
        `capture/` stays exactly what CLAUDE.md says it is -- real,
        unrepeatable audio -- rather than also being a by-product of
        looking at a meter.

        What a monitor is NOT: loudness normalisation of what was
        captured. That was the other candidate fix and it is rejected on
        purpose -- it would be the tool's first ever alteration of
        recorded audio, applied to the one thing it can never re-record.
        The meter solves the actual problem (judging level *before*
        playing), and leaves the recording bit-exact.
        """
        with self._lock:
            if self._state.running:
                raise WoodshedError("a capture is already running")
            resolved_device = device if device is not None else default_device()
            if monitor:
                scratch_dir = Path(tempfile.mkdtemp(prefix="woodshed-monitor-"))
                raw_path = scratch_dir / "monitor.wav"
            else:
                timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                raw_path = repo.capture_dir / f"{timestamp}.wav"
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._stream = None
            self._state = _State(running=True, started_at=time.monotonic(), monitor=monitor)
            thread = threading.Thread(
                target=self._run,
                args=(repo, resolved_device, raw_path, stop_event, monitor),
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def _run(
        self, repo: Repo, device: Device, raw_path: Path, stop_event: threading.Event,
        monitor: bool = False,
    ) -> None:
        state = self._state  # this run's own state -- fixed for its whole lifetime

        def on_level(level: float) -> None:
            state.level = level

        def on_overflow() -> None:
            state.overflowed = True

        def on_stream_ready(stream: object) -> None:
            with self._lock:
                self._stream = stream

        try:
            segments = list(capture(
                device, raw_path.parent, on_level=on_level, on_overflow=on_overflow,
                raw_path=raw_path, stop_event=stop_event, on_stream_ready=on_stream_ready,
            ))
            if monitor:
                # Never a session, never a segment, whatever came back: it
                # was a meter, and half-becoming a recording on stop would
                # be the worst of both.
                shutil.rmtree(raw_path.parent, ignore_errors=True)
            elif segments:
                capture_session.start_session(repo, raw_path, segments)
                state.segment_count = len(segments)
            else:
                raw_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 -- surfaced via status(), never crashes silently
            state.error = str(exc)
            if monitor:
                shutil.rmtree(raw_path.parent, ignore_errors=True)
        finally:
            state.running = False

    def status(self) -> dict:
        """`{running, elapsed_s, level, overflowed, error, segment_count,
        monitor}` -- derived from in-memory state, not disk (see module
        doc). `monitor` is what lets the screen show the same meter under
        a different word: nothing is being kept."""
        state = self._state
        elapsed = time.monotonic() - state.started_at if state.running else 0.0
        return {
            "running": state.running,
            "elapsed_s": elapsed,
            "level": state.level,
            "overflowed": state.overflowed,
            "error": state.error,
            "segment_count": state.segment_count,
            "monitor": state.monitor,
        }

    def stop(self) -> dict:
        """Signal the running capture to stop and wait BRIEFLY for its
        thread to actually finish -- long enough that a normal ~100ms
        chunk read notices `stop_event` and this can honestly report the
        resulting segments are already on disk, but not so long that a
        slow-to-notice thread holds the whole HTTP response hostage.

        **Found live 2026-09-06** ("capture doesn't stop"): the original
        30s join blocked this call's entire response on the real device
        thread noticing `stop_event` -- from a plain `fetch()` with no
        progress indicator, a slow stop and a genuinely stuck one looked
        identical. `status()['running']` still tells the truth either way
        -- `screens/capture.js`'s own `settleStop` polls `GET /api/capture/
        status` until it actually reads false, rather than assuming this
        one call always finishes the job.

        **Found live 2026-09-06, same session, a real capture** ("stuck at
        'Stopping...' forever, no progress at all"): a genuinely WEDGED
        capture thread -- `capture.py`'s own blocking `stream.read()` never
        returning once a WASAPI loopback device stops delivering packets
        (the common trigger: the backing track finished, then Stop was
        pressed) -- never notices `_stop_event` no matter how long anything
        waits, because it is not between reads to check it; the ABOVE fix
        only ever helped a thread that was still polling. If the join below
        times out, this reaches for the stream `capture()`'s own
        `on_stream_ready` handed `_run` and force-closes it: PortAudio
        streams closed from another thread unstick a blocked `read()` (it
        raises rather than hanging), which `capture()`'s own loop now treats
        as a clean stop when `_stop_event` is already set (see its module
        doc) rather than a fabricated overflow. One more short join gives
        that its own chance to land before this call gives up and reports
        whatever `status()` honestly is -- possibly still `running: True`,
        if even the force-close didn't work; `screens/capture.js`'s own
        polling bound (see its module doc) is what keeps THAT case from
        hanging the UI forever too.

        Refuses if nothing is running.
        """
        with self._lock:
            if not self._state.running:
                raise WoodshedError("no capture is running")
            thread = self._thread
            stream = self._stream
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=self._join_timeout_s)
            if thread.is_alive() and stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 -- best-effort; see this method's own doc
                    pass
                thread.join(timeout=self._join_timeout_s)
        return self.status()
