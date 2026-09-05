"""Tests for woodshed.config: the config.yaml loader.

Test contract (see the implementation-plan prompt for this unit):
* the exact YAML printed in docs/02-data-model.md for config.yaml parses into
  Config without error;
* an absent config.yaml (load_config pointed at an empty tmp Repo) yields a
  usable Config built from field defaults, never an exception;
* midi.map round-trips as {int: str} -- YAML integer keys parse as Python
  ints, not strings, and that is easy to get silently wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from woodshed.config import Config, load_config
from woodshed.library import Repo

# The exact config.yaml block printed in docs/02-data-model.md.
DOCS_CONFIG_YAML = """\
library_paths:                    # scanned by `woodshed scan`
  - "D:/Music"
defaults:
  start_speed: 50
  ladder_step: 5
  reps_to_advance: 3
  pre_roll_beats: 4
  auto_confirm: true
midi:
  input: "BOSS GX-100"            # substring match on the port name
  channel: 1
  map:                            # see docs/05-foot-control.md
    80: play_pause
    81: next_section
    82: prev_section
    83: speed_up
    84: speed_down
    85: retract_rep
spotify:
  client_id: ""                   # your own app. Never a secret in this file -- see 04.
render:
  engine: rubberband              # rubberband | soundtouch | none
  formant_preserve: true
  cache_max_gb: 20
"""


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(root=tmp_path)


def test_docs_yaml_parses_without_error(repo: Repo) -> None:
    repo.config_path.write_text(DOCS_CONFIG_YAML, encoding="utf-8")

    config = load_config(repo)

    assert isinstance(config, Config)
    assert config.library_paths == ["D:/Music"]
    assert config.defaults.start_speed == 50
    assert config.defaults.ladder_step == 5
    assert config.defaults.reps_to_advance == 3
    assert config.defaults.pre_roll_beats == 4
    assert config.defaults.auto_confirm is True
    assert config.midi.input == "BOSS GX-100"
    assert config.midi.channel == 1
    assert config.spotify.client_id == ""
    assert config.render.engine == "rubberband"
    assert config.render.formant_preserve is True
    assert config.render.cache_max_gb == 20


def test_missing_config_yaml_yields_defaults_not_an_error(repo: Repo) -> None:
    assert not repo.config_path.exists()

    config = load_config(repo)

    assert isinstance(config, Config)
    # Field defaults, not a raised WoodshedError or any other exception.
    assert config.library_paths == []
    assert config.midi.map == {}
    assert config.spotify.client_id is None


def test_defaults_include_sane_next_up_scoring_weights(repo: Repo) -> None:
    config = load_config(repo)

    assert isinstance(config.defaults.weight_gap, float)
    assert isinstance(config.defaults.weight_cold, float)
    assert isinstance(config.defaults.weight_gig, float)
    # Sane defaults: none of them zeroes out a scoring term by default.
    assert config.defaults.weight_gap > 0
    assert config.defaults.weight_cold > 0
    assert config.defaults.weight_gig > 0


def test_midi_map_round_trips_as_int_keys_not_str(repo: Repo) -> None:
    repo.config_path.write_text(DOCS_CONFIG_YAML, encoding="utf-8")

    config = load_config(repo)

    expected = {
        80: "play_pause",
        81: "next_section",
        82: "prev_section",
        83: "speed_up",
        84: "speed_down",
        85: "retract_rep",
    }
    assert config.midi.map == expected
    for key in config.midi.map:
        assert isinstance(key, int), f"key {key!r} was not coerced to int"


def test_midi_map_defaults_to_empty_dict_when_absent(repo: Repo) -> None:
    repo.config_path.write_text("midi:\n  input: \"\"\n", encoding="utf-8")

    config = load_config(repo)

    assert config.midi.map == {}


def test_partial_config_yaml_fills_remaining_fields_with_defaults(
    repo: Repo,
) -> None:
    repo.config_path.write_text(
        "library_paths:\n  - '/tmp/music'\n", encoding="utf-8"
    )

    config = load_config(repo)

    assert config.library_paths == ["/tmp/music"]
    # Everything else still comes from field defaults.
    assert config.render.engine == "rubberband"
    assert config.midi.channel == 1
