"""Plan 0107 phase 2 — the `defi_fundamentals` MCP tool (ADR-0102, ADR-0031, ADR-0029).

Done-when claims pinned here:
(a) the tool returns the `DefiFundamentals` payload through the registry-selected
    source — {tvl, tvl_trend, dex_volume, fee_apr, reward_apr, mcap, fdv, unlocks,
    as_of, source, notes} — for a covered token, and honest nulls + notes (not an
    error) for an uncovered one;
(b) the response carries NO action/signal/recommendation field (ADR-0029);
(c) an unregistered source is a clear error, not a silent empty;
(d) the tool registers under the name `defi_fundamentals`.

Registration + `EXPECTED_FULL_TOOLSET` membership are also pinned by
`tests/api/test_mcp_tools.py`; the adapter's parse/degrade correctness lives in
`tests/data/test_defillama_fundamentals_adapter.py`. This module tests the wire wiring
with a fake `DefiFundamentalsSource`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools.defi_fundamentals import (
    DEFI_FUNDAMENTALS_DESCRIPTION,
    DefiFundamentalsInput,
    _defi_fundamentals_response,
    register_defi_fundamentals,
)
from market_analyser.defi.models import (
    DefiFundamentals,
    FundamentalsPoint,
    UnlockEvent,
    VolumeSummary,
)

_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class _FakeSource:
    """A `DefiFundamentalsSource` that records the query and returns a fixed model."""

    def __init__(self, result: DefiFundamentals) -> None:
        self._result = result
        self.queries: list[str] = []

    def fetch_fundamentals(self, query: str) -> DefiFundamentals:
        self.queries.append(query)
        return self._result


def _covered() -> DefiFundamentals:
    return DefiFundamentals(
        query="AERO",
        protocol_slug="aerodrome-v1",
        tvl=1_000_000_000.0,
        tvl_trend=[FundamentalsPoint(date=1784072800, value=1_000_000_000.0)],
        dex_volume=VolumeSummary(
            volume_24h=5_000_000.0, volume_7d=40_000_000.0, volume_30d=270_000_000.0
        ),
        fee_apr=6.25,
        reward_apr=11.0,
        mcap=500_000_000.0,
        fdv=None,
        unlocks=[UnlockEvent(date=1790000000, tokens=1_000_000.0, category="team")],
        as_of=_NOW,
        source="defillama",
        notes=["fdv: no keyless DefiLlama source at this tier (honest-null)"],
    )


def _uncovered() -> DefiFundamentals:
    return DefiFundamentals(
        query="nope",
        protocol_slug="nope",
        as_of=_NOW,
        source="defillama",
        notes=["tvl/mcap: DefiLlama unavailable (HTTP 404) — honest-null"],
    )


# -- (a) returns the payload through the selected source --------------------


def test_returns_covered_payload() -> None:
    source = _FakeSource(_covered())

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="AERO",
        )
    )

    assert source.queries == ["AERO"]
    assert result.tvl == pytest.approx(1_000_000_000.0)
    assert result.dex_volume is not None
    assert result.fee_apr == pytest.approx(6.25)
    assert result.reward_apr == pytest.approx(11.0)
    assert result.mcap == pytest.approx(500_000_000.0)
    assert result.unlocks is not None and len(result.unlocks) == 1
    assert result.source == "defillama"


def test_uncovered_token_is_honest_null_not_error() -> None:
    source = _FakeSource(_uncovered())

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="nope",
        )
    )

    assert result.tvl is None
    assert result.mcap is None
    assert result.notes  # the gap is explained, not silent


# -- (b) conditions-only (ADR-0029) -----------------------------------------


def test_response_has_no_call_shaped_field() -> None:
    source = _FakeSource(_covered())

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="AERO",
        )
    )
    dumped = result.model_dump()

    for forbidden in ("action", "signal", "recommendation", "direction", "conviction"):
        assert forbidden not in dumped
    # The tool description advertises the conditions-only boundary.
    assert "never buy/sell advice" in DEFI_FUNDAMENTALS_DESCRIPTION


# -- (c) unregistered source is a clear error -------------------------------


def test_unregistered_source_raises() -> None:
    with pytest.raises(ValueError, match="not configured"):
        asyncio.run(
            _defi_fundamentals_response(
                sources={"defillama": _FakeSource(_covered())},
                source="messari",
                symbol_or_protocol="AERO",
            )
        )


# -- (d) registration -------------------------------------------------------


def test_tool_registers_under_expected_name() -> None:
    server = FastMCP(name="test")
    register_defi_fundamentals(server, fundamentals_sources={"defillama": _FakeSource(_covered())})

    names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert "defi_fundamentals" in names


def test_input_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        DefiFundamentalsInput(symbol_or_protocol="AERO", extra="x")  # type: ignore[call-arg]
