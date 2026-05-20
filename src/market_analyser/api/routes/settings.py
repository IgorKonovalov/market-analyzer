"""Settings routes — reveal and rotate the MCP bearer secret. Plan 0006 phase 5.

Two endpoints, both renderer-bearer-gated by the central middleware:

- `GET /settings/mcp-secret` — read the current envelope so the renderer can
  display it in the Settings page.
- `POST /settings/mcp-secret/rotate` — generate a new secret, atomic-replace
  `mcp-secret.json`, mutate `app.state.mcp_secret` so the bearer middleware's
  next read picks up the new value, return the new envelope.

Rotation is a *renderer-only* privileged operation: the MCP bearer must not
authenticate against these routes (an agent cannot rotate its own credential).
The cross-tenant guarantee is enforced by the middleware in `app.py`, not by
per-route logic here — both routes live outside the `/mcp` prefix, so the
renderer secret gates them and the MCP secret cannot. The phase 5 test suite
asserts the MCP bearer returns 401 on both endpoints.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from market_analyser.api.mcp_secret import McpSecretRecord, read_secret_record, rotate_secret

router = APIRouter(prefix="/settings", tags=["settings"])


def _secret_path(request: Request) -> Path:
    """Return the configured MCP secret path or 503 if the app was built without one.

    503 (Service Unavailable) rather than 404 because the route exists; it's
    the resource it manages that is not configured. In practice the routes are
    only registered when `mcp_secret_path` is set, so this branch is defensive.
    """
    path: Path | None = request.app.state.mcp_secret_path
    if path is None:
        raise HTTPException(status_code=503, detail="mcp secret path not configured")
    return path


@router.get("/mcp-secret", response_model=McpSecretRecord)
def get_mcp_secret(request: Request) -> McpSecretRecord:
    return read_secret_record(_secret_path(request))


@router.post("/mcp-secret/rotate", response_model=McpSecretRecord)
def post_rotate_mcp_secret(request: Request) -> McpSecretRecord:
    record = rotate_secret(_secret_path(request))
    # Mutate the running app's in-memory secret so the bearer middleware's next
    # read sees the new value. Without this, the rewritten file would not
    # invalidate the old bearer until process restart.
    request.app.state.mcp_secret = record.secret
    return record
