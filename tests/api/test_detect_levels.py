"""Plan 0051 phase 3: the `detect_levels` MCP tool.

`detect_levels` computes clustered, volume-weighted support/resistance levels
over cached bars and publishes ONE `chart.show v1` event carrying one
`price_line` overlay per level (role + ranked `S1`/`R1` labels) — compute and
draw in a single call. It is *derived* data: nothing is persisted (the tool
takes no repository at all).

These exercise the factored `_detect_levels_response` body directly on a single
event loop with a real `EventBus` and a stub provider — no live MCP server.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.api.mcp_tools.detect_levels import (
    _detect_levels_response,
    register_detect_levels,
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
_END = datetime(2026, 6, 30, tzinfo=UTC)
_TOL = 1e-9


def _bar(i: int, *, h: float, low: float, v: float = 1000.0) -> Bar:
    mid = (h + low) / 2.0
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=_START + timedelta(days=i),
        open=mid,
        high=h,
        low=low,
        close=mid,
        volume=v,
        source="test",
    )


def _three_zone_bars() -> list[Bar]:
    """Flat 108..110 band with three single-pivot zones of very different
    volume mass: a heavy support at 100 (100k), a resistance spike at 130
    (10k), and a thin support at 90 (100). Strength order is therefore
    S@100 > R@130 > S@90 — expected labels S1, R1, S2."""

    bars: list[Bar] = []
    for i in range(30):
        if i == 5:
            bars.append(_bar(i, h=102.0, low=100.0, v=100_000.0))  # heavy support pivot
        elif i == 12:
            bars.append(_bar(i, h=130.0, low=128.0, v=10_000.0))  # resistance pivot
        elif i == 19:
            bars.append(_bar(i, h=92.0, low=90.0, v=100.0))  # thin support pivot
        else:
            bars.append(_bar(i, h=110.0, low=108.0))
    return bars


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


def _drain(sub: object) -> list[Envelope]:
    items: list[Envelope] = []
    queue = sub.queue  # type: ignore[attr-defined]
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def _run_detect(
    bus: EventBus,
    provider: _StubProvider,
    *,
    max_levels: int = 5,
) -> dict[str, object]:
    return asyncio.run(
        _detect_levels_response(
            provider=provider,
            event_bus=bus,
            symbol="AAPL",
            timeframe="1d",
            range_start=_START,
            range_end=_END,
            max_levels=max_levels,
        )
    )


def test_detect_levels_returns_ranked_levels_and_publishes_one_chart_show() -> None:
    """On the seeded fixture: the ranked Level list comes back as data AND
    exactly one `chart.show v1` event lands on the bus, its overlays all
    `price_line`s with the expected price/role/label."""

    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(bus, _StubProvider(_three_zone_bars()))

    # --- returned data: ranked levels --------------------------------------- #
    assert ack["event_published"] is True
    assert ack["type"] == "chart.show"
    levels = ack["levels"]
    assert isinstance(levels, list)
    assert ack["count"] == len(levels) == 3
    assert [(lv["role"], lv["price"]) for lv in levels] == [
        ("support", 100.0),
        ("resistance", 130.0),
        ("support", 90.0),
    ]
    strengths = [lv["strength"] for lv in levels]
    assert strengths == sorted(strengths, reverse=True)  # strength-ranked
    assert strengths[0] == 1.0  # the heavy zone tops the blend
    assert all(lv["touches"] == 1 for lv in levels)
    # Volume weighting did the ranking: equal touches, decreasing volume mass.
    assert levels[0]["volume_at_level"] > levels[1]["volume_at_level"]
    assert levels[1]["volume_at_level"] > levels[2]["volume_at_level"]

    # --- the bus: exactly one chart.show, all price_line overlays ----------- #
    events = _drain(sub)
    assert len(events) == 1
    env = events[0]
    assert env.type == "chart.show"
    assert env.payload["symbol"] == "AAPL"
    assert env.payload["timeframe"] == "1d"
    overlays = env.payload["overlays"]
    assert isinstance(overlays, list)
    assert all(o["kind"] == "price_line" for o in overlays)
    assert [(o["label"], o["role"], o["price"]) for o in overlays] == [
        ("S1", "support", 100.0),
        ("R1", "resistance", 130.0),
        ("S2", "support", 90.0),
    ]


def test_detect_levels_max_levels_caps_per_role() -> None:
    """max_levels=1 keeps only the strongest level per role: the thin support
    at 90 is dropped from both the data and the overlays."""

    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(bus, _StubProvider(_three_zone_bars()), max_levels=1)

    levels = ack["levels"]
    assert isinstance(levels, list)
    assert [(lv["role"], lv["price"]) for lv in levels] == [
        ("support", 100.0),
        ("resistance", 130.0),
    ]
    env = _drain(sub)[0]
    overlays = env.payload["overlays"]
    assert [(o["label"], o["price"]) for o in overlays] == [("S1", 100.0), ("R1", 130.0)]


def test_detect_levels_empty_cache_publishes_nothing() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(bus, _StubProvider([]))

    assert ack["event_published"] is False
    assert ack["count"] == 0
    assert ack["levels"] == []
    assert _drain(sub) == []


def test_detect_levels_pivotless_bars_publish_nothing() -> None:
    """Cached bars without a single confirmed pivot (flat band) yield no levels
    and no event — not a degenerate all-bars level."""

    bus = EventBus()
    sub = bus.subscribe()
    flat = [_bar(i, h=110.0, low=108.0) for i in range(20)]
    ack = _run_detect(bus, _StubProvider(flat))

    assert ack["event_published"] is False
    assert ack["levels"] == []
    assert _drain(sub) == []


def test_detect_levels_rejects_bad_inputs() -> None:
    bus = EventBus()
    provider = _StubProvider(_three_zone_bars())
    with pytest.raises(ValueError):
        _run_detect(bus, provider, max_levels=0)
    with pytest.raises(ValueError):
        asyncio.run(
            _detect_levels_response(
                provider=provider,
                event_bus=bus,
                symbol="",
                timeframe="1d",
                range_start=_START,
                range_end=_END,
                max_levels=5,
            )
        )
    with pytest.raises(ValueError):
        asyncio.run(
            _detect_levels_response(
                provider=provider,
                event_bus=bus,
                symbol="AAPL",
                timeframe="1d",
                range_start=_END,
                range_end=_START,  # reversed
                max_levels=5,
            )
        )


def test_detect_levels_takes_no_annotations_repository() -> None:
    """Levels are derived, not persisted: the tool's registration depends only
    on the provider and event bus — never an annotations repository."""

    params = set(inspect.signature(register_detect_levels).parameters)
    assert "annotations_repository" not in params
    assert {"provider", "event_bus"} <= params
