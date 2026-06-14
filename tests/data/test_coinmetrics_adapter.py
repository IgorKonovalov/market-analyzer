"""Plan 0057 phase 2 — CoinMetrics community adapter: daily MVRV backfill.

Done-when claims pinned here:
(a) backfill from the fixture lands one MVRV point/day with exact values — exact
    equality between the stored floats and the fixture's decimal strings (no
    float drift through pydantic / SQLite REAL / the read path);
(b) pagination is proven against a multi-page fixture: points are contiguous
    (exact daily spacing), deduplicated, the cursor-advance walk terminates on
    the empty page, and the history reaches 2011-12-29;
(c) re-running the backfill is idempotent (row count unchanged);
(d) pacing is pinned the way Plan 0034 pinned its RPC spacing — exactly one
    pause per burst boundary, asserted against a fake clock with a recording
    sleep, so the test never actually waits.

Fixture provenance: the pages below are built in-code in EXACTLY the documented
`timeseries/asset-metrics` response shape (a `data` list of objects with `asset`,
nanosecond ISO-8601 UTC `time`, and a string-encoded `CapMVRVCur`), anchored on
the phase-1 probe's recorded facts (2026-06-14, from the user's network):
`CapMVRVCur` is keyless with full daily history back to **2011-12-29**, where the
verified value is **0.85308817**. The fake transport reproduces the probe's
ordering finding — an `end_time`-bounded window returns rows ascending — by
serving the rows with `time` in `[start_time, end_time]`, capped to a small
server page so the cursor-advance walk needs several requests. The
`network`-marked test at the bottom verifies the same claims against the live
endpoint (`uv run pytest tests/data/test_coinmetrics_adapter.py -m network -s`).
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.coinmetrics import (
    CoinMetricsCommunityAdapter,
    CoinMetricsError,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.metric_series import (
    SERIES_COINMETRICS_BTC_MVRV,
    UnknownMetricSeriesError,
    is_registered,
)
from market_analyser.data.sources import MetricSeriesSource
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

# 2011-12-29 — the earliest history the phase-1 probe confirmed; day 0's value
# is the verified probe reading.
_BASE = datetime(2011, 12, 29, tzinfo=UTC)
_BASE_TS = int(_BASE.timestamp())
_DAY = 86_400
_N_DAYS = 8
# The fake serves at most this many rows per request — the page cap at fixture
# scale, so the cursor-advance walk needs several requests plus the empty one.
_SERVER_PAGE_ROWS = 3


def _value_str(i: int) -> str:
    """Deterministic 8-decimal MVRV string. Day 0 is the probe's verified
    2011-12-29 reading; the rest climb through ~1.x like real MVRV."""
    if i == 0:
        return "0.85308817"
    return f"{1.10 + i * 0.012345:.8f}"


def _time_str(i: int) -> str:
    """Nanosecond ISO-8601 UTC `time`, the CoinMetrics wire encoding."""
    ts = _BASE_TS + i * _DAY
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def _entry(i: int, *, asset: str = "btc") -> dict[str, Any]:
    return {"asset": asset, "time": _time_str(i), "CapMVRVCur": _value_str(i)}


def _entries() -> list[dict[str, Any]]:
    """Eight unique daily observations with day 3 duplicated verbatim (same
    `time`, same value) — the upstream-quirk shape the dedup must collapse."""
    rows = [_entry(i) for i in range(_N_DAYS)]
    rows.insert(4, _entry(3))
    return rows


def _parse_iso(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())


def _entry_ts(entry: dict[str, Any]) -> int:
    text = str(entry["time"]).split(".")[0].rstrip("Z")
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC).timestamp())


class _FakeTransport:
    """Replaces `ResilientHttpClient._perform_request` (the transport seam). An
    `end_time`-bounded window returns the rows whose `time` falls in
    `[start_time, end_time]`, ascending, capped to the server page size — the
    probe's ascending-when-bounded finding at fixture scale. Records every
    request's query params."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = sorted(entries, key=_entry_ts)
        self.requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        self.requests.append(query)
        start_ts = _parse_iso(query["start_time"])
        end_ts = _parse_iso(query["end_time"])
        page = [e for e in self._entries if start_ts <= _entry_ts(e) <= end_ts]
        page = page[:_SERVER_PAGE_ROWS]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps({"data": page}).encode("utf-8"),
            elapsed_seconds=0.0,
        )


class _FakeClock:
    """A monotonic clock advanced only by the recording sleep — so the pacer's
    window logic is driven entirely by the test, no wall-clock dependency."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _SleepRecorder:
    """Records every pause and advances the paired fake clock by it."""

    def __init__(self, clock: _FakeClock) -> None:
        self.calls: list[float] = []
        self._clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> MetricPointsRepository:
    return MetricPointsRepository(session_factory)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    store: MetricPointsRepository | None,
    *,
    transport: Any | None = None,
    requests_per_window: int = 10,
    window_seconds: float = 6.0,
    sleep: Any | None = None,
    clock: Any | None = None,
) -> tuple[CoinMetricsCommunityAdapter, Any]:
    client = ResilientHttpClient(
        source_name="coinmetrics-test", cache_ttl_seconds=0.0, max_retries=0
    )
    fake = transport if transport is not None else _FakeTransport(_entries())
    monkeypatch.setattr(client, "_perform_request", fake)
    kwargs: dict[str, Any] = {
        "requests_per_window": requests_per_window,
        "window_seconds": window_seconds,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    if clock is not None:
        kwargs["clock"] = clock
    adapter = CoinMetricsCommunityAdapter(http_client=client, metric_store=store, **kwargs)
    return adapter, fake


def _static_response(status_code: int, body: bytes, headers: dict[str, str] | None = None) -> Any:
    """A transport fake that always returns one response."""

    def _call(
        method: str, url: str, req_body: Any, req_headers: Any, *, proxy: Any
    ) -> HttpResponse:
        return HttpResponse(
            status_code=status_code, headers=headers or {}, body=body, elapsed_seconds=0.0
        )

    return _call


# --- registry + contract ------------------------------------------------------


def test_mvrv_series_is_registered() -> None:
    assert is_registered(SERIES_COINMETRICS_BTC_MVRV)


def test_adapter_satisfies_metric_series_source_protocol() -> None:
    assert isinstance(CoinMetricsCommunityAdapter(), MetricSeriesSource)


# --- (b) pagination: contiguous, deduplicated, terminates, reaches 2011-12-29 -


def test_pagination_walks_pages_dedupes_terminates_and_reaches_2011(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    points = adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)

    # Deduplicated: the fixture's 9 entries (day 3 twice) yield 8 points.
    assert len(points) == _N_DAYS
    assert len({p.ts for p in points}) == _N_DAYS
    # Contiguous: exact daily spacing, ascending, reaching 2011-12-29.
    assert points[0].ts == _BASE_TS
    assert all(later.ts - earlier.ts == _DAY for earlier, later in pairwise(points))
    # The walk terminates: 8 days / 3-row pages = 3 data pages + the empty page.
    assert len(fake.requests) == 4
    assert all(req["assets"] == "btc" for req in fake.requests)
    assert all(req["metrics"] == "CapMVRVCur" for req in fake.requests)
    assert all(req["frequency"] == "1d" for req in fake.requests)
    # The cursor starts at the confirmed earliest history and advances forward.
    assert fake.requests[0]["start_time"] == "2011-12-29T00:00:00Z"
    starts = [_parse_iso(req["start_time"]) for req in fake.requests]
    assert all(later > earlier for earlier, later in pairwise(starts))


def test_fetch_series_clips_to_inclusive_window(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    points = adapter.fetch_series(
        SERIES_COINMETRICS_BTC_MVRV,
        start=_BASE_TS + 2 * _DAY,
        end=_BASE_TS + 5 * _DAY,
    )

    assert [p.ts for p in points] == [_BASE_TS + i * _DAY for i in (2, 3, 4, 5)]
    assert fake.requests[0]["start_time"] == "2011-12-31T00:00:00Z"  # day 2
    assert all(req["end_time"] == "2012-01-03T00:00:00Z" for req in fake.requests)  # day 5


def test_duplicate_observation_with_different_value_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """Same timestamp, different value is upstream drift — surfaced typed, never
    silently collapsed to either value."""
    entries = [_entry(0), _entry(1)]
    entries.append({**_entry(1), "CapMVRVCur": "9.99999999"})
    adapter, _ = _adapter(monkeypatch, store, transport=_FakeTransport(entries))

    with pytest.raises(CoinMetricsError, match="different values"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


# --- (a) exact daily values + (c) idempotent backfill -------------------------


def test_backfill_lands_one_point_per_day_with_exact_values(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _adapter(monkeypatch, store)

    inserted = adapter.backfill_series()

    assert inserted == _N_DAYS
    stored = store.range(SERIES_COINMETRICS_BTC_MVRV, 0, _BASE_TS + _N_DAYS * _DAY)
    assert [p.ts for p in stored] == [_BASE_TS + i * _DAY for i in range(_N_DAYS)]
    # Exact equality: every stored float equals the fixture's decimal string.
    assert [p.value for p in stored] == [float(_value_str(i)) for i in range(_N_DAYS)]
    # Anchor against the literal probe value so the claim doesn't lean on the helper.
    assert stored[0].value == 0.85308817


def test_backfill_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _adapter(monkeypatch, store)

    first = adapter.backfill_series()
    second = adapter.backfill_series()

    assert first == _N_DAYS
    assert second == 0  # nothing new on the re-run
    assert len(store.range(SERIES_COINMETRICS_BTC_MVRV, 0, _BASE_TS + _N_DAYS * _DAY)) == _N_DAYS


def test_incremental_backfill_from_start_lands_only_the_tail(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """An incremental update is a backfill from a later `start`: only the tail is
    fetched and inserted. Against a clean store, a `start`-bounded backfill lands
    exactly the days at or after `start`."""
    adapter, _ = _adapter(monkeypatch, store)

    inserted = adapter.backfill_series(start=_BASE_TS + 6 * _DAY)

    assert inserted == 2  # only days 6 and 7
    stored = store.range(SERIES_COINMETRICS_BTC_MVRV, 0, _BASE_TS + _N_DAYS * _DAY)
    assert [p.ts for p in stored] == [_BASE_TS + 6 * _DAY, _BASE_TS + 7 * _DAY]


def test_backfill_without_store_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, store=None)

    with pytest.raises(ValueError, match="metric store"):
        adapter.backfill_series()


# --- (d) pacing: exactly one pause per burst boundary, against a fake clock ----


def test_pacing_inserts_exactly_one_pause_per_burst_boundary(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """With a 2-request window, the 4-request walk crosses one burst boundary:
    requests 1-2 fill the window, request 3 must wait for request 1 to age out
    (one pause), and after the clock advances the window clears for request 4.
    Asserted against a fake clock so the test never waits — and the paced walk
    still lands the full, correct history."""
    clock = _FakeClock()
    sleep = _SleepRecorder(clock)
    adapter, fake = _adapter(
        monkeypatch,
        store,
        requests_per_window=2,
        window_seconds=6.0,
        sleep=sleep,
        clock=clock,
    )

    points = adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)

    assert len(fake.requests) == 4
    # Exactly one pause, of exactly the window length (the oldest request was at
    # t=0, so the full 6s must elapse for it to leave the window).
    assert sleep.calls == [6.0]
    # Pacing does not corrupt the result: the full history still lands, ordered.
    assert [p.ts for p in points] == [_BASE_TS + i * _DAY for i in range(_N_DAYS)]


def test_no_pause_when_walk_stays_within_the_window(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """The 4-request walk under the default 10/6 budget never pauses."""
    clock = _FakeClock()
    sleep = _SleepRecorder(clock)
    adapter, _ = _adapter(monkeypatch, store, sleep=sleep, clock=clock)

    adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)

    assert sleep.calls == []


# --- typed errors -------------------------------------------------------------


def test_429_maps_to_rate_limited_with_retry_after(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    fake = _static_response(429, b"{}", headers={"Retry-After": "12"})
    adapter, _ = _adapter(monkeypatch, store, transport=fake)

    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)

    assert excinfo.value.retry_after_seconds == 12


def test_403_forbidden_maps_to_upstream_unavailable(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """The paywalled-metric response (the probe saw `forbidden` for CapRealUSD /
    SOPR) is a permanent 4xx → typed upstream-unavailable, not a silent empty."""
    fake = _static_response(403, b'{"error":{"type":"forbidden"}}')
    adapter, _ = _adapter(monkeypatch, store, transport=fake)

    with pytest.raises(UpstreamUnavailableError, match="HTTP 403"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


def test_5xx_maps_to_upstream_unavailable(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    fake = _static_response(500, b"")
    adapter, _ = _adapter(monkeypatch, store, transport=fake)

    with pytest.raises(UpstreamUnavailableError, match="HTTP 500"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


# --- input + shape boundary ----------------------------------------------------


def test_fetch_series_rejects_foreign_series_id(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    with pytest.raises(ValueError, match=r"coinmetrics\.btc\.mvrv"):
        adapter.fetch_series("fng.value")
    assert fake.requests == []  # rejected before any fetch


def test_fetch_series_rejects_unregistered_coinmetrics_id(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    with pytest.raises((ValueError, UnknownMetricSeriesError)):
        adapter.fetch_series("coinmetrics.btc.sopr")
    assert fake.requests == []


def test_missing_metric_value_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """A null/absent CapMVRVCur is upstream drift: a typed adapter error, never
    a silently-skipped point."""
    drifted = [{"asset": "btc", "time": _time_str(0)}]  # no CapMVRVCur
    adapter, _ = _adapter(monkeypatch, store, transport=_FakeTransport(drifted))

    with pytest.raises(CoinMetricsError, match="CapMVRVCur"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


def test_foreign_asset_in_payload_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _adapter(monkeypatch, store, transport=_FakeTransport([_entry(0, asset="eth")]))

    with pytest.raises(CoinMetricsError, match="eth"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


def test_missing_data_list_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    def _no_data(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(200, {}, json.dumps({"error": "nope"}).encode("utf-8"), 0.0)

    adapter, _ = _adapter(monkeypatch, store, transport=_no_data)

    with pytest.raises(CoinMetricsError, match="'data' list"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


def test_unparseable_time_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    def _bad_time(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = {"data": [{"asset": "btc", "time": "not-a-time", "CapMVRVCur": "1.0"}]}
        return HttpResponse(200, {}, json.dumps(payload).encode("utf-8"), 0.0)

    adapter, _ = _adapter(monkeypatch, store, transport=_bad_time)

    with pytest.raises(CoinMetricsError, match="unparseable time"):
        adapter.fetch_series(SERIES_COINMETRICS_BTC_MVRV)


# --- live verification (phase 2 smoke; `uv run pytest -m network`) ------------


@pytest.mark.network
def test_live_mvrv_history_reaches_2011_with_the_probe_value() -> None:
    """The phase-2 connectivity + depth smoke, runnable from the user's network:
    a full live pagination lands the whole daily MVRV history, the first point on
    2011-12-29 equal to the probe's verified 0.85308817, ascending throughout,
    with a plausible recent value."""
    points = CoinMetricsCommunityAdapter().fetch_series(SERIES_COINMETRICS_BTC_MVRV)

    first = datetime.fromtimestamp(points[0].ts, tz=UTC)
    print(f"\nBTC MVRV: {len(points)} points, first at {first.isoformat()} = {points[0].value}")
    assert first.date() == _BASE.date()
    assert abs(points[0].value - 0.85308817) < 1e-6
    assert len(points) >= 4000  # ~daily since 2011-12-29
    assert all(later.ts > earlier.ts for earlier, later in pairwise(points))
    assert 0.1 < points[-1].value < 10.0  # MVRV is an O(1) ratio, never absurd
