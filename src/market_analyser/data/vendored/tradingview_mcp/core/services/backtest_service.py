# Carved-out from tradingview-mcp@aae08697a0bd0aac486254e5496a122ea94ce79a src/tradingview_mcp/core/services/backtest_service.py — see ADR-0003.
# Carve-out scope: only `_fetch_ohlcv` and the constants `_UA` and `_YF_BASE` are retained.
# Rationale: Plan 0001 phase 2 needs OHLCV-history retrieval; the rest of upstream
# backtest_service.py (strategy engines, metrics, walk-forward) is deferred to the
# backtest plan and is intentionally NOT vendored this week. Authorized in the
# /dev escalation on 2026-05-17 to resolve the plan-vs-reality mismatch
# (upstream yahoo_finance_service.py exposes only quote, not OHLCV history).
"""
Backtesting Service for tradingview-mcp — v3 (v0.7.0)

Pure Python — no pandas, no numpy, no external backtesting libraries.

Carve-out for market-analyser bootstrap: only the data-fetching helper is
retained; strategy/metrics code is dropped until the backtest plan vendors
the full file.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

_UA       = "tradingview-mcp/0.7.0 backtest-bot"
_YF_BASE  = "https://query1.finance.yahoo.com/v8/finance/chart"


# ─── Data Fetching ────────────────────────────────────────────────────────────

def _fetch_ohlcv(symbol: str, period: str, interval: str = "1d") -> list[dict]:
    url = f"{_YF_BASE}/{symbol}?interval={interval}&range={period}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})

    data = None
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    if data is None:
        try:
            from market_analyser.data.vendored.tradingview_mcp.core.services.proxy_manager import build_opener_with_proxy
            opener = build_opener_with_proxy(_UA)
            with opener.open(url, timeout=18) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Both direct and proxy connections failed: {e}")

    result     = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    q          = result["indicators"]["quote"][0]
    date_fmt   = "%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d"

    candles = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c):
            continue
        candles.append({
            "date":   datetime.fromtimestamp(ts, tz=timezone.utc).strftime(date_fmt),
            "open":   round(o, 4),
            "high":   round(h, 4),
            "low":    round(l, 4),
            "close":  round(c, 4),
            "volume": v or 0,
        })
    return candles
