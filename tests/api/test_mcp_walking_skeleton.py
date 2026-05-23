"""Plan 0006 phase 1 done-when: the MCP middleware seam (post-phase-4 ping removal).

Covers:
- `/mcp` returns 401 without a bearer and with a wrong bearer, and the response
  body does not leak the expected secret.
- `mcp-secret.json` is created with mode 0600 on POSIX (skipped on Windows).
- Cross-tenant escalation is blocked: renderer bearer cannot authenticate
  against `/mcp`, and the MCP bearer cannot authenticate against `/ohlcv`.

Phase-4 swapped the `ping` walking-skeleton tool for the production tools, so
the ping round-trip test moved to `test_mcp_tools.py`. Everything else here is
unchanged — bearer dispatch, cross-tenant guarantees, and file-mode discipline
are still the contract this file defends.
"""

from __future__ import annotations

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
    """Minimal MarketDataProvider stub. Cross-tenant tests only hit /ohlcv with the
    wrong bearer, so they never call into this; the body is never invoked."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise AssertionError("provider should not be reached in cross-tenant tests")

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
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
def app(mcp_secret: str, annotations_repo: AnnotationsRepository) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_mcp_no_bearer_returns_401(client: TestClient) -> None:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


def test_mcp_wrong_bearer_returns_401_without_leaking_secret(
    client: TestClient, mcp_secret: str
) -> None:
    wrong = "definitely-not-the-real-secret"
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {wrong}"},
    )
    assert response.status_code == 401
    assert mcp_secret not in response.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits don't apply on Windows")
def test_mcp_secret_file_mode_is_0600(mcp_secret_path: Path, mcp_secret: str) -> None:
    assert mcp_secret_path.exists()
    mode_bits = stat.S_IMODE(mcp_secret_path.stat().st_mode)
    assert mode_bits == 0o600, f"expected 0600, got {oct(mode_bits)}"


def test_renderer_bearer_does_not_authenticate_mcp(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {RENDERER_SECRET}"},
    )
    assert response.status_code == 401


def test_mcp_bearer_does_not_authenticate_renderer(client: TestClient, mcp_secret: str) -> None:
    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
        headers={"Authorization": f"Bearer {mcp_secret}"},
    )
    assert response.status_code == 401


def test_load_or_generate_is_idempotent(mcp_secret_path: Path) -> None:
    """Calling the loader twice returns the same secret — the file is the source of truth."""
    first = load_or_generate_mcp_secret(mcp_secret_path)
    second = load_or_generate_mcp_secret(mcp_secret_path)
    assert first == second
    assert len(first) == 64  # 32 bytes hex-encoded
