"""Plan 0040 phase 2 — the prediction-market MCP tools.

Done-when claims pinned here:
(a) `search_prediction_markets` returns matching markets (question + ids +
    outcomes with implied probabilities) for a query, through the registry-
    selected source (a spy proves selection + that `limit` is forwarded);
(b) `prediction_market_odds` returns a market's outcomes + implied probabilities
    for a market id, through the same registry-selected source;
(c) both outputs carry a `queried_at` and the `source` identity (provenance);
(d) failures map to typed reasons — `not_found` (unknown id), `rate_limited`,
    `upstream_unavailable`, `malformed_response` — never an exception to the caller;
(e) no output (or tool description) contains advice (ADR-0029 / ADR-0041 — odds
    are a fact, never a call);
(f) both tools are always registered (keyless — no store/secret gate).

All offline — a fake `PredictionMarketSource` stands in for the adapter; no live
Polymarket.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, cast

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ListToolsRequest, ListToolsResult

from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.mcp_tools.prediction_markets import (
    PREDICTION_MARKET_ODDS_DESCRIPTION,
    SEARCH_PREDICTION_MARKETS_DESCRIPTION,
    register_prediction_market_tools,
)
from market_analyser.data.adapters.polymarket import PolymarketError, UnknownMarketError
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.types import MarketOutcome, PredictionMarket
from market_analyser.events import EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.ui_events.buffer import UIEventBuffer

_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _market(
    market_id: str = "558951", question: str = "Will Norway win the World Cup?"
) -> PredictionMarket:
    return PredictionMarket(
        market_id=market_id,
        question=question,
        outcomes=[
            MarketOutcome(label="Yes", implied_probability=0.0585),
            MarketOutcome(label="No", implied_probability=0.9415),
        ],
        closed=False,
        closes_at=datetime(2026, 7, 20, tzinfo=UTC),
        volume_usd=128843528.19,
        liquidity_usd=5244399.88,
        queried_at=_NOW,
        source="polymarket",
    )


class _FakeSource:
    """A `PredictionMarketSource` recording its calls, returning canned markets or
    raising a configured exception."""

    def __init__(
        self,
        *,
        search: list[PredictionMarket] | None = None,
        market: PredictionMarket | None = None,
        search_exc: Exception | None = None,
        fetch_exc: Exception | None = None,
    ) -> None:
        self._search = search if search is not None else []
        self._market = market
        self._search_exc = search_exc
        self._fetch_exc = fetch_exc
        self.search_calls: list[tuple[str, int]] = []
        self.fetch_calls: list[str] = []

    def search_markets(self, query: str, *, limit: int = 20) -> list[PredictionMarket]:
        self.search_calls.append((query, limit))
        if self._search_exc is not None:
            raise self._search_exc
        return self._search[:limit]

    def fetch_market(self, market_id: str) -> PredictionMarket:
        self.fetch_calls.append(market_id)
        if self._fetch_exc is not None:
            raise self._fetch_exc
        assert self._market is not None
        return self._market


def _server(source: _FakeSource) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_prediction_market_tools(server, prediction_market_sources={"polymarket": source})
    return server


def _call(source: _FakeSource, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = anyio.run(_server(source).call_tool, tool, {"params": args})
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)
    return structured


# --- (a) search -----------------------------------------------------------------


def test_search_returns_markets_with_odds() -> None:
    source = _FakeSource(search=[_market("a"), _market("b")])

    result = _call(source, "search_prediction_markets", {"query": "world cup"})

    assert result["error"] is None
    assert result["query"] == "world cup"
    assert result["count"] == 2
    assert [m["market_id"] for m in result["markets"]] == ["a", "b"]
    first = result["markets"][0]
    assert first["question"] == "Will Norway win the World Cup?"
    assert first["outcomes"] == [
        {"label": "Yes", "implied_probability": 0.0585},
        {"label": "No", "implied_probability": 0.9415},
    ]


def test_search_forwards_query_and_limit_to_the_selected_source() -> None:
    source = _FakeSource(search=[_market(str(i)) for i in range(10)])

    result = _call(source, "search_prediction_markets", {"query": "btc", "limit": 3})

    assert source.search_calls == [("btc", 3)]  # registry-selected source, limit forwarded
    assert result["count"] == 3


# --- (b) odds -------------------------------------------------------------------


def test_odds_returns_market_outcomes_by_id() -> None:
    source = _FakeSource(market=_market("558951"))

    result = _call(source, "prediction_market_odds", {"market_id": "558951"})

    assert source.fetch_calls == ["558951"]
    assert result["error"] is None
    market = result["market"]
    assert market["market_id"] == "558951"
    assert [(o["label"], o["implied_probability"]) for o in market["outcomes"]] == [
        ("Yes", 0.0585),
        ("No", 0.9415),
    ]


# --- (c) provenance -------------------------------------------------------------


def test_outputs_carry_queried_at_and_source() -> None:
    source = _FakeSource(search=[_market("a")], market=_market("a"))

    search_result = _call(source, "search_prediction_markets", {"query": "x"})
    odds_result = _call(source, "prediction_market_odds", {"market_id": "a"})

    for result in (search_result, odds_result):
        assert result["source"] == "polymarket"
        # A parseable ISO-8601 provenance timestamp.
        assert datetime.fromisoformat(result["queried_at"]).tzinfo is not None


# --- (d) typed error taxonomy ---------------------------------------------------


def test_odds_unknown_id_maps_to_not_found() -> None:
    source = _FakeSource(fetch_exc=UnknownMarketError("no such market", market_id="999"))

    result = _call(source, "prediction_market_odds", {"market_id": "999"})

    assert result["market"] is None
    assert result["error"] == "not_found"
    assert "no such market" in result["message"]


def test_odds_rate_limited_maps_to_reason() -> None:
    source = _FakeSource(fetch_exc=RateLimitedError("429"))
    result = _call(source, "prediction_market_odds", {"market_id": "a"})
    assert result["market"] is None
    assert result["error"] == "rate_limited"


def test_odds_upstream_unavailable_maps_to_reason() -> None:
    source = _FakeSource(fetch_exc=UpstreamUnavailableError("500"))
    result = _call(source, "prediction_market_odds", {"market_id": "a"})
    assert result["error"] == "upstream_unavailable"


def test_odds_malformed_maps_to_reason() -> None:
    source = _FakeSource(fetch_exc=PolymarketError("shape drift"))
    result = _call(source, "prediction_market_odds", {"market_id": "a"})
    assert result["error"] == "malformed_response"


def test_search_upstream_error_maps_to_reason() -> None:
    source = _FakeSource(search_exc=UpstreamUnavailableError("down"))
    result = _call(source, "search_prediction_markets", {"query": "x"})
    assert result["markets"] is None
    assert result["error"] == "upstream_unavailable"


def test_search_malformed_maps_to_reason() -> None:
    source = _FakeSource(search_exc=PolymarketError("bad shape"))
    result = _call(source, "search_prediction_markets", {"query": "x"})
    assert result["error"] == "malformed_response"


# --- (e) no advice, structurally ------------------------------------------------


def test_outputs_and_descriptions_carry_no_advice_language() -> None:
    """ADR-0041 / ADR-0029: odds are a fact, never a call. No buy/sell/hold/
    recommend language in the responses or the tool descriptions, and the output
    field sets are pinned exactly so an advice-shaped field cannot appear."""
    source = _FakeSource(search=[_market("a")], market=_market("a"))
    search_result = _call(source, "search_prediction_markets", {"query": "x"})
    odds_result = _call(source, "prediction_market_odds", {"market_id": "a"})

    blob = " ".join(
        [
            json.dumps(search_result).lower(),
            json.dumps(odds_result).lower(),
            SEARCH_PREDICTION_MARKETS_DESCRIPTION.lower(),
            PREDICTION_MARKET_ODDS_DESCRIPTION.lower(),
        ]
    )
    for token in ("recommend", "recommendation", "conviction", "entry", "stop", "target"):
        assert not re.search(rf"\b{token}\b", blob), f"advice token {token!r} leaked"

    assert set(search_result.keys()) == {
        "query",
        "markets",
        "count",
        "queried_at",
        "source",
        "error",
        "message",
    }
    assert set(odds_result.keys()) == {"market", "queried_at", "source", "error", "message"}
    assert set(odds_result["market"].keys()) == {
        "market_id",
        "question",
        "outcomes",
        "closed",
        "closes_at",
        "volume_usd",
        "liquidity_usd",
        "queried_at",
        "source",
    }


# --- (f) always registered (keyless) --------------------------------------------


def test_tools_are_always_registered_without_a_store() -> None:
    """Keyless public reads — the tools register with no store/secret gate. A
    minimal `create_mcp_components` (no persistence) still exposes both."""
    engine = make_engine(":memory:")
    apply_migrations(engine)
    session_factory = make_session_factory(engine)
    session_manager, _asgi = create_mcp_components(
        provider=cast("Any", _UnusedProvider()),
        annotations_repository=AnnotationsRepository(session_factory),
        event_bus=EventBus(),
        ui_event_buffer=UIEventBuffer(),
    )
    handler = session_manager.app.request_handlers[ListToolsRequest]
    result = anyio.run(handler, ListToolsRequest(method="tools/list"))
    tools_result = result.root
    assert isinstance(tools_result, ListToolsResult)
    names = {tool.name for tool in tools_result.tools}
    assert {"search_prediction_markets", "prediction_market_odds"} <= names


class _UnusedProvider:
    """A provider that is never called — the always-registered prediction-market
    tools dispatch through their own source registry, not the provider. Dunder
    lookups fall through to `AttributeError` so Python internals don't trip the
    guard during registration."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError(f"provider.{name} must not be called")
