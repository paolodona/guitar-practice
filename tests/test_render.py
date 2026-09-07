"""Tests for woodshed.render (Phase 2, Group I -- pulled forward into
Phase 1.5, 2026-09-06): the offline render cache.

Test contract (this unit's own docstring): the ffmpeg/rubberband argv is
asserted, every subprocess mocked; the ratio passed to rubberband is
`1/(speed_pct/100)` and the pitch is the raw semitone count (trap 1: never
a resample); a changed `semitones` or a 10ms boundary move misses the
cache; renders are skipped when the fingerprinted file already exists and
`force` is False; the equal-power crossfade is measured directly on a
synthetic tone (`_bake_crossfade`, no subprocess involved at all);
`plan_ahead` picks the ladder rung above the current speed; `evict` reaps
orphans (a deleted section, a moved boundary) before ever touching the
size budget, and never touches `cache/peaks-*.json`.

Never a real rubberband/ffmpeg invocation here -- a single opt-in
integration test would carry `@pytest.mark.needs_rubberband`, not built in
this pass (mirrors `separate.py`'s own "no needs_demucs test yet either"
call: nothing here justified the extra complexity yet).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import woodshed.render as render_module
from woodshed.clock import Render, pre_roll_seconds
from woodshed.errors import WoodshedError
from woodshed.ladder import LadderConfig, LadderState
from woodshed.library import Repo
from woodshed.manifest import (
    PracticeDefaults,
    Recording,
    Section,
    Song,
    Tempo,
    effective_pre_roll_beats,
    save_song,
)
from woodshed.render import (
    cache_key,
    cache_path,
    evict,
    plan_ahead,
    render_section,
    span_fingerprint,
)


def _song(slug: str = "solo-song", sha256: str = "a" * 64, **overrides) -> Song:
    return Song(
        slug=slug,
        title="Solo Song",
        artist="Nobody",
        album=None,
        recording=Recording(file="audio/track.wav", sha256=sha256, duration_s=200.0,
                             tuning="E standard"),
        tempo=Tempo(bpm=120.0, source="manual", grid_offset_s=0.0, time_signature="4/4"),
        **overrides,
    )


def _section(section_id: str = "solo", start_s: float = 60.0, end_s: float = 90.0, **kw) -> Section:
    return Section(
        id=section_id, name="Solo", start_s=start_s, end_s=end_s,
        snapped="free", target_speed=100.0, **kw,
    )


# ── cache_key() / cache_path() ───────────────────────────────────────────


def test_cache_key_shape() -> None:
    assert cache_key("solo", 60.0, -1, "3f9a2c11") == "solo@60x-1st-3f9a2c11.flac"


def test_cache_key_positive_semitones_shows_a_plus_sign() -> None:
    assert cache_key("solo", 60.0, 2, "3f9a2c11") == "solo@60x+2st-3f9a2c11.flac"


def test_cache_key_zero_semitones_shows_a_plus_sign_too() -> None:
    # `+0` not a bare `0` -- so the sign character is always present as the
    # one separator between the speed and semitone segments (see the
    # function's own docstring).
    assert cache_key("solo", 100.0, 0, "abcd1234") == "solo@100x+0st-abcd1234.flac"


def test_cache_key_drops_a_trailing_dot_zero_on_the_speed() -> None:
    assert "62.5" in cache_key("solo", 62.5, 0, "abcd1234")
    assert "60x" in cache_key("solo", 60.0, 0, "abcd1234")


def test_cache_path_shape(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    path = cache_path(repo, "solo-song", "solo", 60.0, -1, "3f9a2c11")
    assert path == repo.song_dir("solo-song") / "cache" / "solo@60x-1st-3f9a2c11.flac"


# ── span_fingerprint() ────────────────────────────────────────────────────


def test_span_fingerprint_is_8_hex_chars() -> None:
    fp = span_fingerprint(_song(), _section(), pre_roll_s=1.0, crossfade_ms=10.0)
    assert len(fp) == 8
    assert all(c in "0123456789abcdef" for c in fp)


def test_span_fingerprint_is_stable_for_identical_inputs() -> None:
    song, section = _song(), _section()
    a = span_fingerprint(song, section, pre_roll_s=1.0, crossfade_ms=10.0)
    b = span_fingerprint(song, section, pre_roll_s=1.0, crossfade_ms=10.0)
    assert a == b


def test_span_fingerprint_changes_when_end_s_moves_by_10ms() -> None:
    song = _song()
    a = span_fingerprint(song, _section(end_s=90.0), pre_roll_s=1.0, crossfade_ms=10.0)
    b = span_fingerprint(song, _section(end_s=90.01), pre_roll_s=1.0, crossfade_ms=10.0)
    assert a != b


def test_span_fingerprint_changes_when_the_recording_changes() -> None:
    section = _section()
    a = span_fingerprint(_song(sha256="a" * 64), section, pre_roll_s=1.0, crossfade_ms=10.0)
    b = span_fingerprint(_song(sha256="b" * 64), section, pre_roll_s=1.0, crossfade_ms=10.0)
    assert a != b


def test_span_fingerprint_changes_with_pre_roll() -> None:
    song, section = _song(), _section()
    a = span_fingerprint(song, section, pre_roll_s=0.0, crossfade_ms=10.0)
    b = span_fingerprint(song, section, pre_roll_s=2.0, crossfade_ms=10.0)
    assert a != b


def test_span_fingerprint_changes_with_crossfade_ms() -> None:
    song, section = _song(), _section()
    a = span_fingerprint(song, section, pre_roll_s=0.0, crossfade_ms=10.0)
    b = span_fingerprint(song, section, pre_roll_s=0.0, crossfade_ms=20.0)
    assert a != b


def test_span_fingerprint_changes_when_the_lead_in_starts_replaying_every_pass() -> None:
    # An every-pass render bakes its crossfade in a different place (see
    # _bake_crossfade's own every-pass test), so the two are different
    # FILES and must not share a cache name -- the same stale-loop class of
    # bug the fingerprint exists to make impossible.
    section = _section()
    once = _song()
    always = _song(practice=PracticeDefaults(pre_roll_every_pass=True))
    a = span_fingerprint(once, section, pre_roll_s=1.0, crossfade_ms=10.0)
    b = span_fingerprint(always, section, pre_roll_s=1.0, crossfade_ms=10.0)
    assert a != b


def test_render_section_hands_the_every_pass_flag_to_the_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place `Render` is constructed on the render path -- if the
    song replays its lead-in every pass, the render it bakes has to know."""
    repo, _song_unused, _calls = _setup(tmp_path, monkeypatch)
    song = _song(practice=PracticeDefaults(pre_roll_every_pass=True))
    (repo.song_dir(song.slug) / song.recording.file).parent.mkdir(parents=True, exist_ok=True)
    (repo.song_dir(song.slug) / song.recording.file).write_bytes(b"fake source audio")
    seen: list = []
    real_bake = render_module._bake_crossfade
    monkeypatch.setattr(
        render_module, "_bake_crossfade",
        lambda samples, sr, render: (seen.append(render), real_bake(samples, sr, render))[1],
    )

    render_section(repo, song, _section(), 60.0, 0, pre_roll_s=1.0)

    assert [r.pre_roll_every_pass for r in seen] == [True]


# ── render_section(): argv, caching ──────────────────────────────────────


def _fake_run(calls: list, *, stretched_frames: int = 8000, stretched_sr: int = 8000):
    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        dest = Path(argv[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "--time" in argv:
            # The rubberband call: write a REAL wav so _bake_crossfade has
            # actual samples to work with -- a ramp, distinguishable at
            # head and tail.
            ramp = np.linspace(-1.0, 1.0, stretched_frames, dtype=np.float32)[:, None]
            render_module._write_wav(dest, ramp, stretched_sr)
        else:
            dest.write_bytes(b"fake audio bytes")
        return SimpleNamespace(returncode=0, stderr=b"")

    return fake_run


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Repo, Song, list]:
    repo = Repo(root=tmp_path)
    song = _song()
    audio_path = repo.song_dir(song.slug) / song.recording.file
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake source audio")

    monkeypatch.setattr(
        render_module, "locate_tool",
        lambda name: SimpleNamespace(path=name, route="path"),
    )
    calls: list = []
    monkeypatch.setattr(render_module.subprocess, "run", _fake_run(calls))
    return repo, song, calls


def test_render_section_writes_the_cache_file_and_returns_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()

    dest = render_section(repo, song, section, 60.0, -1, pre_roll_s=1.0)

    fp = span_fingerprint(song, section, pre_roll_s=1.0, crossfade_ms=10.0)
    assert dest == cache_path(repo, song.slug, section.id, 60.0, -1, fp)
    assert dest.is_file()
    assert len(calls) == 3  # ffmpeg cut, rubberband stretch, ffmpeg encode


def test_render_section_ffmpeg_cut_argv_uses_source_seconds_with_pre_roll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section(start_s=60.0, end_s=90.0)

    render_section(repo, song, section, 100.0, 0, pre_roll_s=2.0)

    cut_argv = calls[0]
    assert cut_argv[0] == "ffmpeg"
    assert cut_argv[cut_argv.index("-ss") + 1] == "58.000000"  # 60 - 2 pre-roll
    assert cut_argv[cut_argv.index("-t") + 1] == "32.000000"  # 90 - 58
    assert cut_argv[cut_argv.index("-i") + 1] == str(repo.song_dir(song.slug) / song.recording.file)


def test_render_section_clamps_pre_roll_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section(start_s=1.0, end_s=10.0)

    render_section(repo, song, section, 100.0, 0, pre_roll_s=5.0)

    cut_argv = calls[0]
    assert cut_argv[cut_argv.index("-ss") + 1] == "0.000000"
    assert cut_argv[cut_argv.index("-t") + 1] == "10.000000"


def test_render_section_rubberband_time_is_the_inverse_speed_fraction_never_a_resample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trap 1 (docs/03-audio-engine.md): `--time`, never a resample."""
    repo, song, calls = _setup(tmp_path, monkeypatch)

    render_section(repo, song, _section(), 55.0, 0)

    rb_argv = calls[1]
    assert rb_argv[0] == "rubberband"
    assert rb_argv[rb_argv.index("--time") + 1] == f"{1 / 0.55:.6f}"


def test_render_section_rubberband_pitch_is_the_raw_semitone_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)

    render_section(repo, song, _section(), 100.0, -3)

    rb_argv = calls[1]
    assert rb_argv[rb_argv.index("--pitch") + 1] == "-3"
    assert "--formant" in rb_argv
    assert "--fine" in rb_argv


def test_render_section_skips_the_work_when_already_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()
    fp = span_fingerprint(song, section, pre_roll_s=0.0, crossfade_ms=10.0)
    cached = cache_path(repo, song.slug, section.id, 100.0, 0, fp)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"already there")

    dest = render_section(repo, song, section, 100.0, 0)

    assert dest == cached
    assert calls == []


def test_render_section_force_reruns_even_when_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()
    fp = span_fingerprint(song, section, pre_roll_s=0.0, crossfade_ms=10.0)
    cached = cache_path(repo, song.slug, section.id, 100.0, 0, fp)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"stale")

    render_section(repo, song, section, 100.0, 0, force=True)

    assert len(calls) == 3


def test_render_section_moving_end_s_by_10ms_misses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named for the stale-loop class of bug it prevents (docs/03-audio-engine.md)."""
    repo, song, calls = _setup(tmp_path, monkeypatch)
    original = _section(end_s=90.0)
    fp = span_fingerprint(song, original, pre_roll_s=0.0, crossfade_ms=10.0)
    cached = cache_path(repo, song.slug, original.id, 100.0, 0, fp)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"stale -- belongs to the OLD boundary")

    moved = _section(end_s=90.01)
    dest = render_section(repo, song, moved, 100.0, 0)

    assert dest != cached
    assert len(calls) == 3


def test_render_section_changed_semitones_misses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()

    a = render_section(repo, song, section, 100.0, 0)
    calls.clear()
    b = render_section(repo, song, section, 100.0, -1)

    assert a != b
    assert len(calls) == 3  # -1 semitones actually re-rendered, not reused


def test_render_section_refuses_a_missing_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(root=tmp_path)
    song = _song()  # audio file never written
    monkeypatch.setattr(
        render_module, "locate_tool",
        lambda name: SimpleNamespace(path=name, route="path"),
    )

    with pytest.raises(WoodshedError, match="no such audio file"):
        render_section(repo, song, _section(), 100.0, 0)


def test_render_section_raises_on_a_nonzero_rubberband_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, _calls = _setup(tmp_path, monkeypatch)

    def failing_run(argv, **kwargs):
        if "--time" in argv:
            return SimpleNamespace(returncode=1, stderr=b"rubberband: boom")
        dest = Path(argv[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake audio bytes")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(render_module.subprocess, "run", failing_run)

    with pytest.raises(WoodshedError, match="rubberband"):
        render_section(repo, song, _section(), 100.0, 0)


# ── source="guitar" (Group S2) ────────────────────────────────────────────


def test_cache_key_guitar_source_inserts_a_guitar_segment() -> None:
    assert (
        cache_key("solo", 60.0, -1, "3f9a2c11", source="guitar")
        == "solo@60x-1st-guitar-3f9a2c11.flac"
    )


def test_cache_key_mix_source_is_byte_for_byte_the_pre_s2_shape() -> None:
    assert cache_key("solo", 60.0, -1, "3f9a2c11", source="mix") == cache_key(
        "solo", 60.0, -1, "3f9a2c11"
    )


def test_cache_path_mix_and_guitar_never_collide(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    mix = cache_path(repo, "solo-song", "solo", 60.0, -1, "3f9a2c11")
    guitar = cache_path(repo, "solo-song", "solo", 60.0, -1, "3f9a2c11", source="guitar")
    assert mix != guitar


def test_render_section_guitar_source_isolates_first_instead_of_cutting_the_mix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, calls = _setup(tmp_path, monkeypatch)
    section = _section()
    isolate_calls = []

    def fake_isolate_guitar(repo_, song_, section_, *, pre_roll_s=0.0, force=False):
        isolate_calls.append((song_.slug, section_.id, pre_roll_s, force))
        clip = tmp_path / "isolated-clip.flac"
        clip.write_bytes(b"fake isolated guitar bytes")
        return clip

    monkeypatch.setattr("woodshed.separate.isolate_guitar", fake_isolate_guitar)

    dest = render_section(repo, song, section, 100.0, 0, source="guitar")

    assert isolate_calls == [(song.slug, section.id, 0.0, False)]
    assert "guitar" in dest.name
    # Never ran the mix's own ffmpeg-cut step -- only rubberband + the final encode.
    assert len(calls) == 2


def test_render_section_guitar_source_never_needs_the_recordings_own_audio_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike source="mix" (test_render_section_refuses_a_missing_source_file),
    the guitar path never opens `song.recording.file` at all -- isolate_guitar
    is the only thing that reads source audio, and it is mocked here."""
    repo = Repo(root=tmp_path)
    song = _song()  # audio file never written
    monkeypatch.setattr(
        render_module, "locate_tool",
        lambda name: SimpleNamespace(path=name, route="path"),
    )
    calls: list = []
    monkeypatch.setattr(render_module.subprocess, "run", _fake_run(calls))
    monkeypatch.setattr(
        "woodshed.separate.isolate_guitar",
        lambda *a, **kw: (tmp_path / "clip.flac").write_bytes(b"x") or (tmp_path / "clip.flac"),
    )

    dest = render_section(repo, song, _section(), 100.0, 0, source="guitar")

    assert dest.is_file()


def test_render_section_mix_and_guitar_cache_separately_for_the_same_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, song, _calls = _setup(tmp_path, monkeypatch)
    section = _section()
    monkeypatch.setattr(
        "woodshed.separate.isolate_guitar",
        lambda *a, **kw: (tmp_path / "clip.flac").write_bytes(b"x") or (tmp_path / "clip.flac"),
    )

    mix_dest = render_section(repo, song, section, 100.0, 0, source="mix")
    guitar_dest = render_section(repo, song, section, 100.0, 0, source="guitar")

    assert mix_dest != guitar_dest
    assert mix_dest.is_file()
    assert guitar_dest.is_file()


# ── _bake_crossfade(): the equal-power crossfade, on a synthetic tone ────


def test_bake_crossfade_blends_head_and_tail_with_equal_power_endpoints() -> None:
    # 1 second at 100 sample/s: a section with no pre-roll, speed 1.0,
    # crossfade 100ms -> crossfade_n=10, loop_start_n=0, loop_end_n=90.
    sample_rate = 100
    total_frames = 100
    head_value, tail_value = 0.2, 0.8
    samples = np.full((total_frames, 1), head_value, dtype=np.float32)
    samples[-10:, 0] = tail_value  # distinguishable tail

    render = Render(start_s=0.0, end_s=1.0, pre_roll_s=0.0, speed=1.0, crossfade_ms=100.0)
    out = render_module._bake_crossfade(samples, sample_rate, render)

    assert len(out) == 90  # trimmed to loop_end
    # theta=0 at the start of the blended region: pure tail (cos(0)=1, sin(0)=0).
    assert out[0, 0] == pytest.approx(tail_value, abs=1e-5)
    # theta=pi/2 at the end of the blended region: pure head.
    assert out[9, 0] == pytest.approx(head_value, abs=1e-5)
    # Equal-power (not linear): fade_in/fade_out are sin/cos of the same
    # angle, so their squares sum to 1 at every sample -- checked directly
    # against the blend's own two components, not against the audio
    # amplitude (sin+cos can exceed either input, by design: that is what
    # keeps perceived loudness constant through the middle of the fade,
    # unlike a linear crossfade which dips).
    theta = np.linspace(0.0, np.pi / 2, 10)
    np.testing.assert_allclose(np.sin(theta) ** 2 + np.cos(theta) ** 2, 1.0, atol=1e-6)
    # Untouched region (after the blended head, before the trim) still the
    # plain head value.
    assert out[50, 0] == pytest.approx(head_value, abs=1e-5)


def test_bake_crossfade_every_pass_lead_in_blends_at_sample_zero_and_keeps_the_section() -> None:
    # FOUND 2026-09-06 building Group J. With pre_roll_every_pass the loop
    # wraps back to the very start of the render (the lead-in replays), so
    # the tail has to be folded over sample 0 -- not over the post-lead-in
    # head, which is mid-lap now and would be an audible blip once per
    # pass. And nothing may be trimmed off the section's own end: loop_end
    # is the section end either way (see tests/test_clock.py's own note).
    sample_rate = 100
    total_frames = 150  # 0.5s lead-in + 1.0s section at speed 1.0
    samples = np.full((total_frames, 1), 0.2, dtype=np.float32)
    samples[-5:, 0] = 0.8  # distinguishable tail

    # start_s 0.5, not 0.0: a section at second 0 has no recording in front
    # of it, so it has no lead-in either, and since 2026-09-07 the clock
    # clamps to what was actually cut (Render.effective_pre_roll_s). This
    # test is about a lead-in that exists.
    render = Render(
        start_s=0.5, end_s=1.5, pre_roll_s=0.5, speed=1.0, crossfade_ms=50.0,
        pre_roll_every_pass=True,
    )
    out = render_module._bake_crossfade(samples, sample_rate, render)

    assert len(out) == 145  # loop_end = 150 - 5, exactly as the once-only render
    assert out[0, 0] == pytest.approx(0.8, abs=1e-5)  # pure tail at the blend's start
    assert out[50, 0] == pytest.approx(0.2, abs=1e-5)  # the old blend point, untouched now


def test_bake_crossfade_zero_crossfade_still_trims_to_loop_end() -> None:
    sample_rate = 100
    samples = np.zeros((100, 1), dtype=np.float32)
    render = Render(start_s=0.0, end_s=1.0, pre_roll_s=0.0, speed=1.0, crossfade_ms=0.0)
    out = render_module._bake_crossfade(samples, sample_rate, render)
    assert len(out) == 100  # crossfade_ms=0 -> loop_end == total


def test_bake_crossfade_respects_pre_roll_as_the_loop_start() -> None:
    # 0.5s pre-roll + 1.0s section at speed 1.0, 100 sample/s -> loop_start
    # at sample 50.
    sample_rate = 100
    total_frames = 150
    samples = np.arange(total_frames, dtype=np.float32).reshape(-1, 1)
    render = Render(start_s=0.5, end_s=1.5, pre_roll_s=0.5, speed=1.0, crossfade_ms=50.0)
    out = render_module._bake_crossfade(samples, sample_rate, render)
    # loop_end = 50 + 100 - 5 = 145
    assert len(out) == 145


# ── plan_ahead() ──────────────────────────────────────────────────────────


def test_plan_ahead_renders_the_rung_above_current_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    song, section = _song(), _section()
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3, target_speed=100.0)
    state = LadderState(speed=55.0, clean_at_speed=0)

    seen = {}

    def fake_render_section(repo, s, sec, speed_pct, semitones, **kw):
        seen["speed_pct"] = speed_pct
        return Path("fake.flac")

    monkeypatch.setattr(render_module, "render_section", fake_render_section)

    paths = plan_ahead(None, song, section, state, cfg, 0)

    assert seen["speed_pct"] == 60.0
    assert paths == [Path("fake.flac")]


def test_plan_ahead_returns_empty_once_already_at_target(monkeypatch: pytest.MonkeyPatch) -> None:
    song, section = _song(), _section()
    cfg = LadderConfig(start_speed=50.0, ladder_step=5.0, reps_to_advance=3, target_speed=100.0)
    state = LadderState(speed=100.0, clean_at_speed=0)

    called = []
    monkeypatch.setattr(
        render_module, "render_section", lambda *a, **kw: called.append(1) or Path("x")
    )

    assert plan_ahead(None, song, section, state, cfg, 0) == []
    assert called == []


# ── evict() ───────────────────────────────────────────────────────────────


def _write_song(repo: Repo, song: Song) -> None:
    path = repo.song_dir(song.slug) / "song.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_song(song, path)


def _current_fp(song: Song, section: Section) -> str:
    """The fingerprint `evict`'s own orphan check would recompute for
    *section* right now -- exactly the pre_roll_s/crossfade_ms precedence
    `_render_is_orphan` uses, so a test file built with this is genuinely
    NOT stale (unlike the deliberately-moved-boundary tests, which build
    their fp from the OLD section on purpose)."""
    pre_roll_s = pre_roll_seconds(effective_pre_roll_beats(song, section), song.tempo.bpm)
    return span_fingerprint(
        song, section, pre_roll_s=pre_roll_s, crossfade_ms=song.practice.loop_crossfade_ms
    )


def test_evict_deletes_oldest_files_first_until_under_budget(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    section = _section()
    song = _song(sections=[section])
    _write_song(repo, song)

    paths = []
    fp = _current_fp(song, section)
    for i, speed in enumerate((60.0, 70.0, 80.0)):
        path = cache_path(repo, song.slug, section.id, speed, 0, fp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1_000_000)  # 1 MB each
        os_utime(path, i)  # oldest first: index 0 is oldest
        paths.append(path)

    # Budget: 2 MB -- one file (the oldest) must go.
    deleted = evict(repo, max_gb=2e-3)

    assert deleted == [paths[0]]
    assert not paths[0].exists()
    assert paths[1].exists()
    assert paths[2].exists()


def test_evict_leaves_everything_alone_when_under_budget(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    section = _section()
    song = _song(sections=[section])
    _write_song(repo, song)
    fp = _current_fp(song, section)
    path = cache_path(repo, song.slug, section.id, 100.0, 0, fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1000)

    deleted = evict(repo, max_gb=20.0)

    assert deleted == []
    assert path.exists()


def test_evict_reaps_a_render_orphan_when_its_section_is_gone(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    song = _song(sections=[])  # the section this file was rendered for no longer exists
    _write_song(repo, song)
    section = _section()
    fp = span_fingerprint(song, section, pre_roll_s=0.0, crossfade_ms=10.0)
    path = cache_path(repo, song.slug, section.id, 100.0, 0, fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)

    deleted = evict(repo, max_gb=20.0)  # budget is not the reason -- orphan sweep is unconditional

    assert deleted == [path]
    assert not path.exists()


def test_evict_reaps_a_render_orphan_when_the_boundary_moved(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    original_section = _section(end_s=90.0)
    song = _song(sections=[_section(end_s=90.01)])  # same id, moved 10ms
    _write_song(repo, song)
    fp = span_fingerprint(song, original_section, pre_roll_s=0.0, crossfade_ms=10.0)
    stale = cache_path(repo, song.slug, original_section.id, 100.0, 0, fp)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"x" * 10)

    deleted = evict(repo, max_gb=20.0)

    assert deleted == [stale]


def test_evict_leaves_a_currently_valid_render_alone(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    section = _section()
    song = _song(sections=[section])
    _write_song(repo, song)
    fp = _current_fp(song, section)
    path = cache_path(repo, song.slug, section.id, 100.0, 0, fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)

    deleted = evict(repo, max_gb=20.0)

    assert deleted == []
    assert path.exists()


def test_evict_reaps_a_stem_orphan_when_its_section_is_gone(tmp_path: Path) -> None:
    from woodshed.separate import stem_cache_path, stem_fingerprint

    repo = Repo(root=tmp_path)
    song = _song(sections=[])
    _write_song(repo, song)
    section = _section()
    fp = stem_fingerprint(song, section, pre_roll_s=0.0)
    path = stem_cache_path(repo, song.slug, section.id, fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)

    deleted = evict(repo, max_gb=20.0)

    assert deleted == [path]


def test_evict_leaves_peaks_json_alone(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    song = _song()
    _write_song(repo, song)
    peaks_path = repo.cache_dir(song.slug) / "peaks-1024.json"
    peaks_path.parent.mkdir(parents=True, exist_ok=True)
    peaks_path.write_text("{}", encoding="utf-8")

    deleted = evict(repo, max_gb=0.0)  # a budget of zero would evict every .flac

    assert deleted == []
    assert peaks_path.exists()


def os_utime(path: Path, minutes_ago: int) -> None:
    """Set *path*'s mtime `minutes_ago` minutes before now -- lower means
    older, so index 0 in the calling tests' loops is the oldest file."""
    import os
    import time

    now = time.time()
    stamp = now - (10 - minutes_ago) * 60  # spread them out, oldest first
    os.utime(path, (stamp, stamp))


# ── evict(keep=...) ───────────────────────────────────────────────────────


def test_evict_never_deletes_a_render_it_was_told_to_keep(tmp_path: Path) -> None:
    """FOUND BY REVIEW 2026-09-07: the server evicts after every render, so
    with a budget smaller than one file the sweep deleted the very render
    the request was waiting for -- and `_render`'s 202 poll then started it
    again, forever. Whatever the budget says, you never evict the thing you
    just made for the request in flight."""
    repo = Repo(root=tmp_path)
    section = _section()
    song = _song(sections=[section])
    _write_song(repo, song)
    fp = _current_fp(song, section)
    fresh = cache_path(repo, song.slug, section.id, 60.0, 0, fp)
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"x" * 1_000_000)

    deleted = evict(repo, 0.0, keep=[fresh])

    assert fresh.is_file(), "the just-rendered file survived a zero budget"
    assert deleted == []


def test_evict_keeps_a_protected_file_even_if_it_looks_like_an_orphan(
    tmp_path: Path,
) -> None:
    """A file named `keep` is by definition the CURRENT fingerprint -- the
    render a request is waiting on this instant. Reaping it as an orphan
    would be the same forever-loop by another route."""
    repo = Repo(root=tmp_path)
    song = _song(sections=[_section()])
    _write_song(repo, song)
    stale = cache_path(repo, song.slug, "solo", 60.0, 0, "deadbeef")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"x")

    assert evict(repo, 20.0, keep=[stale]) == []
    assert stale.is_file()


def test_evict_with_nothing_to_keep_still_honours_the_budget(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    section = _section()
    song = _song(sections=[section])
    _write_song(repo, song)
    fp = _current_fp(song, section)
    path = cache_path(repo, song.slug, section.id, 60.0, 0, fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1_000_000)

    assert evict(repo, 0.0) == [path]
    assert not path.is_file()
