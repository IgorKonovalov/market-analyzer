"""Settings routes — MCP bearer secret (Plan 0006) + third-party API keys (Plan 0032).

All endpoints are renderer-bearer-gated by the central middleware. The MCP bearer
must not authenticate against any of them (an agent cannot manage the renderer's
credentials); the guarantee is enforced by the middleware in `app.py`, not per
route here — these routes live outside the `/mcp` prefix, so the renderer secret
gates them and the MCP secret cannot.

MCP bearer (Plan 0006 phase 5):

- `GET /settings/mcp-secret` — read the current envelope for the Settings page.
- `POST /settings/mcp-secret/rotate` — generate a new secret, atomic-replace
  `mcp-secret.json`, mutate `app.state.mcp_secret` so the bearer middleware's
  next read picks up the new value, return the new envelope.

Third-party API keys (Plan 0032 phase 1, ADR-0038 — write-only to the renderer):

- `GET /settings/secrets` — presence/absence per known key; never a value.
- `POST /settings/secret` — set one key's value, return the updated status map.
  The value is consumed server-side and never echoed back in the response.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from market_analyser.api.mcp_secret import McpSecretRecord, read_secret_record, rotate_secret
from market_analyser.persistence.secrets import SecretKey, SecretsStore, SecretStatus

router = APIRouter(prefix="/settings", tags=["settings"])


class SetSecretRequest(BaseModel):
    """Body for `POST /settings/secret`. `key` is constrained to the known keys
    (an unknown key 422s at the boundary); `value` is non-empty."""

    key: SecretKey
    value: str = Field(min_length=1)


def _secrets_store(request: Request) -> SecretsStore:
    """Return the configured `SecretsStore` or 503 if the app was built without one.

    503 (not 404) for the same reason as `_secret_path`: the route exists; the
    resource it manages is not configured. Defensive — production always wires a
    store from `__main__`.
    """
    store: SecretsStore | None = request.app.state.secrets_store
    if store is None:
        raise HTTPException(status_code=503, detail="secrets store not configured")
    return store


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
def get_mcp_secret(request: Request, response: Response) -> McpSecretRecord:
    # `no-store`: the response body carries the live MCP bearer, so no cache
    # (browser, proxy, or disk) may retain it (Plan 0072 phase 4, ADR-0066).
    response.headers["Cache-Control"] = "no-store"
    return read_secret_record(_secret_path(request))


@router.post("/mcp-secret/rotate", response_model=McpSecretRecord)
def post_rotate_mcp_secret(request: Request, response: Response) -> McpSecretRecord:
    # `no-store`: the rotate response likewise returns the new bearer in its
    # body — it must not be cached anywhere (Plan 0072 phase 4, ADR-0066).
    response.headers["Cache-Control"] = "no-store"
    record = rotate_secret(_secret_path(request))
    # Mutate the running app's in-memory secret so the bearer middleware's next
    # read sees the new value. Without this, the rewritten file would not
    # invalidate the old bearer until process restart.
    request.app.state.mcp_secret = record.secret
    return record


@router.get("/secrets", response_model=dict[str, str])
def get_secrets_status(request: Request) -> dict[SecretKey, SecretStatus]:
    """Presence/absence per known third-party API key. Never returns a value."""
    return _secrets_store(request).status()


@router.post("/secret", response_model=dict[str, str])
def post_set_secret(request: Request, body: SetSecretRequest) -> dict[SecretKey, SecretStatus]:
    """Set one key, then return the updated status map. The submitted value is
    written server-side and deliberately not echoed back (ADR-0038 write-only)."""
    store = _secrets_store(request)
    store.set(body.key, body.value)
    return store.status()
