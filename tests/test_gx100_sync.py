"""Tests for woodshed.gx100_sync -- reading real patch names off the pedal into
config/gx100.yaml (#3, N2 follow-up: config/gx100.yaml as a *cache* of the
pedalboard, not a hand-typed list).

Everything below runs against `FakeTransport`, a tiny in-memory stand-in for the
three things :class:`~woodshed.gx100_sync.Transport` needs -- no `mido`, no MIDI
backend, no pedal. That mirrors CLAUDE.md's own rule for the pure modules: the
polling and restore-on-exit logic is hard maths over an interface, and it stays
testable without hardware in the loop. `MidiTransport`/`open_transport` -- the
one seam hardware actually can disagree with -- are exercised nowhere here.

A parallel set of tests below checks the SysEx frame builders (`dt1`/`rq1`/
`parse_dt1`/`checksum`) round-trip correctly; those are the part a wrong byte
would fail *silently* on real hardware, so they get their own direct coverage
rather than only being exercised indirectly through a fake.
"""

from __future__ import annotations

import pytest

from woodshed import gx100_sync
from woodshed.errors import WoodshedError
from woodshed.gx100 import MAX_PC, index_to_memory


class FakeTransport:
    """A pedal with a name for every memory, whose edit buffer takes
    *reads_to_settle* reads after a select before it reports the real name --
    exercising the settle-polling loop without a real clock or a real pedal.
    """

    def __init__(self, names: dict[int, str], reads_to_settle: int = 1):
        self.names = names
        self.reads_to_settle = reads_to_settle
        self.current = 0
        self._reads_since_select = 0
        self.selected_log: list[int] = []
        self.fail_on: set[int] = set()

    def write_patch_number(self, number: int) -> None:
        if number in self.fail_on:
            raise RuntimeError(f"the fake pedal refuses memory {number}")
        self.current = number
        self._reads_since_select = 0
        self.selected_log.append(number)

    def read_patch_number(self) -> int:
        return self.current

    def read_patch_name(self) -> str:
        self._reads_since_select += 1
        if self._reads_since_select < self.reads_to_settle:
            # A different string every call, so an unsettled read is never
            # mistaken for two reads agreeing on a (wrong) stable value.
            return f"SETTLING-{self._reads_since_select}"
        return self.names.get(self.current, "")


def _fast_clock(step: float = 0.05):
    """A clock/sleep pair that advances only when `sleep` is called, so a
    settle loop resolves in microseconds of real time instead of seconds."""
    state = {"t": 0.0}

    def clock() -> float:
        return state["t"]

    def sleep(dt: float) -> None:
        state["t"] += dt

    return clock, sleep


# ── select_patch ─────────────────────────────────────────────────────────


def test_select_patch_writes_the_number_and_returns_the_settled_name() -> None:
    transport = FakeTransport({0: "CLEAN", 1: "LEAD CRUNCH"}, reads_to_settle=1)
    clock, sleep = _fast_clock()
    name = gx100_sync.select_patch(transport, 1, settle_s=0.2, clock=clock, sleep=sleep)
    assert name == "LEAD CRUNCH"
    assert transport.selected_log == [1]


def test_select_patch_waits_for_two_agreeing_reads_past_the_settle_floor() -> None:
    # Three reads before the name stops changing, and a settle floor that
    # needs several 0.05s ticks -- the return must not fire on the first
    # read that merely *looks* stable before the floor has passed, the same
    # trap the sibling repo's own select_patch docstring names.
    transport = FakeTransport({5: "SOLO PATCH"}, reads_to_settle=3)
    clock, sleep = _fast_clock()
    name = gx100_sync.select_patch(transport, 5, settle_s=0.2, clock=clock, sleep=sleep)
    assert name == "SOLO PATCH"
    assert clock() >= 0.2


def test_select_patch_refuses_outside_the_pedals_own_memory_range() -> None:
    transport = FakeTransport({})
    with pytest.raises(WoodshedError):
        gx100_sync.select_patch(transport, 300)


def test_select_patch_raises_if_the_edit_buffer_never_settles() -> None:
    transport = FakeTransport({0: "X"}, reads_to_settle=10_000)  # never gets there
    clock, sleep = _fast_clock()
    with pytest.raises(WoodshedError):
        gx100_sync.select_patch(transport, 0, settle_s=0.1, timeout_s=0.3, clock=clock, sleep=sleep)


# ── read_current_patch ───────────────────────────────────────────────────


def test_read_current_patch_reads_whatever_is_loaded() -> None:
    transport = FakeTransport({})
    transport.current = 42
    assert gx100_sync.read_current_patch(transport) == 42


# ── sync_patch_names ─────────────────────────────────────────────────────


def test_sync_patch_names_reads_every_requested_memory_in_order() -> None:
    transport = FakeTransport({0: "CLEAN", 1: "CRUNCH", 2: "LEAD"})
    clock, sleep = _fast_clock()
    patches = gx100_sync.sync_patch_names(
        transport, memories=range(3), clock=clock, sleep=sleep, settle_s=0.0
    )
    assert [(p.memory, p.name) for p in patches] == [
        (index_to_memory(0), "CLEAN"),
        (index_to_memory(1), "CRUNCH"),
        (index_to_memory(2), "LEAD"),
    ]


def test_sync_patch_names_restores_whatever_was_loaded_before_it_ran() -> None:
    transport = FakeTransport({0: "A", 1: "B", 2: "C"})
    transport.current = 7  # what the pedal was on before sync started
    clock, sleep = _fast_clock()
    gx100_sync.sync_patch_names(
        transport, memories=range(3), clock=clock, sleep=sleep, settle_s=0.0
    )
    assert transport.selected_log[-1] == 7
    assert transport.current == 7


def test_sync_patch_names_restores_even_when_a_select_fails_partway() -> None:
    transport = FakeTransport({0: "A", 1: "B", 2: "C"})
    transport.current = 9
    transport.fail_on = {2}
    clock, sleep = _fast_clock()
    with pytest.raises(RuntimeError):
        gx100_sync.sync_patch_names(
            transport, memories=range(3), clock=clock, sleep=sleep, settle_s=0.0
        )
    # The restore in `finally` still ran, and landed on the original memory.
    assert transport.selected_log[-1] == 9
    assert transport.current == 9


def test_sync_patch_names_reports_progress_as_it_goes() -> None:
    transport = FakeTransport({0: "A", 1: "B"})
    clock, sleep = _fast_clock()
    seen: list[tuple[int, str]] = []
    gx100_sync.sync_patch_names(
        transport, memories=range(2),
        on_progress=lambda index, name: seen.append((index, name)),
        clock=clock, sleep=sleep, settle_s=0.0,
    )
    assert seen == [(0, "A"), (1, "B")]


def test_sync_patch_names_defaults_to_every_program_change_reachable_memory() -> None:
    names = {i: f"P{i}" for i in range(MAX_PC + 1)}
    transport = FakeTransport(names)
    clock, sleep = _fast_clock()
    patches = gx100_sync.sync_patch_names(transport, clock=clock, sleep=sleep, settle_s=0.0)
    assert len(patches) == MAX_PC + 1
    assert patches[0].memory == "U01-1"
    assert patches[-1].memory == "U32-4"


# ── SysEx frame builders -- the part a wrong byte fails silently on hardware ─


def test_dt1_round_trips_through_parse_dt1() -> None:
    frame = gx100_sync.dt1(gx100_sync.SYSTEM, bytes([0x00, 0x00, 0x00, 0x05]))
    parsed = gx100_sync.parse_dt1(frame)
    assert parsed == (gx100_sync.SYSTEM, bytes([0x00, 0x00, 0x00, 0x05]))


def test_parse_dt1_returns_none_for_a_foreign_message() -> None:
    # Not a Roland frame at all -- must not raise, per the module's own "not our
    # business" contract for shared-port traffic.
    assert gx100_sync.parse_dt1(bytes([0xF0, 0x43, 0x00, 0xF7])) is None


def test_parse_dt1_returns_none_for_an_rq1_not_a_dt1() -> None:
    request = gx100_sync.rq1(gx100_sync.EDIT_BUFFER, 16)
    assert gx100_sync.parse_dt1(request) is None


def test_checksum_is_what_roland_expects_it_to_be() -> None:
    # (128 - (sum % 128)) % 128 -- verified against the sibling repo's own
    # hardware-tested implementation of the same manufacturer formula.
    payload = bytes([0x00, 0x00, 0x00, 0x00, 0x05])
    assert (sum(payload) + gx100_sync.checksum(payload)) % 128 == 0
