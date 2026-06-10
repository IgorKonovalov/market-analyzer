"""Binance USDⓈ-M derivatives adapter — Plan 0056 phase 1 (ADR-0052, ADR-0051, ADR-0019).

Keyless calls to `fapi.binance.com` through `BinanceFuturesHttpClient`, a
`ResilientHttpClient` subclass whose classifier pins the one quirk that matters
here: **HTTP 451 is the geo-restriction response** (Binance returns it from
restricted locations even for public read-only endpoints) and is `PERMANENT` —
never retried, surfaced as the typed `GeoRestrictedError` so the fallback
decision is made as an ADR-0052 follow-up by a human, never improvised in the
adapter.

Phase 1 covers the funding-rate series family (`binance.funding_rate.<SYMBOL>`,
implementing `MetricSeriesSource` per ADR-0051):

- `fetch_series` paginates `GET /fapi/v1/fundingRate` (max 1000 rows/page) from
  contract launch, advancing a `startTime` cursor past each page's last print.
  **An empty page is end-of-history, not an error** — full-history-by-pagination
  is confirmed in practice but not doc-guaranteed (ADR-0052 Notes), so the
  terminator is the upstream running out of rows. Points are deduplicated by
  timestamp (a repeated print with the same rate collapses; a repeated print
  with a *different* rate is upstream drift and raises) and returned sorted by
  `ts` ascending.
- `backfill_series` upserts a series' full history into the wired metric store;
  re-runs are idempotent because a same-value re-upsert is a repository no-op.

Funding rates arrive as decimal strings (e.g. ``"0.00010000"``); they are
parsed with `float(...)` and stored in the REAL (C double) `metric_points`
column, so values round-trip at full precision. `fundingTime` arrives as UTC
epoch milliseconds and is floored to epoch seconds (the `MetricPoint.ts`
currency); funding cadence is whatever spacing the data shows — never assumed
to be 8h outside display hints (Plan 0056 risk note).

A `ResilientHttpError` (exhausted retries / permanent failure) is translated
into the typed `UpstreamDataError` taxonomy (451 → geo-restricted, 429 →
rate-limited, else unavailable). A shape-broken 2xx payload raises
`BinanceDerivativesError`.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol / composition root, never by importing this class.
"""

from __future__ import annotations

from typing import Any

from market_analyser.data._http import (
    ErrorKind,
    HttpResponse,
    ResilientHttpClient,
    ResilientHttpError,
)
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.metric_series import MetricPoint, get_series_spec
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
_SOURCE = "binance-futures"

# Upstream page cap for /fapi/v1/fundingRate (ADR-0052 verified facts).
_PAGE_LIMIT = 1000

# The series-id family this adapter produces in phase 1; the symbol is the
# suffix (`binance.funding_rate.BTCUSDT` → `BTCUSDT`). Registration is still
# checked against the registry — the family prefix alone is not a license.
_FUNDING_SERIES_PREFIX = "binance.funding_rate."


class BinanceDerivativesError(ValueError):
    """The upstream 2xx payload broke shape (non-list body, missing/non-numeric
    per-entry field, a foreign symbol, a non-advancing page cursor, or two
    prints at one timestamp with different rates) — raised at the adapter
    boundary before anything reaches the store. Upstream drift surfaces typed,
    never as a silently-skipped point."""


class BinanceFuturesHttpClient(ResilientHttpClient):
    """`ResilientHttpClient` that pins Binance's geo-restriction response.

    HTTP 451 means the caller's network is geo-blocked (ADR-0052) — a
    structural condition, not a transient fault. The base classifier already
    treats non-429 4xx as `PERMANENT`; the explicit branch makes the
    never-retry guarantee independent of the base policy.
    """

    def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
        if response is not None and response.status_code == 451:
            return ErrorKind.PERMANENT
        return super().classify(exc, response)


class BinanceDerivativesAdapter:
    """Fetches Binance USDⓈ-M derivatives series (phase 1: funding rates)."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        metric_store: MetricPointsRepository | None = None,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else BinanceFuturesHttpClient(
                source_name=_SOURCE,
                # History pages are one-shot reads; caching them buys nothing.
                cache_ttl_seconds=0.0,
            )
        )
        self._metric_store = metric_store

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[MetricPoint]:
        """`MetricSeriesSource` (ADR-0051): the funding-rate history for one
        registered `binance.funding_rate.<SYMBOL>` series, paginated from
        contract launch (or from `start`), clipped to the inclusive
        `[start, end]` epoch-second window, sorted by `ts` ascending and
        deduplicated by `ts`.

        Raises `GeoRestrictedError` on HTTP 451 (never retried),
        `RateLimitedError` on 429, `UpstreamUnavailableError` on other upstream
        exhaustion, `BinanceDerivativesError` on a shape-broken payload, and
        `ValueError` / `UnknownMetricSeriesError` for a series id this adapter
        does not produce."""
        symbol = _funding_symbol(series_id)
        points_by_ts: dict[int, MetricPoint] = {}
        cursor_ms = start * 1000 if start is not None else 0
        while True:
            params: dict[str, str | int | float] = {
                "symbol": symbol,
                "startTime": cursor_ms,
                "limit": _PAGE_LIMIT,
            }
            if end is not None:
                params["endTime"] = end * 1000
            try:
                payload = self._http.get(_FUNDING_RATE_URL, params=params, expect_json=True).json()
            except ResilientHttpError as err:
                raise _classify_error(err, symbol) from err
            page = _parse_page(payload, series_id=series_id, symbol=symbol)
            if not page:
                break  # empty page = end-of-history, not an error (ADR-0052)
            for _ts_ms, point in page:
                stored = points_by_ts.get(point.ts)
                if stored is not None and stored.value != point.value:
                    raise BinanceDerivativesError(
                        f"binance-futures: two funding prints for {symbol} at ts={point.ts} "
                        f"with different rates ({stored.value!r} vs {point.value!r})",
                    )
                points_by_ts[point.ts] = point
            last_ms = page[-1][0]
            if last_ms < cursor_ms:
                raise BinanceDerivativesError(
                    f"binance-futures: funding page for {symbol} did not advance past "
                    f"startTime={cursor_ms} (last fundingTime={last_ms}) — refusing to loop",
                )
            cursor_ms = last_ms + 1
        points = [p for _, p in sorted(points_by_ts.items())]
        if start is not None:
            points = [p for p in points if p.ts >= start]
        if end is not None:
            points = [p for p in points if p.ts <= end]
        return points

    def backfill_series(self, series_id: str) -> int:
        """Fetch a series' full history and upsert it into the wired metric
        store, returning how many points were newly inserted. Idempotent: a
        re-run re-upserts the same `(series_id, ts, value)` rows, which the
        repository skips, so the row count is unchanged. A historical rate that
        *changed* upstream raises `MetricPointConflictError` (ADR-0051
        immutability) — a source-quality problem to surface, not absorb."""
        if self._metric_store is None:
            raise ValueError("backfill_series requires a wired metric store")
        points = self.fetch_series(series_id)
        return self._metric_store.upsert_points(points)


def _funding_symbol(series_id: str) -> str:
    """Validate the series id against the family prefix and the registry, and
    return the contract symbol it names. Both checks fail loudly: a foreign
    family is a caller bug (`ValueError`), an unregistered Binance id trips the
    registry boundary (`UnknownMetricSeriesError` — the registry is the schema)."""
    if not series_id.startswith(_FUNDING_SERIES_PREFIX):
        raise ValueError(
            f"BinanceDerivativesAdapter produces only {_FUNDING_SERIES_PREFIX}* series, "
            f"not {series_id!r}",
        )
    get_series_spec(series_id)
    return series_id.removeprefix(_FUNDING_SERIES_PREFIX)


def _parse_page(
    payload: Any,
    *,
    series_id: str,
    symbol: str,
) -> list[tuple[int, MetricPoint]]:
    """Parse one `/fapi/v1/fundingRate` page into `(fundingTime_ms, point)`
    pairs, preserving upstream order (the cursor advances past the page's last
    print, so order matters). Shape drift raises `BinanceDerivativesError`."""
    if not isinstance(payload, list):
        raise BinanceDerivativesError(
            f"binance-futures: fundingRate payload for {symbol} is not a list",
        )
    page: list[tuple[int, MetricPoint]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise BinanceDerivativesError(
                f"binance-futures: non-object fundingRate entry for {symbol}",
            )
        entry_symbol = entry.get("symbol")
        if entry_symbol != symbol:
            raise BinanceDerivativesError(
                f"binance-futures: fundingRate entry symbol {entry_symbol!r} does not "
                f"match requested {symbol!r}",
            )
        funding_time = entry.get("fundingTime")
        if isinstance(funding_time, bool) or not isinstance(funding_time, int):
            raise BinanceDerivativesError(
                f"binance-futures: fundingRate entry for {symbol} missing integer 'fundingTime'",
            )
        raw_rate = entry.get("fundingRate")
        if not isinstance(raw_rate, str):
            raise BinanceDerivativesError(
                f"binance-futures: fundingRate entry for {symbol} missing string 'fundingRate'",
            )
        try:
            rate = float(raw_rate)
        except ValueError as err:
            raise BinanceDerivativesError(
                f"binance-futures: non-numeric fundingRate {raw_rate!r} for {symbol}",
            ) from err
        page.append(
            (funding_time, MetricPoint(series_id=series_id, ts=funding_time // 1000, value=rate)),
        )
    return page


def _classify_error(err: ResilientHttpError, symbol: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy. HTTP 451 → geo-restricted (ADR-0052: surfaced, never improvised
    around); 429 → rate-limited (carrying `Retry-After`); any other status or
    transport failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 451:
        return GeoRestrictedError(
            f"binance-futures: geo-restricted (HTTP 451) fetching funding rates for "
            f"{symbol} — fapi.binance.com is blocked from this network (ADR-0052)",
        )
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"binance-futures: rate limited (HTTP 429) fetching funding rates for {symbol}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"binance-futures: upstream unavailable ({detail}) fetching funding rates for {symbol}",
    )


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (urllib preserves the upstream's casing)."""
    lowered = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lowered), None)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a `Retry-After` header as whole seconds; the HTTP-date form is
    unsupported (returns None) — the agent gets the rate-limit signal regardless."""
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


__all__ = [
    "BinanceDerivativesAdapter",
    "BinanceDerivativesError",
    "BinanceFuturesHttpClient",
]
