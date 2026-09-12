"""Reading real patch names off the pedal into `config/gx100.yaml` (#3, N2 follow-up).

Paolo, 2026-09-11: "config/gx100.yaml should be a *cache* of all the presets I have on
the pedalboard" -- not the hand-typed list docs/05-foot-control.md originally described.
This module is the read side of that: `woodshed gx100 sync` cycles the pedal through
every memory a bare Program Change can reach (U01-1..U32-4, the same 0-127 range
`gx100.memory_to_index` already enforces -- nothing past it is reachable by this lane
regardless of what the sync could technically read) and writes back whatever name is
actually stored on the unit.

**This is a real, visible hardware operation, not a query.** Reading a memory's name
means *loading* it -- there is no other way to ask the unit "what is U14-3 called"
without putting the pedal on U14-3, discarding whatever was in its edit buffer. Over
128 memories that is a noticeable, minutes-long operation, so it only ever runs from
`woodshed gx100 sync` on the terminal, on purpose (never from a page load or a request
handler) -- the same "explicit toggle, not a silent side effect" instinct as
`config.gx100.send_program_changes` and CLAUDE.md's Program Change rule, just applied to
a different message type. It always restores whatever was loaded before it started,
success or failure, so a sync never leaves the pedal on a different patch than it found.

**The SysEx frame shape and addresses below are Roland's own** (GX-100 / GX-10 MIDI
Implementation) -- the same wire protocol the sibling `gx100` repo's `device/sysex.py`
already talks, hardware-verified there (docs/05-foot-control.md: `select_patch`,
2026-08-30, and the settle timing this module reuses). They are necessarily identical
in both repos, describing one physical pedal's protocol rather than either repo's own
design, so they are consulted here rather than re-derived -- but the *code* is
independent: this module implements only the narrow slice `sync_patch_names` needs
(CurrentPatchNum and the edit buffer's name field), not a general parameter/effect API.

**Layering.** `mido` (+ its `python-rtmidi` backend) is a heavy, hardware-facing
dependency, so this is a fourth module alongside `analyze.py` / `render.py` /
`separate.py` allowed one at module level -- see CLAUDE.md's Layering section, updated
to name it. `Transport` is the seam that keeps that dependency out of everything else:
`sync_patch_names`, `select_patch` and `read_current_patch` are plain orchestration over
three methods, tested against `FakeTransport` in tests/test_gx100_sync.py with no MIDI
backend and no pedal in the loop. `MidiTransport` and `open_transport` are the only
things here hardware can disagree with.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from woodshed.errors import WoodshedError
from woodshed.gx100 import MAX_PC, PatchName, index_to_memory
from woodshed.tools import require_module

__all__ = [
    "Transport",
    "MidiTransport",
    "guess_gx100_ports",
    "open_transport",
    "read_current_patch",
    "select_patch",
    "sync_patch_names",
]

# ── Roland SysEx frame constants (GX-100 / GX-10 MIDI Implementation) ───────
ROLAND = 0x41
DEV_ID = 0x10  # default device id; the pedal's own RX CHANNEL, not this byte, is what
#                 docs/05-foot-control.md's config.gx100.midi_output/channel gate
RQ1 = 0x11  # request data
DT1 = 0x12  # send data
MODEL_ID: tuple[int, ...] = (0x00, 0x00, 0x00, 0x00, 0x0B)  # shared by GX-100 and GX-10

GX_HINTS = ("gx-100", "gx100", "boss gx")

NAME_SIZE = 16  # a patch's own name is the edit buffer's first 16 bytes


def _address_of(aa: int, bb: int, cc: int, dd: int) -> int:
    return (aa << 21) | (bb << 14) | (cc << 7) | dd


def _address_bytes(address: int) -> bytes:
    return bytes((address >> shift) & 0x7F for shift in (21, 14, 7, 0))


SYSTEM = _address_of(0x00, 0x00, 0x00, 0x00)  # CurrentPatchNum lives here
EDIT_BUFFER = _address_of(0x10, 0x00, 0x00, 0x00)  # the temporary edit buffer


def checksum(payload: bytes) -> int:
    """Roland address+data checksum."""
    return (128 - (sum(payload) % 128)) % 128


def _frame(command: int, address: int, body: bytes) -> bytes:
    payload = _address_bytes(address) + body
    return bytes((0xF0, ROLAND, DEV_ID, *MODEL_ID, command, *payload, checksum(payload), 0xF7))


def dt1(address: int, data: bytes) -> bytes:
    """Write *data* at *address*."""
    return _frame(DT1, address, data)


def rq1(address: int, length: int) -> bytes:
    """Request *length* bytes from *address*."""
    return _frame(RQ1, address, _address_bytes(length))


def parse_dt1(message: bytes) -> tuple[int, bytes] | None:
    """Address and payload of an incoming DT1, or None if this isn't one of ours.

    None rather than a raise for anything unrecognised: the MIDI port carries traffic
    from every device on it (docs/05-foot-control.md notes the same port-sharing
    concern the sibling repo measured against BOSS TONE STUDIO), and a foreign or
    stray SysEx message is not an error, it's somebody else's business.
    """
    head = 3 + len(MODEL_ID)
    if len(message) < head + 6 or message[0] != 0xF0 or message[-1] != 0xF7:
        return None
    if message[1] != ROLAND or tuple(message[3:head]) != MODEL_ID or message[head] != DT1:
        return None
    body = message[head + 1 : -2]
    if len(body) < 4:
        return None
    return _address_of(*body[:4]), bytes(body[4:])


def _patch_number_bytes(number: int) -> bytes:
    """CurrentPatchNum as four nibble-bytes -- INTEGER4x4 with ofs 0, deliberately not
    the FX-Parameter encoding (which adds 32768; see gx100.py's own note on this same
    trap, one repo over)."""
    return bytes((number >> shift) & 0x0F for shift in (12, 8, 4, 0))


def _un_nibble(data: bytes) -> int:
    return sum((b & 0x0F) << shift for b, shift in zip(data, (12, 8, 4, 0), strict=True))


# ── the seam ─────────────────────────────────────────────────────────────
class Transport(Protocol):
    """What reading the pedal needs -- three operations, so the polling and
    restore-on-exit logic below is tested against a fake device, never a real one."""

    def write_patch_number(self, number: int) -> None:
        """Load memory *number* (0-based) -- the same effect as pressing a patch
        button (docs/05-foot-control.md)."""
        ...

    def read_patch_number(self) -> int:
        """Which memory (0-based) CurrentPatchNum reports right now."""
        ...

    def read_patch_name(self) -> str:
        """The edit buffer's own name field, for whatever is loaded into it right
        now -- trimmed of the trailing spaces the wire pads it with."""
        ...


@dataclass
class MidiTransport:
    """The real transport, over `mido`. The one thing here hardware can disagree
    with -- see :func:`open_transport`."""

    port_in: object
    port_out: object
    timeout_s: float = 1.0

    def _send(self, frame: bytes) -> None:
        mido = require_module("mido", "gx100")
        self.port_out.send(mido.Message("sysex", data=list(frame[1:-1])))

    def _request(self, address: int, size: int) -> bytes:
        self._send(rq1(address, size))
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            for incoming in self.port_in.iter_pending():
                if incoming.type != "sysex":
                    continue
                parsed = parse_dt1(bytes([0xF0, *incoming.data, 0xF7]))
                if parsed is None:
                    continue
                reply_address, data = parsed
                if reply_address != address or len(data) < size:
                    continue  # somebody else's reply, or a short one -- not our answer
                return data[:size]
            time.sleep(0.001)
        raise WoodshedError(
            f"the GX-100 did not answer 0x{address:08X} within {self.timeout_s:.1f}s. "
            "Check `woodshed gx100 ports` and that the pedal's MIDI settings allow "
            "SysEx receive."
        )

    def write_patch_number(self, number: int) -> None:
        self._send(dt1(SYSTEM, _patch_number_bytes(number)))

    def read_patch_number(self) -> int:
        return _un_nibble(self._request(SYSTEM, 4))

    def read_patch_name(self) -> str:
        return self._request(EDIT_BUFFER, NAME_SIZE).decode("ascii", "replace").rstrip()

    def close(self) -> None:
        """Release both ports. Safe to call twice."""
        for port in (self.port_in, self.port_out):
            closer = getattr(port, "close", None)
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # a shutdown path must never raise
                pass


def guess_gx100_ports(inputs: list[str], outputs: list[str]) -> tuple[str | None, str | None]:
    """Best guess at the GX-100's port pair, by name."""

    def pick(names: list[str]) -> str | None:
        for name in names:
            if any(hint in name.lower() for hint in GX_HINTS):
                return name
        return None

    return pick(inputs), pick(outputs)


def open_transport(port_in: str | None = None, port_out: str | None = None) -> MidiTransport:
    """Open the GX-100's ports by name, or guess them from what's plugged in."""
    mido = require_module("mido", "gx100")
    try:
        inputs, outputs = list(mido.get_input_names()), list(mido.get_output_names())
    except Exception as exc:  # rtmidi/portmidi/Windows MM each raise something different
        raise WoodshedError(
            f"no MIDI backend on this machine ({exc}). `uv sync --extra gx100` installs "
            "python-rtmidi; this is separate from the pedal being plugged in."
        ) from exc
    guessed_in, guessed_out = guess_gx100_ports(inputs, outputs)
    name_in, name_out = port_in or guessed_in, port_out or guessed_out
    if not name_in or not name_out:
        raise WoodshedError(
            f"could not find the GX-100's MIDI ports. Inputs: {inputs or 'none'}. "
            f"Outputs: {outputs or 'none'}. Pass --port-in/--port-out if the names don't "
            "contain 'GX-100'."
        )
    return MidiTransport(mido.open_input(name_in), mido.open_output(name_out))


# ── orchestration -- pure over Transport, tested with a fake ────────────────
def read_current_patch(transport: Transport) -> int:
    """Which memory (0-based) is loaded right now."""
    return transport.read_patch_number()


def select_patch(
    transport: Transport,
    number: int,
    *,
    settle_s: float = 0.9,
    timeout_s: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Load memory *number* and return its name, once the edit buffer has settled.

    Settle timing lifted from the sibling `gx100` repo's own hardware measurement
    (docs/05-foot-control.md): CurrentPatchNum reports the new value within 16ms, but
    the edit buffer does not actually hold the new patch for another 530-640ms. This
    polls until two reads agree on a name *and* `settle_s` has passed -- a stability
    check alone is not enough, because for the first half-second the *previous*
    patch's name is perfectly stable too.
    """
    if not 0 <= number <= 299:
        raise WoodshedError(f"memory index {number} is outside 0..299")
    transport.write_patch_number(number)
    deadline = clock() + timeout_s
    floor = clock() + settle_s
    previous: str | None = None
    while clock() < deadline:
        name = transport.read_patch_name()
        if name == previous and clock() >= floor:
            return name
        previous = name
        sleep(0.05)
    raise WoodshedError(
        f"memory {number} did not settle within {timeout_s:.1f}s -- the edit buffer was "
        "still changing. Nothing read after this is trustworthy."
    )


def sync_patch_names(
    transport: Transport,
    memories: range = range(MAX_PC + 1),
    *,
    on_progress: Callable[[int, str], None] | None = None,
    **select_kwargs: object,
) -> list[PatchName]:
    """Read every reachable memory's name off the pedal, restoring whatever was
    loaded before this ran.

    *memories* defaults to every memory a bare Program Change can reach
    (U01-1..U32-4, `gx100.MAX_PC` + 1 of them) -- reading further would name
    presets the patch-change lane could never select anyway, since
    `gx100.memory_to_index` refuses anything past that range.

    Restoring is unconditional (`finally`): a sync that fails partway must not
    leave the pedal parked wherever it happened to be, any more than a
    successful one should leave it on U32-4 just because that was read last.
    """
    original = read_current_patch(transport)
    results: list[PatchName] = []
    try:
        for index in memories:
            name = select_patch(transport, index, **select_kwargs)
            results.append(PatchName(memory=index_to_memory(index), name=name))
            if on_progress is not None:
                on_progress(index, name)
    finally:
        select_patch(transport, original, **select_kwargs)
    return results
