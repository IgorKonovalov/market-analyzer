"""Plan 0010 phase 3 — the `sentiment_for_news` MCP tool.

Driven in-process via `FastMCP.call_tool` against the real provider + RSS adapter
with the captured feed fixtures. `as_of` is deliberately absent from the input
model, so passing it is an unknown key and rejected at the boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.sentiment_for_news import register_sentiment_for_news
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
# Hand-computed mean over the two BTC-token items in the 24h window (compounds
# 0.9274 and -0.128); see test_sentiment_news_aggregation.py.
_EXPECTED_BTC_MEAN = (0.9274 + (-0.128)) / 2


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
    register_sentiment_for_news(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "sentiment_for_news", arguments)


def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {"symbol": "BTC", "window": "24h"}})

    assert structured["source"] == "rss-vader"
    assert structured["window"] == "24h"
    assert -1.0 <= structured["score"] <= 1.0
    assert structured["score"] == pytest.approx(_EXPECTED_BTC_MEAN, abs=1e-9)
    assert structured["breakdown"] == {"positive": 1, "negative": 1, "neutral": 0}
    assert "queried_at" in structured


def test_rejects_empty_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": ""}})


def test_rejects_unknown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "BTC", "window": "12h"}})


def test_rejects_as_of_param(monkeypatch: pytest.MonkeyPatch) -> None:
    # as_of is not part of the input model at all; passing it is an unknown key.
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "BTC", "as_of": "2026-01-01T00:00:00Z"}})


def test_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "BTC", "bogus_key": 1}})
