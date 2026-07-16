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
    EmissionsDetail,
    FundamentalsPoint,
    UnlockEvent,
    VeGaugeStats,
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


def _covered_non_aerodrome() -> DefiFundamentals:
    return DefiFundamentals(
        query="uniswap",
        protocol_slug="uniswap",
        tvl=5_000_000_000.0,
        as_of=_NOW,
        source="defillama",
        notes=[],
    )


class _FakeDeepReader:
    """An `AerodromeDeepReader` stub that records calls and appends a note."""

    def __init__(
        self,
        emissions: EmissionsDetail | None,
        ve_gauge: VeGaugeStats | None,
    ) -> None:
        self._emissions = emissions
        self._ve_gauge = ve_gauge
        self.calls = 0

    def read_aerodrome(
        self, notes: list[str]
    ) -> tuple[EmissionsDetail | None, VeGaugeStats | None]:
        self.calls += 1
        notes.append("aerodrome deep tier read")
        return self._emissions, self._ve_gauge


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


# -- deep tier fold (Plan 0107 phase 5) -------------------------------------


def test_aerodrome_query_folds_deep_fields() -> None:
    source = _FakeSource(_covered())  # protocol_slug "aerodrome-v1"
    reader = _FakeDeepReader(
        EmissionsDetail(weekly_emission=10_000_000.0, weekly_decay_pct=1.0),
        VeGaugeStats(ve_total_locked=500_000_000.0),
    )

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="AERO",
            deep_reader=reader,
        )
    )

    assert reader.calls == 1
    assert result.emissions_detail is not None
    assert result.emissions_detail.weekly_emission == pytest.approx(10_000_000.0)
    assert result.ve_gauge is not None
    assert result.ve_gauge.ve_total_locked == pytest.approx(500_000_000.0)
    # The deep tier's provenance note is merged onto the DefiLlama notes.
    assert "aerodrome deep tier read" in result.notes
    # DefiLlama-tier fields are preserved through the fold.
    assert result.tvl == pytest.approx(1_000_000_000.0)


def test_non_aerodrome_query_skips_deep_reader() -> None:
    source = _FakeSource(_covered_non_aerodrome())  # protocol_slug "uniswap"
    reader = _FakeDeepReader(EmissionsDetail(weekly_emission=1.0), VeGaugeStats())

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="uniswap",
            deep_reader=reader,
        )
    )

    assert reader.calls == 0  # the deep reader is Aerodrome-only
    assert result.emissions_detail is None
    assert result.ve_gauge is None


def test_aerodrome_query_without_reader_has_null_deep_fields() -> None:
    source = _FakeSource(_covered())

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="AERO",
        )
    )

    assert result.emissions_detail is None
    assert result.ve_gauge is None


def test_deep_reader_returning_none_leaves_fields_null_no_error() -> None:
    # A best-effort deep read that finds nothing (both None) must not error and
    # must leave the deep fields null while still merging its note.
    source = _FakeSource(_covered())
    reader = _FakeDeepReader(None, None)

    result = asyncio.run(
        _defi_fundamentals_response(
            sources={"defillama": source},
            source="defillama",
            symbol_or_protocol="AERO",
            deep_reader=reader,
        )
    )

    assert reader.calls == 1
    assert result.emissions_detail is None
    assert result.ve_gauge is None
    assert "aerodrome deep tier read" in result.notes


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
