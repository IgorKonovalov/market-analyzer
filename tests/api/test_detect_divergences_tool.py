"""Phase-5 + phase-8 done-when for Plan 0091: the `detect_divergences` MCP tool.

The body is factored into `_detect_divergences_response` so the fetch, empty-cache,
and `as_of`-replay paths run on a single event loop (no live MCP server). A
`_SeededProvider` returns canned bars for one `(symbol, timeframe)`, honouring the
window + `as_of` truncation. The tool wraps the pure `analysis.divergence`
detector, so the test pins that its result equals the standalone detector on the
same bars, plus the no-bars and trailing-replay paths.

Phase 8 (ADR-0090) adds a chart side effect: when the scan finds a divergence the
tool ALSO publishes one `chart.divergences v1` event onto the bus (layer-only,
active-chart-gated) carrying the divergences; an empty result or a `no_bars` miss
publishes nothing, and the data return shape is unchanged. Those paths are pinned
here with a real `EventBus`, mirroring `test_detect_chart_patterns`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis.divergence import DIVERGENCE_LOOKBACK, Oscillator, detect_divergences
from market_analyser.api.mcp_tools.detect_divergences import (
    DivergencesResponse,
    _detect_divergences_response,
)
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.events import ChartDivergencesPayloadV1, Envelope, EventBus

_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)

# A lower-high price path with heavy up-volume / light down-volume: OBV accumulates
# to a HIGHER high at the lower price peak -> a hidden bearish divergence on OBV.
_ACCUMULATION_CLOSES: list[float] = [
    100.0, 100.0, 100.0, 100.0,
    104.0, 108.0, 112.0, 116.0, 120.0,  # rally1 peak1 = 120
    116.0, 112.0, 108.0, 105.0,  # pull1
    108.0, 111.0, 114.0, 115.0,  # rally2 peak2 = 115 (lower high)
    112.0, 109.0, 107.0,  # pull2
]  # fmt: skip


def _accumulation_bars(symbol: str) -> list[Bar]:
    closes = _ACCUMULATION_CLOSES
    n = len(closes)
    bars: list[Bar] = []
    prev: float | None = None
    for i, c in enumerate(closes):
        v = 500.0 if prev is None else (1000.0 if c > prev else 100.0 if c < prev else 300.0)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_END - timedelta(days=n - 1 - i),
                open=c,
                high=c + 0.5,
                low=c - 0.5,
                close=c,
                volume=v,
                source="fixture",
            )
        )
        prev = c
    return bars


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window
    and truncated at `as_of`. Every other Protocol method raises."""

    def __init__(self, bars_by_key: dict[tuple[str, str], Sequence[Bar]]) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        bars = [b for b in self._by_key.get((symbol, timeframe), []) if start <= b.event_ts <= end]
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return bars

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


def _drain(sub: object) -> list[Envelope]:
    items: list[Envelope] = []
    queue = sub.queue  # type: ignore[attr-defined]
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def _run(
    bus: EventBus,
    provider: _SeededProvider,
    *,
    symbol: str = "A",
    timeframe: str = "1d",
    oscillator: Oscillator = "obv",
    as_of: datetime | None = None,
) -> DivergencesResponse:
    return asyncio.run(
        _detect_divergences_response(
            provider=provider,
            event_bus=bus,
            symbol=symbol,
            timeframe=timeframe,
            oscillator=oscillator,
            lookback=DIVERGENCE_LOOKBACK,
            as_of=as_of,
        )
    )


def test_detect_divergences_matches_standalone_detector() -> None:
    """A populated symbol returns the divergences the pure detector reports on the
    identical bars — the tool is a thin, faithful wrapper."""

    bars = _accumulation_bars("A")
    resp = _run(EventBus(), _SeededProvider({("A", "1d"): bars}), oscillator="obv")

    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.scanned_at.tzinfo is not None
    assert resp.result == detect_divergences(bars, "obv", DIVERGENCE_LOOKBACK)
    # The known hidden-bearish OBV divergence is present with sane anchors.
    hidden = [d for d in resp.result if d.kind == "hidden_bearish"]
    assert len(hidden) == 1
    assert hidden[0].price_pivots[1].price < hidden[0].price_pivots[0].price
    assert hidden[0].oscillator_pivots[1].price > hidden[0].oscillator_pivots[0].price


def test_detect_divergences_no_bars() -> None:
    resp = _run(EventBus(), _SeededProvider({}), oscillator="rsi")
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_detect_divergences_as_of_is_trailing() -> None:
    """`as_of` truncates to bars at or before it — the detection never sees a future
    bar, and it matches a direct computation on the truncated series."""

    bars = _accumulation_bars("A")
    as_of = bars[16].event_ts  # at peak2, before the confirming pullback

    resp = _run(EventBus(), _SeededProvider({("A", "1d"): bars}), oscillator="obv", as_of=as_of)

    assert resp.result is not None
    truncated = [b for b in bars if b.event_ts <= as_of]
    assert resp.result == detect_divergences(truncated, "obv", DIVERGENCE_LOOKBACK)
    # Truncated before the pivot is confirmed -> the divergence is not yet knowable.
    assert resp.result == []


def test_detect_divergences_publishes_one_chart_divergences_when_found() -> None:
    """Phase 8 (ADR-0090): a scan that finds a divergence publishes exactly one
    `chart.divergences v1` event for the scanned symbol/timeframe, whose
    `divergences` equal the tool's `result` (same anchors) — and the data return is
    unchanged. The payload round-trips through the model losslessly (no dropped
    field), so equality is on the reconstructed `Divergence` list."""

    bars = _accumulation_bars("A")
    bus = EventBus()
    sub = bus.subscribe()
    resp = _run(bus, _SeededProvider({("A", "1d"): bars}), oscillator="obv")

    assert resp.result is not None and resp.result != []  # precondition: a divergence exists

    events = _drain(sub)
    assert len(events) == 1
    env = events[0]
    assert env.type == "chart.divergences"
    assert env.payload["symbol"] == "A"
    assert env.payload["timeframe"] == "1d"
    # The published geometry is exactly the returned result (same anchors, no drop).
    republished = ChartDivergencesPayloadV1.model_validate(env.payload)
    assert republished.divergences == resp.result


def test_detect_divergences_empty_result_publishes_nothing() -> None:
    """A scan that runs but finds nothing (here: `as_of`-truncated before the pivot
    confirms, so `result == []`) publishes no event — parity with
    `detect_chart_patterns`'s count=0 no-publish."""

    bars = _accumulation_bars("A")
    as_of = bars[16].event_ts
    bus = EventBus()
    sub = bus.subscribe()
    resp = _run(bus, _SeededProvider({("A", "1d"): bars}), oscillator="obv", as_of=as_of)

    assert resp.result == []
    assert _drain(sub) == []


def test_detect_divergences_no_bars_publishes_nothing() -> None:
    """The `no_bars` miss publishes nothing — an honest miss never draws."""

    bus = EventBus()
    sub = bus.subscribe()
    resp = _run(bus, _SeededProvider({}), oscillator="rsi")

    assert resp.partial_reason == "no_bars"
    assert _drain(sub) == []
