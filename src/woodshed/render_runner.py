"""Background render queue for `GET /api/render`'s 202 path (Phase 2, Group
I3 -- pulled forward into Phase 1.5 alongside S2/S4; see `render.py`'s own
module doc).

**One render at a time.** This began life as a thread per cache key, on the
reasoning that "renders are independent per cache key and may run
concurrently without stepping on each other". They are independent, and they
do step on each other: not through shared state but through the CPU.

FOUND LIVE 2026-09-11, Paolo, practising `tutti-in-fila/full-solo`: six
rapid presses of "Faster" commissioned six `rubberband --fine` renders of
the same 88-second span at once. Each then took roughly six times as long as
it would have alone, the status bar spent minutes flapping between their
percentages, and five of the six outputs were speeds nobody would ever hear
-- the client had already moved past them. Serialising costs nothing when
there is one render (the normal case, now that `screens/practice.js`
debounces a burst of presses into a single target) and is the difference
between seconds and minutes when there is not.

`priority` exists for one asymmetry the queue would otherwise get wrong.
`GET /api/render`'s cache-hit path fires `render.plan_ahead` for the rung
above the one just served, fire-and-forget; that guess must never sit in
front of a render someone is actually waiting to hear. Lower number first,
FIFO within a tier.

This is `capture_runner.CaptureRunner`'s "one at a time" shape, arrived at
from the other direction: that one is serial because a loopback device has
exactly one reader, this one because a CPU has a finite number of cores.

Test contract: tested against fake zero-argument callables, never a real
`render_section` call -- the same "never a real subprocess in this suite"
discipline `capture_runner.py`'s own tests use for `capture()`.
"""

from __future__ import annotations

import heapq
import itertools
import threading

__all__ = ["RenderRunner"]


class RenderRunner:
    """A single background worker draining a priority queue of render jobs,
    keyed by cache path. A second request for a key already queued or
    running is a no-op (the caller just polls again), never a refusal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        #: Heap of (priority, sequence, key, fn) -- `sequence` keeps the
        #: order stable within a priority tier and keeps `fn` (which is not
        #: orderable) out of the comparison.
        self._queue: list[tuple[int, int, str, object]] = []
        self._counter = itertools.count()
        #: Queued but not started yet. Kept apart from `_running` so
        #: `in_progress` can answer for both without scanning the heap.
        self._queued: set[str] = set()
        self._running: str | None = None
        self._errors: dict[str, str] = {}
        self._worker: threading.Thread | None = None

    def ensure_started(self, key: str, fn, *, priority: int = 0) -> bool:
        """Queue *fn* (a zero-argument callable) unless *key* is already
        queued or running. Returns whether it was just queued -- `False`
        means either already pending, or already resolved on a previous call
        (its error, if any, is still available via `error(key)` until the
        next `ensure_started` for that same key clears it by trying again).

        *priority* is 0 for a render someone is waiting to hear and 1 for a
        speculative look-ahead. Lower runs first; ties run in call order.
        """
        with self._lock:
            if key in self._queued or key == self._running:
                return False
            self._queued.add(key)
            heapq.heappush(self._queue, (priority, next(self._counter), key, fn))
            self._ensure_worker_locked()
            self._wake.notify()
        return True

    def in_progress(self, key: str) -> bool:
        """Whether *key* is running OR still waiting its turn. Both count:
        `GET /api/render`'s 202 poll uses this to decide whether to start a
        render, and a queued job that answered `False` here would be started
        all over again on the next poll."""
        with self._lock:
            return key in self._queued or key == self._running

    def error(self, key: str) -> str | None:
        """The exception message from *key*'s most recent finished run, if
        it failed -- `None` if it succeeded, is still pending, or was never
        started."""
        return self._errors.get(key)

    # ── internals ───────────────────────────────────────────────────────────

    def _ensure_worker_locked(self) -> None:
        """Start the worker on first use. Lazy rather than started in
        `__init__` so constructing a runner (every test, every server that
        never renders anything) costs no thread."""
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._worker.start()

    def _drain(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    self._wake.wait()
                _, _, key, fn = heapq.heappop(self._queue)
                self._queued.discard(key)
                self._running = key
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 -- surfaced via error(), never crashes the worker
                self._errors[key] = str(exc)
            else:
                self._errors.pop(key, None)
            finally:
                with self._lock:
                    self._running = None
