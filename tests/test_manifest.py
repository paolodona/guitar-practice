"""Tests for woodshed.manifest: song.yaml / setlist.yaml parsing and I/O.

Test contract (from the B6 unit prompt):
- the exact YAML printed in docs/02-data-model.md for song.yaml and
  setlist.yaml both parse without error
- round-trip (load then save with no edit) is byte-stable for an
  app-written file
- a bare-string setlist entry and a mapping entry both load correctly
- shift: 0 survives as 0 and not None
- a section with end_s <= start_s is refused on load (raise, not silently
  accepted)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from woodshed.errors import WoodshedError
from woodshed.manifest import (
    PracticeDefaults,
    Recording,
    Section,
    Setlist,
    SetlistEntry,
    Song,
    Tempo,
    check_binding,
    effective_pre_roll_beats,
    hash_file,
    load_setlist,
    load_song,
    save_setlist,
    save_song,
    whole_song_section,
)

DOCS_PATH = Path(__file__).resolve().parents[1] / "docs" / "02-data-model.md"


def _extract_fenced_yaml_after(doc_text: str, header: str) -> str:
    """Pull the first ```yaml ... ``` block that follows a given markdown
    header, verbatim (comments included). Reads the block from the doc file
    itself, rather than a copy pasted into this test, so the test fails
    loudly if the doc and this contract ever drift apart.
    """
    header_pos = doc_text.index(header)
    after = doc_text[header_pos:]
    match = re.search(r"```yaml\n(.*?)```", after, flags=re.DOTALL)
    assert match is not None, f"no ```yaml block found after {header!r}"
    return match.group(1)


@pytest.fixture(scope="module")
def docs_text() -> str:
    return DOCS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def song_yaml_text(docs_text: str) -> str:
    return _extract_fenced_yaml_after(docs_text, "## `song.yaml`")


@pytest.fixture(scope="module")
def setlist_yaml_text(docs_text: str) -> str:
    return _extract_fenced_yaml_after(docs_text, "## `setlists/<slug>.yaml`")


# --- the exact docs YAML parses without error -------------------------------


def test_docs_song_yaml_parses(song_yaml_text: str, tmp_path: Path) -> None:
    path = tmp_path / "song.yaml"
    path.write_text(song_yaml_text, encoding="utf-8")
    song = load_song(path)
    # `can-t-stop`, not `cant-stop`: slugify makes an apostrophe a separator
    # and that is settled (see library.slugify's docstring -- a slug is a
    # permanent identity because the ledger names it and is never rewritten).
    # The docs' example says so too, which is what this test reads.
    assert song.slug == "can-t-stop"
    assert song.title == "Can't Stop"
    assert song.recording.file == "audio/cant-stop.flac"
    assert song.recording.tuning == "E standard"
    assert song.tempo.source == "refined"
    assert len(song.sections) == 5
    assert [s.id for s in song.sections] == [
        "intro", "solo-full", "solo-tapping", "solo-run", "whole-song",
    ]
    whole_song = next(s for s in song.sections if s.id == "whole-song")
    assert whole_song.full_song is True
    # per-section overrides and optional fields survive
    tapping = next(s for s in song.sections if s.id == "solo-tapping")
    assert tapping.start_speed == 45.0
    assert tapping.ladder_step == 2.5
    assert tapping.notes is not None and "12th" in tapping.notes
    intro = next(s for s in song.sections if s.id == "intro")
    assert intro.patch == "rhythm"
    solo_full = next(s for s in song.sections if s.id == "solo-full")
    assert solo_full.patch is None
    assert solo_full.start_speed is None
    assert solo_full.ladder_step is None
    # counts_toward_readiness defaults True when absent from the file
    assert all(s.counts_toward_readiness for s in song.sections)


def test_a_leftover_click_key_from_before_it_was_removed_still_parses(
    song_yaml_text: str, tmp_path: Path
) -> None:
    """#5: the lead-in click feature is gone, but every song.yaml committed
    before this change still has `practice.click: lead-in` written on disk
    -- dropping the field from PracticeDefaults must be a read-side no-op,
    not a parse error (CLAUDE.md: "a song.yaml written before a field
    existed still loads"; the same grace has to run in reverse for a field
    that stops existing). `PracticeDefaults`' own `extra="allow"` already
    guarantees this -- an unknown key is accepted, not rejected -- so this
    pins that guarantee down rather than leaving it implicit.
    """
    data = yaml.safe_load(song_yaml_text)
    data["practice"]["click"] = "lead-in"
    path = tmp_path / "song.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    song = load_song(path)  # must not raise
    assert song.practice.start_speed == 50.0  # the rest of the file still loads normally
    assert "click" not in PracticeDefaults.model_fields  # gone as a declared field, not just unused


def test_docs_setlist_yaml_parses(setlist_yaml_text: str, tmp_path: Path) -> None:
    path = tmp_path / "setlist.yaml"
    path.write_text(setlist_yaml_text, encoding="utf-8")
    setlist = load_setlist(path)
    assert setlist.name == "Ramba S.S. — the set"
    assert setlist.tuning == "Eb standard"
    assert str(setlist.date) == "2027-04-17"
    assert setlist.venue == ""
    assert len(setlist.songs) == 3
    assert setlist.songs[0].slug == "can-t-stop"
    assert setlist.songs[0].shift is None  # bare string -> derive
    assert setlist.songs[1].slug == "manlio"
    assert setlist.songs[1].shift == 0  # explicit zero, not derived
    assert setlist.songs[2].slug == "sultans-of-swing"
    assert setlist.songs[2].shift == -2
    assert setlist.notes is not None and "Free text" in setlist.notes


# --- bare-string vs mapping setlist entries, and shift: 0 vs None -----------


def test_setlist_entry_accepts_bare_string() -> None:
    entry = SetlistEntry.model_validate("cant-stop")
    assert entry.slug == "cant-stop"
    assert entry.shift is None


def test_setlist_entry_accepts_mapping() -> None:
    entry = SetlistEntry.model_validate({"slug": "manlio", "shift": 0})
    assert entry.slug == "manlio"
    assert entry.shift == 0


def test_setlist_entry_mapping_without_shift_is_none() -> None:
    entry = SetlistEntry.model_validate({"slug": "sultans-of-swing"})
    assert entry.shift is None


def test_shift_zero_does_not_collapse_to_none() -> None:
    """The whole point of the shift:None vs shift:0 distinction: an entry
    that explicitly says 0 must be told apart from one that never said
    anything, because "explicitly zero" and "derive it" mean different
    things (docs/02-data-model.md's transpose invariant)."""
    explicit_zero = SetlistEntry.model_validate({"slug": "manlio", "shift": 0})
    derive = SetlistEntry.model_validate({"slug": "manlio"})
    assert explicit_zero.shift == 0
    assert explicit_zero.shift is not None
    assert derive.shift is None


@pytest.mark.parametrize("shift", [6, -6, 0, 3])
def test_setlist_entry_shift_within_range_ok(shift: int) -> None:
    entry = SetlistEntry.model_validate({"slug": "s", "shift": shift})
    assert entry.shift == shift


@pytest.mark.parametrize("shift", [7, -7, 100])
def test_setlist_entry_shift_out_of_range_raises(shift: int) -> None:
    with pytest.raises(WoodshedError):
        SetlistEntry.model_validate({"slug": "s", "shift": shift})


# --- tuning is validated at the model, for Recording and Setlist alike ------


@pytest.mark.parametrize(
    "tuning", ["E standard", "Eb standard", "D standard", "Drop D", "Drop C#"],
)
def test_recording_known_tuning_parses(tuning: str) -> None:
    rec = Recording(file="a.flac", sha256="a" * 64, duration_s=10.0, tuning=tuning)
    assert rec.tuning == tuning


def test_recording_unknown_tuning_raises() -> None:
    # The typo this validator exists to catch: "Eb" is not a tuning name,
    # "Eb standard" is -- and this must fail at load, not three calls later
    # when something finally calls tuning.pitch_of on it.
    with pytest.raises(WoodshedError):
        Recording(file="a.flac", sha256="a" * 64, duration_s=10.0, tuning="Eb")


@pytest.mark.parametrize(
    "tuning", ["E standard", "Eb standard", "D standard", "Drop D", "Drop C#"],
)
def test_setlist_known_tuning_parses(tuning: str) -> None:
    setlist = Setlist(name="The Gig", tuning=tuning)
    assert setlist.tuning == tuning


def test_setlist_unknown_tuning_raises() -> None:
    with pytest.raises(WoodshedError):
        Setlist(name="The Gig", tuning="Eb")


# --- a section with end_s <= start_s is refused on load ---------------------


def test_section_end_before_start_raises() -> None:
    with pytest.raises(WoodshedError):
        Section(
            id="bad",
            name="Bad section",
            start_s=10.0,
            end_s=5.0,
            snapped="free",
            target_speed=100,
        )


def test_section_end_equal_start_raises() -> None:
    with pytest.raises(WoodshedError):
        Section(
            id="bad",
            name="Bad section",
            start_s=10.0,
            end_s=10.0,
            snapped="free",
            target_speed=100,
        )


def test_song_load_refuses_bad_section(tmp_path: Path, song_yaml_text: str) -> None:
    data = yaml.safe_load(song_yaml_text)
    data["sections"][0]["end_s"] = data["sections"][0]["start_s"]
    path = tmp_path / "song.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(WoodshedError):
        load_song(path)


# --- the degrade path: tempo.bpm 0 or absent is not an error ---------------
# docs/02-data-model.md:160 -- "if tempo.bpm is 0 or absent, the app still
# works: no grid, no bar ruler, free-dragged boundaries." Every grid
# consumer has to tolerate this and none of them will unless a test says so.


def test_song_with_no_tempo_key_at_all_still_loads(tmp_path: Path, song_yaml_text: str) -> None:
    data = yaml.safe_load(song_yaml_text)
    del data["tempo"]
    path = tmp_path / "song.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    song = load_song(path)
    assert song.tempo.bpm == 0.0
    assert song.tempo.confidence is None


def test_tempo_bpm_defaults_to_zero_not_a_validation_error() -> None:
    assert Tempo().bpm == 0.0


def test_section_valid_span_does_not_raise() -> None:
    section = Section(
        id="ok",
        name="Ok section",
        start_s=5.0,
        end_s=10.0,
        snapped="free",
        target_speed=100,
    )
    assert section.end_s > section.start_s


# --- lead_in_beats (Phase 1, G2) -- docs/00-spec.md:98 ----------------------


def test_section_lead_in_beats_defaults_none() -> None:
    section = Section(
        id="ok", name="Ok", start_s=0.0, end_s=10.0, snapped="free", target_speed=100,
    )
    assert section.lead_in_beats is None


def test_section_lead_in_beats_round_trips() -> None:
    section = Section(
        id="ok", name="Ok", start_s=0.0, end_s=10.0, snapped="free", target_speed=100,
        lead_in_beats=8,
    )
    assert section.lead_in_beats == 8


# --- full_song: the whole-song rep counter -----------------------------


def test_section_full_song_defaults_false() -> None:
    section = Section(
        id="ok", name="Ok", start_s=0.0, end_s=10.0, snapped="free", target_speed=100,
    )
    assert section.full_song is False


def test_section_full_song_round_trips_true() -> None:
    section = Section(
        id="ok", name="Ok", start_s=0.0, end_s=10.0, snapped="free", target_speed=100,
        full_song=True,
    )
    assert section.full_song is True


def test_whole_song_section_spans_the_full_duration() -> None:
    section = whole_song_section(194.78)
    assert section.id == "whole-song"
    assert section.name == "Whole song"
    assert section.start_s == 0.0
    assert section.end_s == 194.78
    assert section.full_song is True
    assert section.snapped == "free"
    assert section.target_speed == 100.0


def test_whole_song_section_is_an_ordinary_removable_section() -> None:
    """Nothing about it is special beyond full_song=True -- it round-trips
    through Song like any other section."""
    song = _song_with_section(whole_song_section(60.0))
    assert song.sections[0].full_song is True
    assert len(song.sections) == 1


def _song_with_section(section: Section, pre_roll_beats: float = 4.0) -> Song:
    return Song(
        slug="s", title="T", artist="A",
        recording=Recording(file="a.flac", sha256="a" * 64, duration_s=10.0, tuning="E standard"),
        practice=PracticeDefaults(pre_roll_beats=pre_roll_beats),
        sections=[section],
    )


def test_effective_pre_roll_beats_uses_song_default_when_section_has_none() -> None:
    section = Section(
        id="ok", name="Ok", start_s=0.0, end_s=10.0, snapped="free", target_speed=100,
    )
    song = _song_with_section(section, pre_roll_beats=4.0)
    assert effective_pre_roll_beats(song, section) == 4.0


def test_effective_pre_roll_beats_section_override_wins() -> None:
    section = Section(
        id="ok", name="Ok", start_s=0.0, end_s=10.0, snapped="free", target_speed=100,
        lead_in_beats=8,
    )
    song = _song_with_section(section, pre_roll_beats=4.0)
    assert effective_pre_roll_beats(song, section) == 8


# --- round trip: byte-stable for an app-written file ------------------------


def _minimal_song() -> Song:
    return Song(
        slug="cant-stop",
        title="Can't Stop",
        artist="Red Hot Chili Peppers",
        album="By the Way",
        recording=Recording(
            file="audio/cant-stop.flac",
            sha256="9f2c",
            duration_s=269.41,
            tuning="E standard",
            spotify_id="3ZOEytgrvLwQaqXreDs2Jx",
            source="own rip, CD",
        ),
        tempo=Tempo(
            bpm=91.53,
            source="refined",
            grid_offset_s=0.412,
            time_signature="4/4",
            confidence=0.86,
        ),
        practice=PracticeDefaults(),
        sections=[
            Section(
                id="intro",
                name="Intro riff",
                start_s=0.412,
                end_s=21.874,
                snapped="beat",
                target_speed=100,
                notes="16ths, muted.",
                patch="rhythm",
            ),
            Section(
                id="solo-full",
                name="Full solo",
                start_s=178.4,
                end_s=262.9,
                snapped="bar",
                target_speed=100,
            ),
        ],
    )


def test_save_song_round_trip_is_byte_stable(tmp_path: Path) -> None:
    song = _minimal_song()
    path1 = tmp_path / "song1.yaml"
    path2 = tmp_path / "song2.yaml"

    save_song(song, path1)
    reloaded = load_song(path1)
    save_song(reloaded, path2)

    assert path1.read_bytes() == path2.read_bytes()


@pytest.mark.parametrize("source", ["detected", "refined", "tapped", "manual"])
def test_tempo_source_round_trips_through_song_yaml(
    source: str, tmp_path: Path
) -> None:
    """Every value Tempo.source can take -- "each recording which it was",
    docs/07-roadmap.md -- survives a save/load cycle unchanged."""
    song = _minimal_song()
    song.tempo = Tempo(bpm=100.0, source=source, grid_offset_s=0.0,
                        time_signature="4/4", confidence=None if source in
                        ("tapped", "manual") else 0.5)
    path = tmp_path / "song.yaml"
    save_song(song, path)
    assert load_song(path).tempo.source == source


def test_save_song_preserves_field_order(tmp_path: Path) -> None:
    song = _minimal_song()
    path = tmp_path / "song.yaml"
    save_song(song, path)
    text = path.read_text(encoding="utf-8")
    top_level_keys = [
        line.split(":")[0]
        for line in text.splitlines()
        if line and not line.startswith(" ") and not line.startswith("-") and ":" in line
    ]
    assert top_level_keys.index("slug") < top_level_keys.index("title")
    assert top_level_keys.index("recording") < top_level_keys.index("tempo")
    assert top_level_keys.index("tempo") < top_level_keys.index("sections")


def test_save_setlist_round_trip_is_byte_stable(tmp_path: Path) -> None:
    setlist = Setlist(
        name="Ramba S.S. — the set",
        tuning="Eb standard",
        date="2027-04-17",
        venue="",
        songs=[
            SetlistEntry(slug="cant-stop"),
            SetlistEntry(slug="manlio", shift=0),
            SetlistEntry(slug="sultans-of-swing", shift=-2),
        ],
        notes="Free text.\n",
    )
    path1 = tmp_path / "setlist1.yaml"
    path2 = tmp_path / "setlist2.yaml"

    save_setlist(setlist, path1)
    reloaded = load_setlist(path1)
    save_setlist(reloaded, path2)

    assert path1.read_bytes() == path2.read_bytes()


def test_save_setlist_shift_zero_round_trips_as_zero(tmp_path: Path) -> None:
    setlist = Setlist(
        name="n",
        tuning="E standard",
        songs=[SetlistEntry(slug="manlio", shift=0)],
    )
    path = tmp_path / "setlist.yaml"
    save_setlist(setlist, path)
    reloaded = load_setlist(path)
    assert reloaded.songs[0].shift == 0
    assert reloaded.songs[0].shift is not None
    text = path.read_text(encoding="utf-8")
    assert "shift: 0" in text


# --- hash_file ---------------------------------------------------------------


def test_hash_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    p = tmp_path / "data.bin"
    p.write_bytes(b"some audio-shaped bytes" * 1000)
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert hash_file(p) == expected


def test_hash_file_streamed_on_large_file(tmp_path: Path) -> None:
    """Not a memory-profiling test (out of reach for a fast unit test) --
    just confirms hash_file gives the right answer for a file larger than
    one obvious read-in-one-go chunk size, which is what a naive
    ``f.read()`` and a streamed reader would both get right; the streaming
    contract itself is enforced by code review / the source reading
    ``open(..., 'rb')`` in fixed chunks, per the module docstring."""
    import hashlib

    p = tmp_path / "big.bin"
    with open(p, "wb") as f:
        for _ in range(20):
            f.write(b"x" * (1024 * 1024))
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert hash_file(p) == expected


# --- check_binding -----------------------------------------------------------


class _FakeRepo:
    """Duck-types library.Repo's song_dir(slug) -> Path, without importing
    library.py (owned by another unit)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def song_dir(self, slug: str) -> Path:
        return self.root / "songs" / slug


def test_check_binding_ok_when_file_matches(tmp_path: Path) -> None:
    repo = _FakeRepo(tmp_path)
    song_dir = repo.song_dir("cant-stop")
    (song_dir / "audio").mkdir(parents=True)
    audio_path = song_dir / "audio" / "cant-stop.flac"
    audio_path.write_bytes(b"fake audio bytes")

    song = _minimal_song()
    song.recording.sha256 = hash_file(audio_path)

    assert check_binding(song, repo) is None


def test_check_binding_reports_missing_file(tmp_path: Path) -> None:
    repo = _FakeRepo(tmp_path)
    song = _minimal_song()

    result = check_binding(song, repo)
    assert result is not None
    assert "cant-stop" in result
    assert "missing" in result.lower()


def test_check_binding_reports_hash_mismatch(tmp_path: Path) -> None:
    repo = _FakeRepo(tmp_path)
    song_dir = repo.song_dir("cant-stop")
    (song_dir / "audio").mkdir(parents=True)
    audio_path = song_dir / "audio" / "cant-stop.flac"
    audio_path.write_bytes(b"fake audio bytes")

    song = _minimal_song()
    song.recording.sha256 = "0" * 64  # deliberately wrong

    result = check_binding(song, repo)
    assert result is not None
    assert "cant-stop" in result
