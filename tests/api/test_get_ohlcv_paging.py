"""Plan 0050 phase 2 done-when: `get_ohlcv` bounded pages + typed `too_large` (ADR-0046).

The returned payload is sliced to at most `MAX_OHLCV_BARS` bars; the cache (the
provider's full series) is never shrunk by paging. `partial_reason="too_large"`
flags a truncated page, and `total_available/offset/returned` let the caller page
forward deterministically.

These exercise the factored `_get_ohlcv_response` / `_paginate` directly so the
slicing logic is testable on one event loop without a live MCP server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.api.mcp_tools.get_ohlcv import (
    MAX_OHLCV_BARS,
    _get_ohlcv_response,
    _paginate,
)
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.types import BackfillResult, Bar, Coverage
from market_analyser.events import EventBus

_T0 = datetime(2015, 1, 1, tzinfo=UTC)


def _bars(n: int) -> list[Bar]:
    return [
        Bar(
            symbol="BTC-USD",
            timeframe="1d",
            event_ts=_T0 + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000_000.0 + i,
            source="yahoo",
        )
        for i in range(n)
    ]


class _FullWindowProvider:
    """A `SupportsBackfill` fake whose cache already holds the full `bars` window.
    `get_ohlcv_with_status` returns the whole series every call, so a second call
    after a paged read proves paging did not shrink the cache."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)
        self.status_calls = 0

    def coverage(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> Coverage:
        return Coverage(cached=list(self._bars), gaps=[])

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return list(self._bars)

    def get_ohlcv_with_status(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> BackfillResult:
        self.status_calls += 1
        return BackfillResult(bars=list(self._bars), partial_reason=None, message=None)


def _call(coord: BackfillCoordinator, *, offset: int = 0, max_bars: int | None = None):
    return asyncio.run(
        _get_ohlcv_response(
            provider=None,  # type: ignore[arg-type]  # sync path uses the coordinator
            coordinator=coord,
            symbol="BTC-USD",
            timeframe="1d",
            start=_T0,
            end=_T0 + timedelta(days=10_000),
            backfill_async=False,
            offset=offset,
            max_bars=max_bars,
        )
    )


# --------------------------------------------------------------------------- #
# Over-cap window: first page is bounded + flagged                             #
# --------------------------------------------------------------------------- #


def test_over_cap_window_returns_capped_first_page_flagged_too_large() -> None:
    total = MAX_OHLCV_BARS + 137
    provider = _FullWindowProvider(_bars(total))
    coord = BackfillCoordinator(provider=provider, event_bus=EventBus())

    resp = _call(coord)

    assert resp.returned == MAX_OHLCV_BARS
    assert len(resp.bars) == MAX_OHLCV_BARS
    assert resp.total_available == total
    assert resp.offset == 0
    assert resp.partial_reason == "too_large"
    assert resp.message is not None
    assert str(total) in resp.message
    # The first page is the head of the series.
    assert resp.bars[0].event_ts == _T0
    assert resp.bars[-1].event_ts == _T0 + timedelta(days=MAX_OHLCV_BARS - 1)


def test_second_page_has_no_overlap_and_no_gap() -> None:
    total = MAX_OHLCV_BARS + 137
    provider = _FullWindowProvider(_bars(total))
    coord = BackfillCoordinator(provider=provider, event_bus=EventBus())

    page1 = _call(coord, offset=0)
    page2 = _call(coord, offset=MAX_OHLCV_BARS)

    # Second page is the remainder: contiguous, no overlap, no gap.
    assert page2.offset == MAX_OHLCV_BARS
    assert page2.returned == 137
    assert page2.total_available == total
    assert page2.partial_reason is None  # nothing remains after this page
    assert page2.bars[0].event_ts == page1.bars[-1].event_ts + timedelta(days=1)
    assert page2.bars[-1].event_ts == _T0 + timedelta(days=total - 1)


def test_cache_holds_whole_window_after_a_capped_read() -> None:
    total = MAX_OHLCV_BARS + 50
    provider = _FullWindowProvider(_bars(total))
    coord = BackfillCoordinator(provider=provider, event_bus=EventBus())

    _call(coord)  # capped read

    # The provider's series is untouched — a fresh coverage read still sees all bars.
    assert len(provider.coverage("BTC-USD", "1d", _T0, _T0).cached) == total


# --------------------------------------------------------------------------- #
# Sub-cap window: unchanged shape                                              #
# --------------------------------------------------------------------------- #


def test_sub_cap_window_returns_all_bars_unflagged() -> None:
    total = MAX_OHLCV_BARS - 1
    provider = _FullWindowProvider(_bars(total))
    coord = BackfillCoordinator(provider=provider, event_bus=EventBus())

    resp = _call(coord)

    assert resp.returned == total
    assert len(resp.bars) == total
    assert resp.total_available == total
    assert resp.partial_reason is None
    assert resp.message is None


# --------------------------------------------------------------------------- #
# max_bars + input validation                                                  #
# --------------------------------------------------------------------------- #


def test_max_bars_shrinks_the_page_but_is_clamped_to_the_cap() -> None:
    provider = _FullWindowProvider(_bars(MAX_OHLCV_BARS + 10))
    coord = BackfillCoordinator(provider=provider, event_bus=EventBus())

    # A smaller max_bars is honored.
    small = _call(coord, max_bars=10)
    assert small.returned == 10
    assert small.partial_reason == "too_large"

    # A max_bars above the cap is clamped to the cap (never exceeds it).
    clamped = _call(coord, max_bars=10_000)
    assert clamped.returned == MAX_OHLCV_BARS


@pytest.mark.parametrize(("offset", "max_bars"), [(-1, None), (0, 0), (0, -5)])
def test_invalid_paging_params_raise(offset: int, max_bars: int | None) -> None:
    provider = _FullWindowProvider(_bars(3))
    coord = BackfillCoordinator(provider=provider, event_bus=EventBus())
    with pytest.raises(ValueError):
        _call(coord, offset=offset, max_bars=max_bars)


# --------------------------------------------------------------------------- #
# A fetch-failure reason wins over too_large, paging hint rides in message     #
# --------------------------------------------------------------------------- #


def test_fetch_failure_reason_takes_precedence_over_too_large() -> None:
    bars = _bars(MAX_OHLCV_BARS + 5)
    resp = _paginate(
        bars,
        offset=0,
        max_bars=None,
        base_reason="rate_limited",
        base_message="yahoo: rate limited (HTTP 429)",
    )
    assert resp.partial_reason == "rate_limited"  # the incomplete-data signal wins
    assert resp.returned == MAX_OHLCV_BARS
    assert resp.total_available == len(bars)
    assert resp.message is not None
    assert "rate limited" in resp.message
    assert "offset=" in resp.message  # the paging hint is not lost


# --------------------------------------------------------------------------- #
# The cap stays under the harness token budget at a realistic per-bar size     #
# --------------------------------------------------------------------------- #


def test_cap_keeps_worst_case_page_under_token_budget() -> None:
    """Pin MAX_OHLCV_BARS against a realistic per-bar char size so a harness
    change is a visible, one-line retune (ADR-0046). The 2026-06-08 incident
    overflowed at 611 bars (~109k chars); a full page must stay well under."""
    # A realistic worst-case bar: long symbol + intraday timestamp + 6 floats.
    worst_case = Bar(
        symbol="BTC-USD",
        timeframe="15m",
        event_ts=datetime(2026, 6, 8, 13, 45, tzinfo=UTC),
        open=123456.78,
        high=123456.78,
        low=123456.78,
        close=123456.78,
        volume=123456789.0,
        source="yahoo",
    )
    per_bar_chars = len(worst_case.model_dump_json())
    worst_case_page_chars = per_bar_chars * MAX_OHLCV_BARS

    # Comfortably under the ~109k-char window that overflowed in the incident.
    assert worst_case_page_chars < 90_000, (
        f"a full page is {worst_case_page_chars} chars at {per_bar_chars}/bar — "
        f"too close to the ~109k overflow point; lower MAX_OHLCV_BARS"
    )
