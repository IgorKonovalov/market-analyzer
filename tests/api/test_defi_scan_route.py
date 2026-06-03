"""Plan 0032 phase 4: the `POST /defi/scan` renderer route.

A fake `WalletPositionsSource` is injected via `create_app(wallet_positions_sources=…)`.
Asserts a valid address returns the decoded positions and streams scan_* events,
an invalid address is rejected with a typed 4xx (422, not 500), a missing key
maps to 400, and the route is renderer-bearer-gated (the MCP bearer is rejected
cross-tenant; a missing bearer is 401).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.adapters.zerion import ZerionAuthError
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.defi.models import DefiPosition, PositionToken
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
_WALLET = "0x1111111111111111111111111111111111111111"


class _FakeSource:
    def __init__(
        self,
        positions: Sequence[DefiPosition] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._positions = list(positions or [])
        self._error = error

    def fetch_positions(self, address: str) -> Sequence[DefiPosition]:
        if self._error is not None:
            raise self._error
        return self._positions


def _position(chain: str = "ethereum", symbol: str = "USDC") -> DefiPosition:
    return DefiPosition(
        position_id=f"{chain}:aave-v3:{symbol}",
        chain=chain,  # type: ignore[arg-type]  # known-good chain
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[PositionToken(symbol=symbol, address="0xabc", amount=1.0)],
        usd_value=1000.0,
    )


class _FakeProvider:
    """Settings/route tests don't exercise the data path; bodies are unused."""

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
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: str = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
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


def _build_app(
    source: _FakeSource,
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        wallet_positions_sources={"zerion": source},
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def app(mcp_secret: str, mcp_secret_path: Path, annotations_repo: AnnotationsRepository) -> FastAPI:
    return _build_app(
        _FakeSource([_position("ethereum"), _position("base", "cbBTC")]),
        mcp_secret,
        mcp_secret_path,
        annotations_repo,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def test_valid_address_returns_positions_and_streams_events(app: FastAPI) -> None:
    bus_sub = app.state.event_bus.subscribe()
    with TestClient(app) as client:
        response = client.post("/defi/scan", json={"address": _WALLET}, headers=_renderer_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["position_count"] == 2
    assert len(body["positions"]) == 2
    assert body["wallet"] == "0x1111…1111"  # masked
    assert _WALLET not in response.text  # full address never surfaced
    types = []
    while not bus_sub.queue.empty():
        types.append(bus_sub.queue.get_nowait().type)
    assert "defi.scan_started" in types
    assert "defi.scan_completed" in types


def test_invalid_address_is_typed_4xx_not_500(client: TestClient) -> None:
    response = client.post(
        "/defi/scan", json={"address": "not-an-address"}, headers=_renderer_headers()
    )
    assert response.status_code == 422


def test_extra_key_rejected(client: TestClient) -> None:
    response = client.post(
        "/defi/scan",
        json={"address": _WALLET, "chain": "ethereum"},
        headers=_renderer_headers(),
    )
    assert response.status_code == 422


def test_missing_key_maps_to_400(
    mcp_secret: str, mcp_secret_path: Path, annotations_repo: AnnotationsRepository
) -> None:
    app = _build_app(
        _FakeSource(error=ZerionAuthError("zerion: no API key configured")),
        mcp_secret,
        mcp_secret_path,
        annotations_repo,
    )
    with TestClient(app) as client:
        response = client.post("/defi/scan", json={"address": _WALLET}, headers=_renderer_headers())
    assert response.status_code == 400


def test_upstream_error_maps_to_502(
    mcp_secret: str, mcp_secret_path: Path, annotations_repo: AnnotationsRepository
) -> None:
    app = _build_app(
        _FakeSource(error=UpstreamUnavailableError("zerion: 503")),
        mcp_secret,
        mcp_secret_path,
        annotations_repo,
    )
    with TestClient(app) as client:
        response = client.post("/defi/scan", json={"address": _WALLET}, headers=_renderer_headers())
    assert response.status_code == 502


def test_rejects_missing_bearer(client: TestClient) -> None:
    response = client.post("/defi/scan", json={"address": _WALLET})
    assert response.status_code == 401


def test_rejects_mcp_bearer_cross_tenant(client: TestClient, mcp_secret: str) -> None:
    response = client.post(
        "/defi/scan",
        json={"address": _WALLET},
        headers={"Authorization": f"Bearer {mcp_secret}"},
    )
    assert response.status_code == 401


def test_route_absent_without_a_source(
    mcp_secret: str, mcp_secret_path: Path, annotations_repo: AnnotationsRepository
) -> None:
    """No source wired → the route is not mounted (404, not a 503/500)."""
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        wallet_positions_sources={},  # explicitly empty
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
    )
    with TestClient(app) as client:
        response = client.post("/defi/scan", json={"address": _WALLET}, headers=_renderer_headers())
    assert response.status_code == 404
