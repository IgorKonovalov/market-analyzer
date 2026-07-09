"""Plan 0064 phase 3 done-when: POST /scan_chart_patterns (the UI trendline sweep).

Hermetic — a stub provider supplies cached bars. Covers: 200 + ack with the
renderer bearer and a single `chart.trendlines` event whose trendlines match the
`detect_chart_patterns` MCP tool's for the same inputs (both run the shared
`_detect_chart_patterns_response` core); 401 without the bearer; an empty range →
{published:false, count:0} (not an error); unknown symbol → 404; an unsupported
timeframe → 422 (never a 500).

Fixture: the head & shoulders path from `test_detect_chart_patterns` — a forming
hit at bar 25 and a confirmed hit at bar 27, each drawing one neckline, so a
sweep publishes two trendlines.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_tools._shared.chart_patterns_response import (
    _detect_chart_patterns_response,
)
from market_analyser.data.errors import UnknownSymbolError
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

RENDERER_SECRET = "renderer-test-secret"

_START = "2025-01-01T00:00:00+00:00"
_END = "2025-03-01T00:00:00+00:00"
_START_DT = datetime(2025, 1, 1, tzinfo=UTC)
_END_DT = datetime(2025, 3, 1, tzinfo=UTC)

# The head & shoulders fixture (shared with test_detect_chart_patterns): a
# forming hit at bar 25 and a confirmed hit at bar 27, one neckline each.
_HS_ANCHORS = [
    (0, 100.0),
    (6, 110.0),
    (10, 100.0),
    (14, 120.0),
    (18, 101.0),
    (22, 110.5),
    (35, 78.0),
]


def _bars_from_path(anchors: list[tuple[int, float]]) -> list[Bar]:
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
            event_ts=_START_DT + timedelta(days=i),
            open=base,
            high=base + 1.0,
            low=base - 1.0,
            close=base,
            volume=1000.0,
            source="test",
        )
        for i, base in enumerate(bases)
    ]


class _BarsProvider:
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


def _renderer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "AAPL",
        "timeframe": "1d",
        "range_start": _START,
        "range_end": _END,
    }
    body.update(overrides)
    return body


def _drain(sub: object) -> list[Envelope]:
    items: list[Envelope] = []
    queue = sub.queue  # type: ignore[attr-defined]
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def test_scan_chart_patterns_publishes_event_and_returns_ack() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    app = create_app(
        secret=RENDERER_SECRET, provider=_BarsProvider(_bars_from_path(_HS_ANCHORS)), event_bus=bus
    )

    response = TestClient(app).post("/scan_chart_patterns", json=_body(), headers=_renderer_auth())

    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack == {"published": True, "count": 2}  # forming + confirmed H&S

    events = _drain(sub)
    assert len(events) == 1
    env = events[0]
    assert env.type == "chart.trendlines"
    assert env.payload["symbol"] == "AAPL"
    assert env.payload["timeframe"] == "1d"
    trendlines = env.payload["trendlines"]
    assert len(trendlines) == 2  # one neckline per hit
    assert [t["style"] for t in trendlines] == ["dashed", "solid"]
    assert all(t["role"] == "neckline" for t in trendlines)


def test_route_trendlines_identical_to_mcp_tool() -> None:
    """The shared-core guarantee: the HTTP recompute trigger and the MCP tool emit
    identical trendlines for identical inputs (both call the same core)."""
    bars = _bars_from_path(_HS_ANCHORS)

    mcp_bus = EventBus()
    mcp_sub = mcp_bus.subscribe()
    asyncio.run(
        _detect_chart_patterns_response(
            provider=_BarsProvider(bars),
            event_bus=mcp_bus,
            symbol="AAPL",
            timeframe="1d",
            range_start=_START_DT,
            range_end=_END_DT,
        )
    )
    mcp_trendlines = _drain(mcp_sub)[0].payload["trendlines"]

    http_bus = EventBus()
    http_sub = http_bus.subscribe()
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(bars), event_bus=http_bus)
    TestClient(app).post("/scan_chart_patterns", json=_body(), headers=_renderer_auth())
    http_trendlines = _drain(http_sub)[0].payload["trendlines"]

    assert http_trendlines == mcp_trendlines


def test_scan_chart_patterns_empty_range_publishes_nothing() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider([]), event_bus=bus)

    response = TestClient(app).post("/scan_chart_patterns", json=_body(), headers=_renderer_auth())

    assert response.status_code == 200, response.text
    assert response.json() == {"published": False, "count": 0}
    assert _drain(sub) == []


def test_scan_chart_patterns_returns_401_without_auth() -> None:
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(_bars_from_path(_HS_ANCHORS)))
    response = TestClient(app).post("/scan_chart_patterns", json=_body())
    assert response.status_code == 401


def test_scan_chart_patterns_unknown_symbol_returns_404() -> None:
    class UnknownProvider(_BarsProvider):
        def get_ohlcv(
            self,
            symbol: str,
            timeframe: str,
            start: datetime,
            end: datetime,
            as_of: datetime | None = None,
        ) -> Sequence[Bar]:
            raise UnknownSymbolError("yahoo: no such symbol", symbol=symbol)

    app = create_app(secret=RENDERER_SECRET, provider=UnknownProvider([]))
    response = TestClient(app).post(
        "/scan_chart_patterns", json=_body(symbol="NOPE"), headers=_renderer_auth()
    )
    assert response.status_code == 404


def test_scan_chart_patterns_unsupported_timeframe_returns_422() -> None:
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(_bars_from_path(_HS_ANCHORS)))
    response = TestClient(app).post(
        "/scan_chart_patterns", json=_body(timeframe="5m"), headers=_renderer_auth()
    )
    assert response.status_code == 422
