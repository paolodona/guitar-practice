"""Background wrapper around `capture.py`'s `capture()` generator, for
`POST /api/capture/start|status|stop` (Phase 1.5, Group T, T2). One capture
at a time -- a loopback device has one reader, so a second `start()` while
one is already running is refused outright, never queued.

**The actual recording runs in its own OS process, not just a background
thread (2026-09-25).** Found live, THREE TIMES in one evening: a native
access violation (`0xc0000005`) inside PyAudioWPatch/PortAudio -- once in
`AUDIOSES.DLL`, once in `_portaudiowpatch`'s own `.pyd`, once in `ntdll` --
took the whole `woodshed serve` process down with it every time, mid-
capture, with no Python exception for anything in this module to catch (a
segfault happens below the interpreter; `except Exception` never sees it).
A background THREAD shares its process's address space with everything
else the server is doing, so that crash killed Stop, the status poller,
every other open screen, all of it, every time. Running `capture()` in a
CHILD PROCESS instead means the same crash still kills that one worker,
but the server itself -- and `stop()`, and `GET /api/capture/status`, and
whatever else was open -- survives to report it and move on.

`_capture_worker` is the `multiprocessing.Process` target in production. It
resolves `capture()` through this module's own top-level name (`capture`,
imported below) at CALL time -- an ordinary global lookup, nothing special
-- which is what already lets tests monkeypatch it today. A REAL child
process (Windows' `spawn` start method) re-imports this module fresh, so a
patch applied in the *parent* test process never reaches a genuine
subprocess; instead, `CaptureRunner`'s own `process_factory` is the seam --
tests inject `_ThreadProcess`, a stand-in with the same start/is_alive/
join/terminate/exitcode shape as `multiprocessing.Process` that runs
`_capture_worker` on a plain thread IN the test's own process, so the
monkeypatched `capture` is exactly what runs. Production leaves
`process_factory` at its default (`multiprocessing.Process`) and gets a
real process boundary. Same trade `capture.py`'s own module doc already
makes for its device-facing half: "no WASAPI loopback hardware in this
environment to run it against... written carefully... left unverified
rather than faked with a mock that would prove nothing true about a real
device." The one thing genuinely untestable here without hardware is a
real native crash; `_ThreadProcess` can exercise "never responds, gets
terminated" (a real wedged `stream.read()`, already found live 2026-09-06)
but not "the OS kills the interpreter out from under it" -- both end up in
the exact same recovery path below regardless.

**Recovery, not just containment.** When the worker ends without ever
reporting a result -- terminated after ignoring `stop_event`, or a real
crash -- `_run` calls `capture.recover_scratch` on whatever the worker's
own ring-buffer scratch file (`capture()`'s "written straight to disk as
it arrives" design) already holds, the exact split `capture()` would have
produced itself. This is what turned three separate live incidents from
"someone has to open a Python shell and recover this by hand" into
"finish naming what's already in Capture Review" -- see `recover_scratch`'s
own doc for the full reasoning.

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
"""

from __future__ import annotations

import dataclasses
import multiprocessing
import queue
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from woodshed import capture_session
from woodshed.capture import Device, Segment, capture, default_device, recover_scratch
from woodshed.errors import WoodshedError
from woodshed.library import Repo

__all__ = ["CaptureRunner"]


def _capture_worker(
    device: Device,
    out_dir: Path,
    raw_path: Path,
    stop_event,
    level_value,
    overflow_flag,
    result_queue,
) -> None:
    """The `multiprocessing.Process` target: runs `capture()` to completion
    and reports back over *result_queue* -- `("done", <segment tuples>)` or
    `("error", <message>)`. Never raises back into whatever ran it; a
    worker that ends WITHOUT putting anything on the queue at all (killed,
    or crashed below the interpreter) is `_run`'s own cue to recover from
    the scratch file instead (see this module's own doc).

    `on_level`/`on_overflow` write into shared primitives rather than
    calling back into the parent directly -- a real separate process
    cannot invoke a plain Python callable living in the parent's memory,
    only these `multiprocessing.Value`/`Event` objects, which work
    identically whether *this* function is actually running in a
    subprocess (production) or on a thread in the test suite's own
    process (`_ThreadProcess`).
    """
    def on_level(level: float) -> None:
        level_value.value = level

    def on_overflow() -> None:
        overflow_flag.set()

    try:
        segments = list(capture(
            device, out_dir, on_level=on_level, on_overflow=on_overflow,
            raw_path=raw_path, stop_event=stop_event,
        ))
        result_queue.put(("done", [dataclasses.astuple(s) for s in segments]))
    except Exception as exc:  # noqa: BLE001 -- reported to the parent, never raised here
        result_queue.put(("error", str(exc)))


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
    """Owns at most one background capture worker at a time."""

    def __init__(self, *, join_timeout_s: float = 2.0, process_factory=None) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = None
        self._state = _State()
        self._join_timeout_s = join_timeout_s
        #: Builds the worker. Defaults to a real `multiprocessing.Process`
        #: -- see the module doc for why, and for what a test passes here
        #: instead.
        self._process_factory = process_factory or multiprocessing.Process

    def start(self, repo: Repo, *, device: Device | None = None, monitor: bool = False) -> None:
        """Arm *device* (default: `default_device()`) and start recording
        on a background worker. Refuses outright if one is already
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
            stop_event = multiprocessing.Event()
            self._stop_event = stop_event
            self._state = _State(running=True, started_at=time.monotonic(), monitor=monitor)
            thread = threading.Thread(
                target=self._run,
                args=(repo, resolved_device, raw_path, stop_event, monitor),
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def _run(
        self, repo: Repo, device: Device, raw_path: Path, stop_event, monitor: bool = False,
    ) -> None:
        state = self._state  # this run's own state -- fixed for its whole lifetime
        level_value = multiprocessing.Value("d", 0.0)
        overflow_flag = multiprocessing.Event()
        result_queue: multiprocessing.Queue = multiprocessing.Queue()

        process = self._process_factory(
            target=_capture_worker,
            args=(device, raw_path.parent, raw_path, stop_event, level_value, overflow_flag,
                  result_queue),
            daemon=True,
        )
        process.start()

        # Mirror the worker's own live level/overflow into `state` (what
        # `status()` reads) until it finishes on its own, OR -- once
        # `stop_event` is set -- for up to `join_timeout_s` longer before
        # this gives up on a clean exit and terminates it outright. A
        # worker that is genuinely wedged (found live 2026-09-06: a real
        # blocked `stream.read()` that never notices `stop_event` between
        # reads) can only be freed by killing its process now that it has
        # one of its own -- there is no stream handle to force-close from
        # out here the way the old in-thread version could.
        stop_deadline: float | None = None
        while True:
            state.level = level_value.value
            if overflow_flag.is_set():
                state.overflowed = True
            if not process.is_alive():
                break
            if stop_event.is_set():
                if stop_deadline is None:
                    stop_deadline = time.monotonic() + self._join_timeout_s
                elif time.monotonic() >= stop_deadline:
                    process.terminate()
                    process.join(timeout=self._join_timeout_s)
                    break
            time.sleep(0.005)

        state.level = level_value.value
        if overflow_flag.is_set():
            state.overflowed = True

        try:
            kind, payload = result_queue.get_nowait()
        except queue.Empty:
            kind, payload = None, None

        if kind == "done":
            segments = [Segment(*s) for s in payload]
        elif kind == "error":
            state.error = payload
            segments = recover_scratch(
                raw_path.parent, raw_path, device.sample_rate, overflowed=state.overflowed,
            )
        else:
            # The worker never reported back at all: terminated above
            # after ignoring `stop_event`, or a native crash killed it
            # outright (an OS-assigned exit code, not a Python exception --
            # see this module's own doc: three of these on 2026-09-25).
            # Either way `capture()`'s own ring-buffer scratch file may
            # still hold real audio worth recovering.
            segments = recover_scratch(
                raw_path.parent, raw_path, device.sample_rate, overflowed=state.overflowed,
            )
            if not segments:
                state.error = (
                    f"the capture process ended unexpectedly (exit code "
                    f"{process.exitcode}) and there was nothing to recover"
                )

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
        """Signal the running capture to stop and wait for `_run` to
        actually finish -- long enough to cover its own grace period
        (`join_timeout_s`, for a worker that notices `stop_event`
        promptly) PLUS the terminate-and-join that follows if it doesn't,
        so this can honestly report the resulting segments are already
        visible via `GET /api/capture/segments` either way.

        Refuses if nothing is running.
        """
        with self._lock:
            if not self._state.running:
                raise WoodshedError("no capture is running")
            thread = self._thread
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=self._join_timeout_s * 2 + 0.5)
        return self.status()
