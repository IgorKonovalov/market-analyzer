"""Plan 0056 phase 4 — the `derivatives_snapshot` MCP tool.

Done-when claims pinned here:
(a) the snapshot computes correct funding/OI values and deltas from a seeded
    store with NO network calls — a spy adapter records every call and the
    default path makes none;
(b) warm-up gaps yield `None`, never zero — empty series, a lone funding
    print (no cadence), a lone OI point (no delta anchors);
(c) the full-toolset registration grows `derivatives_snapshot` when the
    metric store is wired — and omits it without one.

Also pinned: funding cadence is read from the stored points' actual spacing
(a 4h-spaced fixture predicts a 4h-ahead next funding, not 8h); `refresh=true`
is the only path that touches the source (incremental funding fetch + one OI
accrual); and the trailing-only property (a future-timestamped point never
appears).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, cast

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ListToolsRequest, ListToolsResult
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.mcp_tools.derivatives_snapshot import (
    _build_snapshot,
    register_derivatives_snapshot,
)
from market_analyser.api.ui_events.buffer import UIEventBuffer
from market_analyser.data.metric_series import (
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
    MetricPoint,
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
from market_analyser.events import EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_HOUR = 3_600
_DAY = 86_400

# Pinned snapshot instant (hour-aligned for easy bucket arithmetic).
_NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
_NOW_TS = int(_NOW.timestamp())

_FUNDING_ID = SERIES_BINANCE_FUNDING_RATE_BTCUSDT
_OI_ID = SERIES_BINANCE_OPEN_INTEREST_BTCUSDT


class _SpySource:
    """`DerivativesSource` conformer that records every call. `fetch_series`
    serves a canned funding list (clipped to `start`); `accrue_open_interest`
    writes one canned OI point through the store, mimicking the adapter's
    write-through."""

    def __init__(
        self,
        store: MetricPointsRepository | None = None,
        funding_points: Sequence[MetricPoint] = (),
        oi_point: MetricPoint | None = None,
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._store = store
        self._funding_points = list(funding_points)
        self._oi_point = oi_point

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> Sequence[MetricPoint]:
        self.calls.append(("fetch_series", series_id, start, end))
        return [p for p in self._funding_points if start is None or p.ts >= start]

    def accrue_open_interest(self, series_id: str) -> int:
        self.calls.append(("accrue_open_interest", series_id))
        if self._store is not None and self._oi_point is not None:
            return self._store.upsert_points([self._oi_point])
        return 0


class _UnusedProvider:
    """Minimal `MarketDataProvider` conformer for toolset assembly — the
    derivatives tool never touches the provider, so every method refuses."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise NotImplementedError

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: str = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample:
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

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> MetricPointsRepository:
    return MetricPointsRepository(session_factory)


def _seed_funding(store: MetricPointsRepository, *, spacing: int = 8 * _HOUR) -> list[MetricPoint]:
    """Funding prints at `spacing` cadence covering the trailing ~8 days, the
    latest 4 hours before _NOW. Values are a deterministic ramp."""
    latest_ts = _NOW_TS - 4 * _HOUR
    count = (8 * _DAY) // spacing
    points = [
        MetricPoint(
            series_id=_FUNDING_ID,
            ts=latest_ts - i * spacing,
            value=0.0001 + i * 0.00001,
        )
        for i in range(count)
    ]
    points.reverse()
    store.upsert_points(points)
    return points


def _seed_oi(store: MetricPointsRepository) -> None:
    """Hourly OI points covering the trailing 8 days, latest 2h before _NOW,
    with value = 20000 + hours-since-window-start."""
    base = _NOW_TS - 8 * _DAY
    points = [
        MetricPoint(series_id=_OI_ID, ts=base + i * _HOUR, value=20_000.0 + i)
        for i in range(8 * 24 - 1)  # latest lands at _NOW_TS - 2h
    ]
    store.upsert_points(points)


# --- (a) correct values/deltas from a seeded store, zero network calls -----------


def test_snapshot_computes_funding_and_oi_with_no_source_calls(
    store: MetricPointsRepository,
) -> None:
    funding = _seed_funding(store)
    _seed_oi(store)

    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    latest = funding[-1]
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.as_of == _NOW
    assert snapshot.funding_rate == latest.value
    # Cadence from the data: the two latest prints are 8h apart.
    assert snapshot.next_funding_ts == latest.ts + 8 * _HOUR
    in_window = [p.value for p in funding if p.ts >= _NOW_TS - 7 * _DAY]
    assert snapshot.funding_mean_7d == sum(in_window) / len(in_window)
    # OI: latest is at _NOW_TS - 2h with value 20000 + 190; the 24h/7d anchors
    # exist exactly 24 and 168 hourly steps earlier, so the deltas are the
    # hour-per-unit ramp.
    assert snapshot.open_interest == 20_000.0 + 8 * 24 - 2
    assert snapshot.oi_delta_24h == 24.0
    assert snapshot.oi_delta_7d == 168.0


def test_default_path_never_touches_the_source(store: MetricPointsRepository) -> None:
    """The registered tool with refresh unset: the spy source records zero
    calls — offline by default is the contract, not an accident."""
    _seed_funding(store)
    _seed_oi(store)
    spy = _SpySource()
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_derivatives_snapshot(server, metric_points_repository=store, derivatives_source=spy)

    result = anyio.run(server.call_tool, "derivatives_snapshot", {"params": {"symbol": "BTCUSDT"}})
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)

    assert spy.calls == []  # NO network path was exercised
    assert structured["symbol"] == "BTCUSDT"
    assert structured["funding_rate"] is not None
    assert structured["open_interest"] is not None


def test_funding_cadence_is_read_from_the_data_not_hardcoded(
    store: MetricPointsRepository,
) -> None:
    funding = _seed_funding(store, spacing=4 * _HOUR)

    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    # 4h-spaced prints predict a 4h-ahead next funding — an 8h assumption
    # would land one print late.
    assert snapshot.next_funding_ts == funding[-1].ts + 4 * _HOUR


# --- (b) warm-up gaps yield None, never zero --------------------------------------


def test_empty_store_yields_all_nones(store: MetricPointsRepository) -> None:
    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    assert snapshot.funding_rate is None
    assert snapshot.next_funding_ts is None
    assert snapshot.funding_mean_7d is None
    assert snapshot.open_interest is None
    assert snapshot.oi_delta_24h is None
    assert snapshot.oi_delta_7d is None
    # None means "insufficient history" — zero would be a fabricated reading.
    assert snapshot.funding_rate != 0.0
    assert snapshot.oi_delta_24h != 0.0


def test_lone_funding_print_has_value_but_no_cadence(store: MetricPointsRepository) -> None:
    store.upsert_points(
        [MetricPoint(series_id=_FUNDING_ID, ts=_NOW_TS - 4 * _HOUR, value=0.0003)],
    )

    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    assert snapshot.funding_rate == 0.0003
    assert snapshot.next_funding_ts is None  # one print cannot reveal the spacing
    assert snapshot.funding_mean_7d == 0.0003  # a real mean over the one stored print


def test_lone_oi_point_has_value_but_no_deltas(store: MetricPointsRepository) -> None:
    store.upsert_points(
        [MetricPoint(series_id=_OI_ID, ts=_NOW_TS - 2 * _HOUR, value=21_500.0)],
    )

    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    assert snapshot.open_interest == 21_500.0
    assert snapshot.oi_delta_24h is None  # no anchor 24h earlier — None, not 0.0
    assert snapshot.oi_delta_7d is None


def test_oi_warmed_for_24h_but_not_7d(store: MetricPointsRepository) -> None:
    """Two days of accrual: the 24h delta computes, the 7d delta is still an
    honest None."""
    base = _NOW_TS - 2 * _DAY
    store.upsert_points(
        [
            MetricPoint(series_id=_OI_ID, ts=base + i * _HOUR, value=20_000.0 + i)
            for i in range(2 * 24)
        ],
    )

    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    assert snapshot.oi_delta_24h == 24.0
    assert snapshot.oi_delta_7d is None


# --- trailing-only: a future-timestamped point never appears ----------------------


def test_injected_future_points_never_appear(store: MetricPointsRepository) -> None:
    store.upsert_points(
        [
            MetricPoint(series_id=_FUNDING_ID, ts=_NOW_TS - 4 * _HOUR, value=0.0001),
            MetricPoint(series_id=_FUNDING_ID, ts=_NOW_TS + 1, value=0.0099),
            MetricPoint(series_id=_OI_ID, ts=_NOW_TS - _HOUR, value=20_000.0),
            MetricPoint(series_id=_OI_ID, ts=_NOW_TS + _HOUR, value=99_999.0),
        ],
    )

    snapshot = _build_snapshot(store, "BTCUSDT", _NOW)

    assert snapshot.funding_rate == 0.0001  # not 0.0099
    assert snapshot.open_interest == 20_000.0  # not 99999.0


# --- refresh=true is the one network path ------------------------------------------


def test_refresh_fetches_funding_incrementally_and_accrues_oi(
    store: MetricPointsRepository,
) -> None:
    funding = _seed_funding(store)
    latest_stored = funding[-1]
    new_print = MetricPoint(series_id=_FUNDING_ID, ts=latest_stored.ts + 8 * _HOUR, value=0.0007)
    now_ts = int(datetime.now(tz=UTC).timestamp())
    oi_sample = MetricPoint(series_id=_OI_ID, ts=now_ts // _HOUR * _HOUR - _HOUR, value=22_222.0)
    spy = _SpySource(
        store=store,
        funding_points=[latest_stored, new_print],  # upstream re-serves the latest print
        oi_point=oi_sample,
    )
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_derivatives_snapshot(server, metric_points_repository=store, derivatives_source=spy)

    result = anyio.run(
        server.call_tool,
        "derivatives_snapshot",
        {"params": {"symbol": "BTCUSDT", "refresh": True}},
    )
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)

    # Incremental: the fetch starts at the latest stored print, not at None
    # (which would mean a full re-backfill on every refresh).
    assert spy.calls == [
        ("fetch_series", _FUNDING_ID, latest_stored.ts, None),
        ("accrue_open_interest", _OI_ID),
    ]
    # The refreshed data is what the snapshot then reads (the re-served
    # same-value print was a no-op; the new print landed).
    assert structured["funding_rate"] == 0.0007
    assert structured["open_interest"] == 22_222.0


def test_refresh_on_an_empty_store_requests_the_full_backfill(
    store: MetricPointsRepository,
) -> None:
    spy = _SpySource(store=store)
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_derivatives_snapshot(server, metric_points_repository=store, derivatives_source=spy)

    anyio.run(
        server.call_tool,
        "derivatives_snapshot",
        {"params": {"symbol": "BTCUSDT", "refresh": True}},
    )

    assert spy.calls == [
        ("fetch_series", _FUNDING_ID, None, None),  # start=None: from contract launch
        ("accrue_open_interest", _OI_ID),
    ]


# --- input boundary -----------------------------------------------------------------


def test_unregistered_symbol_surfaces_a_tool_error(store: MetricPointsRepository) -> None:
    spy = _SpySource()
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_derivatives_snapshot(server, metric_points_repository=store, derivatives_source=spy)

    with pytest.raises(Exception, match="DOGEUSDT"):
        anyio.run(server.call_tool, "derivatives_snapshot", {"params": {"symbol": "DOGEUSDT"}})
    assert spy.calls == []


# --- (c) full-toolset registration --------------------------------------------------


@pytest.fixture
def annotations_repo(session_factory: sessionmaker[Session]) -> AnnotationsRepository:
    return AnnotationsRepository(session_factory)


def _full_server_tool_names(
    annotations_repo: AnnotationsRepository,
    metric_points_repository: MetricPointsRepository | None,
) -> set[str]:
    session_manager, _asgi = create_mcp_components(
        provider=_UnusedProvider(),
        annotations_repository=annotations_repo,
        event_bus=EventBus(),
        ui_event_buffer=UIEventBuffer(),
        metric_points_repository=metric_points_repository,
    )
    handler = session_manager.app.request_handlers[ListToolsRequest]
    result = anyio.run(handler, ListToolsRequest(method="tools/list"))
    tools_result = result.root
    assert isinstance(tools_result, ListToolsResult)
    return {tool.name for tool in tools_result.tools}


def test_full_toolset_grows_derivatives_snapshot_with_a_store(
    annotations_repo: AnnotationsRepository, store: MetricPointsRepository
) -> None:
    assert "derivatives_snapshot" in _full_server_tool_names(annotations_repo, store)


def test_full_toolset_omits_derivatives_snapshot_without_a_store(
    annotations_repo: AnnotationsRepository,
) -> None:
    assert "derivatives_snapshot" not in _full_server_tool_names(annotations_repo, None)
