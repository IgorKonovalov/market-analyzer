"""Application config — pydantic-validated, loaded from `config.json` on startup.

Per ADR-0006, the SQLite DB lives at the OS-appropriate app-data directory by
default (`%APPDATA%/market-analyser/app.db` on Windows; XDG-equivalent on other
platforms). A `config.json` adjacent to that directory may override the path.
A malformed config refuses to start the sidecar — silent dropping of fields
would mask configuration drift.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

APP_DIRNAME = "market-analyser"
DATA_DIR_ENV_VAR = "MARKET_ANALYSER_DATA_DIR"


class AppConfig(BaseModel):
    """Top-level pydantic config. Strict-extra so typos fail loudly at load time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    db_path: Path = Field(default_factory=lambda: default_app_data_dir() / "app.db")
    # Metric-store self-warming (Plan 0061, ADR-0056): on by default — a fresh
    # deployment must accrue unattended (opt-in-by-default was rejected in the
    # ADR); the off-switch covers the offline/debug case. The interval default
    # (hourly, the store's bucket size) mirrors
    # `data.metric_accrual.DEFAULT_INTERVAL_SECONDS` — kept literal here so the
    # config module stays dependency-free.
    metric_accrual_enabled: bool = True
    metric_accrual_interval_seconds: int = Field(default=3600, ge=1)


def default_app_data_dir() -> Path:
    """Return the OS-appropriate per-user app-data directory.

    Windows: %APPDATA%/market-analyser
    macOS:   ~/Library/Application Support/market-analyser
    Linux:   $XDG_DATA_HOME/market-analyser or ~/.local/share/market-analyser

    `MARKET_ANALYSER_DATA_DIR` overrides the platform default for tests and
    explicit-relocation use cases. The override is taken verbatim (no
    APP_DIRNAME suffix appended) — callers control the full path.
    """
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIRNAME


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load AppConfig from `config_path`. Returns defaults if the file is absent.

    Validation errors raise — never silently dropped (per ADR-0006).
    """
    if config_path is None or not config_path.exists():
        return AppConfig()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return AppConfig.model_validate(raw)
