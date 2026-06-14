"""CoinMetrics community adapter — Plan 0057 phase 2 (ADR-0053, ADR-0051, ADR-0019).

Keyless daily MVRV history from `community-api.coinmetrics.io`. The phase-1 probe
(2026-06-14, from the user's network) established the coverage this adapter is
scoped to: `CapMVRVCur` is available keyless with full daily history back to
**2011-12-29** (verified `0.85308817` on that date), while `CapRealUSD` and
`SOPR` are `forbidden` without paid credentials. So this source produces exactly
**one** registered series — `coinmetrics.btc.mvrv` — not the three ADR-0053
originally anticipated (its probe-outcome section records the reduction).

`fetch_series` walks the `timeseries/asset-metrics` endpoint for `CapMVRVCur`
(BTC, `frequency=1d`) by **advancing a `start_time` cursor**, the same shape the
Binance funding-rate backfill uses (Plan 0056): each request bounds the window
`[cursor, end_time]`, which the probe found returns rows in **ascending** time
order (the endpoint serves descending-from-latest when the window is *not*
`end_time`-bounded), and the cursor advances one second past each page's last
observation. An empty page is end-of-history, not an error. Points are
deduplicated by timestamp (a repeated observation with the same value collapses;
a repeated one with a *different* value is upstream drift and raises) and
returned sorted by `ts` ascending. `backfill_series` upserts the full history;
re-runs are idempotent because a same-value re-upsert is a repository no-op, and
an incremental update is just a backfill from a later `start`.

Pacing: the community tier documents **10 requests / 6-second window**.
`_RatePacer` enforces that ceiling — at most `max_requests` requests per rolling
`window_seconds`, sleeping exactly long enough at a burst boundary for the oldest
in-window request to age out (one pause per boundary). At the daily-MVRV scale a
full backfill fits in a single large page, so the pacer rarely fires in practice;
it exists so a deeper or chunked walk stays inside the budget. `sleep`/`clock`
are injected (defaulting to the stdlib) so the pacing is pinned against a fake
clock without a test ever waiting.

A `ResilientHttpError` (exhausted retries / permanent failure) is translated into
the typed `UpstreamDataError` taxonomy (429 → rate-limited, else unavailable). A
shape-broken 2xx payload raises `CoinMetricsError`.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol / composition root, never by importing this class.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.metric_series import (
    SERIES_COINMETRICS_BTC_MVRV,
    MetricPoint,
    get_series_spec,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_ASSET_METRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_SOURCE = "coinmetrics-community"

# The single metric this community-tier source carries (phase-1 probe): the
# others (CapRealUSD, SOPR) are paywalled keyless. `coinmetrics.btc.mvrv` is the
# one registered series; the metric column on the wire is `CapMVRVCur`.
_ASSET = "btc"
_METRIC = "CapMVRVCur"
_FREQUENCY = "1d"

# Large page size so a daily backfill (~5k rows over ~14 years) lands in one or
# two pages. The cursor-advance walk handles however many pages it takes; this
# only caps the per-request row count.
_PAGE_SIZE = 10_000

# Earliest history the probe confirmed (2011-12-29). Used as the default cursor
# floor so a full backfill starts at the first observation rather than relying on
# the endpoint's default ordering.
_HISTORY_START = datetime(2011, 12, 29, tzinfo=UTC)

# A fixed far-future upper bound for the window. The probe found the endpoint
# returns ascending order only when the window is `end_time`-bounded, so every
# request carries an end_time; a constant (never a wall-clock read) keeps the
# walk deterministic, and the endpoint clips it to the latest data it has.
_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)

# Documented community rate budget (ADR-0053 Notes): 10 requests / 6-second
# window.
_RATE_MAX_REQUESTS = 10
_RATE_WINDOW_SECONDS = 6.0

# CoinMetrics encodes `time` as UTC ISO-8601 with nanosecond precision and a 'Z'
# suffix (e.g. "2011-12-29T00:00:00.000000000Z"). `datetime.fromisoformat`
# rejects >6 fractional digits across the versions we target, so parse the
# components directly — version-independent and offset-free (always UTC).
_CM_TIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z?$",
)


class CoinMetricsError(ValueError):
    """The upstream 2xx payload broke shape (no `data` list, a non-object entry,
    a missing/non-numeric metric value, an unparseable timestamp, a foreign
    asset, or a non-advancing cursor) — raised at the adapter boundary before
    anything reaches the store. Upstream drift surfaces typed, never as a
    silently-skipped point."""


class _RatePacer:
    """Caps wire requests to `max_requests` per rolling `window_seconds`.

    `before_request` is called before every request: it drops timestamps older
    than the window, and if the window is already full, sleeps exactly long
    enough for the oldest in-window request to age out — exactly one pause per
    burst boundary. `sleep`/`clock` are injected so tests pin the pacing against
    a fake clock without waiting (the Plan 0034 spacing-test shape).
    """

    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        self._max = max_requests
        self._window = window_seconds
        self._sleep = sleep
        self._clock = clock
        self._stamps: deque[float] = deque()

    def before_request(self) -> None:
        now = self._clock()
        self._evict(now)
        if len(self._stamps) >= self._max:
            wait = self._window - (now - self._stamps[0])
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
                self._evict(now)
        self._stamps.append(now)

    def _evict(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= self._window:
            self._stamps.popleft()


class CoinMetricsCommunityAdapter:
    """Fetches the daily BTC MVRV history from the CoinMetrics community API."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        metric_store: MetricPointsRepository | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        requests_per_window: int = _RATE_MAX_REQUESTS,
        window_seconds: float = _RATE_WINDOW_SECONDS,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name=_SOURCE,
                # History pages are one-shot reads; caching them buys nothing.
                cache_ttl_seconds=0.0,
            )
        )
        self._metric_store = metric_store
        self._pacer = _RatePacer(
            max_requests=requests_per_window,
            window_seconds=window_seconds,
            sleep=sleep,
            clock=clock,
        )

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[MetricPoint]:
        """`MetricSeriesSource` (ADR-0051): the daily MVRV history for the
        `coinmetrics.btc.mvrv` series, paginated by advancing a `start_time`
        cursor from 2011-12-29 (or from `start`), clipped to the inclusive
        `[start, end]` epoch-second window, sorted by `ts` ascending and
        deduplicated by `ts`.

        Raises `RateLimitedError` on HTTP 429, `UpstreamUnavailableError` on
        other upstream exhaustion, `CoinMetricsError` on a shape-broken payload,
        and `ValueError` / `UnknownMetricSeriesError` for a series id this
        adapter does not produce."""
        _check_series_id(series_id)
        cursor_ts = int(_HISTORY_START.timestamp()) if start is None else max(start, 0)
        end_dt = _FAR_FUTURE if end is None else datetime.fromtimestamp(end, tz=UTC)
        points_by_ts: dict[int, MetricPoint] = {}
        while True:
            page = self._fetch_page(cursor_ts, end_dt)
            if not page:
                break  # empty page = end-of-history (ADR-0053 cursor-advance walk)
            for ts, point in page:
                stored = points_by_ts.get(ts)
                if stored is not None and stored.value != point.value:
                    raise CoinMetricsError(
                        f"coinmetrics-community: two MVRV observations at ts={ts} with "
                        f"different values ({stored.value!r} vs {point.value!r})",
                    )
                points_by_ts[ts] = point
            last_ts = page[-1][0]
            if last_ts < cursor_ts:
                raise CoinMetricsError(
                    f"coinmetrics-community: MVRV page did not advance past start_time="
                    f"{cursor_ts} (last time={last_ts}) — refusing to loop",
                )
            cursor_ts = last_ts + 1
        points = [p for _, p in sorted(points_by_ts.items())]
        if start is not None:
            points = [p for p in points if p.ts >= start]
        if end is not None:
            points = [p for p in points if p.ts <= end]
        return points

    def backfill_series(self, start: int | None = None) -> int:
        """Fetch the MVRV history (full, or from `start` for an incremental
        update) and upsert it into the wired metric store, returning how many
        points were newly inserted. Idempotent: a re-run re-upserts the same
        `(series_id, ts, value)` rows, which the repository skips, so the row
        count is unchanged. A historical value that *changed* upstream raises
        `MetricPointConflictError` (ADR-0051 immutability) — a source-quality
        problem to surface, not absorb."""
        if self._metric_store is None:
            raise ValueError("backfill_series requires a wired metric store")
        points = self.fetch_series(SERIES_COINMETRICS_BTC_MVRV, start=start)
        return self._metric_store.upsert_points(points)

    def _fetch_page(self, start_ts: int, end_dt: datetime) -> list[tuple[int, MetricPoint]]:
        """One paced `asset-metrics` page as `(ts, point)` pairs in upstream
        (ascending) order. The `start_time`/`end_time` window bound forces
        ascending order (phase-1 probe); the cursor advances past the page's
        last observation, so order matters here."""
        params: dict[str, str | int | float] = {
            "assets": _ASSET,
            "metrics": _METRIC,
            "frequency": _FREQUENCY,
            "page_size": _PAGE_SIZE,
            "start_time": _iso(datetime.fromtimestamp(start_ts, tz=UTC)),
            "end_time": _iso(end_dt),
        }
        self._pacer.before_request()
        try:
            payload = self._http.get(_ASSET_METRICS_URL, params=params, expect_json=True).json()
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        return _parse_page(payload)


def _check_series_id(series_id: str) -> None:
    """This source produces only `coinmetrics.btc.mvrv`. A foreign id is a caller
    bug (`ValueError`); the registry membership check (`get_series_spec`) is the
    loud-failure half of "the registry is the schema"."""
    if series_id != SERIES_COINMETRICS_BTC_MVRV:
        raise ValueError(
            f"CoinMetricsCommunityAdapter produces only "
            f"{SERIES_COINMETRICS_BTC_MVRV!r}, not {series_id!r}",
        )
    get_series_spec(series_id)


def _parse_page(payload: Any) -> list[tuple[int, MetricPoint]]:
    """Parse one `asset-metrics` page into `(ts, point)` pairs, preserving
    upstream order. Shape drift raises `CoinMetricsError`."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise CoinMetricsError("coinmetrics-community: asset-metrics payload missing 'data' list")
    page: list[tuple[int, MetricPoint]] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise CoinMetricsError("coinmetrics-community: non-object asset-metrics entry")
        asset = entry.get("asset")
        if asset != _ASSET:
            raise CoinMetricsError(
                f"coinmetrics-community: entry asset {asset!r} does not match requested {_ASSET!r}",
            )
        ts = _parse_time(entry.get("time"))
        value = _parse_value(entry.get(_METRIC))
        page.append((ts, MetricPoint(series_id=SERIES_COINMETRICS_BTC_MVRV, ts=ts, value=value)))
    return page


def _parse_time(raw: Any) -> int:
    """Coerce CoinMetrics' nanosecond ISO-8601 UTC `time` to epoch seconds."""
    if not isinstance(raw, str):
        raise CoinMetricsError("coinmetrics-community: entry missing string 'time'")
    match = _CM_TIME_RE.match(raw.strip())
    if match is None:
        raise CoinMetricsError(f"coinmetrics-community: unparseable time {raw!r}")
    year, month, day, hour, minute, second = (int(g) for g in match.groups())
    return int(datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp())


def _parse_value(raw: Any) -> float:
    """Parse the `CapMVRVCur` value. Upstream encodes it as a decimal string; a
    missing/null/non-numeric value is upstream drift, surfaced typed — never a
    silently-dropped point."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise CoinMetricsError(f"coinmetrics-community: entry missing numeric {_METRIC!r} value")
    try:
        return float(raw)
    except ValueError as err:
        raise CoinMetricsError(
            f"coinmetrics-community: non-numeric {_METRIC} {raw!r}",
        ) from err


def _iso(when: datetime) -> str:
    """Format a UTC datetime as the second-precision ISO-8601 'Z' string the
    endpoint accepts for `start_time`/`end_time`."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy. 429 → rate-limited (carrying `Retry-After` when present); any other
    status or transport failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            "coinmetrics-community: rate limited (HTTP 429) fetching MVRV history",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"coinmetrics-community: upstream unavailable ({detail}) fetching MVRV history",
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
    "CoinMetricsCommunityAdapter",
    "CoinMetricsError",
]
