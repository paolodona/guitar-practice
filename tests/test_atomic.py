"""`atomic.published_atomically` — the guard behind "a cache file is either
absent or complete".

FOUND LIVE 2026-09-12, Paolo, practising tutti-in-fila/1431dc68 at 60%:
"it is now playing a constant 'sine' sound as if a few milliseconds were
looping". `server._render` answers `dest.is_file()` — and `render_section`
had ffmpeg encode the FLAC straight onto `dest`, so the file EXISTS from
the instant the encoder opens it and is complete only when it closes it. A
poll landing in that window is served a truncated FLAC, 200 and all;
Chrome decodes the frames that did arrive, and `BufferEngine` then loops a
buffer of a few milliseconds with loop points meant for a 22-second one.

Measured against the running server the same day, polling a fresh render
every 100ms: the first non-202 response was `200 len=0` — the file
existed, with nothing in it yet.

Two consequences, and the second is the worse one: a render interrupted
mid-encode (Ctrl+C, a crash, a full disk) leaves a short file that
`render_section`'s own `dest.is_file()` fast path then treats as cached
FOREVER. Writing beside the destination and renaming onto it closes both:
`os.replace` is atomic, so a reader sees the old file or the new one and
never the encoder's work in progress.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from woodshed.atomic import published_atomically


def test_the_destination_does_not_exist_while_the_work_is_in_flight(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "solo@60x+0st-abc.flac"
    seen: list[bool] = []

    with published_atomically(dest) as scratch:
        scratch.write_bytes(b"fl")
        seen.append(dest.exists())  # a reader polling right now
        scratch.write_bytes(b"flac-complete")
        seen.append(dest.exists())

    assert seen == [False, False]
    assert dest.read_bytes() == b"flac-complete"


def test_the_scratch_file_is_not_the_destination(tmp_path: Path) -> None:
    dest = tmp_path / "solo.flac"
    with published_atomically(dest) as scratch:
        assert scratch != dest
        scratch.write_bytes(b"x")


def test_the_scratch_file_keeps_the_destination_suffix(tmp_path: Path) -> None:
    """ffmpeg picks its muxer from the output EXTENSION: handed a `.part`
    it would refuse the encode (or write the wrong container). The scratch
    name is only allowed to differ before the suffix."""
    dest = tmp_path / "solo@60x+0st-abc.flac"
    with published_atomically(dest) as scratch:
        assert scratch.suffix == ".flac"
        scratch.write_bytes(b"x")


def test_the_scratch_file_is_beside_the_destination(tmp_path: Path) -> None:
    """Same directory, so the rename is within one filesystem and therefore
    atomic -- a temp dir elsewhere would make it a copy."""
    dest = tmp_path / "cache" / "solo.flac"
    with published_atomically(dest) as scratch:
        assert scratch.parent == dest.parent
        scratch.write_bytes(b"x")


def test_it_creates_the_destination_directory(tmp_path: Path) -> None:
    dest = tmp_path / "deep" / "cache" / "solo.flac"
    with published_atomically(dest) as scratch:
        scratch.write_bytes(b"x")
    assert dest.is_file()


def test_a_failure_leaves_neither_the_destination_nor_the_scratch_file(tmp_path: Path) -> None:
    """The point of the whole exercise: a crashed render must leave NOTHING
    for `render_section`'s `dest.is_file()` fast path to adopt."""
    dest = tmp_path / "solo.flac"
    scratch_seen: list[Path] = []

    with pytest.raises(RuntimeError):
        with published_atomically(dest) as scratch:
            scratch_seen.append(scratch)
            scratch.write_bytes(b"half an encode")
            raise RuntimeError("rubberband died")

    assert not dest.exists()
    assert not scratch_seen[0].exists()


def test_a_failure_leaves_an_EXISTING_destination_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "solo.flac"
    dest.write_bytes(b"the render that already worked")

    with pytest.raises(RuntimeError):
        with published_atomically(dest) as scratch:
            scratch.write_bytes(b"nonsense")
            raise RuntimeError("nope")

    assert dest.read_bytes() == b"the render that already worked"


def test_it_replaces_an_existing_destination(tmp_path: Path) -> None:
    dest = tmp_path / "solo.flac"
    dest.write_bytes(b"old")
    with published_atomically(dest) as scratch:
        scratch.write_bytes(b"new")
    assert dest.read_bytes() == b"new"


def test_producing_nothing_is_an_error_not_an_empty_cache_file(tmp_path: Path) -> None:
    """A tool that exits 0 having written nothing (it happens: a bad argv, a
    muxer that refuses) must not publish a 0-byte render -- that is the
    served-a-partial-file bug with extra steps, and `dest.is_file()` would
    call it cached."""
    dest = tmp_path / "solo.flac"
    with pytest.raises(FileNotFoundError):
        with published_atomically(dest):
            pass
    assert not dest.exists()


def test_concurrent_publishes_do_not_share_a_scratch_name(tmp_path: Path) -> None:
    """Two renders of the same section at the same rung can be in flight at
    once (two browser tabs, or a `plan_ahead` racing a real request). They
    must not write to each other's scratch file."""
    dest = tmp_path / "solo.flac"
    with published_atomically(dest) as a, published_atomically(dest) as b:
        assert a != b
        a.write_bytes(b"a")
        b.write_bytes(b"b")
    assert dest.is_file()
