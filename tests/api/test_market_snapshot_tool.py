"""Plan 0022 phase 3 — the `market_snapshot` MCP tool.

Driven in-process via `FastMCP.call_tool` with a fake provider. The fake records
every `get_quote` symbol and returns a per-symbol `Quote`; a configurable subset
raises a typed `UpstreamDataError`. Every OTHER provider method raises
`AssertionError`, so a green run is itself proof that the snapshot is pure
`get_quote` composition (the "no other data dependency" done-when).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.market_snapshot import (
    MARKET_SNAPSHOT_BASKET,
    register_market_snapshot,
)
from market_analyser.data.errors import UnknownSymbolError, UpstreamDataError
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


def _quote(symbol: str) -> Quote:
    return Quote(
        symbol=symbol,
        price=100.0,
        as_of=datetime(2026, 6, 3, 15, 0, tzinfo=UTC),
        source="yahoo",
        change_pct=1.25,
        previous_close=98.77,
        currency="USD",
        market_state="REGULAR",
    )


class _QuoteFanoutProvider:
    """Records get_quote calls and returns a per-symbol Quote; symbols in
    `failures` raise the mapped error instead. Every other provider method raises
    AssertionError — calling one would mean the snapshot grew an unexpected data
    dependency."""

    def __init__(self, *, failures: dict[str, UpstreamDataError] | None = None) -> None:
        self._failures = failures or {}
        self.quote_calls: list[str] = []

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        self.quote_calls.append(symbol)
        if symbol in self._failures:
            raise self._failures[symbol]
        return _quote(symbol)

    # --- every other Protocol method must NOT be reached by market_snapshot ---
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise AssertionError("market_snapshot must not call get_ohlcv")

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise AssertionError("market_snapshot must not call search_symbols")

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise AssertionError("market_snapshot must not call get_screener")

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise AssertionError("market_snapshot must not call get_sentiment")

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise AssertionError("market_snapshot must not call get_market_sentiment")

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise AssertionError("market_snapshot must not call get_news")

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise AssertionError("market_snapshot must not call get_macro_context")


def _server(provider: _QuoteFanoutProvider) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_market_snapshot(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "market_snapshot", arguments)


def test_happy_path_returns_quote_for_every_basket_symbol() -> None:
    provider = _QuoteFanoutProvider()
    server = _server(provider)

    _content, structured = _call(server, {"params": {}})

    assert set(structured) == {"quotes", "queried_at"}
    quotes = structured["quotes"]
    assert set(quotes) == set(MARKET_SNAPSHOT_BASKET)
    for symbol in MARKET_SNAPSHOT_BASKET:
        entry = quotes[symbol]
        assert entry["error"] is None
        assert entry["message"] is None
        assert entry["quote"]["symbol"] == symbol
        assert entry["quote"]["price"] == 100.0
    assert structured["queried_at"]


def test_one_failing_symbol_degrades_gracefully() -> None:
    provider = _QuoteFanoutProvider(
        failures={"^VIX": UnknownSymbolError("yahoo: no quote for '^VIX'", symbol="^VIX")}
    )
    server = _server(provider)

    _content, structured = _call(server, {"params": {}})
    quotes = structured["quotes"]

    # The failing symbol is null with a typed reason...
    assert quotes["^VIX"]["quote"] is None
    assert quotes["^VIX"]["error"] == "unknown_symbol"
    assert "^VIX" in quotes["^VIX"]["message"]
    # ...and every other basket symbol still returns a quote.
    assert set(quotes) == set(MARKET_SNAPSHOT_BASKET)
    for symbol in MARKET_SNAPSHOT_BASKET:
        if symbol == "^VIX":
            continue
        assert quotes[symbol]["quote"] is not None
        assert quotes[symbol]["error"] is None


def test_composition_calls_get_quote_per_symbol_and_nothing_else() -> None:
    provider = _QuoteFanoutProvider()
    server = _server(provider)

    _call(server, {"params": {}})

    # get_quote called exactly once per basket symbol; the AssertionError stubs on
    # every other method prove no other data dependency was touched.
    assert sorted(provider.quote_calls) == sorted(MARKET_SNAPSHOT_BASKET)
    assert len(provider.quote_calls) == len(MARKET_SNAPSHOT_BASKET)


def test_rejects_any_argument() -> None:
    provider = _QuoteFanoutProvider()
    server = _server(provider)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "AAPL"}})


def test_basket_is_the_documented_eight_symbols() -> None:
    assert MARKET_SNAPSHOT_BASKET == (
        "^GSPC",
        "^IXIC",
        "^VIX",
        "BTC-USD",
        "ETH-USD",
        "EURUSD=X",
        "SPY",
        "GLD",
    )


def test_all_symbols_failing_still_returns_structured_entries() -> None:
    err: UpstreamDataError = UnknownSymbolError("down", symbol="x")
    provider = _QuoteFanoutProvider(failures=dict.fromkeys(MARKET_SNAPSHOT_BASKET, err))
    server = _server(provider)

    _content, structured = _call(server, {"params": {}})

    # Even an all-failure snapshot returns the full keyed map (never raises).
    assert set(structured["quotes"]) == set(MARKET_SNAPSHOT_BASKET)
    for entry in structured["quotes"].values():
        assert entry["quote"] is None
        assert entry["error"] == "unknown_symbol"
