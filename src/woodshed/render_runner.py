"""Background-thread wrapper around `render.render_section`/`render.plan_ahead`,
for `GET /api/render`'s 202 path (Phase 2, Group I3 -- pulled forward into
Phase 1.5 alongside S2/S4; see `render.py`'s own module doc).

Unlike `capture_runner.CaptureRunner`'s "one capture at a time" (a loopback
device has exactly one reader), renders are independent per cache key and
may run concurrently without stepping on each other -- a practice render
and its own `plan_ahead` look-ahead, or two different sections' renders,
never collide. So this tracks a SET of in-flight keys rather than one
slot, and a second request for a key already in flight is a no-op (the
caller just polls again), never a refusal.

Test contract: tested against a fake zero-argument callable, never a real
`render_section` call -- same "never a real subprocess in this suite"
discipline `capture_runner.py`'s own tests use for `capture()`.
"""

from __future__ import annotations

import threading

__all__ = ["RenderRunner"]


class RenderRunner:
    """Owns zero or more background render threads, keyed by cache path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._errors: dict[str, str] = {}

    def ensure_started(self, key: str, fn) -> bool:
        """Start *fn* (a zero-argument callable) on a background thread
        unless *key* is already in flight. Returns whether it just started
        one -- `False` means either already running, or already resolved
        on a previous call (its error, if any, is still available via
        `error(key)` until the next `ensure_started` for that same key
        clears it by trying again).
        """
        with self._lock:
            if key in self._in_flight:
                return False
            self._in_flight.add(key)

        def _run() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 -- surfaced via error(), never crashes the thread silently
                self._errors[key] = str(exc)
            else:
                self._errors.pop(key, None)
            finally:
                with self._lock:
                    self._in_flight.discard(key)

        threading.Thread(target=_run, daemon=True).start()
        return True

    def in_progress(self, key: str) -> bool:
        with self._lock:
            return key in self._in_flight

    def error(self, key: str) -> str | None:
        """The exception message from *key*'s most recent finished run, if
        it failed -- `None` if it succeeded, is still running, or was
        never started."""
        return self._errors.get(key)
