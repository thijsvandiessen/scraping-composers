"""Tests for the rule-1 JSON config loader."""

import json
from pathlib import Path

from composer_gold import DEFAULT_RULE1_CONFIG_PATH, EnsembleRule1Config, PersonRule1Config, Rule1Config


def test_from_json_round_trips_all_fields(tmp_path: Path) -> None:
    path = tmp_path / "rule1_config.json"
    path.write_text(
        json.dumps(
            {
                "persons": {
                    "min_concert_appearances": 2,
                    "min_recording_appearances": 3,
                    "min_appearances_for_composers": 1,
                    "min_sitelinks": 100,
                },
                "ensembles": {
                    "min_concert_appearances": 4,
                    "min_recording_appearances": 5,
                },
            }
        )
    )

    config = Rule1Config.from_json(path)

    assert config.persons == PersonRule1Config(
        min_concert_appearances=2,
        min_recording_appearances=3,
        min_appearances_for_composers=1,
        min_sitelinks=100,
    )
    assert config.ensembles == EnsembleRule1Config(min_concert_appearances=4, min_recording_appearances=5)


def test_from_json_defaults_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "rule1_config.json"
    path.write_text(json.dumps({"persons": {"min_concert_appearances": 7}}))

    config = Rule1Config.from_json(path)

    assert config.persons == PersonRule1Config(min_concert_appearances=7)
    assert config.ensembles == EnsembleRule1Config()


def test_from_json_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    config = Rule1Config.from_json(tmp_path / "does-not-exist.json")

    assert config == Rule1Config()


def test_from_json_malformed_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "rule1_config.json"
    path.write_text("not valid json")

    config = Rule1Config.from_json(path)

    assert config == Rule1Config()


def test_repo_default_config_path_is_loadable() -> None:
    """The repo's rule1_config.json is meant to be tuned (by hand, or through
    the admin API) rather than fixed at particular values, so this only checks
    that it parses into a well-formed config, not what the values are."""
    config = Rule1Config.from_json(DEFAULT_RULE1_CONFIG_PATH)
    assert isinstance(config.persons, PersonRule1Config)
    assert isinstance(config.ensembles, EnsembleRule1Config)


def test_write_json_round_trips_through_from_json(tmp_path: Path) -> None:
    path = tmp_path / "rule1_config.json"
    config = Rule1Config(
        persons=PersonRule1Config(min_concert_appearances=9, min_sitelinks=42),
        ensembles=EnsembleRule1Config(min_recording_appearances=3),
    )

    config.write_json(path)

    assert Rule1Config.from_json(path) == config
    assert path.read_text().endswith("\n")  # a clean, diff-friendly file
