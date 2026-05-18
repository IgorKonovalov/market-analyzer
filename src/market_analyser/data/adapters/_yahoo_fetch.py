"""In-house Yahoo Chart OHLCV fetcher.

A small ``urllib`` + JSON wrapper over Yahoo's chart API. Returns a list of
raw OHLCV row dicts (keys: ``date``, ``open``, ``high``, ``low``, ``close``,
``volume``); the caller is responsible for promoting these into validated
:class:`~market_analyser.data.types.Bar` objects.

Per ADR-0009 this replaces the previous out-of-repo carve-out. No proxy
fallback — restoring it is a follow-up plan if Yahoo rate-limits us in
production.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from typing import Any

from market_analyser import __version__

_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_USER_AGENT = f"market-analyser/{__version__}"
_TIMEOUT_SECONDS = 15


def _fetch_yahoo_ohlcv(
    symbol: str,
    period: str,
    interval: str = "1d",
) -> list[dict[str, Any]]:
    """Fetch OHLCV rows from Yahoo's chart API for ``symbol`` over ``period``.

    Returns one dict per bar (keys: ``date`` str, ``open`` / ``high`` /
    ``low`` / ``close`` float, ``volume`` int). ``date`` is formatted
    ``%Y-%m-%d`` for daily bars and ``%Y-%m-%d %H:%M`` for intraday. Rows
    with any of open/high/low/close missing are skipped.
    """
    url = f"{_YF_BASE}/{symbol}?interval={interval}&range={period}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        payload: Any = json.loads(resp.read().decode("utf-8"))

    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    date_fmt = "%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d"

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
            }
        )
    return rows
