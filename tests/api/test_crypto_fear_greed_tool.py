"""Plan 0011 — the `crypto_fear_greed` MCP tool.

Driven in-process via `FastMCP.call_tool` against the real provider + adapter,
whose transport seam is monkeypatched to a captured Alternative.me payload. The
empty `extra="forbid"` input model rejects any supplied argument at FastMCP's
argument-model layer before the tool body runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.crypto_fear_greed import register_crypto_fear_greed
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.crypto_fear_greed import CryptoFearGreedAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURE_TS = 1715212800
_BODY = json.dumps(
    {
        "name": "Fear and Greed Index",
        "data": [
            {
                "value": "55",
                "value_classification": "Greed",
                "timestamp": str(_FIXTURE_TS),
                "time_until_update": "60000",
            },
        ],
        "metadata": {"error": None},
    },
).encode("utf-8")


def _server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    client = ResilientHttpClient(source_name="fng-test", max_retries=0)

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=200, headers={}, body=_BODY, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    provider = DefaultMarketDataProvider(crypto_fng=CryptoFearGreedAdapter(http_client=client))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_crypto_fear_greed(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "crypto_fear_greed", arguments)


def test_happy_path_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {}})

    assert set(structured) == {"value", "classification", "published_at", "queried_at", "source"}
    assert structured["value"] == 55
    assert structured["classification"] == "Greed"
    assert structured["published_at"] == datetime.fromtimestamp(_FIXTURE_TS, tz=UTC).isoformat()
    assert structured["source"] == "alternative.me-fng"
    assert structured["queried_at"]  # present and non-empty ISO timestamp


def test_rejects_any_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"value": 10}})
