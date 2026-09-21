"""Tests for woodshed.beats -- the cached, per-section beat list.

Test contract, mirroring `test_separate.py`'s (the module this one is
shaped after): the ffmpeg argv is asserted and the decode mocked, a moved
boundary misses the cache, and a warm cache does no work at all. The
maths itself is `beatfit`'s, tested there with no ffmpeg in sight.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import woodshed.beats as beats_module
from woodshed.beats import (
    ANALYSIS_MARGIN_S,
    DETECTOR_VERSION,
    beats_cache_path,
    beats_fingerprint,
    section_beats,
)
from woodshed.errors import WoodshedError
from woodshed.library import Repo
from woodshed.manifest import Recording, Section, Song, Tempo

SR = 22050


def _song(slug: str = "solo-song", sha256: str = "a" * 64, bpm: float = 120.0) -> Song:
    return Song(
        slug=slug,
        title="Solo Song",
        artist="Nobody",
        album=None,
        recording=Recording(file="audio/track.wav", sha256=sha256, duration_s=200.0,
                            tuning="E standard"),
        tempo=Tempo(bpm=bpm, source="manual", grid_offset_s=0.0, time_signature="4/4"),
    )


def _section(section_id: str = "solo-full", start_s: float = 60.0, end_s: float = 90.0) -> Section:
    return Section(
        id=section_id, name="Solo", start_s=start_s, end_s=end_s,
        snapped="free", target_speed=100.0,
    )


def _repo_with_audio(tmp_path: Path, slug: str = "solo-song") -> Repo:
    repo = Repo(root=tmp_path)
    audio = repo.song_dir(slug) / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    (audio / "track.wav").write_bytes(b"not really a wav, ffmpeg is mocked")
    return repo


def _click_bytes(bpm: float = 120.0, seconds: float = 38.0) -> bytes:
    """What the mocked ffmpeg "decodes": a click track, as raw f32le."""
    samples = np.zeros(int(seconds * SR), dtype=np.float32)
    length = 300
    t = np.arange(length, dtype=np.float32) / SR
    rng = np.random.default_rng(5)
    hit = (np.sin(2 * np.pi * 900 * t) + 0.4 * rng.standard_normal(length)).astype(np.float32)
    hit *= np.exp(-t * 200.0).astype(np.float32)
    position = 0.2
    while position < seconds:
        start = int(position * SR)
        end = min(len(samples), start + length)
        samples[start:end] += hit[: end - start]
        position += 60.0 / bpm
    return samples.tobytes()


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list:
    """Records every argv and answers with a decoded click track."""
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout=_click_bytes(), stderr=b"")

    monkeypatch.setattr(
        beats_module, "locate_tool",
        lambda name: SimpleNamespace(path="ffmpeg", route="path"),
    )
    monkeypatch.setattr("woodshed.analyze.subprocess.run", fake_run)
    monkeypatch.setattr(
        "woodshed.analyze.locate_tool",
        lambda name: SimpleNamespace(path="ffmpeg", route="path"),
    )
    return calls


# ── fingerprint / cache path ─────────────────────────────────────────────


def test_fingerprint_is_8_hex_chars() -> None:
    fp = beats_fingerprint(_song(), _section())
    assert len(fp) == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_changes_when_a_boundary_moves_by_10ms() -> None:
    song = _song()
    assert beats_fingerprint(song, _section(end_s=90.0)) != beats_fingerprint(
        song, _section(end_s=90.01)
    )


def test_fingerprint_changes_when_the_recording_changes() -> None:
    section = _section()
    assert beats_fingerprint(_song(sha256="a" * 64), section) != beats_fingerprint(
        _song(sha256="b" * 64), section
    )


def test_fingerprint_changes_with_the_song_tempo() -> None:
    """The song's tempo is the prior that decides the metrical level, so a
    re-analysed song must not be served beats fitted against the old one."""
    section = _section()
    assert beats_fingerprint(_song(bpm=120.0), section) != beats_fingerprint(
        _song(bpm=121.0), section
    )


def test_cache_path_shape(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    path = beats_cache_path(repo, "solo-song", "solo-full", "abcd1234")
    assert path == repo.song_dir("solo-song") / "cache" / "beats" / "solo-full-abcd1234.json"


# ── section_beats() ──────────────────────────────────────────────────────


def test_section_beats_finds_the_beats_of_the_analysed_span(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    repo = _repo_with_audio(tmp_path)
    result = section_beats(repo, _song(), _section())
    assert result["bpm"] == pytest.approx(120.0, abs=0.5)
    assert len(result["beats"]) > 50
    assert result["version"] == DETECTOR_VERSION


def test_section_beats_returns_source_seconds_not_clip_seconds(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    """CLAUDE.md's two-clock rule at the one boundary this module owns:
    ffmpeg cuts a clip that starts at t=0 of its own file, and every beat
    must come back in the RECORDING's clock, or the click plays in the
    wrong place by the whole offset of the section."""
    repo = _repo_with_audio(tmp_path)
    section = _section(start_s=60.0, end_s=90.0)
    result = section_beats(repo, _song(), section)
    assert min(result["beats"]) > 60.0 - ANALYSIS_MARGIN_S - 1
    assert max(result["beats"]) < 90.0 + ANALYSIS_MARGIN_S + 1
    # ... and it covers the section itself end to end.
    assert min(result["beats"]) < 60.0
    assert max(result["beats"]) > 89.0


def test_section_beats_analyses_a_margin_before_the_section(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    """The lead-in needs clicks too -- a count-in is the whole reason a
    pre-roll exists -- so the analysed span starts before the section."""
    repo = _repo_with_audio(tmp_path)
    section_beats(repo, _song(), _section(start_s=60.0, end_s=90.0))
    argv = fake_ffmpeg[0]
    assert argv[argv.index("-ss") + 1] == f"{60.0 - ANALYSIS_MARGIN_S:.6f}"
    assert argv[argv.index("-t") + 1] == f"{30.0 + 2 * ANALYSIS_MARGIN_S:.6f}"
    assert argv[argv.index("-ar") + 1] == str(beats_module.SAMPLE_RATE)
    assert argv[argv.index("-ac") + 1] == "1"


def test_the_margin_is_clamped_at_the_start_of_the_recording(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    repo = _repo_with_audio(tmp_path)
    section_beats(repo, _song(), _section(start_s=1.0, end_s=30.0))
    argv = fake_ffmpeg[0]
    assert argv[argv.index("-ss") + 1] == "0.000000"


def test_the_margin_is_clamped_at_the_end_of_the_recording(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    repo = _repo_with_audio(tmp_path)
    song = _song()  # duration_s 200.0
    section_beats(repo, song, _section(start_s=150.0, end_s=199.0))
    argv = fake_ffmpeg[0]
    assert float(argv[argv.index("-t") + 1]) == pytest.approx(200.0 - (150.0 - ANALYSIS_MARGIN_S))


def test_section_beats_writes_the_cache_and_reuses_it(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    repo = _repo_with_audio(tmp_path)
    song, section = _song(), _section()
    first = section_beats(repo, song, section)
    assert len(fake_ffmpeg) == 1

    cache = beats_cache_path(repo, song.slug, section.id, beats_fingerprint(song, section))
    assert cache.is_file()
    assert json.loads(cache.read_text(encoding="utf-8"))["beats"] == first["beats"]

    second = section_beats(repo, song, section)
    assert second == first
    assert len(fake_ffmpeg) == 1, "a warm cache must not decode anything"


def test_force_reanalyses_even_with_a_warm_cache(tmp_path: Path, fake_ffmpeg: list) -> None:
    repo = _repo_with_audio(tmp_path)
    song, section = _song(), _section()
    section_beats(repo, song, section)
    section_beats(repo, song, section, force=True)
    assert len(fake_ffmpeg) == 2


def test_a_moved_boundary_misses_the_cache(tmp_path: Path, fake_ffmpeg: list) -> None:
    repo = _repo_with_audio(tmp_path)
    song = _song()
    section_beats(repo, song, _section(end_s=90.0))
    section_beats(repo, song, _section(end_s=90.5))
    assert len(fake_ffmpeg) == 2


def test_a_corrupt_cache_file_is_re_analysed_rather_than_raising(
    tmp_path: Path, fake_ffmpeg: list
) -> None:
    """`cache/` is always safe to delete, which means it is always safe to
    be wrong: a half-written or hand-edited file must cost one re-analysis,
    never a 500."""
    repo = _repo_with_audio(tmp_path)
    song, section = _song(), _section()
    section_beats(repo, song, section)
    cache = beats_cache_path(repo, song.slug, section.id, beats_fingerprint(song, section))
    cache.write_text("{ this is not json", encoding="utf-8")

    result = section_beats(repo, song, section)
    assert len(result["beats"]) > 50
    assert len(fake_ffmpeg) == 2


def test_a_missing_audio_file_says_which_file(tmp_path: Path, fake_ffmpeg: list) -> None:
    repo = Repo(root=tmp_path)
    repo.song_dir("solo-song").mkdir(parents=True, exist_ok=True)
    with pytest.raises(WoodshedError, match="no such audio file"):
        section_beats(repo, _song(), _section())


def test_the_song_tempo_is_passed_through_as_the_prior(
    tmp_path: Path, fake_ffmpeg: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prior is what keeps a section's click on the right metrical
    level (beatfit's own reasoning); this is the wire it travels on."""
    seen = {}
    real_fit = beats_module.fit_beats

    def spy(env, times, **kwargs):
        seen.update(kwargs)
        return real_fit(env, times, **kwargs)

    monkeypatch.setattr(beats_module, "fit_beats", spy)
    repo = _repo_with_audio(tmp_path)
    section_beats(repo, _song(bpm=117.0), _section())
    assert seen["bpm_prior"] == 117.0


def test_a_song_with_no_tempo_still_gets_beats(tmp_path: Path, fake_ffmpeg: list) -> None:
    """`tempo.bpm` 0 is docs/02-data-model.md's "not yet known" -- it means
    no prior, not no metronome."""
    repo = _repo_with_audio(tmp_path)
    result = section_beats(repo, _song(bpm=0.0), _section())
    assert len(result["beats"]) > 50
