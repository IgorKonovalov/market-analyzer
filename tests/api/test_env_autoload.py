"""Repo-root `.env` auto-load at sidecar startup.

The sidecar loads a developer's gitignored repo-root `.env` into the process
environment so `MARKET_ANALYSER_*` keys (e.g. the Zerion API key) take effect
without a manual export. The contract, asserted here:

1. A present `.env` populates `os.environ`.
2. A real environment variable WINS over `.env` (`override=False`).
3. A missing `.env` path is a silent no-op.

Canonical secret storage stays `secrets.json` (ADR-0038); this only feeds the
env-override layer `SecretsStore` already reads.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from market_analyser.api.__main__ import _load_repo_dotenv

_KEY = "MARKET_ANALYSER_ZERION_API_KEY"


def test_dotenv_populates_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {})
    env_file = tmp_path / ".env"
    env_file.write_text(f"{_KEY}=zk_fromfile\n", encoding="utf-8")

    _load_repo_dotenv(env_file)

    assert os.environ.get(_KEY) == "zk_fromfile"


def test_real_env_wins_over_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {_KEY: "zk_realenv"})
    env_file = tmp_path / ".env"
    env_file.write_text(f"{_KEY}=zk_fromfile\n", encoding="utf-8")

    _load_repo_dotenv(env_file)

    assert os.environ[_KEY] == "zk_realenv"


def test_missing_dotenv_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {})

    _load_repo_dotenv(tmp_path / "does-not-exist.env")

    assert _KEY not in os.environ
