"""Plan 0049 phase 4 done-when: POST /scan_patterns (the UI sweep trigger).

Hermetic — a stub provider supplies cached bars. Covers: 200 + ack with the
renderer bearer and a single `chart.highlight` event whose markers are IDENTICAL
to the `scan_patterns` MCP tool's for the same inputs (the shared-mapper
guarantee); 401 without the bearer; cross-tenant MCP bearer → 401; an empty range
→ {published:false, count:0} (not an error); unknown symbol → 404; upstream
failure → 502; an unsupported timeframe / reversed range → 422 (never a 500).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_tools.scan_patterns import _scan_patterns_response
from market_analyser.data.errors import UnknownSymbolError, UpstreamUnavailableError
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
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
MCP_SECRET = "mcp-test-secret"

_START = "2026-05-01T00:00:00+00:00"
_END = "2026-05-31T00:00:00+00:00"


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
    """Declining series whose last bar is BOTH a doji and a hammer — two distinct
    patterns on the same bar (one neutral, one bullish)."""
    return [
        _bar(11, o=111.0, h=112.0, low=109.5, c=110.0),
        _bar(12, o=110.0, h=111.0, low=108.5, c=109.0),
        _bar(13, o=109.0, h=110.0, low=106.5, c=107.0),
        _bar(14, o=107.0, h=108.0, low=104.5, c=105.0),
        _bar(15, o=108.4, h=108.8, low=100.0, c=108.0),
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


def test_scan_patterns_publishes_event_and_returns_ack() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    app = create_app(
        secret=RENDERER_SECRET, provider=_BarsProvider(_doji_and_hammer_bars()), event_bus=bus
    )

    response = TestClient(app).post("/scan_patterns", json=_body(), headers=_renderer_auth())

    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["published"] is True
    assert ack["count"] == 2  # doji + hammer

    events = _drain(sub)
    assert len(events) == 1
    env = events[0]
    assert env.type == "chart.highlight"
    patterns = {(m["pattern"], m["kind"]) for m in env.payload["markers"]}
    assert ("doji", "neutral_marker") in patterns
    assert ("hammer", "bullish_marker") in patterns


def test_route_markers_identical_to_mcp_tool() -> None:
    """The shared-mapper guarantee: the HTTP trigger and the MCP trigger emit
    byte-identical markers for identical inputs."""
    bars = _doji_and_hammer_bars()

    # MCP path.
    mcp_bus = EventBus()
    mcp_sub = mcp_bus.subscribe()
    asyncio.run(
        _scan_patterns_response(
            provider=_BarsProvider(bars),
            event_bus=mcp_bus,
            symbol="AAPL",
            timeframe="1d",
            range_start=datetime(2026, 5, 1, tzinfo=UTC),
            range_end=datetime(2026, 5, 31, tzinfo=UTC),
            patterns=None,
            min_strength=None,
        )
    )
    mcp_markers = _drain(mcp_sub)[0].payload["markers"]

    # HTTP path.
    http_bus = EventBus()
    http_sub = http_bus.subscribe()
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(bars), event_bus=http_bus)
    TestClient(app).post("/scan_patterns", json=_body(), headers=_renderer_auth())
    http_markers = _drain(http_sub)[0].payload["markers"]

    assert http_markers == mcp_markers


def test_scan_patterns_empty_range_publishes_nothing() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider([]), event_bus=bus)

    response = TestClient(app).post("/scan_patterns", json=_body(), headers=_renderer_auth())

    assert response.status_code == 200, response.text
    assert response.json() == {"published": False, "count": 0}
    assert _drain(sub) == []


def test_scan_patterns_returns_401_without_auth() -> None:
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(_doji_and_hammer_bars()))
    response = TestClient(app).post("/scan_patterns", json=_body())
    assert response.status_code == 401


def test_scan_patterns_unknown_symbol_returns_404() -> None:
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
        "/scan_patterns", json=_body(symbol="NOPE"), headers=_renderer_auth()
    )
    assert response.status_code == 404


def test_scan_patterns_upstream_error_returns_502() -> None:
    class DownProvider(_BarsProvider):
        def get_ohlcv(
            self,
            symbol: str,
            timeframe: str,
            start: datetime,
            end: datetime,
            as_of: datetime | None = None,
        ) -> Sequence[Bar]:
            raise UpstreamUnavailableError("yahoo: upstream unavailable")

    app = create_app(secret=RENDERER_SECRET, provider=DownProvider([]))
    response = TestClient(app).post("/scan_patterns", json=_body(), headers=_renderer_auth())
    assert response.status_code == 502


def test_scan_patterns_unsupported_timeframe_returns_422() -> None:
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(_doji_and_hammer_bars()))
    response = TestClient(app).post(
        "/scan_patterns", json=_body(timeframe="5m"), headers=_renderer_auth()
    )
    assert response.status_code == 422


def test_scan_patterns_reversed_range_returns_422() -> None:
    app = create_app(secret=RENDERER_SECRET, provider=_BarsProvider(_doji_and_hammer_bars()))
    response = TestClient(app).post(
        "/scan_patterns",
        json=_body(range_start=_END, range_end=_START),
        headers=_renderer_auth(),
    )
    assert response.status_code == 422


def test_scan_patterns_with_mcp_bearer_returns_401() -> None:
    """Cross-tenant escalation blocked: the MCP bearer must not authenticate the
    renderer route (the agent uses the scan_patterns MCP tool instead)."""

    repo = _annotations_repo()
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        provider=_BarsProvider(_doji_and_hammer_bars()),
        annotations_repository=repo,
    )
    with TestClient(app) as client:
        response = client.post(
            "/scan_patterns",
            json=_body(),
            headers={"Authorization": f"Bearer {MCP_SECRET}"},
        )
    assert response.status_code == 401


def _annotations_repo() -> AnnotationsRepository:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    return AnnotationsRepository(make_session_factory(engine))
