"""Done-when for Plan 0109 phase 3: the unified `sentiment(source=…)` tool (ADR-0104).

Folds the retired `sentiment_for_news` (Plan 0010 — RSS + VADER) and
`stocktwits_sentiment` (Plan 0012 — StockTwits crowd labels) into `source` modes of one
tool. Each source section reproduces its predecessor tool's assertions (payload,
boundary rejections, source-specific symbol rules) driven in-process via
`FastMCP.call_tool`. Two consolidation guards: an unregistered `source` is rejected, and
adding a source is demonstrably a one-registry-entry change (a stub third source is
served through `_sentiment_response` with **no new module and no new `register_*` call**).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.sentiment import (
    DEFAULT_SENTIMENT_SOURCES,
    _news_source,
    _sentiment_response,
    register_sentiment,
)
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data._windows import SentimentWindow
from market_analyser.data.adapters import rss_news, stocktwits
from market_analyser.data.adapters.rss_news import _FEED_CATALOG, RssNewsAdapter
from market_analyser.data.adapters.stocktwits import StockTwitsAdapter, StockTwitsHttpClient
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.provider import MarketDataProvider

_FIXTURES = Path(__file__).parents[1] / "data" / "fixtures"


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "sentiment", arguments)


# --------------------------------------------------------------------------- #
# source="news" (was sentiment_for_news)                                        #
# --------------------------------------------------------------------------- #

_NEWS_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
_EMPTY_RSS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<rss version="2.0"><channel><title>empty</title></channel></rss>'
)
# Hand-computed mean over the two BTC-token items in the 24h window (0.9274, -0.128).
_EXPECTED_BTC_MEAN = (0.9274 + (-0.128)) / 2


def _news_server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    monkeypatch.setattr(rss_news, "_now", lambda: _NEWS_FROZEN_NOW)
    bodies = {
        _FEED_CATALOG["coindesk"].url: (_FIXTURES / "rss_news_coindesk.xml").read_bytes(),
        _FEED_CATALOG["cointelegraph"].url: _EMPTY_RSS,
        _FEED_CATALOG["yahoo_finance"].url: (_FIXTURES / "rss_news_yahoo.xml").read_bytes(),
        _FEED_CATALOG["marketwatch"].url: _EMPTY_RSS,
        _FEED_CATALOG["cnbc"].url: (_FIXTURES / "rss_news_cnbc.xml").read_bytes(),
    }
    client = ResilientHttpClient(source_name="rss-test", max_retries=0)

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=200, headers={}, body=bodies[url], elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    provider = DefaultMarketDataProvider(news=RssNewsAdapter(http_client=client))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_sentiment(server, provider=provider)
    return server


def test_news_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _news_server(monkeypatch)
    _content, structured = _call(server, {"params": {"source": "news", "symbol": "BTC"}})

    assert structured["source"] == "rss-vader"
    assert structured["window"] == "24h"
    assert structured["score"] == pytest.approx(_EXPECTED_BTC_MEAN, abs=1e-9)
    assert structured["breakdown"] == {"positive": 1, "negative": 1, "neutral": 0}
    assert "queried_at" in structured
    assert "symbol" not in structured  # news payload carries no symbol echo


def test_news_rejects_empty_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _news_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "news", "symbol": ""}})


def test_news_rejects_unknown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _news_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "news", "symbol": "BTC", "window": "12h"}})


def test_news_rejects_as_of_param(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _news_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(
            server,
            {"params": {"source": "news", "symbol": "BTC", "as_of": "2026-01-01T00:00:00Z"}},
        )


def test_news_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _news_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "news", "symbol": "BTC", "bogus_key": 1}})


# --------------------------------------------------------------------------- #
# source="stocktwits" (was stocktwits_sentiment)                                #
# --------------------------------------------------------------------------- #

_AAPL_BYTES = (_FIXTURES / "stocktwits_AAPL_response.json").read_bytes()
_ST_FROZEN_NOW = datetime(2026, 5, 25, 20, 0, 0, tzinfo=UTC)
_AAPL_COUNTS = {"positive": 7, "negative": 4, "neutral": 19}
_NOT_FOUND_BODY = b'{"errors":[{"message":"Symbol not found"}],"response":{"status":404}}'


def _stocktwits_server(
    monkeypatch: pytest.MonkeyPatch, *, body: bytes = _AAPL_BYTES, status: int = 200
) -> FastMCP:
    monkeypatch.setattr(stocktwits, "_now", lambda: _ST_FROZEN_NOW)
    client = StockTwitsHttpClient(source_name="st-test", max_retries=0)

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    provider = DefaultMarketDataProvider(stocktwits=StockTwitsAdapter(http_client=client))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_sentiment(server, provider=provider)
    return server


def test_stocktwits_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stocktwits_server(monkeypatch)
    _content, structured = _call(server, {"params": {"source": "stocktwits", "symbol": "AAPL"}})

    assert structured["symbol"] == "AAPL"
    assert structured["source"] == "stocktwits"
    assert structured["window"] == "24h"
    k1, k2 = _AAPL_COUNTS["positive"], _AAPL_COUNTS["negative"]
    assert structured["score"] == pytest.approx((k1 - k2) / (k1 + k2), abs=1e-12)
    assert structured["breakdown"] == _AAPL_COUNTS
    assert "queried_at" in structured


def test_stocktwits_lowercase_symbol_echoed_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stocktwits_server(monkeypatch)
    _content, structured = _call(server, {"params": {"source": "stocktwits", "symbol": "aapl"}})
    assert structured["symbol"] == "AAPL"


def test_stocktwits_rejects_empty_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stocktwits_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "stocktwits", "symbol": ""}})


def test_stocktwits_rejects_punctuated_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stocktwits_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "stocktwits", "symbol": "AAPL$"}})


def test_stocktwits_rejects_unknown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stocktwits_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "stocktwits", "symbol": "AAPL", "window": "12h"}})


def test_stocktwits_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stocktwits_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "stocktwits", "symbol": "AAPL", "bogus_key": 1}})


def test_stocktwits_untracked_symbol_is_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # An upstream 404 surfaces as a clear tool error, not an unhandled 500.
    server = _stocktwits_server(monkeypatch, body=_NOT_FOUND_BODY, status=404)
    with pytest.raises(ToolError, match="not tracked by StockTwits"):
        _call(server, {"params": {"source": "stocktwits", "symbol": "MADEUP"}})


# --------------------------------------------------------------------------- #
# Consolidation guards: unknown source + one-entry extensibility                #
# --------------------------------------------------------------------------- #


def test_rejects_unknown_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `source` outside the enum is rejected at the boundary (the Literal)."""

    server = _news_server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"source": "reddit", "symbol": "BTC"}})


def test_adding_a_source_is_one_registry_entry() -> None:
    """ADR-0104 extension point: a new source is one registry binding — no new module,
    no new `register_*` call. A stub handler added to the registry is dispatched by the
    shared body, and the built-in registry still resolves `news`/`stocktwits`."""

    seen: dict[str, Any] = {}

    async def _stub_source(
        provider: MarketDataProvider, symbol: str, window: SentimentWindow
    ) -> dict[str, Any]:
        seen["symbol"] = symbol
        return {"source": "stub", "symbol": symbol, "window": window, "score": 0.42}

    extended = {**DEFAULT_SENTIMENT_SOURCES, "stub": _stub_source}

    result = anyio.run(
        lambda: _sentiment_response(
            provider=DefaultMarketDataProvider(),
            source="stub",
            symbol="XYZ",
            window="24h",
            sources=extended,
        )
    )
    assert result == {"source": "stub", "symbol": "XYZ", "window": "24h", "score": 0.42}
    assert seen["symbol"] == "XYZ"
    # The built-ins are untouched by extending the registry.
    assert set(DEFAULT_SENTIMENT_SOURCES) == {"news", "stocktwits"}
    assert DEFAULT_SENTIMENT_SOURCES["news"] is _news_source
