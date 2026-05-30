"""Yahoo Finance live-quote adapter (Plan 0019).

Implements the stubbed `get_quote` against Yahoo's `/v8/finance/chart` endpoint —
the same endpoint family the OHLCV fetcher uses (`_yahoo_fetch.py`), read for its
`meta` block rather than its bar series. The `meta` block carries the live fields
a quote needs (price, previous close, day range, 52-week range, currency, market
state, volume) without a separate, crumb-gated quote endpoint, keeping the single
HTTP path ADR-0019 requires.

`change_pct` is *derived* from `previous_close` (`price/previous_close - 1`) rather
than read from Yahoo's `regularMarketChangePercent`, per the Plan 0019 risk
decision: one consistent derivation across the data layer instead of trusting an
upstream field whose basis (regular vs extended session) drifts.

Per ADR-0007 this adapter is package-internal: downstream code imports the
`MarketDataProvider` Protocol, not this class.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.types import Quote

_YF_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Quotes are live: a short TTL collapses a burst of "what's X trading at" calls
# into one upstream hit without serving a stale price. Distinct from the OHLCV
# client (store-less — bars are cached cross-session in SQLite).
_CACHE_TTL_SECONDS = 30.0
_REQUEST_TIMEOUT_SECONDS = 15.0
_SOURCE = "yahoo"


class _FetchQuoteFn(Protocol):
    def __call__(self, symbol: str, /) -> dict[str, Any]: ...


class YahooQuoteAdapter:
    """Adapter over Yahoo's chart `meta` block. Returns a validated `Quote`."""

    def __init__(
        self,
        fetcher: _FetchQuoteFn | None = None,
        http_client: ResilientHttpClient | None = None,
    ) -> None:
        self._client = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="yahoo-quote",
                cache_ttl_seconds=_CACHE_TTL_SECONDS,
                request_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
            )
        )
        self._fetch: _FetchQuoteFn = fetcher if fetcher is not None else self._default_fetch

    def _default_fetch(self, symbol: str) -> dict[str, Any]:
        return _fetch_yahoo_quote(symbol, client=self._client)

    def get_quote(self, symbol: str) -> Quote:
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        try:
            meta = self._fetch(symbol)
        except ResilientHttpError as err:
            raise _classify_quote_error(err, symbol) from err
        # An empty/priceless meta block is Yahoo's shape for a symbol it does not
        # carry (chart.result is null) — typed as unknown-symbol, mirroring the
        # OHLCV adapter's empty-rows path (Plan 0013 vocabulary).
        if not meta or _get_float(meta.get("regularMarketPrice")) is None:
            raise UnknownSymbolError(
                f"yahoo: no quote for {symbol.upper()!r} — symbol is likely unknown or unlisted",
                symbol=symbol.upper(),
            )
        return _meta_to_quote(symbol, meta)


def _meta_to_quote(symbol: str, meta: dict[str, Any]) -> Quote:
    """Map a Yahoo chart `meta` block onto a validated `Quote`. Numeric fields the
    upstream omits stay `None`; `currency`/`market_state` default to ``""``."""
    price = _get_float(meta.get("regularMarketPrice"))
    assert price is not None  # guarded by the caller's unknown-symbol check
    previous_close = _get_float(meta.get("previousClose")) or _get_float(
        meta.get("chartPreviousClose")
    )
    change_pct: float | None = None
    if previous_close:  # not None and not 0 — avoids a divide-by-zero
        change_pct = (price / previous_close - 1.0) * 100.0
    return Quote(
        symbol=symbol.upper(),
        price=price,
        as_of=_as_of_from_meta(meta),
        source=_SOURCE,
        change_pct=change_pct,
        previous_close=previous_close,
        day_high=_get_float(meta.get("regularMarketDayHigh")),
        day_low=_get_float(meta.get("regularMarketDayLow")),
        week52_high=_get_float(meta.get("fiftyTwoWeekHigh")),
        week52_low=_get_float(meta.get("fiftyTwoWeekLow")),
        currency=str(meta.get("currency") or ""),
        market_state=_normalise_market_state(meta.get("marketState")),
        volume=_get_float(meta.get("regularMarketVolume")),
    )


def _as_of_from_meta(meta: dict[str, Any]) -> datetime:
    """The quote's upstream timestamp from `regularMarketTime` (epoch seconds),
    normalised to UTC. Falls back to the wall clock only when Yahoo omits it (the
    `_now` seam is monkeypatchable so tests stay deterministic)."""
    ts = meta.get("regularMarketTime")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts), tz=UTC)
    return _now()


def _normalise_market_state(value: Any) -> str:
    """Collapse Yahoo's six market-state values onto the plan's four-value set.

    Yahoo emits REGULAR / PRE / PREPRE / POST / POSTPOST / CLOSED; the extended
    pre/post sub-phases fold into PRE/POST. An unrecognised/missing value yields
    ``""`` (the `Quote.market_state` default)."""
    state = str(value or "").upper()
    if state == "REGULAR":
        return "REGULAR"
    if state in ("PRE", "PREPRE"):
        return "PRE"
    if state in ("POST", "POSTPOST"):
        return "POST"
    if state == "CLOSED":
        return "CLOSED"
    return ""


def _get_float(value: Any) -> float | None:
    """Coerce a Yahoo numeric field to `float`, or `None` if absent/non-numeric.
    `bool` is rejected (it is an `int` subclass but never a valid price)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fetch_yahoo_quote(symbol: str, *, client: ResilientHttpClient) -> dict[str, Any]:
    """Fetch the chart `meta` block for ``symbol`` through ``client``.

    Requests a minimal `1d`/`1d` window — only the `meta` block is read, so the
    (single) bar it returns is ignored. Returns ``{}`` when the payload carries no
    usable result (Yahoo's unknown-symbol shape); the caller types that as
    `UnknownSymbolError`.
    """
    # Percent-encode the symbol into the path segment so a stray space (e.g. a
    # mistyped "BTC USD") cannot raise InvalidURL before the request leaves the
    # process; unreserved chars (incl. '-' in "BTC-USD") are untouched.
    url = f"{_YF_CHART_BASE}/{quote(symbol, safe='')}?interval=1d&range=1d"
    response = client.get(url, expect_json=True)
    return _parse_quote_meta(response.json())


def _parse_quote_meta(payload: Any) -> dict[str, Any]:
    """Pull `chart.result[0].meta` out of a Yahoo chart payload, defensively.

    Any structural surprise (non-dict payload, null result, missing meta) degrades
    to ``{}`` rather than raising — the adapter treats an empty meta as an unknown
    symbol, the same way the search fetcher treats a missing `quotes` array."""
    if not isinstance(payload, dict):
        return {}
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return {}
    result = chart.get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return {}
    meta = result[0].get("meta")
    return meta if isinstance(meta, dict) else {}


def _classify_quote_error(err: ResilientHttpError, symbol: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy, mirroring the OHLCV/search seams. HTTP 429 → rate-limited (carrying
    `Retry-After`); any other status or transport failure → upstream-unavailable.
    The unknown-symbol path is the empty-meta case, not an HTTP error."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"yahoo: rate limited (HTTP 429) quoting {symbol.upper()!r}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"yahoo: upstream unavailable ({detail}) quoting {symbol.upper()!r}",
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


def _now() -> datetime:
    """Wall-clock seam for the `as_of` fallback, monkeypatched by tests when a
    fixture omits `regularMarketTime` (cf. `default_provider._now`)."""
    return datetime.now(tz=UTC)


__all__ = ["YahooQuoteAdapter"]
