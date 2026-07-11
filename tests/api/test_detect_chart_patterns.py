"""Plan 0052 phase 2: the `detect_chart_patterns` MCP tool.

`detect_chart_patterns` runs classical-pattern detection over cached bars and
publishes ONE `chart.trendlines v1` event carrying one `TrendlineSpec` per hit
line (anchored on the real pivot endpoints, `dashed` for forming / `solid` for
confirmed) — detect and draw in a single call. The event is layer-only and
active-chart-gated (ADR-0059, Plan 0064): it draws onto the chart already
showing that symbol/timeframe, never mounting one, so no `chart.show` race can
wipe the lines. It is *derived* data: nothing is persisted (the tool takes no
repository at all).

These exercise the factored `_detect_chart_patterns_response` body directly on
a single event loop with a real `EventBus` and a stub provider — no live MCP
server. The fixture is the phase-1 H&S path: forming hit at bar 25, confirmed
at bar 27, one neckline through (bar 10, 99.0)-(bar 18, 100.0).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from market_analyser.api.mcp_tools._shared.chart_patterns_response import (
    _detect_chart_patterns_response,
)
from market_analyser.api.mcp_tools.detect_chart_patterns import register_detect_chart_patterns
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

_START = datetime(2025, 1, 1, tzinfo=UTC)
_RANGE_END = datetime(2025, 3, 1, tzinfo=UTC)

# The phase-1 head & shoulders fixture: shoulders 111 @6 / 111.5 @22, head
# 121 @14, neckline troughs 99 @10 / 100 @18, then a decline through the
# neckline. Forming completes at bar 25 (right shoulder + PIVOT_RIGHT);
# the k*ATR break confirms at bar 27.
_HS_ANCHORS = [
    (0, 100.0),
    (6, 110.0),
    (10, 100.0),
    (14, 120.0),
    (18, 101.0),
    (22, 110.5),
    (35, 78.0),
]


# A symmetrical triangle (falling highs, rising lows) that breaks out upward —
# used to exercise the confirmed-hit measured-move projection (Plan 0083 ph2).
_SYM_TRIANGLE_ANCHORS = [
    (0, 110.0),
    (6, 120.0),
    (10, 100.0),
    (14, 116.0),
    (18, 104.0),
    (24, 110.0),
    (29, 126.0),
]

# A double bottom (two troughs ~99 / 99.5, peak 116) breaking out upward — for the
# ph8 neckline + base + projection publish test.
_DOUBLE_BOTTOM_ANCHORS = [(0, 120.0), (6, 100.0), (12, 115.0), (18, 100.5), (33, 135.0)]


def _bars_from_path(anchors: list[tuple[int, float]]) -> list[Bar]:
    """Sample a piecewise-linear base path into bars (high/low straddle the
    base by 1.0) — the same construction as the phase-1 detector tests."""

    n = anchors[-1][0] + 1
    bases: list[float] = []
    for i in range(n):
        for (x1, p1), (x2, p2) in pairwise(anchors):
            if x1 <= i <= x2:
                bases.append(p1 + (p2 - p1) * (i - x1) / (x2 - x1))
                break
    return [
        Bar(
            symbol="AAPL",
            timeframe="1d",
            event_ts=_START + timedelta(days=i),
            open=base,
            high=base + 1.0,
            low=base - 1.0,
            close=base,
            volume=1000.0,
            source="test",
        )
        for i, base in enumerate(bases)
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
    patterns: list[str] | None = None,
    states: list[str] | None = None,
) -> dict[str, object]:
    return asyncio.run(
        _detect_chart_patterns_response(
            provider=provider,
            event_bus=bus,
            symbol="AAPL",
            timeframe="1d",
            range_start=_START,
            range_end=_RANGE_END,
            patterns=patterns,
            states=states,
        )
    )


def _iso(bars: list[Bar], b: int) -> str:
    """The published ISO timestamp of bar `b` (Zulu, matching the bus dump)."""
    return bars[b].event_ts.isoformat().replace("+00:00", "Z")


def test_detect_chart_patterns_returns_hits_and_publishes_one_chart_trendlines() -> None:
    """On the seeded H&S fixture: the typed hit list comes back as data AND
    exactly one `chart.trendlines v1` event lands on the bus. Its trendlines
    carry, per hit, the neckline through the two troughs and a `skeleton`
    polyline through the five pivots (LS→t1→head→t2→RS), plus — on the confirmed
    hit only — a vertical `projection` to the measured-move target. `style`
    matches each hit's state (dashed=forming, solid=confirmed); the layer-only
    payload carries symbol/timeframe/trendlines and NO range fields."""

    bus = EventBus()
    sub = bus.subscribe()
    bars = _bars_from_path(_HS_ANCHORS)
    ack = _run_detect(bus, _StubProvider(bars))

    # --- returned data: the forming + confirmed hits ------------------------- #
    assert ack["event_published"] is True
    assert ack["type"] == "chart.trendlines"
    hits = ack["hits"]
    assert isinstance(hits, list)
    assert ack["count"] == len(hits) == 2
    assert [(h["pattern"], h["state"], h["bar_index"]) for h in hits] == [
        ("head_shoulders", "forming", 25),
        ("head_shoulders", "confirmed", 27),
    ]
    assert all(h["direction"] == "bearish" for h in hits)
    assert all("action" not in h and "buy" not in h and "sell" not in h for h in hits)

    # --- the bus: exactly one chart.trendlines, grouped by role -------------- #
    events = _drain(sub)
    assert len(events) == 1
    env = events[0]
    assert env.type == "chart.trendlines"
    assert env.payload["symbol"] == "AAPL"
    assert env.payload["timeframe"] == "1d"
    assert "range_start" not in env.payload and "range_end" not in env.payload
    trendlines = env.payload["trendlines"]
    assert isinstance(trendlines, list)
    by_role: dict[str, list[Any]] = {}
    for t in trendlines:
        assert t["pattern"] == "head_shoulders"
        by_role.setdefault(t["role"], []).append(t)
    assert set(by_role) == {"neckline", "skeleton", "projection"}

    # Neckline: one per state, through the two troughs.
    assert [t["style"] for t in by_role["neckline"]] == ["dashed", "solid"]
    for spec in by_role["neckline"]:
        assert [(p["ts"], p["price"]) for p in spec["points"]] == [
            (_iso(bars, 10), 99.0),
            (_iso(bars, 18), 100.0),
        ]

    # Skeleton: one per state, LS→t1→head→t2→RS through the five pivots.
    expected_skeleton = [
        (_iso(bars, 6), 111.0),
        (_iso(bars, 10), 99.0),
        (_iso(bars, 14), 121.0),
        (_iso(bars, 18), 100.0),
        (_iso(bars, 22), 111.5),
    ]
    assert [t["style"] for t in by_role["skeleton"]] == ["dashed", "solid"]
    for spec in by_role["skeleton"]:
        assert [(p["ts"], p["price"]) for p in spec["points"]] == expected_skeleton

    # Projection: confirmed only, vertical, ending below the break (bearish).
    assert len(by_role["projection"]) == 1
    proj = by_role["projection"][0]
    assert proj["style"] == "solid"
    assert proj["points"][0]["ts"] == proj["points"][1]["ts"]  # vertical
    assert proj["points"][1]["price"] < proj["points"][0]["price"]  # downward
    assert {t["label"] for t in trendlines} == {
        "head_shoulders (forming)",
        "head_shoulders (confirmed)",
    }


def test_detect_chart_patterns_publishes_projection_on_confirmed_trendline() -> None:
    """Plan 0083 ph2: a confirmed trendline pattern publishes a `projection`
    TrendlineSpec (the vertical measured-move) beside its two bounding lines,
    while the forming hit publishes only the two boundaries. The projection is
    solid (confirmed), pattern-tagged, and its two points share a timestamp."""

    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(
        bus,
        _StubProvider(_bars_from_path(_SYM_TRIANGLE_ANCHORS)),
        patterns=["symmetrical_triangle"],
    )
    assert ack["event_published"] is True

    trendlines = _drain(sub)[0].payload["trendlines"]
    roles = [t["role"] for t in trendlines]
    # forming -> upper + lower; confirmed -> upper + lower + projection.
    assert roles.count("projection") == 1
    assert roles.count("upper_trendline") == 2
    assert roles.count("lower_trendline") == 2

    proj = next(t for t in trendlines if t["role"] == "projection")
    assert proj["style"] == "solid"  # confirmed hits only
    assert proj["pattern"] == "symmetrical_triangle"
    pts = proj["points"]
    assert len(pts) == 2
    assert pts[0]["ts"] == pts[1]["ts"]  # vertical: shared timestamp
    assert pts[0]["price"] != pts[1]["price"]


def test_detect_chart_patterns_double_publishes_neckline_base_projection() -> None:
    """Plan 0083 ph8: a confirmed double bottom publishes a neckline + a `base`
    horizontal through the two troughs + an upward `projection`; the forming hit
    publishes neckline + base but no projection. No skeleton/fill for a double."""

    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(
        bus,
        _StubProvider(_bars_from_path(_DOUBLE_BOTTOM_ANCHORS)),
        patterns=["double_bottom"],
    )
    assert ack["event_published"] is True

    trendlines = _drain(sub)[0].payload["trendlines"]
    roles = [t["role"] for t in trendlines]
    assert roles.count("neckline") == 2  # forming + confirmed
    assert roles.count("base") == 2
    assert roles.count("projection") == 1  # confirmed only
    assert "skeleton" not in roles

    proj = next(t for t in trendlines if t["role"] == "projection")
    assert proj["style"] == "solid"
    pts = proj["points"]
    assert pts[0]["ts"] == pts[1]["ts"]  # vertical
    assert pts[1]["price"] > pts[0]["price"]  # upward (bottom)


def test_detect_chart_patterns_states_filter_narrows_hits_and_event() -> None:
    """states=["confirmed"] drops the forming hit from both the data and the
    published trendlines."""

    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(bus, _StubProvider(_bars_from_path(_HS_ANCHORS)), states=["confirmed"])

    hits = ack["hits"]
    assert isinstance(hits, list)
    assert [(h["state"], h["bar_index"]) for h in hits] == [("confirmed", 27)]
    trendlines = _drain(sub)[0].payload["trendlines"]
    # The forming hit is dropped: only the confirmed hit's solid specs remain —
    # neckline + skeleton + projection.
    assert all(t["style"] == "solid" for t in trendlines)
    assert {t["role"] for t in trendlines} == {"neckline", "skeleton", "projection"}


def test_detect_chart_patterns_pattern_filter_can_empty_the_result() -> None:
    """patterns=["double_top"] on the H&S fixture matches nothing: no hits, no
    event published."""

    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(bus, _StubProvider(_bars_from_path(_HS_ANCHORS)), patterns=["double_top"])

    assert ack["event_published"] is False
    assert ack["count"] == 0
    assert ack["hits"] == []
    assert _drain(sub) == []


def test_detect_chart_patterns_empty_cache_publishes_nothing() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    ack = _run_detect(bus, _StubProvider([]))

    assert ack["event_published"] is False
    assert ack["count"] == 0
    assert ack["hits"] == []
    assert _drain(sub) == []


def test_detect_chart_patterns_rejects_bad_inputs() -> None:
    bus = EventBus()
    provider = _StubProvider(_bars_from_path(_HS_ANCHORS))
    with pytest.raises(ValueError, match="unknown patterns"):
        _run_detect(bus, provider, patterns=["cup_and_handle"])
    with pytest.raises(ValueError, match="unknown states"):
        _run_detect(bus, provider, states=["pending"])
    with pytest.raises(ValueError):
        asyncio.run(
            _detect_chart_patterns_response(
                provider=provider,
                event_bus=bus,
                symbol="",
                timeframe="1d",
                range_start=_START,
                range_end=_RANGE_END,
            )
        )
    with pytest.raises(ValueError):
        asyncio.run(
            _detect_chart_patterns_response(
                provider=provider,
                event_bus=bus,
                symbol="AAPL",
                timeframe="1d",
                range_start=_RANGE_END,
                range_end=_START,  # reversed
            )
        )


def test_detect_chart_patterns_takes_no_repository() -> None:
    """Hits are derived, not persisted: the tool's registration depends only on
    the provider and event bus — never a repository."""

    params = set(inspect.signature(register_detect_chart_patterns).parameters)
    assert "annotations_repository" not in params
    assert {"provider", "event_bus"} <= params
