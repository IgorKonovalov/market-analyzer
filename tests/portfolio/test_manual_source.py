"""Manual positions-file source + unified `Holding` model tests (Plan 0041
phase 2).

The done-when, read at the assertion level:

(a) a positions file is parsed into validated `Holding`s carrying symbol,
    venue, quantity, cost basis, and as-of;
(b) a missing file yields an empty source, not an error;
(c) a malformed entry raises a clear validation error naming the bad row;
(d) the model carries a per-holding `venue` and `as_of` so freshness is never
    blended away — plus the paired-provenance rule (`usd_value` never without
    `pricing_source`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pydantic
import pytest

from market_analyser.portfolio.models import Holding, PortfolioSummary
from market_analyser.portfolio.sources import (
    ManualPositionsError,
    ManualPositionsSource,
)

_FILE_AS_OF = "2026-07-01T00:00:00Z"
_ROW_AS_OF = "2026-06-15T12:30:00Z"


def _file_payload() -> dict[str, Any]:
    return {
        "as_of": _FILE_AS_OF,
        "positions": [
            {"symbol": "AAPL", "quantity": 100, "avg_cost": 185.5},
            {"symbol": "GLD", "quantity": 20.5, "as_of": _ROW_AS_OF},
            {"symbol": "TBILL-LADDER", "quantity": 1, "avg_cost": 25000.0},
        ],
    }


def _write(tmp_path: Path, payload: Any) -> ManualPositionsSource:
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ManualPositionsSource(path)


# --- (a) happy path ---------------------------------------------------------------


def test_positions_file_parses_into_validated_manual_holdings(tmp_path: Path) -> None:
    source = _write(tmp_path, _file_payload())
    holdings = source.load_holdings()

    assert [(h.symbol, h.quantity, h.avg_cost) for h in holdings] == [
        ("AAPL", 100.0, 185.5),
        ("GLD", 20.5, None),  # omitted cost basis is honestly-unknown, never 0.0
        ("TBILL-LADDER", 1.0, 25000.0),
    ]
    assert all(h.venue == "manual" for h in holdings)
    assert all(h.kind == "manual" for h in holdings)
    # Unpriced at parse time: valuation (and its named reference) is the
    # aggregator's job.
    assert all(h.usd_value is None and h.pricing_source is None for h in holdings)


def test_file_as_of_stamps_rows_and_per_row_override_wins(tmp_path: Path) -> None:
    source = _write(tmp_path, _file_payload())
    holdings = source.load_holdings()

    file_stamp = datetime(2026, 7, 1, tzinfo=UTC)
    row_stamp = datetime(2026, 6, 15, 12, 30, tzinfo=UTC)
    assert holdings[0].as_of == file_stamp
    assert holdings[1].as_of == row_stamp  # the row updated more recently keeps its own stamp
    assert holdings[2].as_of == file_stamp


# --- (b) missing file --------------------------------------------------------------


def test_missing_file_is_an_empty_source_not_an_error(tmp_path: Path) -> None:
    source = ManualPositionsSource(tmp_path / "portfolio.json")
    assert source.load_holdings() == []


# --- (c) malformed entries name the bad row ----------------------------------------


def test_non_numeric_quantity_names_row_index_and_symbol(tmp_path: Path) -> None:
    payload = _file_payload()
    payload["positions"][1]["quantity"] = "twenty"
    source = _write(tmp_path, payload)
    with pytest.raises(ManualPositionsError, match=r"row 1 \(GLD\).*quantity"):
        source.load_holdings()


def test_typo_key_is_refused_and_names_the_row(tmp_path: Path) -> None:
    payload = _file_payload()
    payload["positions"][0]["avgcost"] = 185.5  # typo'd key must fail loud, not drop
    del payload["positions"][0]["avg_cost"]
    source = _write(tmp_path, payload)
    with pytest.raises(ManualPositionsError, match=r"row 0 \(AAPL\)"):
        source.load_holdings()


def test_zero_quantity_row_is_refused(tmp_path: Path) -> None:
    payload = _file_payload()
    payload["positions"][2]["quantity"] = 0
    source = _write(tmp_path, payload)
    with pytest.raises(ManualPositionsError, match=r"row 2 \(TBILL-LADDER\)"):
        source.load_holdings()


def test_negative_avg_cost_is_refused(tmp_path: Path) -> None:
    payload = _file_payload()
    payload["positions"][0]["avg_cost"] = -1.0
    source = _write(tmp_path, payload)
    with pytest.raises(ManualPositionsError, match=r"row 0 \(AAPL\).*avg_cost"):
        source.load_holdings()


def test_missing_file_level_as_of_is_a_named_file_error(tmp_path: Path) -> None:
    payload = _file_payload()
    del payload["as_of"]
    source = _write(tmp_path, payload)
    with pytest.raises(ManualPositionsError, match="as_of"):
        source.load_holdings()


def test_naive_as_of_is_refused(tmp_path: Path) -> None:
    payload = _file_payload()
    payload["as_of"] = "2026-07-01T00:00:00"  # no timezone — ambiguous freshness
    source = _write(tmp_path, payload)
    with pytest.raises(ManualPositionsError, match="as_of"):
        source.load_holdings()


def test_non_json_file_is_a_named_error(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.json"
    path.write_text("as_of: 2026-07-01\npositions: []\n", encoding="utf-8")  # YAML, not JSON
    with pytest.raises(ManualPositionsError, match=r"portfolio\.json"):
        ManualPositionsSource(path).load_holdings()


# --- (d) the unified Holding model -------------------------------------------------


def _holding(**overrides: Any) -> Holding:
    base: dict[str, Any] = {
        "symbol": "BTC",
        "venue": "binance",
        "quantity": 0.5,
        "as_of": datetime(2026, 7, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return Holding(**base)


def test_holding_carries_per_holding_venue_and_as_of() -> None:
    holding = _holding()
    assert holding.venue == "binance"
    assert holding.as_of == datetime(2026, 7, 1, tzinfo=UTC)


def test_holding_normalizes_as_of_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    holding = _holding(as_of=datetime(2026, 7, 1, 12, 0, tzinfo=plus_two))
    assert holding.as_of == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def test_holding_rejects_unknown_venue_zero_quantity_and_naive_as_of() -> None:
    with pytest.raises(pydantic.ValidationError):
        _holding(venue="kraken")
    with pytest.raises(pydantic.ValidationError):
        _holding(quantity=0)
    with pytest.raises(pydantic.ValidationError):
        _holding(as_of=datetime(2026, 7, 1))


def test_holding_valuation_and_pricing_source_are_paired() -> None:
    priced = _holding(usd_value=30000.0, pricing_source="binance")
    assert priced.usd_value == 30000.0
    with pytest.raises(pydantic.ValidationError, match="paired"):
        _holding(usd_value=30000.0)  # a valuation with no named reference
    with pytest.raises(pydantic.ValidationError, match="paired"):
        _holding(pricing_source="binance")  # a reference that priced nothing


def test_portfolio_summary_is_strict_and_utc() -> None:
    summary = PortfolioSummary(
        holdings=[_holding()],
        unrealized_pnl_usd=None,
        exposure_by_asset={"BTC": 30000.0},
        exposure_by_venue={"binance": 30000.0},
        legs_as_of={"binance": datetime(2026, 7, 1, tzinfo=UTC)},
        queried_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    assert summary.legs_as_of["binance"].tzinfo == UTC
    with pytest.raises(pydantic.ValidationError):
        PortfolioSummary(
            holdings=[],
            unrealized_pnl_usd=None,
            exposure_by_asset={},
            exposure_by_venue={},
            legs_as_of={},
            queried_at=datetime(2026, 7, 2, tzinfo=UTC),
            advice="rebalance",  # type: ignore[call-arg]  # no advice-shaped extras, ever
        )
