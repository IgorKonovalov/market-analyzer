"""Typed upstream/adapter error taxonomy (Plan 0013 phase 1).

Every external-data adapter (OHLCV, sentiment, news, screener) draws from this
one hierarchy when an *upstream* failure occurs, so callers — the MCP tools, the
backfill coordinator, the renderer via SSE — can branch on a typed reason
("wait and retry" vs "the symbol doesn't exist" vs "the source is down") instead
of parsing a free-form message. The base is named for upstream/adapter failure,
not "backfill", because it is shared across the data layer (not just the OHLCV
backfill path).

Caller bugs (bad timeframe, malformed datetime, non-UTC tz) are NOT upstream
failures — those keep raising `ValueError` at the adapter's input boundary.
"""

from __future__ import annotations

from typing import Literal

FailureReason = Literal[
    "rate_limited",
    "upstream_unavailable",
    "unknown_symbol",
    "history_exceeded",
]


class UpstreamDataError(Exception):
    """Base for upstream/adapter-driven failures across the data layer
    (OHLCV, sentiment, news, screener — not just the backfill path).

    Caller bugs (bad timeframe, malformed datetime) keep raising `ValueError`."""


class RateLimitedError(UpstreamDataError):
    """Upstream returned HTTP 429 or an equivalent throttle signal.

    `retry_after_seconds` carries the upstream's `Retry-After` value when it
    sends one (and is parseable as whole seconds); otherwise `None`. The
    coordinator does not auto-retry — it surfaces this so the agent can wait."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class UpstreamUnavailableError(UpstreamDataError):
    """Upstream connection refused, timed out, or returned 5xx (or any other
    non-429 HTTP failure that exhausted the resilient client's retries)."""


class UnknownSymbolError(UpstreamDataError):
    """The upstream does not have the requested symbol. Two detection paths
    converge on this one type:

    - StockTwits returns a definitive 404 ("not tracked"); and
    - Yahoo accepts the request but returns no rows for a window that reaches the
      leading edge (its `end` within one bar of "now"), where a live, listed name
      must have data — distinguished by window **recency** (ADR-0033), not period
      size. A strictly-historical empty window is a legitimate end-of-history
      (the symbol predates the range, or Yahoo's coverage does) and returns `[]`,
      NOT this error; the recency reference is the provider's `_now`/`as_of` seam.

    The caller treats both identically ("symbol unusable"); `message` conveys
    which upstream and why. Supersedes Plan 0012's `SymbolNotCoveredError`."""

    def __init__(self, message: str, *, symbol: str) -> None:
        super().__init__(message)
        self.symbol = symbol


class GeoRestrictedError(UpstreamDataError):
    """Upstream returned HTTP 451: the endpoint is geo-blocked from this network
    (Binance returns it from restricted locations even for public read-only
    endpoints — ADR-0052). Never retried — the resilient client classifies 451
    as permanent — and never improvised around in an adapter: the caller
    surfaces it so the fallback decision (Bybit / binance.us / network posture)
    is made as an ADR-0052 follow-up, by a human."""


class HistoryExceededError(UpstreamDataError):
    """The requested window reaches further back than the timeframe's `max_history`
    cap (Yahoo serves intraday history for a bounded span only — ~60 days for 15m,
    ~730 for 1h/4h; Plan 0025 / ADR-0028). Distinct from a transient
    `UpstreamUnavailableError`: narrowing the window or using a coarser timeframe
    is the fix, not a retry."""


def failure_reason(err: UpstreamDataError) -> FailureReason:
    """Map a typed upstream error onto the closed `ohlcv.backfill_failed` /
    `partial_reason` vocabulary. Lives here (alongside the error classes) so both
    the data layer and the backfill coordinator share one mapping without either
    reaching into the other."""
    if isinstance(err, RateLimitedError):
        return "rate_limited"
    if isinstance(err, UnknownSymbolError):
        return "unknown_symbol"
    if isinstance(err, HistoryExceededError):
        return "history_exceeded"
    return "upstream_unavailable"


__all__ = [
    "FailureReason",
    "GeoRestrictedError",
    "HistoryExceededError",
    "RateLimitedError",
    "UnknownSymbolError",
    "UpstreamDataError",
    "UpstreamUnavailableError",
    "failure_reason",
]
