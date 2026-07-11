"""Plan 0079 phase 3 — `scan_pool_discrepancies` MCP tool.

Phase-3 done-when claims pinned here:
- the tool returns ranked net-of-cost observations through the swappable
  `PoolPriceSource` registry, with full provenance (queried_at, per-observation
  data, source identity) and each observation's capturability note;
- no advice / execution language in the agent-facing payload (charter-safe);
- oversized sets return the typed `too_large` page (total_available / offset /
  returned);
- typed error reasons for an unconfigured registry, a config error, throttle,
  outage, and an on-chain shape drift.

(The tool's presence in the canonical toolset is pinned by
`test_mcp_tools.test_full_toolset_registration_is_exhaustive`.)
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from market_analyser.api.mcp_tools.pool_discrepancies import (
    MAX_DISCREPANCY_OBSERVATIONS,
    ScanPoolDiscrepanciesInput,
    _scan_response,
)
from market_analyser.data.adapters.onchain_pools import PoolPriceConfigError, PoolPriceError
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.sources import PoolPriceSource
from market_analyser.defi.models import PoolQuote

_AS_OF = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _quote(
    *, pool_id: str, dex: str, base_reserve: float, quote_reserve: float, fee_bps: float, pair: str
) -> PoolQuote:
    return PoolQuote(
        pool_id=pool_id,
        dex=dex,
        chain="base",
        pair=pair,
        base_token="0xbase000000000000000000000000000000000001",
        quote_token="0xquote00000000000000000000000000000000002",
        trade_size=1.0,
        price=quote_reserve / base_reserve,
        fee_bps=fee_bps,
        liquidity_base=base_reserve,
        liquidity_quote=quote_reserve,
        as_of=_AS_OF,
    )


class _FakeSource:
    """Structurally conforms to `PoolPriceSource`; returns configured quotes per
    pair, or raises a configured error to exercise the taxonomy."""

    def __init__(
        self,
        quotes_by_pair: dict[str, list[PoolQuote]] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._quotes_by_pair = quotes_by_pair or {}
        self._raises = raises

    def fetch_pool_quotes(self, pair: str, *, trade_size: float) -> list[PoolQuote]:
        if self._raises is not None:
            raise self._raises
        return list(self._quotes_by_pair.get(pair, []))


def _weth_usdc_pools(pair: str = "WETH/USDC") -> list[PoolQuote]:
    return [
        _quote(
            pool_id="0xA",
            dex="aerodrome",
            base_reserve=1000,
            quote_reserve=3_000_000,
            fee_bps=5,
            pair=pair,
        ),
        _quote(
            pool_id="0xB",
            dex="uniswap-v2",
            base_reserve=1000,
            quote_reserve=3_030_000,
            fee_bps=30,
            pair=pair,
        ),
    ]


def _run(sources: dict[str, PoolPriceSource], **kwargs: Any) -> dict[str, Any]:
    params = ScanPoolDiscrepanciesInput(**kwargs)
    return asyncio.run(_scan_response(pool_price_sources=sources, params=params))


# --- happy path + provenance ----------------------------------------------------


def test_returns_ranked_net_of_cost_observations_with_provenance() -> None:
    source = _FakeSource({"WETH/USDC": _weth_usdc_pools()})
    result = _run({"onchain": source}, pairs=["WETH/USDC"], trade_size=1.0)

    assert result["error"] is None
    assert result["source"] == "onchain"
    assert result["pairs"] == ["WETH/USDC"]
    assert result["total_available"] == 1
    assert result["returned"] == 1
    assert result["partial_reason"] is None
    assert "UPPER BOUND" in result["capturability_note"]

    (obs,) = result["observations"]
    assert obs["buy_pool"] == "0xA"
    assert obs["sell_pool"] == "0xB"
    assert obs["net_spread"] < obs["gross_spread"]  # costs subtracted
    assert obs["net_spread"] == pytest.approx(12.380023976)
    assert obs["capturable_at_threshold"] is True
    assert "UPPER BOUND" in obs["capturability_note"]
    # Provenance is the quote's as_of (pydantic serializes UTC with a trailing Z).
    assert obs["queried_at"].startswith("2026-07-11T12:00:00")


def test_swappable_source_selected_by_name() -> None:
    """A registry with a differently-named source and no 'onchain' entry is
    unconfigured — the tool selects by name, staying source-agnostic."""
    result = _run(
        {"other": _FakeSource({"WETH/USDC": _weth_usdc_pools()})}, pairs=["X/Y"], trade_size=1.0
    )
    assert result["error"] == "unconfigured"


def test_no_advice_or_execution_language_in_payload() -> None:
    source = _FakeSource({"WETH/USDC": _weth_usdc_pools()})
    result = _run({"onchain": source}, pairs=["WETH/USDC"], trade_size=1.0)
    blob = json.dumps(result).lower()
    for token in [r"\brecommend", r"\byou should\b", r"\bplace an order\b", r"\bguaranteed\b"]:
        assert not re.search(token, blob), f"advice language leaked: {token}"


# --- bounding (ADR-0046) --------------------------------------------------------


def test_oversized_result_returns_too_large_page() -> None:
    source = _FakeSource(
        {"WETH/USDC": _weth_usdc_pools("WETH/USDC"), "WBTC/USDC": _weth_usdc_pools("WBTC/USDC")}
    )
    page = _run(
        {"onchain": source},
        pairs=["WETH/USDC", "WBTC/USDC"],
        trade_size=1.0,
        max_results=1,
    )
    assert page["total_available"] == 2
    assert page["returned"] == 1
    assert page["partial_reason"] == "too_large"
    assert "offset=1" in page["message"]

    nxt = _run(
        {"onchain": source},
        pairs=["WETH/USDC", "WBTC/USDC"],
        trade_size=1.0,
        max_results=1,
        offset=1,
    )
    assert nxt["returned"] == 1
    assert nxt["partial_reason"] is None


def test_max_results_capped_at_module_max() -> None:
    assert MAX_DISCREPANCY_OBSERVATIONS == 50


# --- error taxonomy -------------------------------------------------------------


def test_unconfigured_registry_returns_typed_error() -> None:
    result = _run({}, pairs=["WETH/USDC"], trade_size=1.0)
    assert result["error"] == "unconfigured"
    assert result["observations"] is None
    assert result["total_available"] is None


def test_config_error_maps_to_config_error() -> None:
    source = _FakeSource(raises=PoolPriceConfigError("no RPC URL"))
    result = _run({"onchain": source}, pairs=["WETH/USDC"], trade_size=1.0)
    assert result["error"] == "config_error"
    assert result["observations"] is None


def test_rate_limited_maps_to_rate_limited() -> None:
    source = _FakeSource(raises=RateLimitedError("429"))
    result = _run({"onchain": source}, pairs=["WETH/USDC"], trade_size=1.0)
    assert result["error"] == "rate_limited"


def test_upstream_unavailable_maps_to_upstream_unavailable() -> None:
    source = _FakeSource(raises=UpstreamUnavailableError("500"))
    result = _run({"onchain": source}, pairs=["WETH/USDC"], trade_size=1.0)
    assert result["error"] == "upstream_unavailable"


def test_malformed_response_maps_to_malformed_response() -> None:
    source = _FakeSource(raises=PoolPriceError("shape drift"))
    result = _run({"onchain": source}, pairs=["WETH/USDC"], trade_size=1.0)
    assert result["error"] == "malformed_response"


# --- input validation -----------------------------------------------------------


def test_rejects_empty_pairs() -> None:
    with pytest.raises(ValueError, match="pairs"):
        ScanPoolDiscrepanciesInput(pairs=[], trade_size=1.0)


def test_rejects_non_positive_trade_size() -> None:
    with pytest.raises(ValueError, match="trade_size"):
        ScanPoolDiscrepanciesInput(pairs=["WETH/USDC"], trade_size=0.0)
