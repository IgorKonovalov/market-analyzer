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
  The cursor floor is a **nonzero** epoch-millisecond (`_HISTORY_START_MS`):
  Binance treats `startTime=0` as "parameter not sent" and falls back to a
  latest-window mode that ignores the page `limit` — verified live 2026-06-10
  (plan 0056 phase 2 smoke finding), so a falsy cursor never reaches the wire.
  **An empty page is end-of-history, not an error** — full-history-by-pagination
  is confirmed in practice but not doc-guaranteed (ADR-0052 Notes), so the
  terminator is the upstream running out of rows. Points are deduplicated by
  timestamp (a repeated print with the same rate collapses; a repeated print
  with a *different* rate is upstream drift and raises) and returned sorted by
  `ts` ascending.
- `backfill_series` upserts a series' full history into the wired metric store;
  re-runs are idempotent because a same-value re-upsert is a repository no-op.

Phase 3 adds the open-interest family (`binance.open_interest.<SYMBOL>`), which
is **recorded, not fetched** (ADR-0052): upstream's `openInterestHist` serves
only the latest ~1 month, so:

- `seed_open_interest` lands whatever that window holds (period `1h`,
  paginated), hour-truncated; and
- `accrue_open_interest` samples `GET /fapi/v1/openInterest` into the same
  hour-truncated buckets on the Plan 0055 phase-3 write-through pattern: at
  most one point per series per hour, **first write in a bucket wins** —
  across seed, accrual, and re-seed alike, so an overlap never duplicates a
  bucket and never trips the repository's conflict check. The bucket key is
  the upstream payload's own timestamp, never a wall-clock read.

OI values are stored in base-asset units (`sumOpenInterest` / `openInterest`,
the raw observation — Plan 0056 open question resolved to the default; USD
notional is derivable at read time with a bar join).

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
_OPEN_INTEREST_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"
_OPEN_INTEREST_URL = "https://fapi.binance.com/fapi/v1/openInterest"
_SOURCE = "binance-futures"

# Upstream page cap for /fapi/v1/fundingRate (ADR-0052 verified facts).
_PAGE_LIMIT = 1000

# /futures/data/openInterestHist contract (official docs, checked 2026-06-10):
# period enum includes "1h"; `limit` default 30, max 500; only the latest
# ~1 month is retained; with neither startTime nor endTime "the most recent
# data is returned".
_OI_HIST_PERIOD = "1h"
_OI_HIST_PAGE_LIMIT = 500

# Accrual bucket width (Plan 0055 phase-3 pattern): OI timestamps truncate to
# the hour, so each series grows by at most one point per hour.
_OI_BUCKET_SECONDS = 3600

# How far before the latest observed print the seed starts walking: the
# documented retention is "the latest 1 month", bounded from below without a
# wall-clock read (the anchor is upstream's own latest timestamp). One hour
# INSIDE the 30-day mark, not exactly 30 days: verified live 2026-07-06 (Plan
# 0061 phase-4 smoke), upstream now rejects any startTime >= 720h old with
# HTTP 400 and CLAMPS a younger-but-pre-retention startTime to the ~21 days it
# actually holds — so the hour of margin costs nothing (the clamp serves
# everything available) while an exact 30d cursor, anchored on an
# hour-truncated data timestamp that trails wall-clock now, lands on the
# rejected side and the seed 400s forever.
_OI_SEED_WINDOW_MS = (30 * 24 - 1) * 3_600 * 1000

# What a startTime *older than retention* returns is undocumented. A clamping
# upstream (the live-verified /fapi/v1/fundingRate behavior) serves rows from
# the earliest it has; an empty-answering upstream would silently end the walk
# at the window's edge — so on an empty page that cannot be end-of-history the
# cursor skips forward a day at a time until it re-enters the served window.
_OI_SEED_SKIP_MS = 86_400 * 1000

# First-page `startTime` floor: 1 ms after the epoch — at/before any contract
# launch, and never zero. Binance treats `startTime=0` exactly like an absent
# parameter and serves only the most recent window (ignoring `limit`), which
# silently truncates the backfill to ~200 recent prints. Verified live
# 2026-06-10 against /fapi/v1/fundingRate (plan 0056 phase 2 smoke finding):
# `startTime=0&limit=1000` returned the same 200 rows as no params at all,
# while `startTime=1&limit=5` returned the Sep-2019 launch prints.
_HISTORY_START_MS = 1

# The series-id families this adapter produces; the symbol is the suffix
# (`binance.funding_rate.BTCUSDT` → `BTCUSDT`). Registration is still checked
# against the registry — the family prefix alone is not a license.
_FUNDING_SERIES_PREFIX = "binance.funding_rate."
_OI_SERIES_PREFIX = "binance.open_interest."


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
        # Clamp to the nonzero floor: Binance discards a falsy startTime and
        # would silently serve the latest window instead of contract launch.
        cursor_ms = _HISTORY_START_MS if start is None else max(start * 1000, _HISTORY_START_MS)
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

    def seed_open_interest(self, series_id: str) -> int:
        """One-time open-interest seed (Plan 0056 phase 3): collect whatever
        the upstream's ~30-day `openInterestHist` window holds (period `1h`),
        truncate each timestamp to its hour bucket, and insert only the buckets
        the wired store does not already have. First write in a bucket wins —
        across seed, accrual, and re-seed alike — so a re-seed is idempotent
        (inserts 0 over an already-seeded window) and a same-hour overlap with
        the accrual path neither duplicates the bucket nor trips the
        repository's conflict check.

        The walk is anchored on upstream's own latest timestamp (a probe
        without `startTime` returns "the most recent data" per the docs), never
        a wall-clock read, then paginates forward from 30 days before it with a
        never-falsy `startTime` cursor. Raises the same typed taxonomy as
        `fetch_series`."""
        if self._metric_store is None:
            raise ValueError("seed_open_interest requires a wired metric store")
        symbol = _oi_symbol(series_id)
        # Probe: the latest served window, used only as the time anchor (and
        # merged into the seed — its rows are real observations).
        pairs = list(self._oi_hist_page(symbol, start_ms=None))
        if not pairs:
            return 0
        latest_ms = max(ts_ms for ts_ms, _ in pairs)
        cursor_ms = max(latest_ms - _OI_SEED_WINDOW_MS, 1)
        while True:
            page = self._oi_hist_page(symbol, start_ms=cursor_ms)
            if not page:
                if cursor_ms > latest_ms:
                    break  # walked past everything upstream is known to hold
                # Empty answer inside the known window: an upstream that does
                # not clamp a too-early startTime (undocumented either way —
                # see _OI_SEED_SKIP_MS). Skip forward until back in the window.
                cursor_ms += _OI_SEED_SKIP_MS
                continue
            pairs.extend(page)
            last_ms = page[-1][0]
            if last_ms < cursor_ms:
                raise BinanceDerivativesError(
                    f"binance-futures: openInterestHist page for {symbol} did not advance "
                    f"past startTime={cursor_ms} (last timestamp={last_ms}) — refusing to loop",
                )
            cursor_ms = last_ms + 1
        # Hour-truncate; first observation in a bucket wins, deterministically
        # by ascending raw timestamp (stable sort keeps probe-vs-page order for
        # identical timestamps, which carry identical values anyway).
        per_bucket: dict[int, float] = {}
        for ts_ms, value in sorted(pairs, key=lambda pair: pair[0]):
            bucket = ts_ms // 1000 // _OI_BUCKET_SECONDS * _OI_BUCKET_SECONDS
            per_bucket.setdefault(bucket, value)
        buckets = sorted(per_bucket)
        existing = {
            point.ts for point in self._metric_store.range(series_id, buckets[0], buckets[-1])
        }
        new_points = [
            MetricPoint(series_id=series_id, ts=bucket, value=per_bucket[bucket])
            for bucket in buckets
            if bucket not in existing
        ]
        if not new_points:
            return 0
        return self._metric_store.upsert_points(new_points)

    def accrue_open_interest(self, series_id: str) -> int:
        """Sample the current open interest (`GET /fapi/v1/openInterest`) into
        the wired store, hour-truncated on the Plan 0055 phase-3 write-through
        pattern: the bucket key is the upstream payload's own `time` floored to
        the hour, and the first write in a bucket wins — a later same-hour
        sample (or a bucket the seed already landed) is skipped, never a
        duplicate, never a conflict. Returns 1 when a point was written, 0 on
        the same-hour no-op."""
        if self._metric_store is None:
            raise ValueError("accrue_open_interest requires a wired metric store")
        symbol = _oi_symbol(series_id)
        try:
            payload = self._http.get(
                _OPEN_INTEREST_URL, params={"symbol": symbol}, expect_json=True
            ).json()
        except ResilientHttpError as err:
            raise _classify_error(err, symbol, what="open interest") from err
        ts_ms, value = _parse_oi_snapshot(payload, symbol=symbol)
        bucket = ts_ms // 1000 // _OI_BUCKET_SECONDS * _OI_BUCKET_SECONDS
        existing = self._metric_store.as_of(series_id, bucket)
        if existing is not None and existing.ts == bucket:
            return 0  # first write in the hour wins
        return self._metric_store.upsert_points(
            [MetricPoint(series_id=series_id, ts=bucket, value=value)],
        )

    def _oi_hist_page(self, symbol: str, *, start_ms: int | None) -> list[tuple[int, float]]:
        """One `/futures/data/openInterestHist` page as `(timestamp_ms, value)`
        pairs in upstream order. `start_ms=None` deliberately omits `startTime`
        (the documented latest-window probe); a supplied cursor is clamped to
        the nonzero floor so a falsy `startTime` never reaches the wire (the
        phase-2 fundingRate smoke finding, assumed shared)."""
        params: dict[str, str | int | float] = {
            "symbol": symbol,
            "period": _OI_HIST_PERIOD,
            "limit": _OI_HIST_PAGE_LIMIT,
        }
        if start_ms is not None:
            params["startTime"] = max(start_ms, _HISTORY_START_MS)
        try:
            payload = self._http.get(
                _OPEN_INTEREST_HIST_URL, params=params, expect_json=True
            ).json()
        except ResilientHttpError as err:
            raise _classify_error(err, symbol, what="open interest history") from err
        return _parse_oi_hist_page(payload, symbol=symbol)


def _funding_symbol(series_id: str) -> str:
    """Validate the series id against the funding family prefix and the
    registry, and return the contract symbol it names. Both checks fail loudly:
    a foreign family is a caller bug (`ValueError`), an unregistered Binance id
    trips the registry boundary (`UnknownMetricSeriesError` — the registry is
    the schema)."""
    if not series_id.startswith(_FUNDING_SERIES_PREFIX):
        raise ValueError(
            f"fetch_series serves only {_FUNDING_SERIES_PREFIX}* series "
            f"(open interest goes through seed_open_interest / accrue_open_interest), "
            f"not {series_id!r}",
        )
    get_series_spec(series_id)
    return series_id.removeprefix(_FUNDING_SERIES_PREFIX)


def _oi_symbol(series_id: str) -> str:
    """`_funding_symbol`'s open-interest twin: family-prefix + registry checks,
    returning the contract symbol."""
    if not series_id.startswith(_OI_SERIES_PREFIX):
        raise ValueError(
            f"open-interest seed/accrual serves only {_OI_SERIES_PREFIX}* series, "
            f"not {series_id!r}",
        )
    get_series_spec(series_id)
    return series_id.removeprefix(_OI_SERIES_PREFIX)


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


def _parse_oi_hist_page(payload: Any, *, symbol: str) -> list[tuple[int, float]]:
    """Parse one `/futures/data/openInterestHist` page into `(timestamp_ms,
    open_interest)` pairs, preserving upstream order (the cursor advances past
    the page's last row). Only the fields the series needs are required —
    extras (`sumOpenInterestValue`, `CMCCirculatingSupply`) are ignored, an
    added field is not drift. Shape drift on the required fields raises."""
    if not isinstance(payload, list):
        raise BinanceDerivativesError(
            f"binance-futures: openInterestHist payload for {symbol} is not a list",
        )
    page: list[tuple[int, float]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise BinanceDerivativesError(
                f"binance-futures: non-object openInterestHist entry for {symbol}",
            )
        entry_symbol = entry.get("symbol")
        if entry_symbol != symbol:
            raise BinanceDerivativesError(
                f"binance-futures: openInterestHist entry symbol {entry_symbol!r} does not "
                f"match requested {symbol!r}",
            )
        ts_ms = _epoch_ms(entry.get("timestamp"), field="timestamp", symbol=symbol)
        value = _decimal_str(entry.get("sumOpenInterest"), field="sumOpenInterest", symbol=symbol)
        page.append((ts_ms, value))
    return page


def _parse_oi_snapshot(payload: Any, *, symbol: str) -> tuple[int, float]:
    """Parse the `/fapi/v1/openInterest` snapshot into `(time_ms, open_interest)`."""
    if not isinstance(payload, dict):
        raise BinanceDerivativesError(
            f"binance-futures: openInterest payload for {symbol} is not an object",
        )
    entry_symbol = payload.get("symbol")
    if entry_symbol != symbol:
        raise BinanceDerivativesError(
            f"binance-futures: openInterest snapshot symbol {entry_symbol!r} does not "
            f"match requested {symbol!r}",
        )
    ts_ms = _epoch_ms(payload.get("time"), field="time", symbol=symbol)
    value = _decimal_str(payload.get("openInterest"), field="openInterest", symbol=symbol)
    return ts_ms, value


def _epoch_ms(value: Any, *, field: str, symbol: str) -> int:
    """Coerce an upstream epoch-millisecond field to `int`. The official docs
    show `openInterestHist.timestamp` both as a JSON number and as a numeric
    string across revisions, so both encodings are accepted; anything else is
    drift."""
    if isinstance(value, bool):
        pass  # bool is an int subclass but never a timestamp — fall through to raise
    elif isinstance(value, int):
        return value
    elif isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise BinanceDerivativesError(
        f"binance-futures: openInterest entry for {symbol} missing epoch-ms {field!r}",
    )


def _decimal_str(value: Any, *, field: str, symbol: str) -> float:
    """Parse an upstream string-encoded decimal (the wire encoding for rates
    and open interest) to `float`."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise BinanceDerivativesError(
        f"binance-futures: openInterest entry for {symbol} missing decimal-string {field!r}",
    )


def _classify_error(
    err: ResilientHttpError, symbol: str, *, what: str = "funding rates"
) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy. HTTP 451 → geo-restricted (ADR-0052: surfaced, never improvised
    around); 429 → rate-limited (carrying `Retry-After`); any other status or
    transport failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 451:
        return GeoRestrictedError(
            f"binance-futures: geo-restricted (HTTP 451) fetching {what} for "
            f"{symbol} — fapi.binance.com is blocked from this network (ADR-0052)",
        )
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"binance-futures: rate limited (HTTP 429) fetching {what} for {symbol}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"binance-futures: upstream unavailable ({detail}) fetching {what} for {symbol}",
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
