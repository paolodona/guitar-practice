"""Publish a cache file all at once, or not at all.

`server.py` answers "is this render ready?" with `dest.is_file()`, which is
the only question it can cheaply ask and the right one -- PROVIDED a file
appears only when it is finished. Writing an encoder's output straight onto
the destination breaks that promise: the file exists from the moment the
encoder opens it, and everything that reads it in between gets a prefix of
a FLAC served with a 200.

FOUND LIVE 2026-09-12, Paolo, on tutti-in-fila/1431dc68 at 60%: "a constant
'sine' sound as if a few milliseconds were looping" -- a truncated render,
decoded to a buffer of a few milliseconds, looped by the browser with loop
points computed for the full 22 seconds. Polling the running server every
100ms while the same render built caught it directly: the first non-202
answer was `200` with `Content-Length: 0`.

The fix is the oldest one there is: write beside the destination, then
`os.replace` onto it. That call is atomic on both POSIX and Windows, so a
concurrent reader sees the previous file or the new one, never an encoder's
work in progress -- and a render that dies halfway leaves nothing behind
for `render_section`'s `dest.is_file()` fast path to mistake for a cache
hit ever after.

Stdlib only, and deliberately: the modules that need it (`render.py`,
`separate.py`, `peaks.py`) are the ones CLAUDE.md's Layering section allows
a heavy dependency, but this helper is imported from above that line too.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def published_atomically(dest: Path) -> Iterator[Path]:
    """Yield a scratch path to write; move it onto *dest* on a clean exit.

    The scratch file lives in *dest*'s own directory (so the rename is
    within one filesystem, which is what makes it atomic rather than a
    copy) and keeps *dest*'s suffix, because ffmpeg chooses its muxer from
    the output extension and a `.part` would encode the wrong container.

    Raises `FileNotFoundError` if the block produced no file at all: a tool
    that exits 0 having written nothing must not publish a 0-byte render,
    which is the bug this module exists to prevent, only quieter.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    scratch = dest.with_name(f".{dest.stem}.{uuid.uuid4().hex[:8]}.part{dest.suffix}")
    try:
        yield scratch
        if not scratch.is_file():
            raise FileNotFoundError(
                f"nothing was written to {scratch} -- {dest.name} not published"
            )
        os.replace(scratch, dest)
    finally:
        # Whatever happened, the scratch file is never left lying in the
        # cache directory: on success it has already been renamed away, and
        # on failure it is exactly the half-written thing we refuse to serve.
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            # Windows can refuse the unlink if the encoder still holds the
            # handle. A leftover dotfile is inert -- nothing reads it, the
            # name is unique, and `render.evict` treats it as an orphan.
            pass
