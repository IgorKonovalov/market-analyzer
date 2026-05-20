"""Plan 0006 phase 5 done-when: the Settings page's MCP-secret routes.

Asserted behaviors:
- `GET /settings/mcp-secret` returns the current envelope to the renderer bearer.
- `POST /settings/mcp-secret/rotate` generates a new secret, atomic-rewrites
  the file, preserves 0600 on POSIX, and the response carries the new value.
- Rotation invalidates the old MCP bearer on the next request to `/mcp` — the
  in-memory middleware state moves in lock-step with the file (the gap the
  handoff flagged: closure-captured `mcp_secret` would not have done this).
- The new MCP bearer authenticates against `/mcp` (positive path).
- Both settings endpoints reject the MCP bearer with 401: rotation is a
  renderer-only privileged operation; an agent cannot rotate its own credential.
- Both settings endpoints reject missing/wrong renderer bearer with 401.
- Rotation does not affect the renderer secret: `/ohlcv` still authenticates
  with the original renderer bearer after MCP rotation.
"""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.types import (
    Bar,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"


class _FakeProvider:
    """Settings tests don't exercise the data path; bodies are never invoked."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return []

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_news(
        self, symbol: str, window: str, as_of: datetime | None = None
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


@pytest.fixture
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Enter TestClient as a context manager so the MCP session manager's
    `run()` lifespan is entered. Without this, requests to `/mcp` raise
    'Task group is not initialized'."""
    with TestClient(app) as c:
        yield c


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def test_get_returns_current_secret_to_renderer_bearer(
    client: TestClient, mcp_secret: str
) -> None:
    response = client.get("/settings/mcp-secret", headers=_renderer_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["secret"] == mcp_secret
    assert "created_at" in body and body["created_at"]


def test_get_rejects_missing_bearer(client: TestClient) -> None:
    response = client.get("/settings/mcp-secret")
    assert response.status_code == 401


def test_get_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: MCP bearer must not authenticate to the renderer-side settings route."""
    response = client.get("/settings/mcp-secret", headers=_mcp_headers(mcp_secret))
    assert response.status_code == 401


def test_post_rotate_returns_new_secret(client: TestClient, mcp_secret: str) -> None:
    response = client.post("/settings/mcp-secret/rotate", headers=_renderer_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    new_secret = body["secret"]
    assert isinstance(new_secret, str)
    assert len(new_secret) == 64  # 32 bytes hex-encoded
    assert new_secret != mcp_secret
    assert "created_at" in body and body["created_at"]


def test_post_rotate_writes_new_secret_to_disk(
    client: TestClient, mcp_secret_path: Path, mcp_secret: str
) -> None:
    response = client.post("/settings/mcp-secret/rotate", headers=_renderer_headers())
    assert response.status_code == 200
    new_secret = response.json()["secret"]
    on_disk = json.loads(mcp_secret_path.read_text(encoding="utf-8"))
    assert on_disk["secret"] == new_secret
    assert on_disk["secret"] != mcp_secret


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits don't apply on Windows")
def test_post_rotate_preserves_0600_mode(
    client: TestClient, mcp_secret_path: Path
) -> None:
    response = client.post("/settings/mcp-secret/rotate", headers=_renderer_headers())
    assert response.status_code == 200
    mode_bits = stat.S_IMODE(mcp_secret_path.stat().st_mode)
    assert mode_bits == 0o600, f"expected 0600 after rotation, got {oct(mode_bits)}"


def test_post_rotate_invalidates_old_mcp_bearer(
    client: TestClient, mcp_secret: str
) -> None:
    """The handoff-flagged gap: rotation must make the next /mcp request with the
    old bearer 401, not the next process restart."""
    pre_rotation = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=_mcp_headers(mcp_secret),
    )
    assert pre_rotation.status_code != 401, (
        f"old bearer should authenticate pre-rotation, got {pre_rotation.status_code}"
    )

    rotated = client.post("/settings/mcp-secret/rotate", headers=_renderer_headers())
    assert rotated.status_code == 200

    post_rotation = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=_mcp_headers(mcp_secret),
    )
    assert post_rotation.status_code == 401, (
        f"old bearer should 401 post-rotation, got {post_rotation.status_code}"
    )


def test_post_rotate_new_bearer_authenticates_mcp(client: TestClient) -> None:
    rotated = client.post("/settings/mcp-secret/rotate", headers=_renderer_headers())
    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=_mcp_headers(new_secret),
    )
    # The MCP transport may return 200, 202, 400, or another status depending on
    # protocol-init state — anything except 401 proves the bearer middleware
    # passed the request through.
    assert response.status_code != 401, (
        f"new bearer must authenticate to /mcp, got {response.status_code}"
    )


def test_post_rotate_rejects_missing_bearer(client: TestClient) -> None:
    response = client.post("/settings/mcp-secret/rotate")
    assert response.status_code == 401


def test_post_rotate_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: agents cannot rotate their own credential."""
    response = client.post("/settings/mcp-secret/rotate", headers=_mcp_headers(mcp_secret))
    assert response.status_code == 401


def test_renderer_bearer_unaffected_by_rotation(client: TestClient) -> None:
    """Rotation is MCP-scoped; the renderer secret keeps working against renderer routes."""
    rotated = client.post("/settings/mcp-secret/rotate", headers=_renderer_headers())
    assert rotated.status_code == 200

    healthz = client.get("/healthz")
    assert healthz.status_code == 200

    # `/ohlcv` still authenticates with the original renderer secret post-rotation.
    ohlcv = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
        headers=_renderer_headers(),
    )
    # The stub provider returns [], so the route returns 200 with an empty list —
    # the assertion is "renderer bearer still works", not data shape.
    assert ohlcv.status_code != 401
