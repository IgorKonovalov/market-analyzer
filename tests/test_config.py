"""Plan 0001 phase 3: AppConfig loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_analyser.config import AppConfig, default_app_data_dir, load_config


def test_load_config_returns_defaults_when_path_is_none() -> None:
    config = load_config(None)
    assert isinstance(config, AppConfig)
    assert config.db_path == default_app_data_dir() / "app.db"


def test_load_config_returns_defaults_when_path_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.json")
    assert config.db_path == default_app_data_dir() / "app.db"


def test_load_config_reads_db_path_override(tmp_path: Path) -> None:
    custom = tmp_path / "data" / "app.db"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"db_path": str(custom)}), encoding="utf-8")
    config = load_config(config_path)
    assert config.db_path == custom


def test_load_config_rejects_unknown_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"db_path": "/tmp/x.db", "what": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(config_path)
