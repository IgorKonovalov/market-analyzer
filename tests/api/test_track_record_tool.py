"""Plan 0080 phase 4: the read-only `get_track_record` MCP tool (ADR-0075/0046).

Covered:
- over a fixture of scored ledger rows, the aggregates (hit-rate, mean R,
  baseline) match the pure aggregation, and the baseline comparison is present;
- an insufficient sample returns `sufficient: false` and no conclusive hit-rate;
- the recent-calls list is bounded (ADR-0046): an oversized set pages with a
  typed `too_large` reason and an honest offset;
- the tool carries no advice — a word-boundary assert on the agent-facing
  description (a factual record, never "you should …", never "trust it");
- registration lives in `tests/api/test_mcp_tools.py` (the full-toolset assert).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.api.mcp_tools.track_record import (
    GET_TRACK_RECORD_DESCRIPTION,
    MAX_RECENT_CALLS,
    _get_track_record_response,
)
from market_analyser.persistence.advice_ledger_repository import (
    AdviceLedgerEntry,
    AdviceLedgerRepository,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_DAY = timedelta(days=1)


def _repo() -> AdviceLedgerRepository:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    return AdviceLedgerRepository(make_session_factory(engine))


def _scored(
    *,
    symbol: str = "AAA",
    idx: int = 0,
    directional_correct: bool = True,
    realized_r: float = 1.0,
) -> AdviceLedgerEntry:
    # `idx` varies the as-of bar so each row is a distinct call identity.
    return AdviceLedgerEntry(
        symbol=symbol,
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=_T0 + idx * _DAY,
        horizon_bars=5,
        direction="long",
        entry_zone=(99.0, 101.0),
        stop=90.0,
        targets=[110.0],
        conviction=0.6,
        forecast_prob=0.6,
        artifact_path=None,
        created_at=_T0 + idx * _DAY,
        outcome_class="target_hit" if directional_correct else "stopped",
        realized_return=0.1 if realized_r >= 0 else -0.1,
        realized_r=realized_r,
        directional_correct=directional_correct,
        scored_at=_T0 + idx * _DAY,
    )


def test_aggregates_over_scored_rows_with_baseline_present() -> None:
    repo = _repo()
    for i in range(13):
        repo.record(_scored(idx=i, directional_correct=True, realized_r=1.0))
    for i in range(7):
        repo.record(_scored(idx=100 + i, directional_correct=False, realized_r=-1.0))

    resp = _get_track_record_response(repository=repo, symbol=None, offset=0, max_calls=None)

    tr = resp.track_record
    assert tr.n == 20
    assert tr.sufficient is True
    assert tr.hit_rate == pytest.approx(0.65)
    assert tr.mean_r == pytest.approx(0.3)
    assert tr.baseline_hit_rate is not None  # the comparison is always present
    assert tr.hit_rate_vs_baseline == pytest.approx(0.0)  # all-long, no edge over buy-and-hold
    assert resp.total_available == 20
    assert resp.returned == 20
    assert resp.partial_reason is None


def test_insufficient_sample_returns_no_conclusive_hit_rate() -> None:
    repo = _repo()
    for i in range(3):
        repo.record(_scored(idx=i))
    resp = _get_track_record_response(repository=repo, symbol=None, offset=0, max_calls=None)
    assert resp.track_record.n == 3
    assert resp.track_record.sufficient is False
    assert resp.track_record.hit_rate is None


def test_recent_calls_are_bounded_and_page() -> None:
    repo = _repo()
    total = MAX_RECENT_CALLS + 1  # one past the page cap
    for i in range(total):
        repo.record(_scored(idx=i))

    resp = _get_track_record_response(repository=repo, symbol=None, offset=0, max_calls=None)
    assert resp.returned == MAX_RECENT_CALLS
    assert resp.total_available == total
    assert resp.partial_reason == "too_large"
    assert resp.message is not None and f"offset={MAX_RECENT_CALLS}" in resp.message

    # Page on: the remainder comes back on the next offset.
    rest = _get_track_record_response(
        repository=repo, symbol=None, offset=MAX_RECENT_CALLS, max_calls=None
    )
    assert rest.returned == 1
    assert rest.partial_reason is None


def test_symbol_filter_scopes_the_record() -> None:
    repo = _repo()
    for i in range(20):
        repo.record(_scored(symbol="AAA", idx=i))
    for i in range(5):
        repo.record(_scored(symbol="BBB", idx=200 + i))

    resp = _get_track_record_response(repository=repo, symbol="BBB", offset=0, max_calls=None)
    assert resp.track_record.n == 5
    assert all(call.symbol == "BBB" for call in resp.recent)


def test_negative_offset_rejected() -> None:
    with pytest.raises(ValueError, match="offset must be >= 0"):
        _get_track_record_response(repository=_repo(), symbol=None, offset=-1, max_calls=None)


def test_description_carries_no_advice() -> None:
    """Charter-safe (ADR-0029): the agent-facing description reports the record as
    fact and never turns it into a call to act. A word-boundary check on the
    advice imperatives a factual surface must never use."""
    text = GET_TRACK_RECORD_DESCRIPTION.lower()
    forbidden = ["should", "trust", "advise", "advice", "you buy", "you sell"]
    for token in forbidden:
        assert re.search(rf"\b{re.escape(token)}\b", text) is None, (
            f"track-record description contains advice token {token!r}"
        )
    # "buy-and-hold" is the baseline's name, not an instruction — it may appear.
    assert "buy-and-hold" in text
