"""Renderer-bearer-gated `POST /settings/stop` (ADR-0016, Plan 0007 phase 1).

The Settings page's "Stop sidecar" button posts here. Under ADR-0016 the
sidecar's lifecycle is detached from Electron, so closing the viewer no longer
stops the sidecar — this endpoint is the in-viewer escape hatch.

Implementation note: we schedule the actual `os.kill(SIGTERM)` slightly after
the 200 response is written, so the HTTP client (and the renderer that issued
the click) sees a successful ack instead of a connection-reset. The sidecar's
SIGTERM handler then runs the lockfile-cleanup `finally` block on shutdown.

Cross-tenant gate: the central bearer middleware in `app.py` routes everything
outside `/mcp` through the renderer bearer. An agent (MCP-authenticated) cannot
stop the sidecar via this endpoint — Plan 0006's cross-tenant test pattern.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/settings", tags=["settings"])


def _send_self_sigterm() -> None:
    """Signal the current process to terminate.

    Uses `signal.raise_signal(...)` rather than `os.kill(os.getpid(), ...)`:
    on Windows the latter unconditionally calls `TerminateProcess`, which
    bypasses Python's signal handlers AND the asyncio loop AND our
    `finally`-block lockfile cleanup. `signal.raise_signal()` goes through
    Python's signal machinery and produces a real `KeyboardInterrupt`/SIGINT
    that uvicorn (and our `__main__` `finally`) actually observes.
    """
    sig = signal.SIGINT if sys.platform == "win32" else signal.SIGTERM
    signal.raise_signal(sig)


@router.post("/stop")
async def post_stop() -> JSONResponse:
    """Schedule a graceful sidecar shutdown after this response is sent."""
    loop = asyncio.get_running_loop()
    # Fire the signal on the next tick so this handler completes (the JSONResponse
    # has to make it back to the client) before the process tears down.
    loop.call_later(0.05, _send_self_sigterm)
    return JSONResponse({"stopping": True}, status_code=200)
