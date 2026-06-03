"""Plan 0022 phase 2 — the `bitcoin_market_pulse` MCP tool.

Driven in-process via `FastMCP.call_tool` against the real provider + CoinGecko
adapter, whose transport seam is monkeypatched and dispatches by URL to the
committed `/global` capture plus an inline `/simple/price` body — so the suite
never touches the network. The `extra="forbid"` input model rejects stray keys at
FastMCP's argument-model layer before the tool body runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market_analyser.api.mcp_tools.bitcoin_market_pulse import register_bitcoin_market_pulse
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.coingecko import CoinGeckoAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider

_GLOBAL_BYTES = (
    Path(__file__).parent.parent / "data" / "fixtures" / "coingecko_global.json"
).read_bytes()
_FIXTURE_UPDATED_AT = 1716544000
_PRICE_BYTES = json.dumps({"bitcoin": {"usd": 65789.47, "usd_24h_change": 3.2}}).encode("utf-8")

_MACRO_FIELDS = {
    "market",
    "btc_price",
    "btc_change_24h",
    "btc_dominance_pct",
    "total_market_cap_usd",
    "total_market_cap_change_24h",
    "regime",
    "as_of",
    "source",
}


def _server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    client = ResilientHttpClient(source_name="coingecko-test", max_retries=0)

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = _PRICE_BYTES if "simple/price" in url else _GLOBAL_BYTES
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    provider = DefaultMarketDataProvider(coingecko=CoinGeckoAdapter(http_client=client))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_bitcoin_market_pulse(server, provider=provider)
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "bitcoin_market_pulse", arguments)


def test_happy_path_returns_macro_and_queried_at(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {}})

    assert set(structured) == {"macro", "queried_at"}
    macro = structured["macro"]
    assert set(macro) == _MACRO_FIELDS
    assert macro["market"] == "crypto"
    assert macro["btc_price"] == 65789.47
    assert macro["btc_change_24h"] == 3.2
    assert macro["btc_dominance_pct"] == 52.3
    assert macro["total_market_cap_usd"] == 2500000000000.0
    assert macro["total_market_cap_change_24h"] == 1.5
    assert macro["regime"] == "btc_led"
    # pydantic's model_dump(mode="json") serialises a UTC datetime with a "Z"
    # suffix (not "+00:00"), so the expected value matches that form.
    expected_as_of = (
        datetime.fromtimestamp(_FIXTURE_UPDATED_AT, tz=UTC).isoformat().replace("+00:00", "Z")
    )
    assert macro["as_of"] == expected_as_of
    assert macro["source"] == "coingecko"
    assert structured["queried_at"]  # present, non-empty ISO timestamp


def test_explicit_crypto_market_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    _content, structured = _call(server, {"params": {"market": "crypto"}})

    assert structured["macro"]["regime"] == "btc_led"


def test_rejects_unknown_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"bogus_key": 1}})


def test_rejects_unknown_market_value(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    with pytest.raises(ToolError):
        _call(server, {"params": {"market": "equity"}})


def test_description_advertises_structural_condition_not_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(monkeypatch)
    tools = anyio.run(server.list_tools)
    tool = next(t for t in tools if t.name == "bitcoin_market_pulse")
    description = (tool.description or "").lower()
    assert "coingecko" in description
    assert "regime" in description
    # Honesty: the regime is a structural condition, not advice (Plan 0022 guardrail).
    assert "not a buy/sell" in description or "structural condition" in description
    assert "point-in-time" in description or "no as_of" in description
