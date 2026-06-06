"""Plan 0049 phase 3: the `scan_patterns` MCP tool.

`scan_patterns` sweeps a range and publishes EVERY detected pattern in one
`chart.highlight v1` event — one marker per pattern, multi-bar ones span-bearing,
neutral ones included. It is *derived* data: it writes no annotation row (the
tool takes no repository at all). The detect→filter→map core is the pure
`analysis.markers` shared with the HTTP route (phase 4).

These exercise the factored `_scan_patterns_response` body directly on a single
event loop with a real `EventBus` and a stub provider — no live MCP server.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from datetime import UTC, datetime

from market_analyser.api.mcp_tools.scan_patterns import (
    _scan_patterns_response,
    register_scan_patterns,
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
from market_analyser.events import Envelope, EventBus

_START = datetime(2026, 5, 1, tzinfo=UTC)
_END = datetime(2026, 5, 31, tzinfo=UTC)


def _bar(day: int, *, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 5, day, tzinfo=UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1_000.0,
        source="test",
    )


def _doji_and_hammer_bars() -> list[Bar]:
    """Declining series whose last bar is BOTH a doji (neutral) and a hammer
    (bullish) — two distinct patterns on the same bar, plus a neutral one."""
    return [
        _bar(11, o=111.0, h=112.0, low=109.5, c=110.0),
        _bar(12, o=110.0, h=111.0, low=108.5, c=109.0),
        _bar(13, o=109.0, h=110.0, low=106.5, c=107.0),
        _bar(14, o=107.0, h=108.0, low=104.5, c=105.0),
        _bar(15, o=108.4, h=108.8, low=100.0, c=108.0),
    ]


class _StubProvider:
    """Returns a fixed bar list on get_ohlcv; everything else is unused."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return self._bars

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


def _drain(bus: EventBus, sub: object) -> list[Envelope]:
    items: list[Envelope] = []
    queue = sub.queue  # type: ignore[attr-defined]
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def _run_scan(
    bus: EventBus,
    provider: _StubProvider,
    *,
    patterns: list[str] | None = None,
    min_strength: float | None = None,
) -> dict[str, object]:
    return asyncio.run(
        _scan_patterns_response(
            provider=provider,
            event_bus=bus,
            symbol="AAPL",
            timeframe="1d",
            range_start=_START,
            range_end=_END,
            patterns=patterns,
            min_strength=min_strength,
        )
    )


def test_scan_publishes_exactly_one_event_with_one_marker_per_pattern() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    bars = _doji_and_hammer_bars()
    provider = _StubProvider(bars)

    ack = _run_scan(bus, provider)

    events = _drain(bus, sub)
    assert len(events) == 1
    env = events[0]
    assert env.type == "chart.highlight"
    markers = env.payload["markers"]
    assert isinstance(markers, list)
    assert ack["event_published"] is True
    assert ack["count"] == len(markers)
    patterns_seen = {(m["pattern"], m["kind"]) for m in markers}
    # The same-bar doji (neutral) and hammer (bullish) both made it onto the wire.
    assert ("doji", "neutral_marker") in patterns_seen
    assert ("hammer", "bullish_marker") in patterns_seen


def test_scan_patterns_filter_limits_emitted_set() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    provider = _StubProvider(_doji_and_hammer_bars())

    ack = _run_scan(bus, provider, patterns=["doji"])

    env = _drain(bus, sub)[0]
    markers = env.payload["markers"]
    assert {m["pattern"] for m in markers} == {"doji"}
    assert ack["count"] == 1


def test_scan_min_strength_filter_limits_emitted_set() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    provider = _StubProvider(_doji_and_hammer_bars())

    # A very high threshold drops everything → no event published, count 0.
    ack = _run_scan(bus, provider, min_strength=0.999)

    assert ack["event_published"] is False
    assert ack["count"] == 0
    assert _drain(bus, sub) == []


def test_scan_empty_range_publishes_nothing() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    provider = _StubProvider([])  # nothing cached

    ack = _run_scan(bus, provider)

    assert ack["event_published"] is False
    assert ack["count"] == 0
    assert _drain(bus, sub) == []


def test_scan_patterns_takes_no_annotations_repository() -> None:
    """A sweep is derived, not persisted: the tool's registration depends only on
    the provider and event bus — never an annotations repository (ADR-0045)."""
    params = set(inspect.signature(register_scan_patterns).parameters)
    assert "annotations_repository" not in params
    assert {"provider", "event_bus"} <= params
