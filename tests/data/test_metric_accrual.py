"""Plan 0061 phase 1 — the metric-store self-warming job (ADR-0056).

Done-when claims pinned here, with fake sources over a REAL repository:

(a) a first tick against an empty store issues full-history fetches for
    F&G / funding / MVRV (spy-asserted ``start=None``) and a seed+sample for
    open interest;
(b) a second tick issues only incremental fetches (spy-asserted
    ``start == latest stored ts``) and writes nothing new into an
    already-written hour (first-write-wins asserted through the real
    repository, for both open interest and dominance);
(c) one series' raising source leaves the other four accrued in the same tick
    (containment), with the failure recorded per series in the heartbeat;
(d) ``metric_accrual_enabled=False`` constructs no job and the fake sources
    record zero calls, even through a full lifespan cycle;
(e) `/healthz` carries the heartbeat with per-series status including the
    failed series' error;
(f) the job is absent in the persistence-free test app.

Plus the boot posture ADR-0056 depends on: ``run()`` ticks immediately on
start (a cold store begins warming at boot, not one interval later).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.data.metric_accrual import (
    MetricAccrualJob,
    MetricAccrualSources,
)
from market_analyser.data.metric_series import (
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_COINMETRICS_BTC_MVRV,
    SERIES_FNG_VALUE,
    MetricPoint,
)
from market_analyser.data.types import MacroContext
from market_analyser.forecast.features import EXOGENOUS_SERIES_IDS_V2
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

SECRET = "test-secret"

NOW = datetime(2026, 7, 6, 12, 10, tzinfo=UTC)
LATER_SAME_HOUR = datetime(2026, 7, 6, 12, 40, tzinfo=UTC)
NOW_TS = int(NOW.timestamp())
LATER_TS = int(LATER_SAME_HOUR.timestamp())
HOUR_BUCKET = NOW_TS // 3600 * 3600

_MAX_TS = 253_402_300_799

FNG_HISTORY = [
    MetricPoint(series_id=SERIES_FNG_VALUE, ts=86_400, value=30.0),
    MetricPoint(series_id=SERIES_FNG_VALUE, ts=172_800, value=40.0),
    MetricPoint(series_id=SERIES_FNG_VALUE, ts=259_200, value=50.0),
]
FUNDING_HISTORY = [
    MetricPoint(series_id=SERIES_BINANCE_FUNDING_RATE_BTCUSDT, ts=28_800, value=0.0001),
    MetricPoint(series_id=SERIES_BINANCE_FUNDING_RATE_BTCUSDT, ts=57_600, value=0.0002),
]
MVRV_HISTORY = [
    MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=86_400, value=1.2),
    MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=172_800, value=1.5),
]
OI_SEED_BUCKETS = [(3_600, 100.0), (7_200, 101.0)]


class FakeSeriesSource:
    """Records `fetch_series` calls; serves a fixed ts-ascending history,
    clipped to the inclusive [start, end] window like the real adapters."""

    def __init__(self, series_id: str, points: list[MetricPoint]) -> None:
        self._series_id = series_id
        self._points = points
        self.calls: list[dict[str, int | None]] = []

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[MetricPoint]:
        assert series_id == self._series_id
        self.calls.append({"start": start, "end": end})
        points = self._points
        if start is not None:
            points = [p for p in points if p.ts >= start]
        if end is not None:
            points = [p for p in points if p.ts <= end]
        return points


class RaisingSeriesSource:
    """A dead upstream: every fetch raises the typed unavailable error while
    ``failing`` is set. Flipping it off (the upstream recovering) makes the
    source serve ``recovery_points`` like a normal fetch."""

    def __init__(self, recovery_points: list[MetricPoint] | None = None) -> None:
        self.calls: list[dict[str, int | None]] = []
        self.failing = True
        self._recovery_points = recovery_points if recovery_points is not None else []

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[MetricPoint]:
        self.calls.append({"start": start, "end": end})
        if self.failing:
            raise UpstreamUnavailableError("binance-futures: upstream unavailable (HTTP 503)")
        points = self._recovery_points
        if start is not None:
            points = [p for p in points if p.ts >= start]
        return points


class FakeOpenInterestSource:
    """Mimics the Plan 0056 adapter's write semantics through the REAL
    repository: seed lands fixed hour buckets; each accrue call consumes the
    next queued (ts, value) sample and writes its hour bucket, first write in
    a bucket wins (the `as_of` skip, exactly the real adapter's check)."""

    def __init__(
        self,
        store: MetricPointsRepository,
        *,
        seed_buckets: list[tuple[int, float]],
        samples: list[tuple[int, float]],
    ) -> None:
        self._store = store
        self._seed_buckets = seed_buckets
        self._samples = list(samples)
        self.seed_calls = 0
        self.accrue_calls = 0

    def seed_open_interest(self, series_id: str) -> int:
        self.seed_calls += 1
        points = [
            MetricPoint(series_id=series_id, ts=ts, value=value) for ts, value in self._seed_buckets
        ]
        return self._store.upsert_points(points)

    def accrue_open_interest(self, series_id: str) -> int:
        self.accrue_calls += 1
        ts, value = self._samples.pop(0)
        bucket = ts // 3600 * 3600
        existing = self._store.as_of(series_id, bucket)
        if existing is not None and existing.ts == bucket:
            return 0
        return self._store.upsert_points([MetricPoint(series_id=series_id, ts=bucket, value=value)])


class FakeMacroSource:
    """Mimics the CoinGecko dominance write-through (Plan 0055 phase 3)
    through the REAL repository: each fetch consumes the next queued
    (as_of_ts, dominance) snapshot and drops its hour bucket, first write in
    a bucket wins."""

    def __init__(
        self,
        store: MetricPointsRepository,
        *,
        snapshots: list[tuple[int, float]],
    ) -> None:
        self._store = store
        self._snapshots = list(snapshots)
        self.calls = 0

    def fetch_macro_context(self) -> MacroContext:
        self.calls += 1
        as_of_ts, dominance = self._snapshots.pop(0)
        bucket = as_of_ts // 3600 * 3600
        existing = self._store.as_of(SERIES_COINGECKO_BTC_DOMINANCE, bucket)
        if existing is None or existing.ts != bucket:
            self._store.upsert_points(
                [MetricPoint(series_id=SERIES_COINGECKO_BTC_DOMINANCE, ts=bucket, value=dominance)],
            )
        return MacroContext(
            market="crypto",
            btc_price=100_000.0,
            btc_change_24h=1.0,
            btc_dominance_pct=dominance,
            total_market_cap_usd=3.0e12,
            total_market_cap_change_24h=0.5,
            regime="neutral",
            as_of=datetime.fromtimestamp(as_of_ts, tz=UTC),
            source="coingecko",
        )


@pytest.fixture
def store() -> Iterator[MetricPointsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield MetricPointsRepository(make_session_factory(engine))
    engine.dispose()


def _sources(
    store: MetricPointsRepository,
    *,
    funding: FakeSeriesSource | RaisingSeriesSource | None = None,
) -> tuple[MetricAccrualSources, dict[str, object]]:
    fng = FakeSeriesSource(SERIES_FNG_VALUE, FNG_HISTORY)
    macro = FakeMacroSource(store, snapshots=[(NOW_TS, 55.0), (LATER_TS, 44.0)])
    effective_funding = (
        funding
        if funding is not None
        else FakeSeriesSource(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, FUNDING_HISTORY)
    )
    open_interest = FakeOpenInterestSource(
        store,
        seed_buckets=OI_SEED_BUCKETS,
        samples=[(NOW_TS, 111.0), (LATER_TS, 999.0)],
    )
    mvrv = FakeSeriesSource(SERIES_COINMETRICS_BTC_MVRV, MVRV_HISTORY)
    sources = MetricAccrualSources(
        fng=fng,
        macro=macro,
        funding=effective_funding,
        open_interest=open_interest,
        mvrv=mvrv,
    )
    fakes: dict[str, object] = {
        "fng": fng,
        "macro": macro,
        "funding": effective_funding,
        "open_interest": open_interest,
        "mvrv": mvrv,
    }
    return sources, fakes


def _job(store: MetricPointsRepository, sources: MetricAccrualSources) -> MetricAccrualJob:
    return MetricAccrualJob(metric_store=store, sources=sources)


def test_first_tick_backfills_empty_store_full_history_and_seeds_oi(
    store: MetricPointsRepository,
) -> None:
    sources, fakes = _sources(store)
    job = _job(store, sources)

    asyncio.run(job.tick_once(NOW))

    fng = fakes["fng"]
    funding = fakes["funding"]
    mvrv = fakes["mvrv"]
    oi = fakes["open_interest"]
    macro = fakes["macro"]
    assert isinstance(fng, FakeSeriesSource)
    assert isinstance(funding, FakeSeriesSource)
    assert isinstance(mvrv, FakeSeriesSource)
    assert isinstance(oi, FakeOpenInterestSource)
    assert isinstance(macro, FakeMacroSource)

    # Full-history fetches: start=None for every backfillable series.
    assert fng.calls == [{"start": None, "end": None}]
    assert funding.calls == [{"start": None, "end": None}]
    assert mvrv.calls == [{"start": None, "end": None}]
    # Open interest: the one-time seed plus the first live sample.
    assert oi.seed_calls == 1
    assert oi.accrue_calls == 1
    # Dominance: one write-through fetch.
    assert macro.calls == 1

    # The store now holds the histories, the seed+sample, and one bucket.
    assert len(store.range(SERIES_FNG_VALUE, 0, _MAX_TS)) == len(FNG_HISTORY)
    assert len(store.range(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, 0, _MAX_TS)) == len(FUNDING_HISTORY)
    assert len(store.range(SERIES_COINMETRICS_BTC_MVRV, 0, _MAX_TS)) == len(MVRV_HISTORY)
    oi_points = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, 0, _MAX_TS)
    assert [(p.ts, p.value) for p in oi_points] == [*OI_SEED_BUCKETS, (HOUR_BUCKET, 111.0)]
    dominance_points = store.range(SERIES_COINGECKO_BTC_DOMINANCE, 0, _MAX_TS)
    assert [(p.ts, p.value) for p in dominance_points] == [(HOUR_BUCKET, 55.0)]

    heartbeat = job.heartbeat()
    assert heartbeat.tick_count == 1
    assert heartbeat.last_tick_at == NOW
    assert heartbeat.series_errors == {}
    assert heartbeat.series_last_success_at == dict.fromkeys(EXOGENOUS_SERIES_IDS_V2, NOW)


def test_second_tick_is_incremental_and_never_rewrites_a_written_hour(
    store: MetricPointsRepository,
) -> None:
    sources, fakes = _sources(store)
    job = _job(store, sources)

    asyncio.run(job.tick_once(NOW))
    asyncio.run(job.tick_once(LATER_SAME_HOUR))

    fng = fakes["fng"]
    funding = fakes["funding"]
    mvrv = fakes["mvrv"]
    oi = fakes["open_interest"]
    macro = fakes["macro"]
    assert isinstance(fng, FakeSeriesSource)
    assert isinstance(funding, FakeSeriesSource)
    assert isinstance(mvrv, FakeSeriesSource)
    assert isinstance(oi, FakeOpenInterestSource)
    assert isinstance(macro, FakeMacroSource)

    # Incremental fetches: start == the latest stored ts for each series.
    assert fng.calls[1]["start"] == FNG_HISTORY[-1].ts
    assert funding.calls[1]["start"] == FUNDING_HISTORY[-1].ts
    assert mvrv.calls[1]["start"] == MVRV_HISTORY[-1].ts
    # No re-seed on a warm series; one more sample taken.
    assert oi.seed_calls == 1
    assert oi.accrue_calls == 2

    # First-write-wins through the real repository: the second same-hour
    # OI sample (999.0) and dominance snapshot (44.0) change nothing.
    oi_points = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, 0, _MAX_TS)
    assert [(p.ts, p.value) for p in oi_points] == [*OI_SEED_BUCKETS, (HOUR_BUCKET, 111.0)]
    dominance_points = store.range(SERIES_COINGECKO_BTC_DOMINANCE, 0, _MAX_TS)
    assert [(p.ts, p.value) for p in dominance_points] == [(HOUR_BUCKET, 55.0)]
    # And the backfillable series did not grow (the incremental fetch returned
    # only the already-stored tail point — an idempotent re-upsert).
    assert len(store.range(SERIES_FNG_VALUE, 0, _MAX_TS)) == len(FNG_HISTORY)

    heartbeat = job.heartbeat()
    assert heartbeat.tick_count == 2
    assert heartbeat.series_errors == {}


def test_one_raising_source_leaves_the_other_four_accrued(
    store: MetricPointsRepository,
) -> None:
    raising = RaisingSeriesSource(recovery_points=FUNDING_HISTORY)
    sources, _ = _sources(store, funding=raising)
    job = _job(store, sources)

    asyncio.run(job.tick_once(NOW))

    # The failed series is recorded, named, and cleared nowhere else.
    heartbeat = job.heartbeat()
    assert set(heartbeat.series_errors) == {SERIES_BINANCE_FUNDING_RATE_BTCUSDT}
    assert (
        "UpstreamUnavailableError" in heartbeat.series_errors[SERIES_BINANCE_FUNDING_RATE_BTCUSDT]
    )
    assert heartbeat.series_last_success_at[SERIES_BINANCE_FUNDING_RATE_BTCUSDT] is None
    for series_id in EXOGENOUS_SERIES_IDS_V2:
        if series_id != SERIES_BINANCE_FUNDING_RATE_BTCUSDT:
            assert heartbeat.series_last_success_at[series_id] == NOW

    # The other four accrued in the same tick.
    assert len(store.range(SERIES_FNG_VALUE, 0, _MAX_TS)) == len(FNG_HISTORY)
    assert len(store.range(SERIES_COINMETRICS_BTC_MVRV, 0, _MAX_TS)) == len(MVRV_HISTORY)
    assert len(store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, 0, _MAX_TS)) == 3
    assert len(store.range(SERIES_COINGECKO_BTC_DOMINANCE, 0, _MAX_TS)) == 1
    assert store.range(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, 0, _MAX_TS) == []

    # The upstream recovering clears the error on the next clean pass (the
    # heartbeat is current health, not history) and the missed series catches
    # up with a full backfill (its store is still empty → start=None).
    raising.failing = False
    asyncio.run(job.tick_once(LATER_SAME_HOUR))
    healed = job.heartbeat()
    assert healed.series_errors == {}
    assert healed.series_last_success_at[SERIES_BINANCE_FUNDING_RATE_BTCUSDT] == LATER_SAME_HOUR
    assert raising.calls[1]["start"] is None
    assert len(store.range(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, 0, _MAX_TS)) == len(FUNDING_HISTORY)


def test_run_ticks_immediately_on_start(store: MetricPointsRepository) -> None:
    """ADR-0056 boot posture: the first tick fires at startup, not one
    interval later — a cold store starts warming the moment the sidecar is up
    (the accrue-only series lose un-accrued hours forever)."""
    sources, fakes = _sources(store)
    job = MetricAccrualJob(metric_store=store, sources=sources, interval_seconds=3600)

    async def scenario() -> None:
        task = asyncio.create_task(job.run())
        for _ in range(200):
            if job.heartbeat().tick_count >= 1:
                break
            await asyncio.sleep(0.01)
        assert job.heartbeat().tick_count == 1
        assert job.heartbeat().running is True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert job.heartbeat().running is False
    fng = fakes["fng"]
    assert isinstance(fng, FakeSeriesSource)
    assert fng.calls == [{"start": None, "end": None}]


def test_disabled_flag_constructs_no_job_and_sources_are_never_touched() -> None:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    repo = MetricPointsRepository(make_session_factory(engine))
    sources, fakes = _sources(repo)
    app = create_app(
        secret=SECRET,
        engine=engine,
        metric_accrual_enabled=False,
        metric_accrual_sources=sources,
    )
    assert app.state.metric_accrual_job is None
    # A full lifespan cycle (startup + shutdown) still never touches a source.
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert "metric_accrual" not in body
    fng = fakes["fng"]
    funding = fakes["funding"]
    mvrv = fakes["mvrv"]
    oi = fakes["open_interest"]
    macro = fakes["macro"]
    assert isinstance(fng, FakeSeriesSource)
    assert isinstance(funding, FakeSeriesSource)
    assert isinstance(mvrv, FakeSeriesSource)
    assert isinstance(oi, FakeOpenInterestSource)
    assert isinstance(macro, FakeMacroSource)
    assert fng.calls == []
    assert funding.calls == []
    assert mvrv.calls == []
    assert oi.seed_calls == 0 and oi.accrue_calls == 0
    assert macro.calls == 0
    engine.dispose()


def test_persistence_free_app_has_no_job(store: MetricPointsRepository) -> None:
    """No engine → no metric store → no job, even when explicitly enabled and
    handed sources (the watch-scheduler posture)."""
    sources, fakes = _sources(store)
    app = create_app(
        secret=SECRET,
        metric_accrual_enabled=True,
        metric_accrual_sources=sources,
    )
    assert app.state.metric_accrual_job is None
    fng = fakes["fng"]
    assert isinstance(fng, FakeSeriesSource)
    assert fng.calls == []


def test_healthz_carries_heartbeat_including_failed_series_error() -> None:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    repo = MetricPointsRepository(make_session_factory(engine))
    raising = RaisingSeriesSource()
    sources, _ = _sources(repo, funding=raising)
    app = create_app(
        secret=SECRET,
        engine=engine,
        metric_accrual_enabled=True,
        metric_accrual_sources=sources,
    )
    job = app.state.metric_accrual_job
    assert isinstance(job, MetricAccrualJob)
    asyncio.run(job.tick_once(NOW))

    # No lifespan context on purpose: the tick above is the deterministic
    # driver; /healthz reads the same job instance the app state holds.
    body = TestClient(app).get("/healthz").json()
    heartbeat = body["metric_accrual"]
    assert heartbeat["tick_count"] == 1
    assert heartbeat["last_tick_at"] == NOW.isoformat().replace("+00:00", "Z")
    errors = heartbeat["series_errors"]
    assert set(errors) == {SERIES_BINANCE_FUNDING_RATE_BTCUSDT}
    assert "UpstreamUnavailableError" in errors[SERIES_BINANCE_FUNDING_RATE_BTCUSDT]
    per_series = heartbeat["series_last_success_at"]
    assert set(per_series) == set(EXOGENOUS_SERIES_IDS_V2)
    assert per_series[SERIES_BINANCE_FUNDING_RATE_BTCUSDT] is None
    assert per_series[SERIES_FNG_VALUE] is not None
    engine.dispose()
