"""The one error type the CLI and server ever raise on purpose."""

from __future__ import annotations


class WoodshedError(RuntimeError):
    """A refusal addressed to the human. main() prints it and exits 2."""
