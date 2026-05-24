"""Plan 0010 phase 3 — the `news_for` MCP tool.

Driven in-process via `FastMCP.call_tool` against the real provider + RSS adapter,
whose transport seam is monkeypatched to the captured feed fixtures (shared with
the data-layer tests). Boundary validation (extra-key, limit bounds, window enum,
empty symbol) fires at FastMCP's argument-model layer before the tool body runs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.news_for import register_news_for
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import rss_news
from market_analyser.data.adapters.rss_news import _FEED_CATALOG, RssNewsAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURES = Path(__file__).parents[1] / "data" / "fixtures"
_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
_EMPTY_RSS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<rss version="2.0"><channel><title>empty</title></channel></rss>'
)


def _server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    monkeypatch.setattr(rss_news, "_now", lambda: _FROZEN_NOW)
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
    register_news_for(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "news_for", arguments)


def test_happy_path_btc(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(
        server, {"params": {"symbol": "BTC", "window": "24h", "limit": 10}}
    )

    items = structured["items"]
    assert len(items) == 2  # min(10, the two BTC-token items in the 24h window)
    for item in items:
        assert item["title"] and item["url"] and item["published_at"] and item["source"]
        assert item["compound_sentiment"] is None  # with_sentiment defaults to False
    assert "queried_at" in structured


def test_with_sentiment_populates_compound(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(
        server, {"params": {"symbol": "BTC", "window": "24h", "with_sentiment": True}}
    )

    for item in structured["items"]:
        assert isinstance(item["compound_sentiment"], float)


def test_symbol_none_returns_unfiltered_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {"symbol": None, "window": "24h", "limit": 3}})

    # Six items inside the 24h window; capped to the requested limit, newest-first.
    assert len(structured["items"]) == 3


def test_rejects_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"limit": 101}})


def test_rejects_unknown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"window": "12h"}})


def test_rejects_empty_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": ""}})


def test_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"bogus_key": 1}})
