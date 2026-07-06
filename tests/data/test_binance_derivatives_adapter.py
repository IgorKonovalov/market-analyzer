"""Plan 0056 phases 1 + 3 — Binance derivatives adapter: funding-rate backfill
and open-interest seed/accrual.

Phase-1 done-when claims pinned here:
(a) pagination is proven against a 3-page fixture (two data pages + the empty
    terminator): points are contiguous (exact 8h spacing), deduplicated, and
    the walk terminates on the empty page;
(b) a 451 fixture raises `GeoRestrictedError` — exactly one transport attempt
    (not a retry), and the typed geo error (not a generic HTTP error);
(c) re-running the backfill is idempotent (row count unchanged);
(d) funding values round-trip at full precision — exact equality between the
    stored values and the fixture's decimal strings.

Phase-3 done-when claims pinned here (the OI section below):
(a) the seed lands the fixture's window and a re-seed is idempotent (inserts 0,
    row count unchanged);
(b) accrual writes at most one point per hour — the Plan 0055 phase-3 dual
    assertion: two same-hour samples produce one point (first write wins),
    samples in different hours produce two;
(c) a seed/accrual overlap (the same hour reached from both paths, either
    order) neither duplicates the bucket nor raises a conflict.

Fixture provenance: the pages below are built in-code in EXACTLY the
documented `/fapi/v1/fundingRate` response shape (list of objects with
`symbol`, string-encoded 8-decimal `fundingRate`, integer epoch-millisecond
`fundingTime`, string `markPrice`), and the 451 body mirrors Binance's real
restricted-location response. The fake transport reproduces the
**live-verified parameter contract** (probed 2026-06-10, the plan 0056
phase 2 smoke finding): a genuine (nonzero) `startTime` serves rows from that
instant honoring `limit`; a missing OR zero `startTime` falls into the
latest-window mode that ignores `limit` and serves only recent prints — so a
backfill that lets a falsy `startTime` reach the wire fails these fixtures the
same way it fails live. The `network`-marked test at the bottom verifies the
same claims against the live endpoint when run from the user's network
(`uv run pytest tests/data/test_binance_derivatives_adapter.py -m network -s`)
— that run is Plan 0056 phase 2's smoke.
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

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.binance_derivatives import (
    BinanceDerivativesAdapter,
    BinanceDerivativesError,
    BinanceFuturesHttpClient,
)
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from market_analyser.data.metric_series import (
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_BINANCE_FUNDING_RATE_ETHUSDT,
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
    SERIES_BINANCE_OPEN_INTEREST_ETHUSDT,
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

# 2019-09-10T08:00:00Z — fixture launch anchor (BTCUSDT's real first prints are
# Sep 2019; the exact instant is pinned by the phase-2 live smoke, not here).
_LAUNCH_TS = int(datetime(2019, 9, 10, 8, tzinfo=UTC).timestamp())
_FUNDING_INTERVAL = 8 * 3600  # majors print every 8h; the adapter never assumes it
_HISTORY_PRINTS = 10
# The fake upstream serves at most this many rows per request — the 1000-row
# page cap at fixture scale, so the walk needs two data pages plus the empty one.
_SERVER_PAGE_ROWS = 6
# Latest-window mode at fixture scale: when `startTime` is absent OR zero,
# Binance ignores `limit` and serves only the most recent prints (live probe
# 2026-06-10: `startTime=0&limit=1000` returned the same ~200 recent rows as
# no params at all — never the launch-anchored history).
_LATEST_WINDOW_ROWS = 4

# Binance's real restricted-location response body (HTTP 451).
_BODY_451 = json.dumps(
    {
        "code": 0,
        "msg": (
            "Service unavailable from a restricted location according to "
            "'b. Eligibility' in https://www.binance.com/en/terms. Please contact "
            "customer service if you believe you received this message in error."
        ),
    },
).encode("utf-8")


def _rate_str(print_index: int) -> str:
    """Deterministic 8-decimal funding-rate string ~1e-4, mixed sign — the
    captured wire encoding (e.g. ``"-0.00003750"``)."""
    return f"{(print_index - 3) * 1.25e-05:.8f}"


def _print_ts(print_index: int) -> int:
    return _LAUNCH_TS + print_index * _FUNDING_INTERVAL


def _entry(print_index: int, *, symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "fundingTime": _print_ts(print_index) * 1000,
        "fundingRate": _rate_str(print_index),
        "markPrice": "34287.54619963",
    }


def _history_entries() -> list[dict[str, Any]]:
    """Ten unique prints with print 4 duplicated verbatim (same fundingTime,
    same rate) — the upstream-quirk shape the dedup must collapse."""
    entries = [_entry(i) for i in range(_HISTORY_PRINTS)]
    entries.insert(5, _entry(4))
    return entries


class _FakeTransport:
    """Replaces `ResilientHttpClient._perform_request` (the transport seam),
    reproducing the live-verified `/fapi/v1/fundingRate` parameter contract
    (plan 0056 phase 2 smoke finding): a genuine (nonzero) `startTime` serves
    rows from that instant, honoring `limit` up to the server page cap; a
    missing or **zero** `startTime` is treated as "not sent" and falls into
    latest-window mode — the most recent `_LATEST_WINDOW_ROWS`, `limit`
    ignored. Records every request's query params."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries
        self.requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        self.requests.append(query)
        start_ms = int(query.get("startTime", "0"))
        if start_ms == 0:
            page = self._entries[-_LATEST_WINDOW_ROWS:]
        else:
            limit = int(query.get("limit", "100"))
            page = [e for e in self._entries if int(e["fundingTime"]) >= start_ms]
            page = page[: min(limit, _SERVER_PAGE_ROWS)]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(page).encode("utf-8"),
            elapsed_seconds=0.0,
        )


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
) -> tuple[BinanceDerivativesAdapter, Any]:
    client = BinanceFuturesHttpClient(
        source_name="binance-test", cache_ttl_seconds=0.0, max_retries=0
    )
    fake = transport if transport is not None else _FakeTransport(_history_entries())
    monkeypatch.setattr(client, "_perform_request", fake)
    return BinanceDerivativesAdapter(http_client=client, metric_store=store), fake


def _static_response(status_code: int, body: bytes, headers: dict[str, str] | None = None) -> Any:
    """A transport fake that always returns one response, counting attempts."""

    class _Static:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(
            self, method: str, url: str, req_body: Any, req_headers: Any, *, proxy: Any
        ) -> HttpResponse:
            self.attempts += 1
            return HttpResponse(
                status_code=status_code,
                headers=headers or {},
                body=body,
                elapsed_seconds=0.0,
            )

    return _Static()


# --- registry + contract ------------------------------------------------------


def test_both_funding_series_are_registered() -> None:
    assert is_registered(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)
    assert is_registered(SERIES_BINANCE_FUNDING_RATE_ETHUSDT)


def test_adapter_satisfies_metric_series_source_protocol() -> None:
    assert isinstance(BinanceDerivativesAdapter(), MetricSeriesSource)


# --- (a) pagination: contiguous, deduplicated, terminates on the empty page ---


def test_pagination_walks_pages_dedupes_and_terminates_on_empty_page(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    points = adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    # Deduplicated: the fixture's 11 entries (print 4 twice) yield 10 points.
    assert len(points) == _HISTORY_PRINTS
    assert len({p.ts for p in points}) == _HISTORY_PRINTS
    # Contiguous: exact 8h spacing from the launch print, ascending.
    assert points[0].ts == _LAUNCH_TS
    assert all(later.ts - earlier.ts == _FUNDING_INTERVAL for earlier, later in pairwise(points))
    # Values come straight from the fixture's wire strings.
    assert [p.value for p in points] == [float(_rate_str(i)) for i in range(_HISTORY_PRINTS)]
    # Three requests: two data pages, then the empty page that terminates the
    # walk (empty page = end-of-history, never an error — ADR-0052).
    assert len(fake.requests) == 3
    assert all(req["symbol"] == "BTCUSDT" for req in fake.requests)
    assert all(req["limit"] == "1000" for req in fake.requests)
    # The cursor starts at the nonzero epoch floor — Binance treats
    # `startTime=0` as absent and would serve only the latest window (the
    # phase-2 smoke finding) — and advances past each page's last print; the
    # final request starts beyond the last known print. No request ever
    # carries a falsy startTime.
    assert fake.requests[0]["startTime"] == "1"
    assert all(int(req["startTime"]) >= 1 for req in fake.requests)
    assert int(fake.requests[-1]["startTime"]) == _print_ts(_HISTORY_PRINTS - 1) * 1000 + 1


def test_fetch_series_clips_to_inclusive_window(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    points = adapter.fetch_series(
        SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
        start=_print_ts(2),
        end=_print_ts(5),
    )

    assert [p.ts for p in points] == [_print_ts(2), _print_ts(3), _print_ts(4), _print_ts(5)]
    assert fake.requests[0]["startTime"] == str(_print_ts(2) * 1000)
    assert all(req["endTime"] == str(_print_ts(5) * 1000) for req in fake.requests)


def test_fetch_series_start_zero_never_sends_falsy_start_time(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """`start=0` means "from the epoch" — full history — but a literal
    `startTime=0` on the wire would flip Binance into latest-window mode and
    silently truncate the backfill. The cursor must clamp to the nonzero floor."""
    adapter, fake = _adapter(monkeypatch, store)

    points = adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, start=0)

    assert len(points) == _HISTORY_PRINTS
    assert points[0].ts == _LAUNCH_TS
    assert fake.requests[0]["startTime"] == "1"


def test_eth_series_requests_eth_symbol(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    transport = _FakeTransport([_entry(i, symbol="ETHUSDT") for i in range(3)])
    adapter, fake = _adapter(monkeypatch, store, transport=transport)

    points = adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_ETHUSDT)

    assert len(points) == 3
    assert all(p.series_id == SERIES_BINANCE_FUNDING_RATE_ETHUSDT for p in points)
    assert all(req["symbol"] == "ETHUSDT" for req in fake.requests)


def test_duplicate_print_with_different_rate_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """Same timestamp, different rate is upstream drift — surfaced typed,
    never silently collapsed to either value."""
    entries = [_entry(0), _entry(1)]
    entries.append({**_entry(1), "fundingRate": "0.00099000"})
    adapter, _ = _adapter(monkeypatch, store, transport=_FakeTransport(entries))

    with pytest.raises(BinanceDerivativesError, match="different rates"):
        adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)


# --- (b) HTTP 451 → GeoRestrictedError, exactly one attempt -------------------


def test_451_raises_geo_restricted_error_not_a_retry(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    # max_retries=3: retries are *available*, so one attempt proves 451 is
    # classified permanent, not that the budget ran out.
    client = BinanceFuturesHttpClient(
        source_name="binance-test", cache_ttl_seconds=0.0, max_retries=3
    )
    fake = _static_response(451, _BODY_451)
    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = BinanceDerivativesAdapter(http_client=client, metric_store=store)

    with pytest.raises(GeoRestrictedError, match="451") as excinfo:
        adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    # The typed geo error, not a generic HTTP/upstream error.
    assert excinfo.type is GeoRestrictedError
    # Not a retry: exactly one transport attempt, zero retries recorded.
    assert fake.attempts == 1
    assert client.stats().retries == 0


def test_429_maps_to_rate_limited_with_retry_after(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    fake = _static_response(429, b"{}", headers={"Retry-After": "30"})
    adapter, _ = _adapter(monkeypatch, store, transport=fake)

    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    assert excinfo.value.retry_after_seconds == 30


def test_5xx_maps_to_upstream_unavailable(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    fake = _static_response(500, b"")
    adapter, _ = _adapter(monkeypatch, store, transport=fake)

    with pytest.raises(UpstreamUnavailableError, match="HTTP 500"):
        adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)


# --- (c) backfill is idempotent ------------------------------------------------


def test_backfill_lands_full_history(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _adapter(monkeypatch, store)

    inserted = adapter.backfill_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    assert inserted == _HISTORY_PRINTS
    stored = store.range(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, 0, _print_ts(_HISTORY_PRINTS))
    assert len(stored) == _HISTORY_PRINTS
    assert stored[0].ts == _LAUNCH_TS


def test_backfill_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _adapter(monkeypatch, store)

    first = adapter.backfill_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)
    second = adapter.backfill_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    assert first == _HISTORY_PRINTS
    assert second == 0  # nothing new on the re-run
    stored = store.range(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, 0, _print_ts(_HISTORY_PRINTS))
    assert len(stored) == _HISTORY_PRINTS  # row count unchanged


def test_backfill_without_store_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, store=None)

    with pytest.raises(ValueError, match="metric store"):
        adapter.backfill_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)


# --- (d) full-precision round-trip ---------------------------------------------


def test_funding_values_round_trip_at_full_precision(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """8h points with rates ~1e-4: every stored value equals the fixture's
    decimal string parsed at the wire boundary — exact equality, no float
    truncation through pydantic, SQLite REAL, or the read path."""
    adapter, _ = _adapter(monkeypatch, store)
    adapter.backfill_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    stored = store.range(SERIES_BINANCE_FUNDING_RATE_BTCUSDT, 0, _print_ts(_HISTORY_PRINTS))

    assert [p.value for p in stored] == [float(_rate_str(i)) for i in range(_HISTORY_PRINTS)]
    # Anchor against literals so the claim doesn't depend on the helper:
    # print 0 is "-0.00003750" and print 4 is "0.00001250" on the wire.
    assert stored[0].value == -3.75e-05
    assert stored[4].value == 1.25e-05


# --- input boundary -------------------------------------------------------------


def test_fetch_series_rejects_foreign_series_id(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    with pytest.raises(ValueError, match=r"binance\.funding_rate\."):
        adapter.fetch_series("fng.value")
    assert fake.requests == []  # rejected before any fetch


def test_fetch_series_rejects_unregistered_binance_id(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _adapter(monkeypatch, store)

    with pytest.raises(UnknownMetricSeriesError, match="DOGEUSDT"):
        adapter.fetch_series("binance.funding_rate.DOGEUSDT")
    assert fake.requests == []


def test_shape_drift_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """A renamed per-entry field is upstream drift: a typed adapter error,
    never a silently-skipped point."""
    drifted = [{"symbol": "BTCUSDT", "fundingTime": _print_ts(0) * 1000, "rate": "0.0001"}]
    adapter, _ = _adapter(monkeypatch, store, transport=_FakeTransport(drifted))

    with pytest.raises(BinanceDerivativesError, match="fundingRate"):
        adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)


def test_foreign_symbol_in_payload_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _adapter(
        monkeypatch, store, transport=_FakeTransport([_entry(0, symbol="ETHUSDT")])
    )

    with pytest.raises(BinanceDerivativesError, match="ETHUSDT"):
        adapter.fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)


# =================================================================================
# Phase 3 — open interest: seed + accrual
# =================================================================================

# 2026-05-01T00:00:00Z — hour-aligned OI fixture anchor (the seed window is
# whatever upstream retains, ~30 days; the fixture scales it down to hours).
_OI_BASE_TS = int(datetime(2026, 5, 1, tzinfo=UTC).timestamp())
_OI_HIST_HOURS = 14
# The fake upstream serves at most this many hist rows per genuine-startTime
# request — the 500-row page cap at fixture scale, forcing a multi-page walk.
_OI_SERVER_PAGE_ROWS = 6
# Latest-window mode at fixture scale (no/zero startTime): `limit` ignored,
# only the most recent rows — the conservative reading of the fundingRate
# smoke finding, assumed shared by the hist endpoint.
_OI_LATEST_WINDOW_ROWS = 4


def _oi_value_str(hour_index: int) -> str:
    """Deterministic 8-decimal base-asset OI string (e.g. ``"20004.50000000"``)."""
    return f"{20_000 + hour_index * 1.5:.8f}"


def _oi_hist_entry(hour_index: int, *, symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "sumOpenInterest": _oi_value_str(hour_index),
        "sumOpenInterestValue": "150570784.07809979",
        "CMCCirculatingSupply": "165880.538",
        "timestamp": (_OI_BASE_TS + hour_index * 3600) * 1000,
    }


def _oi_snapshot_body(*, ts_ms: int, value: str, symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {"openInterest": value, "symbol": symbol, "time": ts_ms}


class _FakeOiTransport:
    """Transport seam serving both OI endpoints.

    `/futures/data/openInterestHist`: a genuine (nonzero) `startTime` serves
    rows from that instant honoring `limit` up to the server page cap; a
    missing or zero `startTime` falls into latest-window mode (`limit`
    ignored, most recent rows) — the same parameter contract the fundingRate
    smoke verified, conservatively assumed shared. With
    `clamp_early_start=False` the fake instead answers a startTime older than
    its earliest retained row with an EMPTY page (the undocumented
    non-clamping alternative the seed must survive). With
    `reject_start_at_or_before_ms` set, a paged startTime at or before that
    instant answers **HTTP 400** — the live-verified 2026-07-06 boundary
    behavior (any startTime >= 720h old is rejected outright, never clamped).
    With `echo_latest_past_end=True`, a paged startTime PAST the newest row
    re-serves the newest row instead of answering empty — the other
    live-verified 2026-07-06 behavior (startTime = newest+1 returned the
    newest row again; the empty-page terminator is gone).

    `/fapi/v1/openInterest`: returns `snapshot_holder["body"]` (mutable
    between calls so a test can steer the sample's timestamp/value).
    """

    def __init__(
        self,
        entries: list[dict[str, Any]],
        snapshot_holder: dict[str, dict[str, Any]] | None = None,
        *,
        clamp_early_start: bool = True,
        reject_start_at_or_before_ms: int | None = None,
        echo_latest_past_end: bool = False,
    ) -> None:
        self._entries = entries
        self._snapshot_holder = snapshot_holder if snapshot_holder is not None else {}
        self._clamp_early_start = clamp_early_start
        self._reject_start_at_or_before_ms = reject_start_at_or_before_ms
        self._echo_latest_past_end = echo_latest_past_end
        self.hist_requests: list[dict[str, str]] = []
        self.snapshot_requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        if "openInterestHist" in url:
            self.hist_requests.append(query)
            if (
                self._reject_start_at_or_before_ms is not None
                and "startTime" in query
                and int(query["startTime"]) <= self._reject_start_at_or_before_ms
            ):
                return HttpResponse(status_code=400, headers={}, body=b"{}", elapsed_seconds=0.0)
            payload: Any = self._hist_page(query)
        else:
            self.snapshot_requests.append(query)
            payload = self._snapshot_holder["body"]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(payload).encode("utf-8"),
            elapsed_seconds=0.0,
        )

    def _hist_page(self, query: dict[str, str]) -> list[dict[str, Any]]:
        start_ms = int(query.get("startTime", "0"))
        if start_ms == 0:
            return self._entries[-_OI_LATEST_WINDOW_ROWS:]
        if not self._clamp_early_start and self._entries:
            earliest = int(str(self._entries[0]["timestamp"]))
            if start_ms < earliest:
                return []
        if self._echo_latest_past_end and self._entries:
            newest = int(str(self._entries[-1]["timestamp"]))
            if start_ms > newest:
                return [self._entries[-1]]
        limit = int(query.get("limit", "30"))
        page = [e for e in self._entries if int(str(e["timestamp"])) >= start_ms]
        return page[: min(limit, _OI_SERVER_PAGE_ROWS)]


def _oi_adapter(
    monkeypatch: pytest.MonkeyPatch,
    store: MetricPointsRepository | None,
    *,
    transport: Any | None = None,
) -> tuple[BinanceDerivativesAdapter, Any]:
    client = BinanceFuturesHttpClient(
        source_name="binance-test", cache_ttl_seconds=0.0, max_retries=0
    )
    fake = (
        transport
        if transport is not None
        else _FakeOiTransport([_oi_hist_entry(i) for i in range(_OI_HIST_HOURS)])
    )
    monkeypatch.setattr(client, "_perform_request", fake)
    return BinanceDerivativesAdapter(http_client=client, metric_store=store), fake


_OI_ALL = (0, 4_000_000_000)


def test_both_open_interest_series_are_registered() -> None:
    assert is_registered(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    assert is_registered(SERIES_BINANCE_OPEN_INTEREST_ETHUSDT)


# --- (a) seed lands the window; re-seed idempotent --------------------------------


def test_oi_seed_lands_the_fixture_window(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _oi_adapter(monkeypatch, store)

    inserted = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert inserted == _OI_HIST_HOURS
    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert [p.ts for p in stored] == [_OI_BASE_TS + i * 3600 for i in range(_OI_HIST_HOURS)]
    # Hour-truncated buckets, values in base-asset units straight from the wire.
    assert all(p.ts % 3600 == 0 for p in stored)
    assert [p.value for p in stored] == [float(_oi_value_str(i)) for i in range(_OI_HIST_HOURS)]
    # The probe deliberately omits startTime (the documented latest-window
    # read); every paginated request carries a nonzero startTime — a falsy one
    # would flip upstream into latest-window mode (the phase-2 smoke finding).
    assert "startTime" not in fake.hist_requests[0]
    assert all("startTime" in req for req in fake.hist_requests[1:])
    assert all(int(req["startTime"]) >= 1 for req in fake.hist_requests[1:])
    assert all(req["period"] == "1h" for req in fake.hist_requests)
    assert all(req["limit"] == "500" for req in fake.hist_requests)
    assert all(req["symbol"] == "BTCUSDT" for req in fake.hist_requests)


def test_oi_reseed_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, _ = _oi_adapter(monkeypatch, store)

    first = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    second = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert first == _OI_HIST_HOURS
    assert second == 0  # nothing new on the re-seed
    assert len(store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)) == _OI_HIST_HOURS


def test_oi_seed_survives_a_non_clamping_upstream(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """What a startTime older than retention returns is undocumented: a
    clamping upstream serves from its earliest row, a non-clamping one answers
    empty. Against the empty-answering variant the seed must still terminate
    and land at least the latest served window (skip-forward recovery), never
    loop or crash."""
    transport = _FakeOiTransport(
        [_oi_hist_entry(i) for i in range(_OI_HIST_HOURS)], clamp_early_start=False
    )
    adapter, fake = _oi_adapter(monkeypatch, store, transport=transport)

    inserted = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert inserted == len(stored) >= _OI_LATEST_WINDOW_ROWS
    assert stored[-1].ts == _OI_BASE_TS + (_OI_HIST_HOURS - 1) * 3600
    # Bounded walk: the day-sized skip covers the 30-day window in ~30 probes.
    assert len(fake.hist_requests) < 40


def test_oi_seed_window_stays_inside_the_upstream_400_boundary(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """Verified live 2026-07-06 (Plan 0061 phase-4 smoke): upstream answers
    HTTP 400 to any paged startTime 720 or more hours old — it CLAMPS a
    younger pre-retention startTime to the ~21 days it actually holds, but a
    startTime at the exact 30-day mark is rejected outright, not clamped and
    not answered empty. The seed cursor anchors on the latest data timestamp
    (hour-truncated, so slightly older than upstream's wall clock), which put
    the old exactly-30-day window on the rejected side every time: the seed
    400'd forever and the series never seeded. The window is now one hour
    inside the mark; against a fake enforcing the live-verified boundary the
    seed must land the full fixture and the paginated cursor must stay
    strictly on the accepted side."""
    entries = [_oi_hist_entry(i) for i in range(_OI_HIST_HOURS)]
    latest_ms = int(str(entries[-1]["timestamp"]))
    transport = _FakeOiTransport(
        entries, reject_start_at_or_before_ms=latest_ms - 720 * 3_600 * 1000
    )
    adapter, fake = _oi_adapter(monkeypatch, store, transport=transport)

    inserted = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert inserted == _OI_HIST_HOURS
    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert [p.ts for p in stored] == [_OI_BASE_TS + i * 3600 for i in range(_OI_HIST_HOURS)]
    # The first paged cursor is exactly one hour inside the 30-day boundary —
    # pinned as an independent literal so a regression back to the rejected
    # exact-30d window fails here, not in production.
    first_paged = next(req for req in fake.hist_requests if "startTime" in req)
    assert int(first_paged["startTime"]) == latest_ms - (30 * 24 - 1) * 3_600 * 1000
    assert int(first_paged["startTime"]) > latest_ms - 720 * 3_600 * 1000


def test_oi_seed_terminates_on_the_latest_window_echo_past_the_newest_row(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """Verified live 2026-07-06 (Plan 0061 phase-4 smoke, second finding): a
    paged startTime PAST upstream's newest row no longer answers empty — it is
    clamped back and the newest row is re-served. The walk must read that
    non-advancing echo as end-of-data (the cursor is already past the
    probe-anchored latest) and terminate with the full window landed — never
    raise refusing-to-loop, never actually loop. A non-advancing page BEFORE
    the latest row is still the loud error (the stuck-cursor guard holds)."""
    transport = _FakeOiTransport(
        [_oi_hist_entry(i) for i in range(_OI_HIST_HOURS)], echo_latest_past_end=True
    )
    adapter, fake = _oi_adapter(monkeypatch, store, transport=transport)

    inserted = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert inserted == _OI_HIST_HOURS
    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert [p.ts for p in stored] == [_OI_BASE_TS + i * 3600 for i in range(_OI_HIST_HOURS)]
    # Values intact through the echoed-row dedup (first write in a bucket wins).
    assert [p.value for p in stored] == [float(_oi_value_str(i)) for i in range(_OI_HIST_HOURS)]
    # Terminated on the first past-end echo: bounded walk, no loop.
    past_end = [
        req
        for req in fake.hist_requests
        if "startTime" in req
        and int(req["startTime"]) > (_OI_BASE_TS + (_OI_HIST_HOURS - 1) * 3600) * 1000
    ]
    assert len(past_end) == 1


def test_oi_seed_with_empty_upstream_window_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _oi_adapter(monkeypatch, store, transport=_FakeOiTransport([]))

    assert adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT) == 0
    assert store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL) == []
    assert len(fake.hist_requests) == 1  # the probe alone decides emptiness


def test_oi_hist_timestamp_string_encoding_accepted(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """The official docs show `timestamp` both as a JSON number and as a
    numeric string across revisions — both must parse."""
    entries = [_oi_hist_entry(i) for i in range(3)]
    for entry in entries:
        entry["timestamp"] = str(entry["timestamp"])
    adapter, _ = _oi_adapter(monkeypatch, store, transport=_FakeOiTransport(entries))

    assert adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT) == 3


# --- (b) accrual: at most one point per hour (the 0055 dual assertion) ------------


def test_oi_accrual_two_same_hour_samples_produce_one_point(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    sample_ts = _OI_BASE_TS + 100 * 3600 + 37 * 60  # hh:37, off the hour boundary
    bucket = sample_ts // 3600 * 3600
    holder = {"body": _oi_snapshot_body(ts_ms=sample_ts * 1000, value="20100.50000000")}
    adapter, _ = _oi_adapter(monkeypatch, store, transport=_FakeOiTransport([], holder))

    first = adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    # 10 minutes later, same hour, drifted value: a no-op, not a second point
    # and not a conflict.
    holder["body"] = _oi_snapshot_body(ts_ms=(sample_ts + 600) * 1000, value="20999.00000000")
    second = adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert (first, second) == (1, 0)
    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert [p.ts for p in stored] == [bucket]
    assert stored[0].value == 20100.5  # first write in the hour wins


def test_oi_accrual_samples_in_different_hours_produce_two_points(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    sample_ts = _OI_BASE_TS + 100 * 3600 + 37 * 60
    bucket = sample_ts // 3600 * 3600
    holder = {"body": _oi_snapshot_body(ts_ms=sample_ts * 1000, value="20100.50000000")}
    adapter, _ = _oi_adapter(monkeypatch, store, transport=_FakeOiTransport([], holder))

    adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    holder["body"] = _oi_snapshot_body(ts_ms=(sample_ts + 3600) * 1000, value="20999.00000000")
    adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert [p.ts for p in stored] == [bucket, bucket + 3600]
    assert [p.value for p in stored] == [20100.5, 20999.0]


# --- (c) seed/accrual same-hour overlap: no duplicate, no conflict ----------------


def test_oi_seed_then_accrual_same_hour_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    last_hour_ts = _OI_BASE_TS + (_OI_HIST_HOURS - 1) * 3600
    holder = {
        # A snapshot 30 minutes into the seed's last hour, with a different value.
        "body": _oi_snapshot_body(ts_ms=(last_hour_ts + 1800) * 1000, value="99999.00000000"),
    }
    adapter, _ = _oi_adapter(
        monkeypatch,
        store,
        transport=_FakeOiTransport([_oi_hist_entry(i) for i in range(_OI_HIST_HOURS)], holder),
    )

    adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    accrued = adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert accrued == 0
    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert len(stored) == _OI_HIST_HOURS  # no duplicate bucket
    assert stored[-1].value == float(_oi_value_str(_OI_HIST_HOURS - 1))  # seed's write wins


def test_oi_accrual_then_seed_same_hour_is_skipped_not_conflicted(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    last_hour_ts = _OI_BASE_TS + (_OI_HIST_HOURS - 1) * 3600
    holder = {
        "body": _oi_snapshot_body(ts_ms=(last_hour_ts + 1800) * 1000, value="99999.00000000"),
    }
    adapter, _ = _oi_adapter(
        monkeypatch,
        store,
        transport=_FakeOiTransport([_oi_hist_entry(i) for i in range(_OI_HIST_HOURS)], holder),
    )

    adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    seeded = adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)

    assert seeded == _OI_HIST_HOURS - 1  # everything except the accrual-owned bucket
    stored = store.range(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT, *_OI_ALL)
    assert len(stored) == _OI_HIST_HOURS
    assert stored[-1].ts == last_hour_ts
    assert stored[-1].value == 99999.0  # the accrual's first write survives the seed


# --- typed errors + boundary checks on the OI paths -------------------------------


def test_oi_451_raises_geo_restricted_error(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    fake = _static_response(451, _BODY_451)
    adapter, _ = _oi_adapter(monkeypatch, store, transport=fake)

    with pytest.raises(GeoRestrictedError, match="451"):
        adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    with pytest.raises(GeoRestrictedError, match="451"):
        adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)


def test_oi_hist_shape_drift_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    drifted = [_oi_hist_entry(0)]
    del drifted[0]["sumOpenInterest"]
    adapter, _ = _oi_adapter(monkeypatch, store, transport=_FakeOiTransport(drifted))

    with pytest.raises(BinanceDerivativesError, match="sumOpenInterest"):
        adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)


def test_oi_snapshot_foreign_symbol_raises(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    holder = {"body": _oi_snapshot_body(ts_ms=_OI_BASE_TS * 1000, value="1.0", symbol="ETHUSDT")}
    adapter, _ = _oi_adapter(monkeypatch, store, transport=_FakeOiTransport([], holder))

    with pytest.raises(BinanceDerivativesError, match="ETHUSDT"):
        adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)


def test_oi_paths_reject_foreign_and_unregistered_series_ids(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter, fake = _oi_adapter(monkeypatch, store)

    with pytest.raises(ValueError, match=r"binance\.open_interest\."):
        adapter.accrue_open_interest(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)
    with pytest.raises(UnknownMetricSeriesError, match="DOGEUSDT"):
        adapter.seed_open_interest("binance.open_interest.DOGEUSDT")
    assert fake.hist_requests == [] and fake.snapshot_requests == []  # rejected pre-fetch


def test_oi_paths_without_store_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _oi_adapter(monkeypatch, store=None)

    with pytest.raises(ValueError, match="metric store"):
        adapter.seed_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)
    with pytest.raises(ValueError, match="metric store"):
        adapter.accrue_open_interest(SERIES_BINANCE_OPEN_INTEREST_BTCUSDT)


# --- live verification (phase 2 smoke; `uv run pytest -m network`) --------------


@pytest.mark.network
def test_live_btcusdt_funding_history_reaches_2019() -> None:
    """Plan 0056 phase 2's connectivity + depth smoke, runnable from the user's
    network: a full live pagination either geo-fails typed (451 →
    `GeoRestrictedError`, reported as-is) or lands the whole history with the
    first print in Sep 2019 and ~3 prints/day since."""
    points = BinanceDerivativesAdapter().fetch_series(SERIES_BINANCE_FUNDING_RATE_BTCUSDT)

    first = datetime.fromtimestamp(points[0].ts, tz=UTC)
    print(f"\nBTCUSDT funding: {len(points)} points, first at {first.isoformat()}")
    assert first.year == 2019 and first.month == 9
    assert len(points) >= 7000  # ~3/day since Sep 2019
    assert all(later.ts > earlier.ts for earlier, later in pairwise(points))
    assert all(abs(p.value) < 0.05 for p in points)  # rates are decimals, not percents
