"""Plan 0035 phase 7: the `POST /defi/pnl` renderer route.

Fake tx/positions/price sources are injected via `create_app(...)` over a real
in-memory engine (the decoded-tx + price-snapshot caches). Asserts a valid
address returns the per-position + total realized/unrealized JSON, a
non-address is 422 (typed, never 500), a missing key maps to 400, a rate limit
to 429, any other upstream failure to 502, and the route is
renderer-bearer-gated.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from market_analyser.api.app import create_app
from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.adapters.zerion import ZerionAuthError
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
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
from market_analyser.defi.models import Chain, DefiPosition, PositionToken, RewardAmount
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.persistence.engine import make_engine

RENDERER_SECRET = "renderer-test-secret"
_WALLET = "0x2222222222222222222222222222222222222222"
_POOL = "0xpool0000000000000000000000000000000000001"
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_TS1 = datetime(2025, 1, 1, tzinfo=UTC)


class _FakeTxSource:
    def __init__(self, txs: list[DecodedTx] | None = None, error: Exception | None = None) -> None:
        self._txs = txs or []
        self._error = error

    def fetch_transactions(
        self, address: str, *, min_mined_at: datetime | None = None
    ) -> list[DecodedTx]:
        if self._error is not None:
            raise self._error
        return self._txs


class _FakePositionsSource:
    def fetch_positions(self, address: str) -> list[DefiPosition]:
        return [
            DefiPosition(
                position_id="base:aerodrome:lp-1",
                chain="base",
                protocol="aerodrome",
                kind="lp",
                tokens=[PositionToken(symbol="USDC", address=_USDC, amount=1000.0)],
                usd_value=1100.0,
                pool="USDC pool",
                pool_address=_POOL,
            )
        ]


class _TablePriceSource:
    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return {(f"base:{_USDC}", int(_TS1.timestamp())): 1.0}.get((token_key(chain, address), ts))


class _FakeUnclaimedSource:
    """Returns a fixed owed-reward for the wallet's single open LP position."""

    def fetch_unclaimed(self, *, position: DefiPosition, owner: str) -> list[RewardAmount]:
        return [RewardAmount(symbol="AERO", amount=34.2, usd_value=18.0)]


class _FakeProvider:
    """The route never touches the data path; bodies are unused."""

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


_DEPOSIT_TX = DecodedTx.model_validate(
    {
        "chain": "base",
        "hash": "0xadd",
        "operation_type": "deposit",
        "mined_at": _TS1,
        "mined_at_block": 100,
        "status": "confirmed",
        "transfers": [{"direction": "out", "symbol": "USDC", "address": _USDC, "amount": 1000.0}],
        "acts": [{"act_id": "a1", "type": "deposit", "contract_address": _POOL}],
    }
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = make_engine(":memory:")
    yield eng
    eng.dispose()


def _client(engine: Engine, tx_source: _FakeTxSource) -> TestClient:
    app = create_app(
        secret=RENDERER_SECRET,
        engine=engine,
        provider=_FakeProvider(),
        wallet_positions_sources={"zerion": _FakePositionsSource()},
        tx_history_sources={"zerion": tx_source},
        historical_price_source=_TablePriceSource(),
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def test_valid_address_returns_reconstruction(engine: Engine) -> None:
    with _client(engine, _FakeTxSource([_DEPOSIT_TX])) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET}, headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["wallet"] == "0x2222…2222"  # masked
    assert body["position_count"] == 1
    assert body["incomplete"] is False
    assert body["realized_usd"] == 0.0
    assert body["unrealized_usd"] == 100.0  # usd_value 1100 - basis 1000
    position = body["positions"][0]
    assert position["position_id"] == "base:aerodrome:lp-1"
    assert position["cost_basis_usd"] == 1000.0
    assert position["incomplete"] is False


def test_unclaimed_rewards_are_surfaced_when_a_source_is_wired(engine: Engine) -> None:
    """Plan 0084 phase 5 done-when: the open position reports its owed-but-unclaimed
    gauge rewards, and the wallet roll-up carries the same figure."""
    app = create_app(
        secret=RENDERER_SECRET,
        engine=engine,
        provider=_FakeProvider(),
        wallet_positions_sources={"zerion": _FakePositionsSource()},
        tx_history_sources={"zerion": _FakeTxSource([_DEPOSIT_TX])},
        historical_price_source=_TablePriceSource(),
        unclaimed_rewards_sources={"rpc": _FakeUnclaimedSource()},
    )
    with TestClient(app) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET}, headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    position = body["positions"][0]
    assert position["unclaimed_rewards"] == [{"symbol": "AERO", "amount": 34.2, "usd_value": 18.0}]
    assert body["unclaimed_rewards"] == [{"symbol": "AERO", "amount": 34.2, "usd_value": 18.0}]
    # The replay figures are untouched by the current-state augmentation.
    assert body["realized_usd"] == 0.0
    assert body["unrealized_usd"] == 100.0


def test_unclaimed_rewards_is_null_when_no_source_is_wired(engine: Engine) -> None:
    with _client(engine, _FakeTxSource([_DEPOSIT_TX])) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET}, headers=_headers())
    body = response.json()
    assert body["unclaimed_rewards"] is None
    assert body["positions"][0]["unclaimed_rewards"] is None


def test_non_address_is_422(engine: Engine) -> None:
    with _client(engine, _FakeTxSource([_DEPOSIT_TX])) as client:
        response = client.post("/defi/pnl", json={"address": "vitalik.eth"}, headers=_headers())
    assert response.status_code == 422


def test_missing_key_maps_to_400(engine: Engine) -> None:
    source = _FakeTxSource(error=ZerionAuthError("zerion: no API key configured"))
    with _client(engine, source) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET}, headers=_headers())
    assert response.status_code == 400
    assert "key" in response.json()["detail"].lower()


def test_rate_limit_maps_to_429(engine: Engine) -> None:
    source = _FakeTxSource(error=RateLimitedError("zerion: throttled"))
    with _client(engine, source) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET}, headers=_headers())
    assert response.status_code == 429


def test_upstream_failure_maps_to_502(engine: Engine) -> None:
    source = _FakeTxSource(error=UpstreamUnavailableError("zerion: 503"))
    with _client(engine, source) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET}, headers=_headers())
    assert response.status_code == 502


def test_route_is_bearer_gated(engine: Engine) -> None:
    with _client(engine, _FakeTxSource([_DEPOSIT_TX])) as client:
        response = client.post("/defi/pnl", json={"address": _WALLET})
    assert response.status_code == 401
