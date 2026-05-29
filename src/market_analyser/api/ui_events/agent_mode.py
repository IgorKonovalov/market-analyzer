"""Persisted agent-mode toggle (Plan 0014 phase 1, ADR-0021).

Agent mode is a single sidecar-resident boolean: when OFF (the default) the
renderer's UI gestures are not buffered for the agent; when ON they are. It
persists to `<data-dir>/agent_mode.json` (mode 0600 on POSIX, per ADR-0020's
data-dir contract) so the choice survives a sidecar restart.

File shape (a JSON envelope so future fields land without a format migration):

    {"enabled": false}

`AgentModeStore` caches the value in memory after construction so `is_enabled()`
is a field read, not a disk read on every `POST /ui_events`. `set_enabled`
atomically rewrites the file (tempfile + `os.replace`, reasserting 0600) and
updates the cache in lock-step. A fresh store on the same path picks up the
persisted value — that's the cross-restart contract the phase-1 done-when pins.

A missing file reads as the default `{enabled: false}` and creates nothing. A
malformed file (invalid JSON, or missing/ill-typed `enabled`) also reads as the
default and logs a WARN — a tampered or truncated toggle must never crash
sidecar boot.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POSIX_FILE_MODE = 0o600
AGENT_MODE_FILENAME = "agent_mode.json"


def _read_enabled(path: Path) -> bool:
    """Read the persisted `enabled` flag, defaulting to False on absence or any
    malformation (logged at WARN). Never raises."""
    if not path.exists():
        return False
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("agent_mode.json unreadable (%s); defaulting to disabled", exc)
        return False
    enabled = raw.get("enabled") if isinstance(raw, dict) else None
    if not isinstance(enabled, bool):
        logger.warning(
            "agent_mode.json malformed (%r); defaulting to disabled",
            raw,
        )
        return False
    return enabled


class AgentModeStore:
    """Read/write the persisted agent-mode boolean, caching the current value."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._enabled = _read_enabled(path)

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Atomically persist `enabled` (0600 on POSIX) and update the cache.

        Atomic write (tempfile + `os.replace`) so a crash mid-write cannot leave
        a half-formed file; the temp file is gone once the call returns. 0600 is
        reasserted after replace because mkstemp's default mode is platform-
        dependent and the guarantee must hold on every write, not just the first.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"enabled": enabled}
        fd, tmp_name = tempfile.mkstemp(prefix=".agent-mode.", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_name, self._path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        if sys.platform != "win32":
            os.chmod(self._path, POSIX_FILE_MODE)
        self._enabled = enabled
