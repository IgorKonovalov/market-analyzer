"""Plan 0012 phase 3 — the `stocktwits_sentiment` MCP tool.

Driven in-process via `FastMCP.call_tool` against the real provider + StockTwits
adapter with the committed AAPL capture. `_now` is frozen so the captured posts
stay inside the window. Boundary failures (empty/punctuated symbol, unknown
window/key) raise `ToolError`; an untracked symbol (upstream 404) surfaces as a
clear tool error, not an unhandled 500.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.stocktwits_sentiment import register_stocktwits_sentiment
from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters import stocktwits
from market_analyser.data.adapters.stocktwits import StockTwitsAdapter, StockTwitsHttpClient
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURES = Path(__file__).parents[1] / "data" / "fixtures"
_AAPL_BYTES = (_FIXTURES / "stocktwits_AAPL_response.json").read_bytes()
# Just after the AAPL capture's newest post, so window="24h" keeps all 30.
_FROZEN_NOW = datetime(2026, 5, 25, 20, 0, 0, tzinfo=UTC)
_AAPL_COUNTS = {"positive": 7, "negative": 4, "neutral": 19}

_NOT_FOUND_BODY = b'{"errors":[{"message":"Symbol not found"}],"response":{"status":404}}'


def _server(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes = _AAPL_BYTES,
    status: int = 200,
) -> FastMCP:
    monkeypatch.setattr(stocktwits, "_now", lambda: _FROZEN_NOW)
    client = StockTwitsHttpClient(source_name="st-test", max_retries=0)

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    provider = DefaultMarketDataProvider(stocktwits=StockTwitsAdapter(http_client=client))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_stocktwits_sentiment(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "stocktwits_sentiment", arguments)


def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {"symbol": "AAPL", "window": "24h"}})

    assert structured["symbol"] == "AAPL"
    assert structured["source"] == "stocktwits"
    assert structured["window"] == "24h"
    k1, k2 = _AAPL_COUNTS["positive"], _AAPL_COUNTS["negative"]
    assert structured["score"] == pytest.approx((k1 - k2) / (k1 + k2), abs=1e-12)
    assert structured["breakdown"] == _AAPL_COUNTS
    assert "queried_at" in structured


def test_lowercase_symbol_echoed_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {"symbol": "aapl", "window": "24h"}})

    assert structured["symbol"] == "AAPL"


def test_rejects_empty_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": ""}})


def test_rejects_punctuated_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "AAPL$"}})


def test_rejects_unknown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "AAPL", "window": "12h"}})


def test_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"symbol": "AAPL", "bogus_key": 1}})


def test_untracked_symbol_is_clear_error_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch, body=_NOT_FOUND_BODY, status=404)
    with pytest.raises(ToolError, match="not tracked by StockTwits"):
        _call(server, {"params": {"symbol": "MADEUP", "window": "24h"}})
