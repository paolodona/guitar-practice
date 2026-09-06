"""Tests for woodshed.ledger -- CLAUDE.md invariants 5 and 6.

Uses the shared ``repo`` (ScratchRepo) fixture from tests/conftest.py for the
on-disk layout, wrapped in a tiny local adapter that exposes the
``ledger_path()`` method ledger.py's ``_HasLedgerPath`` Protocol expects --
``library.Repo`` is a different unit's file and must not be imported here.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from woodshed import ledger

DOCS_PATH = Path(__file__).resolve().parents[1] / "docs" / "02-data-model.md"

# The exact bytes of docs/02-data-model.md's printed practice/reps.jsonl
# example (its two physical lines joined -- the line break there is only
# markdown wrapping of one JSON object).
DOC_EXAMPLE = (
    '{"t":"2026-09-05T19:22:41Z","song":"can-t-stop","section":"solo","speed":55,'
    '"semitones":-1,"pass":true,"clean":true,"loop_s":31.2,"setlist":"gig","source":"midi"}'
)


@dataclass(frozen=True)
class _LedgerRepo:
    """Minimal stand-in satisfying ledger.py's ``_HasLedgerPath`` Protocol."""

    root: Path

    def ledger_path(self) -> Path:
        return self.root / "practice" / "reps.jsonl"


@pytest.fixture
def ledger_repo(repo) -> _LedgerRepo:
    return _LedgerRepo(root=repo.root)


def make_rep(**overrides) -> ledger.Rep:
    values = dict(
        id=uuid4().hex,
        t="2026-09-05T19:22:41Z",
        song="can-t-stop",
        section="solo",
        speed=55,  # matches docs/02-data-model.md's example bytes exactly ("55", not "55.0")
        semitones=-1,
        passed=True,
        clean=True,
        loop_s=31.2,
        setlist="gig",
        source="midi",
    )
    values.update(overrides)
    return ledger.Rep(**values)


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# --- docs example must confirm the fixture above matches the real file -----


def test_doc_example_constant_matches_the_file():
    text = DOCS_PATH.read_text(encoding="utf-8")
    match = re.search(r"## `practice/reps\.jsonl`.*?```json\n(.*?)\n```", text, re.S)
    assert match, "couldn't find the reps.jsonl example in docs/02-data-model.md"
    joined = "".join(line.strip() for line in match.group(1).splitlines())
    assert joined == DOC_EXAMPLE


# --- exact on-disk bytes ----------------------------------------------------


def test_appended_line_matches_documented_example_bytes(ledger_repo):
    rep = make_rep(id="deadbeefcafebabe0000000000000000")
    ledger.append(ledger_repo, rep)

    lines = read_lines(ledger_repo.ledger_path())
    assert len(lines) == 1
    expected = DOC_EXAMPLE[:-1] + f',"id":"{rep.id}"}}'
    assert lines[0] == expected
    # and it really does say "pass", never "passed"
    assert '"pass":true' in lines[0]
    assert "passed" not in lines[0]


def test_ordinary_rep_carries_no_retracted_keys(ledger_repo):
    ledger.append(ledger_repo, make_rep())
    (line,) = read_lines(ledger_repo.ledger_path())
    data = json.loads(line)
    assert "retracted" not in data
    assert "retracts" not in data


# --- append-only -------------------------------------------------------------


def test_append_twice_then_read_gives_exactly_two_lines(ledger_repo):
    ledger.append(ledger_repo, make_rep())
    ledger.append(ledger_repo, make_rep())

    assert list(ledger.read(ledger_repo)) != []
    reps = list(ledger.read(ledger_repo))
    assert len(reps) == 2
    assert len(read_lines(ledger_repo.ledger_path())) == 2


def test_file_length_only_ever_grows(ledger_repo):
    path = ledger_repo.ledger_path()
    sizes = []

    ledger.append(ledger_repo, make_rep())
    sizes.append(path.stat().st_size)

    ledger.append(ledger_repo, make_rep(section="chorus"))
    sizes.append(path.stat().st_size)

    target = make_rep()
    ledger.append(ledger_repo, target)
    sizes.append(path.stat().st_size)

    retraction = make_rep(retracted=True, retracts=target.id)
    ledger.append(ledger_repo, retraction)
    sizes.append(path.stat().st_size)

    assert sizes == sorted(sizes)
    assert all(b > a for a, b in zip(sizes, sizes[1:], strict=False))


def test_retraction_removes_exactly_one_rep_but_file_keeps_all_lines(ledger_repo):
    first = make_rep()
    second = make_rep()
    ledger.append(ledger_repo, first)
    ledger.append(ledger_repo, second)
    ledger.append(
        ledger_repo, make_rep(retracted=True, retracts=first.id, passed=False, clean=False)
    )

    raw = list(ledger.read(ledger_repo))
    assert len(raw) == 3  # nothing was deleted from disk

    resolved = ledger.resolve(raw)
    assert len(resolved) == 1
    assert resolved[0].id == second.id


# --- corrupt lines ------------------------------------------------------------


def test_corrupt_middle_line_does_not_stop_read(ledger_repo):
    path = ledger_repo.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    good_before = make_rep(section="intro")
    good_after = make_rep(section="outro")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ledger._rep_to_dict(good_before), separators=(",", ":")) + "\n")
        f.write("{not valid json at all\n")
        f.write('{"song":"missing-fields-only"}\n')
        f.write(json.dumps(ledger._rep_to_dict(good_after), separators=(",", ":")) + "\n")

    reps = list(ledger.read(ledger_repo))
    assert [r.section for r in reps] == ["intro", "outro"]


# --- best_sustained_speed -----------------------------------------------------


def test_best_sustained_speed_needs_reps_to_advance_cleans_not_just_one(ledger_repo):
    reps = [
        make_rep(speed=60.0, clean=True),
        make_rep(speed=80.0, clean=True),  # only one clean at 80
    ]
    assert ledger.best_sustained_speed(reps, "can-t-stop", "solo", reps_to_advance=3) == 0.0

    reps += [make_rep(speed=60.0, clean=True), make_rep(speed=60.0, clean=True)]
    # now 60 has 3 cleans, 80 still has 1
    assert ledger.best_sustained_speed(reps, "can-t-stop", "solo", reps_to_advance=3) == 60.0


def test_best_sustained_speed_picks_the_highest_qualifying_speed(ledger_repo):
    reps = (
        [make_rep(speed=50.0, clean=True) for _ in range(3)]
        + [make_rep(speed=70.0, clean=True) for _ in range(3)]
        + [make_rep(speed=90.0, clean=True) for _ in range(2)]
    )
    assert ledger.best_sustained_speed(reps, "can-t-stop", "solo", reps_to_advance=3) == 70.0


# --- a retraction line itself never counts ------------------------------------


def test_retraction_line_excluded_from_totals_clean_by_speed_and_best_sustained_speed(
    ledger_repo,
):
    target = make_rep(speed=75.0, clean=True, passed=True)
    # The retraction line spuriously carries passed=True, clean=True at the
    # same speed -- it must still never be counted, because retracted=True
    # excludes it regardless of its own flags.
    retraction = make_rep(
        speed=75.0, clean=True, passed=True, retracted=True, retracts=target.id
    )
    reps = [target, retraction]

    assert ledger.clean_by_speed(reps, "can-t-stop", "solo") == {}
    assert ledger.best_sustained_speed(reps, "can-t-stop", "solo", reps_to_advance=1) == 0.0
    totals = ledger.totals(reps, "can-t-stop", "solo")
    assert totals.passes == 0
    assert totals.cleans == 0
    assert totals.minutes == 0.0


# --- last_practised and totals -------------------------------------------------


def test_last_practised_is_max_t_among_matching_resolved_reps(ledger_repo):
    reps = [
        make_rep(t="2026-09-01T10:00:00Z"),
        make_rep(t="2026-09-05T19:22:41Z"),
        make_rep(t="2026-09-03T08:00:00Z", song="other-song"),
    ]
    result = ledger.last_practised(reps, "can-t-stop", "solo")
    assert result is not None
    assert result.isoformat() == "2026-09-05T19:22:41+00:00"


def test_last_practised_returns_none_when_nothing_matches(ledger_repo):
    assert ledger.last_practised([], "can-t-stop", "solo") is None


def test_totals_sums_passes_cleans_and_minutes(ledger_repo):
    reps = [
        make_rep(passed=True, clean=True, loop_s=30.0),
        make_rep(passed=True, clean=False, loop_s=30.0),
        make_rep(passed=False, clean=False, loop_s=60.0),
    ]
    result = ledger.totals(reps, "can-t-stop", "solo")
    assert result.passes == 2
    assert result.cleans == 1
    assert result.minutes == pytest.approx(2.0)


def test_totals_and_last_practised_scope_by_section_when_given(ledger_repo):
    reps = [
        make_rep(section="solo", loop_s=60.0),
        make_rep(section="intro", loop_s=60.0),
    ]
    assert ledger.totals(reps, "can-t-stop", "solo").passes == 1
    assert ledger.totals(reps, "can-t-stop").passes == 2  # no section -> whole song


# --- last_speed -----------------------------------------------------------
# Found live 2026-09-06, Paolo: "not all songs or sections will be practiced
# from 50%" -- a full_song section resumes at the LAST speed practiced,
# not an earned ladder rung (see server.py's `_section_starting_speed`).


def test_last_speed_is_the_most_recently_timestamped_reps_speed(ledger_repo):
    reps = [
        make_rep(t="2026-09-01T10:00:00Z", speed=50.0),
        make_rep(t="2026-09-05T19:22:41Z", speed=85.0),
        make_rep(t="2026-09-03T08:00:00Z", speed=70.0),
    ]
    assert ledger.last_speed(reps, "can-t-stop", "solo") == 85.0


def test_last_speed_returns_none_when_nothing_matches(ledger_repo):
    assert ledger.last_speed([], "can-t-stop", "whole-song") is None


def test_last_speed_ignores_clean_flag_unclean_still_counts(ledger_repo):
    """Full-song practice remembers wherever the last pass left off, clean
    or not -- there is no rung being earned to gate it on."""
    reps = [
        make_rep(t="2026-09-01T10:00:00Z", speed=60.0, clean=True),
        make_rep(t="2026-09-05T19:22:41Z", speed=92.0, clean=False),
    ]
    assert ledger.last_speed(reps, "can-t-stop", "solo") == 92.0


def test_last_speed_ignores_a_retracted_last_rep(ledger_repo):
    original = make_rep(t="2026-09-01T10:00:00Z", speed=60.0)
    later = make_rep(t="2026-09-05T19:22:41Z", speed=92.0)
    retraction = make_rep(
        t="2026-09-05T19:23:00Z", speed=92.0, retracted=True, retracts=later.id
    )
    assert ledger.last_speed([original, later, retraction], "can-t-stop", "solo") == 60.0


def test_last_speed_scopes_by_section(ledger_repo):
    reps = [
        make_rep(section="solo", speed=90.0, t="2026-09-05T19:22:41Z"),
        make_rep(section="intro", speed=50.0, t="2026-09-06T09:00:00Z"),
    ]
    assert ledger.last_speed(reps, "can-t-stop", "solo") == 90.0


# --- append() creates the parent directory -------------------------------------


def test_append_creates_practice_dir_if_missing(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    lr = _LedgerRepo(root=root)
    assert not lr.ledger_path().parent.exists()
    ledger.append(lr, make_rep())
    assert lr.ledger_path().exists()


# --- the module never opens the ledger for anything but append/read-only -------


def test_module_never_opens_ledger_in_write_or_read_write_mode():
    source = Path(inspect.getfile(ledger)).read_text(encoding="utf-8")
    calls = re.findall(r"open\(([^)]*)\)", source)
    assert calls, "expected at least one open() call in ledger.py"
    forbidden = ("'w'", '"w"', "'r+'", '"r+"', "'a+'", '"a+"', "'x'", '"x"')
    for call in calls:
        assert not any(bad in call for bad in forbidden), f"forbidden mode in open({call})"
        # every open() must be explicit read (default) or append ("a")
        if "'a'" in call or '"a"' in call:
            continue
        # otherwise it must not declare any writing mode at all
        assert "'w'" not in call and '"w"' not in call
