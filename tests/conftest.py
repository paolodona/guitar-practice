"""Shared pytest fixtures for the Woodshed test suite.

This file is collected before any test module, on every run, regardless of
which units have landed. woodshed.library (the module that will eventually
own the real Repo type) may not exist yet -- another unit is writing it
concurrently in this same pass -- so this file must not import anything
from woodshed at collection time. It builds the scratch library tree with
nothing but pathlib and tmp_path, and is deliberately minimal: other units
may extend it later without conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class ScratchRepo:
    """A Repo-shaped scratch directory, without importing woodshed.library.

    Mirrors the on-disk layout woodshed.library.Repo will expect: a root
    containing songs/, setlists/, practice/ and config.yaml. Exposed as
    properties (not fields) so the paths are always derived from root,
    the same way the real Repo derives them.
    """

    root: Path

    @property
    def songs_dir(self) -> Path:
        return self.root / "songs"

    @property
    def setlists_dir(self) -> Path:
        return self.root / "setlists"

    @property
    def practice_dir(self) -> Path:
        return self.root / "practice"

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"


@pytest.fixture
def repo(tmp_path: Path) -> ScratchRepo:
    """A fresh, empty library tree rooted at tmp_path.

    Creates songs/, setlists/, practice/ and an empty config.yaml so a test
    can drop a song or setlist under the right directory without also having
    to invent the scaffold itself. Returns a ScratchRepo rather than
    tmp_path so call sites read ``repo.songs_dir`` and stay correct if the
    layout ever changes.
    """
    (tmp_path / "songs").mkdir()
    (tmp_path / "setlists").mkdir()
    (tmp_path / "practice").mkdir()
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    return ScratchRepo(root=tmp_path)
