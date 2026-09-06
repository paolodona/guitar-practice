"""Tests for woodshed.library: repo layout, slugs, and song lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from woodshed.errors import WoodshedError
from woodshed.library import Repo, find_root, slugify

# ── find_root ────────────────────────────────────────────────────────────


def test_find_root_discovers_root_walking_up_from_nested_start(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    nested = tmp_path / "songs" / "some-song" / "audio"
    nested.mkdir(parents=True)

    assert find_root(nested) == tmp_path.resolve()


def test_find_root_recognises_any_of_the_root_markers(tmp_path: Path) -> None:
    (tmp_path / "woodshed.toml").write_text("", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert find_root(nested) == tmp_path.resolve()

    dotgit_root = tmp_path / "other"
    (dotgit_root / ".git").mkdir(parents=True)
    nested2 = dotgit_root / "c" / "d"
    nested2.mkdir(parents=True)

    assert find_root(nested2) == dotgit_root.resolve()


def test_find_root_raises_when_no_marker_found_up_to_filesystem_root(
    tmp_path: Path,
) -> None:
    # tmp_path is a bare pytest scratch dir with none of ROOT_MARKERS present
    # anywhere above it, so the walk should exhaust every ancestor and refuse.
    nested = tmp_path / "deep" / "nesting"
    nested.mkdir(parents=True)

    with pytest.raises(WoodshedError):
        find_root(nested)


# ── slugify ───────────────────────────────────────────────────────────────


def test_slugify_folds_accents_lowercases_and_hyphenates() -> None:
    assert slugify("Perché No") == "perche-no"


def test_slugify_handles_straight_and_curly_apostrophes() -> None:
    # An apostrophe is a SEPARATOR, deliberately -- not deleted. A slug is a
    # permanent identity (practice/reps.jsonl names it on every line and is
    # never rewritten), so this is settled rather than tuned; see slugify's
    # own docstring and CLAUDE.md's Conventions.
    assert slugify("Can't Stop") == "can-t-stop"
    assert slugify("Can’t Stop") == "can-t-stop"


def test_slugify_collapses_punctuation_and_trims_hyphens() -> None:
    assert slugify("  Sultans Of Swing!! ") == "sultans-of-swing"


# ── Repo directory properties ────────────────────────────────────────────


def test_repo_directory_properties_resolve_under_root(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)

    assert repo.songs_dir == tmp_path / "songs"
    assert repo.setlists_dir == tmp_path / "setlists"
    assert repo.practice_dir == tmp_path / "practice"
    assert repo.config_path == tmp_path / "config.yaml"
    assert repo.web_dir == tmp_path / "web"
    assert repo.capture_dir == tmp_path / "capture"


def test_repo_per_song_paths(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)

    assert repo.song_dir("cant-stop") == tmp_path / "songs" / "cant-stop"
    assert repo.audio_dir("cant-stop") == tmp_path / "songs" / "cant-stop" / "audio"
    assert repo.cache_dir("cant-stop") == tmp_path / "songs" / "cant-stop" / "cache"


def test_repo_ledger_path(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)

    assert repo.ledger_path() == tmp_path / "practice" / "reps.jsonl"


# ── list_songs / list_setlists ───────────────────────────────────────────


def _make_song(repo: Repo, slug: str) -> None:
    song_dir = repo.song_dir(slug)
    song_dir.mkdir(parents=True)
    (song_dir / "song.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")


def test_list_songs_returns_sorted_slugs(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    for slug in ["sultans-of-swing", "cant-stop", "money-for-nothing"]:
        _make_song(repo, slug)

    assert repo.list_songs() == [
        "cant-stop",
        "money-for-nothing",
        "sultans-of-swing",
    ]


def test_list_songs_ignores_directories_without_song_yaml(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    _make_song(repo, "cant-stop")
    (repo.songs_dir / "half-imported").mkdir()

    assert repo.list_songs() == ["cant-stop"]


def test_list_songs_empty_when_songs_dir_missing(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)

    assert repo.list_songs() == []


def test_list_setlists_returns_sorted_slugs(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.setlists_dir.mkdir()
    (repo.setlists_dir / "band-in-eb.yaml").write_text("", encoding="utf-8")
    (repo.setlists_dir / "acoustic-duo.yaml").write_text("", encoding="utf-8")
    (repo.setlists_dir / "notes.txt").write_text("", encoding="utf-8")

    assert repo.list_setlists() == ["acoustic-duo", "band-in-eb"]


# ── find_song ─────────────────────────────────────────────────────────────


def test_find_song_resolves_exact_slug(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    _make_song(repo, "cant-stop")
    _make_song(repo, "sultans-of-swing")

    assert repo.find_song("cant-stop") == "cant-stop"


def test_find_song_resolves_unambiguous_title_match(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    _make_song(repo, "can-t-stop")
    _make_song(repo, "sultans-of-swing")

    assert repo.find_song("Can't Stop") == "can-t-stop"
    assert repo.find_song("SULTANS of Swing") == "sultans-of-swing"
    assert repo.find_song("swing") == "sultans-of-swing"


def test_find_song_raises_naming_candidates_on_ambiguity(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    _make_song(repo, "sultans-of-swing")
    _make_song(repo, "sultans-revenge")

    with pytest.raises(WoodshedError) as exc_info:
        repo.find_song("sultans")

    message = str(exc_info.value)
    assert "sultans-of-swing" in message
    assert "sultans-revenge" in message


def test_find_song_raises_on_no_match(tmp_path: Path) -> None:
    repo = Repo(root=tmp_path)
    repo.songs_dir.mkdir()
    _make_song(repo, "cant-stop")

    with pytest.raises(WoodshedError) as exc_info:
        repo.find_song("nonexistent-tune")

    assert "nonexistent-tune" in str(exc_info.value)
