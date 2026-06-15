"""Sidecar lockfile primitives (ADR-0016, Plan 0007 phase 1).

The lockfile lives at `<user-data>/sidecar.lock` and enforces single-instance
sidecar mode. Its contents drive Electron's attach-or-spawn decision:

    {
      "pid": 12345,
      "port": 53221,
      "renderer_secret": "<64 hex chars>",
      "started_at": "<iso8601 UTC>",
      "process_create_time": 1747749781.5,
      "sidecar_version": "0.1.0"
    }

Writes are atomic (`tempfile.mkstemp` + `os.replace`) so a crash mid-write
cannot leave a half-formed file. POSIX file mode is 0600 — same discipline as
`mcp-secret.json` (ADR-0014). Removal happens in `__main__`'s `finally` block.

The liveness probe (`is_owner_alive`) cross-checks `psutil.Process(pid)`'s
`create_time()` against the record's `process_create_time` with ±5s tolerance
so PID reuse after the sidecar exits doesn't pass as a still-live sidecar.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel

DEFAULT_LOCKFILE_NAME = "sidecar.lock"
POSIX_FILE_MODE = 0o600
CREATE_TIME_TOLERANCE_S = 5.0
# Windows-only: a transient handle on the destination lockfile (Electron
# reading it for its attach-or-spawn decision, an AV scan) can deny
# MoveFileEx's replace-existing with ERROR_ACCESS_DENIED. Retry briefly — the
# handle is held only for the read — before the delete-then-rename fallback.
_REPLACE_RETRY_ATTEMPTS = 5
_REPLACE_RETRY_SLEEP_S = 0.1


class LockfileRecord(BaseModel):
    """The on-disk lockfile envelope. All six fields are required."""

    pid: int
    port: int
    renderer_secret: str
    started_at: datetime
    process_create_time: float
    sidecar_version: str


def read_lockfile(path: Path) -> LockfileRecord | None:
    """Read the lockfile at `path`, returning `None` if absent.

    A malformed file (missing fields, non-JSON) raises — same loud-failure
    discipline as `mcp_secret.read_secret_record`.
    """
    if not path.exists():
        return None
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(raw).__name__}")
    return LockfileRecord.model_validate(raw)


def _atomic_replace(tmp_name: str, path: Path) -> None:
    """Rename `tmp_name` onto `path`, replacing any existing file.

    POSIX keeps the single atomic `os.replace`. On Windows a transient handle
    on the destination can deny the replace-existing with a `PermissionError`
    (ERROR_ACCESS_DENIED); retry briefly (the handle is held only for a read),
    then fall back to delete-then-rename. Stays loud — re-raises if the
    destination is genuinely unwritable after the bounded retry.

    The whole Windows retry path sits inside an `if sys.platform == "win32"`
    block so mypy's platform narrowing skips its contents on Linux (and the
    POSIX `else` on Windows) without `--strict`'s `--warn-unreachable` firing.
    The earlier catch-and-re-raise-on-POSIX form left that path as dead code on
    Linux — green on Windows, red on the Linux CI runner.
    """
    if sys.platform == "win32":
        try:
            os.replace(tmp_name, path)
            return
        except PermissionError:
            pass
        last_error: PermissionError | None = None
        for _ in range(_REPLACE_RETRY_ATTEMPTS):
            time.sleep(_REPLACE_RETRY_SLEEP_S)
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError as exc:
                last_error = exc
        # Last resort: drop the destination, then a plain (create-new) rename.
        with contextlib.suppress(FileNotFoundError, PermissionError):
            os.unlink(path)
        try:
            os.replace(tmp_name, path)
        except PermissionError as exc:
            raise exc from last_error
    else:
        os.replace(tmp_name, path)  # POSIX: one atomic rename; errors propagate.


def write_lockfile(path: Path, record: LockfileRecord) -> None:
    """Atomic-write the lockfile + reassert 0600 on POSIX.

    The temp file shares the lockfile's parent dir so the rename is on the same
    filesystem (atomic on POSIX and on Windows for files <4 GB). The replace is
    Windows-resilient — see `_atomic_replace`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump(mode="json")
    fd, tmp_name = tempfile.mkstemp(
        prefix=".sidecar-lock.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        _atomic_replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    if sys.platform != "win32":
        os.chmod(path, POSIX_FILE_MODE)


def remove_lockfile(path: Path) -> None:
    """Delete the lockfile if it exists. Idempotent — no error if absent."""
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def is_owner_alive(record: LockfileRecord) -> bool:
    """Return True iff `record.pid` names a process whose `create_time` matches.

    Cross-checks `process_create_time` with ±5s tolerance (`CREATE_TIME_TOLERANCE_S`)
    so PID reuse by an unrelated process does not pass as live. Returns False
    when the PID is gone, when the process is a zombie, or when the create-time
    delta exceeds tolerance.
    """
    try:
        proc = psutil.Process(record.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    try:
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        actual_create_time = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return abs(actual_create_time - record.process_create_time) <= CREATE_TIME_TOLERANCE_S


def build_self_record(
    *,
    port: int,
    renderer_secret: str,
    sidecar_version: str,
) -> LockfileRecord:
    """Build the lockfile record describing the current process.

    Reads `os.getpid()` and `psutil.Process().create_time()`. The caller owns
    the atomic write — separating record construction from disk I/O lets tests
    drive the probe without touching the filesystem.
    """
    pid = os.getpid()
    create_time = psutil.Process(pid).create_time()
    return LockfileRecord(
        pid=pid,
        port=port,
        renderer_secret=renderer_secret,
        started_at=datetime.now(tz=UTC),
        process_create_time=create_time,
        sidecar_version=sidecar_version,
    )
