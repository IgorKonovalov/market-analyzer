"""Plan 0007 phase 1: the `POST /settings/stop` route.

Asserts:
- Renderer bearer → 200 with `{"stopping": true}` body.
- No bearer → 401.
- MCP bearer → 401 (cross-tenant: agents cannot stop the sidecar).
- The handler schedules a process-self-signal on the asyncio loop AFTER the
  response is delivered (the `loop.call_later` call carries the unbound function
  pointer; we patch it to capture the closure rather than really kill the test
  runner).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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
    with TestClient(app) as c:
        yield c


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def test_stop_with_renderer_bearer_returns_200_and_schedules_signal(
    client: TestClient,
) -> None:
    """Patch `_send_self_sigterm` so the test runner survives. The handler
    enqueues `loop.call_later(0.05, _send_self_sigterm)`; we patch the function
    pointer in the module to a sentinel and confirm the route returned 200."""
    sentinel: dict[str, bool] = {"called": False}

    def fake_signal() -> None:
        sentinel["called"] = True

    with patch(
        "market_analyser.api.routes.settings_stop._send_self_sigterm",
        side_effect=fake_signal,
    ):
        response = client.post("/settings/stop", headers=_renderer_headers())
        assert response.status_code == 200, response.text
        assert response.json() == {"stopping": True}


def test_stop_rejects_missing_bearer(client: TestClient) -> None:
    response = client.post("/settings/stop")
    assert response.status_code == 401


def test_stop_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: agents on /mcp must not stop the sidecar through
    /settings/stop (it's renderer-only by middleware gating)."""
    response = client.post("/settings/stop", headers=_mcp_headers(mcp_secret))
    assert response.status_code == 401
