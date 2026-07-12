"""Plan 0078 phase 2 — the `find_convergence_opportunities` MCP tool (ADR-0041/0029).

Done-when claims pinned here (the event-publishing tool pattern):

(a) the tool returns ranked opportunities for a query through the registry-selected
    source, each carrying provenance (`queried_at`, `source`, `market_url`) + full risk
    context (resolution_risk, liquidity_caution, capital_lockup_note) and no advice —
    the market_url provenance link flows through (the URL, or null when no slug), and
    the tool description advertises it in the returned-shape list (Plan 0089);
(b) oversized result sets return the typed `too_large` page (total_available /
    offset / returned), not an unbounded dump (ADR-0046);
(c) the `prediction.screen_completed v1` envelope publishes EXACTLY ONCE on a
    success that yields ≥1 opportunity, strictly after the page is built, and ZERO
    envelopes on every other class — an empty/all-filtered screen, an upstream
    failure, and an input-validation rejection;
(d) failures map to typed reasons (`rate_limited` / `upstream_unavailable` /
    `malformed_response`), never an exception to the caller;
(e) the filter knobs (search_limit, min_confidence) flow through to the screener.

Registration + `EXPECTED_FULL_TOOLSET` membership are pinned by
`tests/api/test_mcp_tools.py`; the screener's ranking/edge/risk correctness lives in
`tests/prediction/test_convergence.py`. This module tests the wire wiring. All
offline — a fake `PredictionMarketSource` stands in for the adapter.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools.prediction_screener import (
    FIND_CONVERGENCE_OPPORTUNITIES_DESCRIPTION,
    MAX_CONVERGENCE_OPPORTUNITIES,
    FindConvergenceOpportunitiesInput,
    _screen_response,
    register_prediction_screener,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.types import MarketOutcome, PredictionMarket
from market_analyser.events import Envelope, EventBus

_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _market(
    *,
    market_id: str,
    prob: float = 0.95,
    closes_in: timedelta = timedelta(days=3),
    volume_usd: float | None = 5_000_000.0,
    outcomes: list[MarketOutcome] | None = None,
    market_url: str | None = None,
) -> PredictionMarket:
    return PredictionMarket(
        market_id=market_id,
        question=f"Will market {market_id} resolve yes?",
        outcomes=outcomes
        if outcomes is not None
        else [
            MarketOutcome(label="Yes", implied_probability=round(1.0 - prob, 6)),
            MarketOutcome(label="No", implied_probability=prob),
        ],
        closed=False,
        closes_at=_NOW + closes_in,
        volume_usd=volume_usd,
        liquidity_usd=None,
        queried_at=_NOW,
        source="polymarket",
        market_url=market_url,
    )


class _FakeSource:
    """A `PredictionMarketSource` recording its search calls, returning canned
    markets or raising a configured exception."""

    def __init__(
        self,
        *,
        search: list[PredictionMarket] | None = None,
        search_exc: Exception | None = None,
    ) -> None:
        self._search = search if search is not None else []
        self._search_exc = search_exc
        self.search_calls: list[tuple[str, int]] = []

    def search_markets(self, query: str, *, limit: int = 20) -> list[PredictionMarket]:
        self.search_calls.append((query, limit))
        if self._search_exc is not None:
            raise self._search_exc
        return self._search[:limit]

    def fetch_market(self, market_id: str) -> PredictionMarket:  # pragma: no cover - unused
        raise AssertionError("the screener never calls fetch_market")


def _call(
    source: _FakeSource,
    bus: EventBus,
    **params: Any,
) -> dict[str, Any]:
    params.setdefault("query", "election")
    return asyncio.run(
        _screen_response(
            prediction_market_sources={"polymarket": source},
            event_bus=bus,
            params=FindConvergenceOpportunitiesInput(**params),
            now=_NOW,
        )
    )


def _drain(run: Callable[[EventBus], Awaitable[object]]) -> tuple[list[Envelope], Exception | None]:
    """Open a subscription, run `run(bus)` (capturing any raise), then drain
    everything published — so nothing published before the drain is missed."""
    bus = EventBus()

    async def _go() -> tuple[list[Envelope], Exception | None]:
        sub = bus.subscribe()
        try:
            error: Exception | None = None
            try:
                await run(bus)
            except Exception as exc:
                error = exc
            envelopes: list[Envelope] = []
            try:
                while True:
                    envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.3))
            except TimeoutError:
                pass
            return envelopes, error
        finally:
            sub.close()

    return asyncio.run(_go())


# --- (a) ranked opportunities + provenance + risk context -----------------------


def test_returns_ranked_opportunities_with_provenance_and_risk() -> None:
    source = _FakeSource(
        search=[
            _market(
                market_id="deep",
                prob=0.95,
                volume_usd=5_000_000.0,
                market_url="https://polymarket.com/event/deep-market",
            ),
            _market(market_id="thin", prob=0.96, volume_usd=8_000.0),
        ]
    )
    result = _call(source, EventBus())

    assert result["error"] is None
    assert result["source"] == "polymarket"
    assert datetime.fromisoformat(result["queried_at"]) == _NOW
    # Ranked by gross return descending: deep (0.0526) > thin (0.0417).
    assert [o["market_id"] for o in result["opportunities"]] == ["deep", "thin"]
    first = result["opportunities"][0]
    # Full risk context + the provenance link travel with every opportunity.
    assert set(first) == {
        "market_id",
        "question",
        "outcome_label",
        "implied_probability",
        "implied_return_if_right",
        "time_to_resolution",
        "capital_lockup_note",
        "liquidity_caution",
        "resolution_risk",
        "volume_usd",
        "closes_at",
        "queried_at",
        "source",
        "market_url",
    }
    assert set(first["resolution_risk"]) == {"level", "reasons"}
    assert result["opportunities"][1]["liquidity_caution"] is not None  # thin book flagged
    # The market_url provenance link flows through (the URL when present, null when the
    # source gave no slug — present either way so the renderer mirror sees the key).
    assert first["market_url"] == "https://polymarket.com/event/deep-market"
    assert result["opportunities"][1]["market_url"] is None


# --- (b) bounded pages ----------------------------------------------------------


def test_oversized_result_set_is_paged_with_too_large() -> None:
    markets = [_market(market_id=f"m{i}", prob=0.90 + i * 0.001) for i in range(5)]
    source = _FakeSource(search=markets)

    result = _call(source, EventBus(), max_results=2)

    assert result["returned"] == 2
    assert result["total_available"] == 5
    assert result["partial_reason"] == "too_large"
    assert result["offset"] == 0
    assert "more remain" in result["message"]
    # The page cap is honoured even when a caller asks for more than the max.
    assert MAX_CONVERGENCE_OPPORTUNITIES == 50


def test_second_page_by_offset_closes_the_set() -> None:
    markets = [_market(market_id=f"m{i}", prob=0.90 + i * 0.001) for i in range(5)]
    source = _FakeSource(search=markets)

    result = _call(source, EventBus(), max_results=2, offset=4)

    assert result["returned"] == 1
    assert result["partial_reason"] is None
    assert result["message"] is None


# --- (c) publish-once / bus-untouched discipline --------------------------------


def test_publishes_exactly_one_envelope_on_success() -> None:
    source = _FakeSource(search=[_market(market_id="deep")])
    envelopes, error = _drain(lambda bus: _run(source, bus))
    assert error is None
    assert len(envelopes) == 1
    assert envelopes[0].type == "prediction.screen_completed"
    assert envelopes[0].version == 1


def test_publishes_nothing_on_empty_fetch() -> None:
    source = _FakeSource(search=[])
    envelopes, error = _drain(lambda bus: _run(source, bus))
    assert error is None
    assert envelopes == []


def test_publishes_nothing_when_all_markets_filtered_out() -> None:
    # A far-from-close market fetched fine but passes no filter -> nothing to show.
    source = _FakeSource(search=[_market(market_id="far", closes_in=timedelta(days=60))])
    envelopes, error = _drain(lambda bus: _run(source, bus))
    assert error is None
    assert envelopes == []


def test_publishes_nothing_on_upstream_error() -> None:
    source = _FakeSource(search_exc=UpstreamUnavailableError("down"))
    envelopes, error = _drain(lambda bus: _run(source, bus))
    assert error is None  # the tool converts it to an error dict, does not raise
    assert envelopes == []


def test_publishes_nothing_on_input_validation_rejection() -> None:
    """A bad param is rejected at the FastMCP boundary before the body runs, so the
    bus stays untouched — driven through the real registered tool."""
    source = _FakeSource(search=[_market(market_id="deep")])

    async def _go() -> tuple[list[Envelope], Exception | None]:
        bus = EventBus()
        sub = bus.subscribe()
        server = FastMCP(name="test", stateless_http=True, json_response=True)
        register_prediction_screener(
            server, prediction_market_sources={"polymarket": source}, event_bus=bus
        )
        error: Exception | None = None
        try:
            # min_confidence > 1.0 violates the input model's le=1.0 bound.
            await server.call_tool(
                "find_convergence_opportunities",
                {"params": {"query": "x", "min_confidence": 1.5}},
            )
        except Exception as exc:
            error = exc
        envelopes: list[Envelope] = []
        try:
            while True:
                envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.3))
        except TimeoutError:
            pass
        sub.close()
        return envelopes, error

    envelopes, error = asyncio.run(_go())
    assert error is not None  # the boundary rejected it
    assert envelopes == []  # and nothing was published


def _run(source: _FakeSource, bus: EventBus, **params: Any) -> Awaitable[object]:
    params.setdefault("query", "election")
    return _screen_response(
        prediction_market_sources={"polymarket": source},
        event_bus=bus,
        params=FindConvergenceOpportunitiesInput(**params),
        now=_NOW,
    )


# --- (d) typed error taxonomy ---------------------------------------------------


def test_upstream_unavailable_maps_to_reason() -> None:
    source = _FakeSource(search_exc=UpstreamUnavailableError("500"))
    result = _call(source, EventBus())
    assert result["opportunities"] is None
    assert result["error"] == "upstream_unavailable"


def test_rate_limited_maps_to_reason() -> None:
    source = _FakeSource(search_exc=RateLimitedError("429"))
    result = _call(source, EventBus())
    assert result["error"] == "rate_limited"


def test_malformed_maps_to_reason() -> None:
    from market_analyser.data.adapters.polymarket import PolymarketError

    source = _FakeSource(search_exc=PolymarketError("shape drift"))
    result = _call(source, EventBus())
    assert result["error"] == "malformed_response"


# --- (e) filter knobs flow through ----------------------------------------------


def test_search_limit_forwarded_and_min_confidence_applied() -> None:
    # A single market at 0.92: passes the default 0.90 floor, fails a 0.95 floor.
    source = _FakeSource(search=[_market(market_id="edge", prob=0.92)])

    passing = _call(source, EventBus(), search_limit=7)
    assert source.search_calls[-1] == ("election", 7)  # limit forwarded to the source
    assert [o["market_id"] for o in passing["opportunities"]] == ["edge"]

    filtered = _call(source, EventBus(), min_confidence=0.95)
    assert filtered["opportunities"] == []
    assert filtered["error"] is None


# --- no advice, structurally ----------------------------------------------------


def test_output_and_description_carry_no_advice_language() -> None:
    source = _FakeSource(search=[_market(market_id="deep"), _market(market_id="thin", prob=0.96)])
    result = _call(source, EventBus())
    blob = " ".join(
        [json.dumps(result).lower(), FIND_CONVERGENCE_OPPORTUNITIES_DESCRIPTION.lower()]
    )
    for token in (
        "recommend",
        "recommendation",
        "buy",
        "sell",
        "hold",
        "short",
        "conviction",
        "entry",
        "stop",
        "target",
        "should",
    ):
        assert not re.search(rf"\b{token}\b", blob), f"advice token {token!r} leaked"


def test_description_lists_market_url_in_returned_shape() -> None:
    """The tool description advertises `market_url` in the returned-shape field list so
    the agent knows the clickable provenance link is available (Plan 0089)."""
    assert "market_url" in FIND_CONVERGENCE_OPPORTUNITIES_DESCRIPTION
