"""Load, read, and rotate the long-lived MCP bearer secret (ADR-0014, Plan 0006).

The MCP bearer is a second secret on the sidecar's loopback listener, separate
from the renderer's per-launch `MARKET_ANALYSER_SECRET` (ADR-0011). It lives on
disk in `mcp-secret.json` in the user's app data directory because its
consumer — Claude Desktop, configured once by the user — outlives any single
launch of the Electron app. POSIX file mode is 0600 so other local users on the
same machine cannot read it.

File shape (JSON envelope so future fields can land without a format migration):

    {
      "secret": "<64 hex chars (= 32 bytes)>",
      "created_at": "<iso8601 UTC>"
    }

Public entry points:

- `load_or_generate_mcp_secret(path)` — first-boot path. Returns the secret
  string. Generates a new file via `rotate_secret` if absent.
- `read_secret_record(path)` — read the full envelope (secret + created_at).
  Used by the Settings page's GET endpoint to display the current secret.
- `rotate_secret(path)` — generate a fresh secret, atomic-replace the file
  preserving 0600 on POSIX, return the new record. Used by the Settings
  page's POST rotate endpoint; also used internally on first-boot when the
  file is absent.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

POSIX_FILE_MODE = 0o600
_SECRET_BYTES = 32  # → 64 hex chars


class McpSecretRecord(BaseModel):
    """The on-disk envelope's two fields. Serialized straight to the Settings API."""

    secret: str
    created_at: datetime


def load_or_generate_mcp_secret(path: Path) -> str:
    """Return the MCP bearer secret from `path`, generating a new file if absent.

    A malformed existing file raises rather than silently regenerating — a
    tampered or truncated secret file should surface loudly, not be papered over.
    """
    if path.exists():
        return read_secret_record(path).secret
    return rotate_secret(path).secret


def read_secret_record(path: Path) -> McpSecretRecord:
    """Read the secret envelope from disk. Raises if the file is missing or malformed."""
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(raw).__name__}")
    secret = raw.get("secret")
    if not isinstance(secret, str) or not secret:
        raise ValueError(f"{path}: missing or empty `secret` field")
    created_at_raw = raw.get("created_at")
    if not isinstance(created_at_raw, str) or not created_at_raw:
        raise ValueError(f"{path}: missing or empty `created_at` field")
    return McpSecretRecord(secret=secret, created_at=datetime.fromisoformat(created_at_raw))


def rotate_secret(path: Path) -> McpSecretRecord:
    """Generate a fresh secret + atomic-replace the file + return the new record.

    Atomic write (tempfile.mkstemp + os.replace) so a crash mid-write cannot
    leave a half-formed file. POSIX mode 0600 is reasserted after replace
    because mkstemp's default mode is platform-dependent and we need the
    guarantee on every rotation, not just the first boot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new_secret = secrets.token_hex(_SECRET_BYTES)
    created_at = datetime.now(tz=UTC)
    payload = {"secret": new_secret, "created_at": created_at.isoformat()}
    fd, tmp_name = tempfile.mkstemp(prefix=".mcp-secret.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    if sys.platform != "win32":
        os.chmod(path, POSIX_FILE_MODE)
    return McpSecretRecord(secret=new_secret, created_at=created_at)
