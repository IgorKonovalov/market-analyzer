"""Plan 0014 phase 1 done-when: `AgentModeStore` persistence.

Asserts: default-disabled on a fresh data dir, persisted-true survives a new
store instance (cross-restart contract), 0600 on POSIX, atomic write leaves no
temp file behind, and a malformed file degrades to disabled with a WARN rather
than crashing sidecar boot.
"""

from __future__ import annotations

import logging
import stat
import sys
from pathlib import Path

import pytest

from market_analyser.api.ui_events.agent_mode import AgentModeStore


def test_fresh_dir_reads_disabled(tmp_path: Path) -> None:
    store = AgentModeStore(tmp_path / "agent_mode.json")
    assert store.is_enabled() is False
    # Reading the default must not create the file.
    assert not (tmp_path / "agent_mode.json").exists()


def test_set_enabled_persists_and_survives_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "agent_mode.json"
    store = AgentModeStore(path)
    store.set_enabled(True)
    assert store.is_enabled() is True

    import json

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"enabled": True}

    # A fresh store on the same path picks up the persisted value.
    reopened = AgentModeStore(path)
    assert reopened.is_enabled() is True


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file modes don't map per Plan 0006 phase 1",
)
def test_set_enabled_writes_0600_on_posix(tmp_path: Path) -> None:
    path = tmp_path / "agent_mode.json"
    AgentModeStore(path).set_enabled(True)
    mode_bits = stat.S_IMODE(path.stat().st_mode)
    assert mode_bits == 0o600, f"expected 0600, got {oct(mode_bits)}"


def test_rewrite_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "agent_mode.json"
    store = AgentModeStore(path)
    store.set_enabled(True)
    store.set_enabled(False)

    import json

    assert json.loads(path.read_text(encoding="utf-8")) == {"enabled": False}
    # The atomic write (tempfile + os.replace) must leave nothing behind.
    leftovers = list(tmp_path.glob(".agent-mode.*"))
    assert leftovers == [], f"temp file(s) not cleaned up: {leftovers}"


def test_invalid_json_degrades_to_disabled_with_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "agent_mode.json"
    path.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        store = AgentModeStore(path)
    assert store.is_enabled() is False
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_missing_enabled_key_degrades_to_disabled_with_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "agent_mode.json"
    path.write_text("{}", encoding="utf-8")  # valid JSON, missing `enabled`
    with caplog.at_level(logging.WARNING):
        store = AgentModeStore(path)
    assert store.is_enabled() is False
    assert any(record.levelno == logging.WARNING for record in caplog.records)
