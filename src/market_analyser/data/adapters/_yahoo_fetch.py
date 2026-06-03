"""In-house Yahoo Chart OHLCV fetcher.

Builds the Yahoo chart request URL and parses the response into raw OHLCV row
dicts (keys: ``date``, ``open``, ``high``, ``low``, ``close``, ``volume``); the
caller promotes these into validated
:class:`~market_analyser.data.types.Bar` objects.

Per Plan 0009 phase 3 (ADR-0019) the request itself is issued through the shared
:class:`~market_analyser.data._http.ResilientHttpClient` — this module no longer
opens sockets directly. It builds the URL and parses the chart payload; the
resilience concerns (transient-failure handling, timeouts, concurrency,
proxying) live entirely in the shared client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.data.timeframes import yahoo_interval_uses_intraday_timestamp

_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"


def _fetch_yahoo_ohlcv(
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1d",
    *,
    client: ResilientHttpClient,
) -> list[dict[str, Any]]:
    """Fetch OHLCV rows from Yahoo's chart API for ``symbol`` over the absolute
    window ``[start, end]``, issuing the request through ``client``.

    The window is passed verbatim via Yahoo's absolute ``period1``/``period2``
    (Unix-second) parameters rather than the now-relative ``range=`` — so a
    window that ends in the past (Plan 0030 backward paging, Plan 0031) is
    fetched as requested instead of being clamped to the most recent N days.

    Returns one dict per bar (keys: ``date`` str, ``open`` / ``high`` / ``low`` /
    ``close`` float, ``volume`` int). ``date`` is formatted ``%Y-%m-%d`` for daily
    bars and ``%Y-%m-%d %H:%M`` for intraday. Rows with any of open/high/low/close
    missing are skipped.
    """
    # Percent-encode the symbol into its path segment: an un-encoded space (e.g.
    # a mistyped "BTC USD") otherwise raises http.client.InvalidURL before the
    # request leaves the process. Unreserved chars (incl. '-' in "BTC-USD") are
    # left untouched by quote(), so existing symbols are unchanged.
    period1 = int(start.timestamp())
    period2 = int(end.timestamp())
    url = (
        f"{_YF_BASE}/{quote(symbol, safe='')}"
        f"?period1={period1}&period2={period2}&interval={interval}"
    )
    response = client.get(url, expect_json=True)
    return _parse_chart_payload(response.json(), interval)


def _parse_chart_payload(payload: Any, interval: str) -> list[dict[str, Any]]:
    """Parse a Yahoo chart payload into OHLCV row dicts.

    Yahoo returns 200 OK even for failures, signalling them in the body: a
    populated ``chart.error`` envelope or a null/empty ``chart.result``. Those —
    and any structurally broken payload — raise `UpstreamUnavailableError` rather
    than escaping as a raw ``KeyError``/``TypeError`` (→ 500). A *well-formed but
    empty* result (no ``timestamp`` key, which Yahoo returns for a window with no
    data) yields ``[]``; the adapter's empty-response heuristic decides whether
    that is an unknown symbol or a legitimately-empty window.
    """
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise UpstreamUnavailableError("yahoo: malformed chart payload (no 'chart' object)")
    if chart.get("error") is not None:
        raise UpstreamUnavailableError(f"yahoo: chart error envelope: {chart['error']!r}")
    results = chart.get("result")
    if not results:
        raise UpstreamUnavailableError("yahoo: chart payload has null/empty 'result'")
    result = results[0]
    timestamps = result.get("timestamp")
    if not timestamps:
        # Well-formed result, no bars for the window — let the caller classify.
        return []
    quote = result["indicators"]["quote"][0]
    intraday = yahoo_interval_uses_intraday_timestamp(interval)
    date_fmt = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"

    rows: list[dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        o = quote["open"][i]
        h = quote["high"][i]
        low = quote["low"][i]
        c = quote["close"][i]
        v = quote["volume"][i]
        if None in (o, h, low, c):
            continue
        o = round(o, 4)
        h = round(h, 4)
        low = round(low, 4)
        c = round(c, 4)
        # Reconcile the OHLC envelope. Yahoo occasionally emits a bar — almost
        # always the current, still-forming bar of a 24/7 market like BTC-USD —
        # whose close or open sits just outside the recorded [low, high]: the
        # high/low arrays lag the latest tick, and independent 4-dp rounding can
        # nudge two near-equal values across the boundary. Since `high` is by
        # definition the period maximum and `low` the minimum, widen the envelope
        # to enclose all four prices instead of letting one glitchy bar fail
        # `Bar` validation and 422 the entire chart load.
        hi = max(o, h, low, c)
        lo = min(o, h, low, c)
        rows.append(
            {
                "date": datetime.fromtimestamp(ts, tz=UTC).strftime(date_fmt),
                "open": o,
                "high": hi,
                "low": lo,
                "close": c,
                "volume": v or 0,
            },
        )
    return rows
