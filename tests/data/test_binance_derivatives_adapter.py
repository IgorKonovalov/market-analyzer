"""Plan 0056 phase 1 — Binance derivatives adapter: funding-rate backfill.

Done-when claims pinned here:
(a) pagination is proven against a 3-page fixture (two data pages + the empty
    terminator): points are contiguous (exact 8h spacing), deduplicated, and
    the walk terminates on the empty page;
(b) a 451 fixture raises `GeoRestrictedError` — exactly one transport attempt
    (not a retry), and the typed geo error (not a generic HTTP error);
(c) re-running the backfill is idempotent (row count unchanged);
(d) funding values round-trip at full precision — exact equality between the
    stored values and the fixture's decimal strings.

Fixture provenance: a verbatim capture could not be taken in this environment
(no raw network access), so the pages below are built in-code in EXACTLY the
documented `/fapi/v1/fundingRate` response shape (list of objects with
`symbol`, string-encoded 8-decimal `fundingRate`, integer epoch-millisecond
`fundingTime`, string `markPrice`), and the 451 body mirrors Binance's real
restricted-location response. The `network`-marked test at the bottom verifies
the same claims against the live endpoint when run from the user's network
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
    """Replaces `ResilientHttpClient._perform_request` (the transport seam):
    serves the entry list page-by-page from the request's `startTime`, capped
    at `_SERVER_PAGE_ROWS` rows, and records every request's query params."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries
        self.requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        self.requests.append(query)
        start_ms = int(query["startTime"])
        page = [e for e in self._entries if int(e["fundingTime"]) >= start_ms]
        page = page[:_SERVER_PAGE_ROWS]
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
    # The cursor starts at contract launch (0) and advances past each page's
    # last print; the final request starts beyond the last known print.
    assert fake.requests[0]["startTime"] == "0"
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
