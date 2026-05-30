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
from market_analyser.data.timeframes import yahoo_interval_uses_intraday_timestamp

_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"


def _fetch_yahoo_ohlcv(
    symbol: str,
    period: str,
    interval: str = "1d",
    *,
    client: ResilientHttpClient,
) -> list[dict[str, Any]]:
    """Fetch OHLCV rows from Yahoo's chart API for ``symbol`` over ``period``,
    issuing the request through ``client``.

    Returns one dict per bar (keys: ``date`` str, ``open`` / ``high`` / ``low`` /
    ``close`` float, ``volume`` int). ``date`` is formatted ``%Y-%m-%d`` for daily
    bars and ``%Y-%m-%d %H:%M`` for intraday. Rows with any of open/high/low/close
    missing are skipped.
    """
    # Percent-encode the symbol into its path segment: an un-encoded space (e.g.
    # a mistyped "BTC USD") otherwise raises http.client.InvalidURL before the
    # request leaves the process. Unreserved chars (incl. '-' in "BTC-USD") are
    # left untouched by quote(), so existing symbols are unchanged.
    url = f"{_YF_BASE}/{quote(symbol, safe='')}?interval={interval}&range={period}"
    response = client.get(url, expect_json=True)
    return _parse_chart_payload(response.json(), interval)


def _parse_chart_payload(payload: Any, interval: str) -> list[dict[str, Any]]:
    """Parse a Yahoo chart payload into OHLCV row dicts."""
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
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
        rows.append(
            {
                "date": datetime.fromtimestamp(ts, tz=UTC).strftime(date_fmt),
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": v or 0,
            },
        )
    return rows
