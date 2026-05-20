"""Load-or-generate the long-lived MCP bearer secret (ADR-0014, Plan 0006 phase 1).

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

POSIX_FILE_MODE = 0o600
_SECRET_BYTES = 32  # → 64 hex chars


def load_or_generate_mcp_secret(path: Path) -> str:
    """Return the MCP bearer secret from `path`, generating a new file if absent.

    Atomic write on generation (tmp + os.replace) so a crash mid-write cannot
    leave a half-formed file. A malformed existing file raises rather than
    silently regenerating — a tampered or truncated secret file should surface
    loudly, not be papered over.
    """
    if path.exists():
        return _read_secret(path)
    return _generate_and_write(path)


def _read_secret(path: Path) -> str:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(raw).__name__}")
    secret = raw.get("secret")
    if not isinstance(secret, str) or not secret:
        raise ValueError(f"{path}: missing or empty `secret` field")
    return secret


def _generate_and_write(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(_SECRET_BYTES)
    payload = {"secret": secret, "created_at": datetime.now(tz=UTC).isoformat()}
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
    return secret
