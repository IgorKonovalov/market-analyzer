"""Plan 0009 phase 4 — screener end-to-end (network-marked).

Exercises the whole path in one go: the `screener_query` MCP tool ->
`DefaultMarketDataProvider.get_screener` -> `TradingViewScreenerAdapter` ->
`ResilientHttpClient` -> the live TradingView scanner. Skipped in CI (the
`network` marker is filtered out there); run locally with:

    uv run pytest -m network tests/integration/test_screener_end_to_end.py
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools.screener_query import register_screener_query
from market_analyser.data.default_provider import DefaultMarketDataProvider


@pytest.mark.network
def test_screener_query_end_to_end_live() -> None:
    server = FastMCP(name="e2e", stateless_http=True, json_response=True)
    register_screener_query(server, provider=DefaultMarketDataProvider())

    # call_tool returns (content_blocks, structured_dict); typed Any so the
    # 2-tuple unpacks cleanly (its declared return is a union).
    result: Any = anyio.run(
        server.call_tool,
        "screener_query",
        {
            "params": {
                "filters": {"RSI": {"lt": 35}},
                "market": "america",
                "exchange": "NASDAQ",
                "limit": 5,
            },
        },
    )
    _content, structured = result

    rows: list[dict[str, Any]] = structured["rows"]
    assert 1 <= len(rows) <= 5
    for row in rows:
        assert row["symbol"]
        assert row["fields"]["RSI"] < 35
