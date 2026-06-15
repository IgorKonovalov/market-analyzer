"""Plan 0055 phase 4 — the `btc_cycle_snapshot` MCP tool; MVRV added by Plan 0057.

Done-when claims pinned here:
(a) cycle math arrives in the snapshot exactly as the fixture predicts (Mayer
    from a known SMA200; the halving clock from a pinned `now`);
(b) `dist_200w_ma` is `None` (not a number) when fewer than 1400 daily bars
    exist;
(d) the full-toolset registration grows `btc_cycle_snapshot` (and its sibling
    `get_metric_series`) when the metric store is wired — and omits them
    without one;
(e) trailing-only: the store reads go through `as_of`, so an injected
    future-timestamped point never appears in the snapshot.

Plan 0057 done-when pinned in the MVRV section below:
(a) the MVRV percentile is trailing-only — an injected future point shifts
    neither `mvrv` nor `mvrv_percentile`;
(b) absent series yield `None` for both MVRV fields;
(c) `mvrv` and `mvrv_percentile` match hand-computed fixture values exactly.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ListToolsRequest, ListToolsResult
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_app import create_mcp_components
from market_analyser.api.mcp_tools.cycle_snapshot import (
    _build_snapshot,
    register_btc_cycle_snapshot,
)
from market_analyser.api.ui_events.buffer import UIEventBuffer
from market_analyser.data.metric_series import (
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_COINMETRICS_BTC_MVRV,
    SERIES_FNG_VALUE,
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

_DAY = 86_400

# Pinned snapshot instant: 781 days after the 2024-04-19 halving.
_NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
_NOW_TS = int(_NOW.timestamp())


def _bars(closes: Sequence[float], end: datetime) -> list[Bar]:
    """Daily BTC-USD bars whose closes are `closes`, ending at `end`."""
    n = len(closes)
    return [
        Bar(
            symbol="BTC-USD",
            timeframe="1d",
            event_ts=end - timedelta(days=n - 1 - i),
            open=close,
            high=close + 1.0,
            low=close - 1.0 if close > 1.0 else close * 0.5,
            close=close,
            volume=1_000.0,
            source="yahoo",
        )
        for i, close in enumerate(closes)
    ]


class _FakeProvider:
    """Minimal MarketDataProvider conformer serving a fixed daily-bar series."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)
        self.calls: list[tuple[str, str]] = []

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.calls.append((symbol, timeframe))
        return list(self._bars)

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


# --- (a) + (b): the snapshot carries the exact cycle math -------------------------


def test_snapshot_pins_cycle_math_from_known_fixture(store: MetricPointsRepository) -> None:
    # closes 1..200: SMA200 = 100.5 -> Mayer exactly 200/100.5; only 200 daily
    # bars exist, so dist_200w_ma is None (not a number).
    closes = [float(i) for i in range(1, 201)]
    provider = _FakeProvider(_bars(closes, end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.as_of == _NOW
    assert snapshot.mayer_multiple == 200.0 / 100.5
    assert snapshot.dist_200w_ma is None
    assert snapshot.bars_available == 200
    # Halving clock at the pinned instant: 781 days into the open cycle.
    assert snapshot.days_since_halving == 781
    assert snapshot.days_to_next_halving_est == 1387 - 781
    assert snapshot.halving_phase == 781 / 1387
    assert snapshot.next_halving_date_est == "2028-02-05"
    assert provider.calls == [("BTC-USD", "1d")]


def test_snapshot_dist_200w_present_with_full_history(store: MetricPointsRepository) -> None:
    closes = [100.0] * 1399 + [130.0]
    provider = _FakeProvider(_bars(closes, end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    sma = (1399 * 100.0 + 130.0) / 1400
    assert snapshot.dist_200w_ma == 130.0 / sma - 1.0


def test_snapshot_with_no_bars_is_honest_nones(store: MetricPointsRepository) -> None:
    provider = _FakeProvider([])

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.mayer_multiple is None
    assert snapshot.dist_200w_ma is None
    assert snapshot.bars_available == 0


# --- store reads: latest values, deltas, warm-up honesty --------------------------


def test_snapshot_reads_latest_fng_with_7_and_30_day_deltas(
    store: MetricPointsRepository,
) -> None:
    store.upsert_points(
        [
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_NOW_TS - 31 * _DAY, value=25.0),
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_NOW_TS - 8 * _DAY, value=30.0),
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_NOW_TS - _DAY, value=40.0),
        ],
    )
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.fng == 40.0
    assert snapshot.fng_delta_7d == 10.0  # 40 - 30 (the point 7d before the latest)
    assert snapshot.fng_delta_30d == 15.0  # 40 - 25
    # Dominance accrual is cold: honest Nones, not fabricated values.
    assert snapshot.btc_dominance is None
    assert snapshot.dominance_delta_7d is None
    assert snapshot.dominance_delta_30d is None


def test_dominance_delta_none_until_accrual_warms_up(store: MetricPointsRepository) -> None:
    # One lone dominance point: a latest value exists but no 7d-earlier point.
    store.upsert_points(
        [MetricPoint(series_id=SERIES_COINGECKO_BTC_DOMINANCE, ts=_NOW_TS - _DAY, value=52.3)],
    )
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.btc_dominance == 52.3
    assert snapshot.dominance_delta_7d is None
    assert snapshot.dominance_delta_30d is None


# --- (e) trailing-only: an injected future point must not appear ------------------


def test_injected_future_point_never_appears(store: MetricPointsRepository) -> None:
    store.upsert_points(
        [
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_NOW_TS - _DAY, value=40.0),
            # One second past the snapshot instant — must be invisible.
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_NOW_TS + 1, value=99.0),
        ],
    )
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.fng == 40.0  # not 99.0


def test_tool_call_through_the_real_server_excludes_future_points(
    store: MetricPointsRepository,
) -> None:
    """The registered tool end-to-end: a future-timestamped point (relative to
    the tool's own wall-clock `now`) is injected and must not appear."""
    now_ts = int(datetime.now(tz=UTC).timestamp())
    store.upsert_points(
        [
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=now_ts - _DAY, value=40.0),
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=now_ts + _DAY, value=99.0),
        ],
    )
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=datetime.now(tz=UTC)))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_btc_cycle_snapshot(server, provider=provider, metric_points_repository=store)

    result = anyio.run(server.call_tool, "btc_cycle_snapshot", {"params": {}})
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)

    assert structured["fng"] == 40.0
    assert structured["mayer_multiple"] == 200.0 / 100.5
    assert structured["dist_200w_ma"] is None
    assert 0.0 <= structured["halving_phase"] <= 1.0


# --- MVRV (Plan 0057): hand-computed percentile, absent-series Nones, trailing ----


def test_mvrv_and_percentile_match_hand_computed_fixture(store: MetricPointsRepository) -> None:
    # Five trailing daily MVRV points; the latest (yesterday) is 1.0. Three of
    # the five values are at or below 1.0 (0.5, 0.8, 1.0) -> percentile 60.0.
    values_by_offset = {5: 0.5, 4: 3.0, 3: 2.0, 2: 0.8, 1: 1.0}
    store.upsert_points(
        [
            MetricPoint(
                series_id=SERIES_COINMETRICS_BTC_MVRV,
                ts=_NOW_TS - offset * _DAY,
                value=value,
            )
            for offset, value in values_by_offset.items()
        ],
    )
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.mvrv == 1.0  # the latest trailing observation
    assert snapshot.mvrv_percentile == 60.0  # 100 * 3/5, rank-inclusive


def test_mvrv_fields_none_when_series_absent(store: MetricPointsRepository) -> None:
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.mvrv is None
    assert snapshot.mvrv_percentile is None


def test_mvrv_percentile_is_trailing_only(store: MetricPointsRepository) -> None:
    """An injected future point (a fresh all-time high one day past the snapshot
    instant) must shift neither `mvrv` nor `mvrv_percentile`: both reads are
    bounded at the snapshot's own `as_of`, so the future high is invisible."""
    store.upsert_points(
        [
            MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=_NOW_TS - 2 * _DAY, value=0.5),
            MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=_NOW_TS - _DAY, value=1.0),
            # One day past the snapshot instant — a record high that must not leak.
            MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=_NOW_TS + _DAY, value=99.0),
        ],
    )
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=_NOW))

    snapshot = _build_snapshot(provider, store, _NOW)

    assert snapshot.mvrv == 1.0  # the trailing latest, not 99.0
    # Two trailing points, both <= 1.0 -> 100.0; the future high does not enter
    # the denominator or shift the rank.
    assert snapshot.mvrv_percentile == 100.0


# --- (Plan 0057 phase 5) MVRV refresh path: offline default, full + incremental ---


class _SpyMvrvSource:
    """A `MetricSeriesSource` spy: records every `fetch_series` call and returns
    a preset MVRV history. The offline default path must never call it."""

    def __init__(self, points: Sequence[MetricPoint] = ()) -> None:
        self._points = list(points)
        self.calls: list[dict[str, Any]] = []

    def fetch_series(
        self, series_id: str, start: int | None = None, end: int | None = None
    ) -> Sequence[MetricPoint]:
        self.calls.append({"series_id": series_id, "start": start, "end": end})
        return list(self._points)


def _call_snapshot_with_source(
    store: MetricPointsRepository, spy: _SpyMvrvSource, *, refresh: bool
) -> dict[str, Any]:
    """Invoke the registered tool end-to-end with a wired MVRV source, returning
    the structured payload. End-to-end (not `_build_snapshot`) because refresh
    lives in the tool wrapper, not the builder."""
    provider = _FakeProvider(_bars([float(i) for i in range(1, 201)], end=datetime.now(tz=UTC)))
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_btc_cycle_snapshot(
        server, provider=provider, metric_points_repository=store, mvrv_source=spy
    )
    result = anyio.run(server.call_tool, "btc_cycle_snapshot", {"params": {"refresh": refresh}})
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)
    return structured


def test_refresh_false_never_touches_the_source(store: MetricPointsRepository) -> None:
    """The default offline read: a wired source's `fetch_series` is never
    called, so no network is touched."""
    spy = _SpyMvrvSource()

    _call_snapshot_with_source(store, spy, refresh=False)

    assert spy.calls == []


def test_refresh_true_empty_series_does_full_backfill_that_surfaces(
    store: MetricPointsRepository,
) -> None:
    """First refresh against an empty series fetches from the start (full
    backfill) and the fetched points land in the store and surface in the
    snapshot's MVRV fields."""
    now_ts = int(datetime.now(tz=UTC).timestamp())
    history = [
        MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=now_ts - 2 * _DAY, value=0.5),
        MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=now_ts - _DAY, value=1.0),
    ]
    spy = _SpyMvrvSource(history)

    structured = _call_snapshot_with_source(store, spy, refresh=True)

    # Empty series -> fetched from the start (no `start` bound = full history).
    assert spy.calls == [{"series_id": SERIES_COINMETRICS_BTC_MVRV, "start": None, "end": None}]
    # The fetched history persisted...
    stored = store.range(SERIES_COINMETRICS_BTC_MVRV, 0, now_ts)
    assert [p.value for p in stored] == [0.5, 1.0]
    # ...and surfaces in the snapshot (latest 1.0; both points <= 1.0 -> 100.0).
    assert structured["mvrv"] == 1.0
    assert structured["mvrv_percentile"] == 100.0


def test_refresh_true_warm_series_fetches_incrementally(store: MetricPointsRepository) -> None:
    """Against a warm series, refresh fetches incrementally from the latest
    stored ts forward — not from scratch."""
    now_ts = int(datetime.now(tz=UTC).timestamp())
    seeded_ts = now_ts - 5 * _DAY
    store.upsert_points(
        [MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=seeded_ts, value=0.7)],
    )
    spy = _SpyMvrvSource(
        [MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=now_ts - _DAY, value=1.0)],
    )

    _call_snapshot_with_source(store, spy, refresh=True)

    assert len(spy.calls) == 1
    assert spy.calls[0]["start"] == seeded_ts  # incremental from the latest stored ts, not None


# --- (d) full-toolset registration ------------------------------------------------


@pytest.fixture
def annotations_repo(session_factory: sessionmaker[Session]) -> AnnotationsRepository:
    return AnnotationsRepository(session_factory)


def _full_server_tool_names(
    annotations_repo: AnnotationsRepository,
    metric_points_repository: MetricPointsRepository | None,
) -> set[str]:
    session_manager, _asgi = create_mcp_components(
        provider=_FakeProvider([]),
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


def test_full_toolset_grows_both_metric_tools_with_a_store(
    annotations_repo: AnnotationsRepository, store: MetricPointsRepository
) -> None:
    names = _full_server_tool_names(annotations_repo, store)
    assert "btc_cycle_snapshot" in names
    assert "get_metric_series" in names


def test_full_toolset_omits_metric_tools_without_a_store(
    annotations_repo: AnnotationsRepository,
) -> None:
    names = _full_server_tool_names(annotations_repo, None)
    assert "btc_cycle_snapshot" not in names
    assert "get_metric_series" not in names
