"""Plan 0058 phase 1 — Binance spot klines adapter: fetch + pagination.

Phase-1 done-when claims pinned here:
(a) a 3-page fixture window (three data pages + the empty terminator) returns
    contiguous, gap-free, deduplicated bars in ascending timestamp order;
(b) a past-ending historical window returns exactly the fixture's bars for that
    window — absolute `startTime`/`endTime` on the wire, never a now-relative
    range (the Plan 0031 lesson applied from day one);
(c) bad rows (zero/negative price) raise the typed `BinanceKlinesError`, never
    silently pass;
(d) a 451 fixture raises `GeoRestrictedError` — exactly one transport attempt
    (classified permanent, not a retry).

Plus the ADR-0033 empty-window split (leading-edge empty → `UnknownSymbolError`,
strictly-historical empty → `[]`), the canonical-registry interval map, and the
typed-error taxonomy (429 / 5xx).

Fixture provenance: the kline arrays below are built in-code in EXACTLY the
documented `/api/v3/klines` response shape (12-element arrays: integer
epoch-millisecond open time, string-encoded decimal OHLCV, integer close time,
string quote volume, integer trade count, string taker volumes, string ignore
field), and the 451 body mirrors Binance's real restricted-location response.
The fake transport honors the wire parameter contract: `startTime`/`endTime`
filter by open time inclusively; `limit` is honored up to the server page cap
(the 1000-row cap at fixture scale); a missing or zero `startTime` falls into
latest-window mode (the live-verified Plan 0056 phase-2 smoke finding,
conservatively assumed shared by the spot API).
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters.binance_klines import (
    BinanceKlinesAdapter,
    BinanceKlinesError,
    BinanceSpotHttpClient,
)
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UnknownSymbolError,
    UpstreamUnavailableError,
)
from market_analyser.data.sources import OhlcvSource
from market_analyser.data.timeframes import registry_timeframes

# 2024-01-01T00:00:00Z — hour-aligned fixture anchor.
_BASE = datetime(2024, 1, 1, tzinfo=UTC)
_BASE_TS = int(_BASE.timestamp())
_HOUR = 3600
_HISTORY_BARS = 14
# The fake upstream serves at most this many rows per request — the 1000-row
# page cap at fixture scale, so the full walk needs three data pages plus the
# empty terminator.
_SERVER_PAGE_ROWS = 6
# Latest-window mode at fixture scale: when `startTime` is absent OR zero,
# Binance ignores the request window and serves only the most recent rows
# (the plan 0056 phase-2 smoke finding, assumed shared by the spot API).
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


def _bar_ts(index: int) -> int:
    return _BASE_TS + index * _HOUR


def _ohlcv_strs(index: int) -> tuple[str, str, str, str, str]:
    """Deterministic 8-decimal wire strings with the OHLC invariants intact."""
    return (
        f"{100.0 + index:.8f}",  # open
        f"{101.5 + index:.8f}",  # high
        f"{99.0 + index:.8f}",  # low
        f"{100.5 + index:.8f}",  # close
        f"{10.0 + index:.8f}",  # volume
    )


def _kline(index: int) -> list[Any]:
    """One kline array in the documented 12-element wire shape."""
    o, h, low, c, v = _ohlcv_strs(index)
    open_ms = _bar_ts(index) * 1000
    return [
        open_ms,
        o,
        h,
        low,
        c,
        v,
        open_ms + _HOUR * 1000 - 1,  # close time
        "2434.19055334",  # quote asset volume
        308,  # number of trades
        "1756.87402397",  # taker buy base volume
        "28.46694368",  # taker buy quote volume
        "0",  # unused field, ignore
    ]


def _history_klines() -> list[list[Any]]:
    """Fourteen hourly klines with bar 4 duplicated verbatim (same open time,
    same values) — the upstream-quirk shape the dedup must collapse."""
    klines = [_kline(i) for i in range(_HISTORY_BARS)]
    klines.insert(5, _kline(4))
    return klines


class _FakeTransport:
    """Replaces `ResilientHttpClient._perform_request` (the transport seam),
    honoring the documented `/api/v3/klines` parameter contract: a genuine
    (nonzero) `startTime` serves rows with open time in
    `[startTime, endTime]`, honoring `limit` up to the server page cap; a
    missing or **zero** `startTime` is treated as "not sent" and falls into
    latest-window mode (most recent rows, request window ignored). Records
    every request's query params."""

    def __init__(self, klines: list[list[Any]]) -> None:
        self._klines = klines
        self.requests: list[dict[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        raw_query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = {k: v[0] for k, v in raw_query.items()}
        self.requests.append(query)
        start_ms = int(query.get("startTime", "0"))
        if start_ms == 0:
            page = self._klines[-_LATEST_WINDOW_ROWS:]
        else:
            end_ms = int(query.get("endTime", str(2**62)))
            limit = int(query.get("limit", "500"))
            page = [k for k in self._klines if start_ms <= int(k[0]) <= end_ms]
            page = page[: min(limit, _SERVER_PAGE_ROWS)]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(page).encode("utf-8"),
            elapsed_seconds=0.0,
        )


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any | None = None,
    max_retries: int = 0,
) -> tuple[BinanceKlinesAdapter, Any]:
    client = BinanceSpotHttpClient(
        source_name="binance-test", cache_ttl_seconds=0.0, max_retries=max_retries
    )
    fake = transport if transport is not None else _FakeTransport(_history_klines())
    monkeypatch.setattr(client, "_perform_request", fake)
    return BinanceKlinesAdapter(http_client=client), fake


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


def _window(first_bar: int, last_bar: int) -> tuple[datetime, datetime]:
    """The [start, end] datetimes covering fixture bars `first_bar..last_bar`."""
    return (
        datetime.fromtimestamp(_bar_ts(first_bar), tz=UTC),
        datetime.fromtimestamp(_bar_ts(last_bar), tz=UTC),
    )


# A `now` far past the fixture so every test window is strictly historical
# (the leading-edge classification is exercised explicitly further down).
_LONG_AFTER = _BASE + timedelta(days=365)


# --- contract -------------------------------------------------------------------


def test_adapter_satisfies_ohlcv_source_protocol() -> None:
    assert isinstance(BinanceKlinesAdapter(), OhlcvSource)


def test_interval_map_covers_exactly_the_canonical_registry() -> None:
    """Every canonical timeframe is native on Binance; the map and the registry
    cannot drift apart in either direction."""
    from market_analyser.data.adapters.binance_klines import _BINANCE_INTERVALS

    assert frozenset(_BINANCE_INTERVALS) == registry_timeframes()


# --- (a) pagination: contiguous, deduplicated, ascending, empty page terminates --


def test_three_page_window_returns_contiguous_deduplicated_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, fake = _adapter(monkeypatch)
    start, end = _window(0, _HISTORY_BARS - 1)

    bars = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)

    # Deduplicated: the fixture's 15 rows (bar 4 twice) yield 14 bars.
    assert len(bars) == _HISTORY_BARS
    assert len({b.event_ts for b in bars}) == _HISTORY_BARS
    # Contiguous and gap-free: exact 1h spacing from the anchor, ascending.
    assert bars[0].event_ts == _BASE
    assert all(
        later.event_ts - earlier.event_ts == timedelta(hours=1) for earlier, later in pairwise(bars)
    )
    # Values come straight from the fixture's wire strings; provenance pinned.
    assert [b.close for b in bars] == [float(_ohlcv_strs(i)[3]) for i in range(_HISTORY_BARS)]
    assert all(b.symbol == "BTCUSDT" for b in bars)
    assert all(b.timeframe == "1h" for b in bars)
    assert all(b.source == "binance" for b in bars)
    # Four requests: three data pages (6+6+2 at the fixture's page cap), then
    # the empty page that terminates the walk (empty page = end-of-history,
    # never an error — ADR-0052).
    assert len(fake.requests) == 4
    assert all(req["symbol"] == "BTCUSDT" for req in fake.requests)
    assert all(req["interval"] == "1h" for req in fake.requests)
    assert all(req["limit"] == "1000" for req in fake.requests)
    # The cursor advances past each page's last open time; no request ever
    # carries a falsy startTime (Binance would flip into latest-window mode —
    # the plan 0056 phase-2 smoke finding).
    assert all(int(req["startTime"]) >= 1 for req in fake.requests)
    assert int(fake.requests[-1]["startTime"]) == _bar_ts(_HISTORY_BARS - 1) * 1000 + 1


# --- (b) absolute-window semantics: a past-ending window is served verbatim ------


def test_past_ending_historical_window_returns_exactly_its_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Plan 0031 lesson applied from day one: the requested window goes to
    the wire as absolute startTime/endTime, so a window that ended long before
    `now` returns exactly that window's bars — never a now-relative range."""
    adapter, fake = _adapter(monkeypatch)
    start, end = _window(2, 9)

    bars = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)

    assert [int(b.event_ts.timestamp()) for b in bars] == [_bar_ts(i) for i in range(2, 10)]
    assert [b.open for b in bars] == [float(_ohlcv_strs(i)[0]) for i in range(2, 10)]
    # Absolute window on the wire: the first request carries the requested
    # start verbatim, and every request pins the requested end.
    assert fake.requests[0]["startTime"] == str(_bar_ts(2) * 1000)
    assert all(req["endTime"] == str(_bar_ts(9) * 1000) for req in fake.requests)


def test_window_filter_is_inclusive_of_both_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    start, end = _window(3, 3 + 1)  # exactly two bar timestamps

    bars = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)

    assert [int(b.event_ts.timestamp()) for b in bars] == [_bar_ts(3), _bar_ts(4)]


# --- (c) bad rows raise typed validation errors ----------------------------------


@pytest.mark.parametrize("bad_price", ["0.00000000", "-1.50000000"])
def test_zero_or_negative_price_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, bad_price: str
) -> None:
    klines = [_kline(0)]
    klines[0][4] = bad_price  # close
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(klines))
    start, end = _window(0, 1)

    with pytest.raises(BinanceKlinesError, match="non-positive close"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)


def test_non_numeric_price_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    klines = [_kline(0)]
    klines[0][1] = "not-a-price"
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(klines))
    start, end = _window(0, 1)

    with pytest.raises(BinanceKlinesError, match="open"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)


def test_truncated_kline_array_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    klines = [_kline(0)[:4]]  # open time + 3 prices, volume missing
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(klines))
    start, end = _window(0, 1)

    with pytest.raises(BinanceKlinesError, match="malformed kline"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)


def test_non_list_payload_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _static_response(200, json.dumps({"code": -1121, "msg": "Invalid symbol."}).encode())
    adapter, _ = _adapter(monkeypatch, transport=fake)
    start, end = _window(0, 1)

    with pytest.raises(BinanceKlinesError, match="not a list"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)


def test_duplicate_open_time_with_different_values_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same open time, different values is upstream drift — surfaced typed,
    never silently collapsed to either bar."""
    drifted = _kline(1)
    drifted[4] = "555.00000000"
    drifted[2] = "556.00000000"  # keep high >= close so only the dedup trips
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport([_kline(0), _kline(1), drifted]))
    start, end = _window(0, 2)

    with pytest.raises(BinanceKlinesError, match="different values"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)


# --- (d) HTTP 451 → GeoRestrictedError, exactly one attempt ----------------------


def test_451_raises_geo_restricted_error_not_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # max_retries=3: retries are *available*, so one attempt proves 451 is
    # classified permanent, not that the budget ran out.
    client = BinanceSpotHttpClient(source_name="binance-test", cache_ttl_seconds=0.0, max_retries=3)
    fake = _static_response(451, _BODY_451)
    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = BinanceKlinesAdapter(http_client=client)
    start, end = _window(0, 1)

    with pytest.raises(GeoRestrictedError, match="451") as excinfo:
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)

    # The typed geo error, not a generic HTTP/upstream error.
    assert excinfo.type is GeoRestrictedError
    # Not a retry: exactly one transport attempt, zero retries recorded.
    assert fake.attempts == 1
    assert client.stats().retries == 0


def test_429_maps_to_rate_limited_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _static_response(429, b"{}", headers={"Retry-After": "30"})
    adapter, _ = _adapter(monkeypatch, transport=fake)
    start, end = _window(0, 1)

    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)
    assert excinfo.value.retry_after_seconds == 30


def test_5xx_maps_to_upstream_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _static_response(500, b"")
    adapter, _ = _adapter(monkeypatch, transport=fake)
    start, end = _window(0, 1)

    with pytest.raises(UpstreamUnavailableError, match="HTTP 500"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=_LONG_AFTER)


# --- ADR-0033: empty-window classification by recency ----------------------------


def test_leading_edge_empty_window_raises_unknown_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport([]))
    start, end = _window(0, 5)

    with pytest.raises(UnknownSymbolError) as excinfo:
        # `now` == the window's end: the window reaches the leading edge, where
        # a live, listed pair must have data.
        adapter.fetch_ohlcv("NOPEUSDT", "1h", start, end, now=end)
    assert excinfo.value.symbol == "NOPEUSDT"


def test_strictly_historical_empty_window_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pair listed after the requested window has no bars there — that is a
    legitimate end-of-history (ADR-0033), not an unknown symbol."""
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport([]))
    start, end = _window(0, 5)

    bars = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, now=end + timedelta(hours=2))

    assert bars == []


def test_no_now_reference_keeps_the_conservative_leading_edge_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport([]))
    start, end = _window(0, 5)

    with pytest.raises(UnknownSymbolError):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)


# --- interval mapping on the wire -------------------------------------------------


def test_1mo_maps_to_binance_1M_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch, transport=_FakeTransport([]))

    bars = adapter.fetch_ohlcv(
        "BTCUSDT",
        "1mo",
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2020, 6, 1, tzinfo=UTC),
        now=_LONG_AFTER,
    )

    assert bars == []
    assert fake.requests[0]["interval"] == "1M"


def test_1w_maps_to_binance_1w_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch, transport=_FakeTransport([]))

    adapter.fetch_ohlcv(
        "BTCUSDT",
        "1w",
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2020, 6, 1, tzinfo=UTC),
        now=_LONG_AFTER,
    )

    assert fake.requests[0]["interval"] == "1w"


# --- input boundary ----------------------------------------------------------------


def test_caller_bugs_raise_value_error_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, fake = _adapter(monkeypatch)
    start, end = _window(0, 5)

    with pytest.raises(ValueError, match="non-empty"):
        adapter.fetch_ohlcv("  ", "1h", start, end)
    with pytest.raises(ValueError, match="unknown timeframe"):
        adapter.fetch_ohlcv("BTCUSDT", "2h", start, end)
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", start.replace(tzinfo=None), end)
    with pytest.raises(ValueError, match="strictly before"):
        adapter.fetch_ohlcv("BTCUSDT", "1h", end, start)
    assert fake.requests == []  # all rejected before any fetch


def test_symbol_is_normalized_to_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch)
    start, end = _window(0, 2)

    bars = adapter.fetch_ohlcv(" btcusdt ", "1h", start, end, now=_LONG_AFTER)

    assert all(b.symbol == "BTCUSDT" for b in bars)
    assert all(req["symbol"] == "BTCUSDT" for req in fake.requests)
