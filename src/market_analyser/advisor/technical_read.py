"""Single-indicator technical reads — the lesser advisory tier (Plan 0074, ADR-0068).

A `technical_read` maps **one** curated regime indicator to a direction by its
textbook mechanical rule and returns a `TechnicalRead`: direction + regime_state +
rationale, with **no** conviction and **no** entry/stop/target levels. The honesty
comes from structural omission, not corroboration — the fused `recommend` tier
(ADR-0029) is untouched, and a read never feeds `fuse()`.

Every rule reads the **last closed bar** of a purely trailing indicator series
(`analysis/indicators.py`), so a read on ``bars[:k]`` equals the read on the full
series as of bar ``k-1`` (anti-lookahead, ADR-0023). The curated set is fixed to
indicators with an unambiguous regime→direction reading; strength/level-only
indicators (ADX, ATR, bare RSI level) are excluded — they imply no direction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, cast

import market_analyser.analysis.indicators as ind
from market_analyser.advisor.models import TechnicalRead
from market_analyser.data.types import Bar

# EMA-stack periods — the canonical trend-classifier stack (analysis/snapshot.py).
EMA_SHORT = 20
EMA_LONG = 50
# Ichimoku classic-default displacement: the cloud under bar `i` is the spans
# computed `displacement` bars ago, `senkou_*[i - ICHIMOKU_DISPLACEMENT]` (the
# trailing displaced read, ADR-0067), matching `analysis/snapshot.py`.
ICHIMOKU_DISPLACEMENT = 26

Direction = Literal["long", "short", "flat"]
IndicatorId = Literal["supertrend", "ema_stack", "macd", "ichimoku"]

# A rule reads the trailing indicator over `bars` and returns the direction call,
# the regime_state read in words, and the mechanical-rule rationale line(s).
Read = tuple[Direction, str, list[str]]
_Rule = Callable[[Sequence[Bar]], Read]


def _last[T](series: Sequence[T | None]) -> T | None:
    """The last defined entry of a trailing indicator series, or ``None`` when the
    indicator is undefined over the whole series (too little history)."""

    for value in reversed(series):
        if value is not None:
            return value
    return None


def _supertrend_read(bars: Sequence[Bar]) -> Read:
    st = _last(ind.supertrend(bars))
    if st is None:
        return (
            "flat",
            "supertrend undefined (too little history)",
            [
                "supertrend has no defined value yet — flat until it is",
            ],
        )
    if st.direction == 1:
        return (
            "long",
            "supertrend direction=+1 (uptrend)",
            [
                "supertrend rule: long while direction == +1 (active band is the lower band)",
            ],
        )
    return (
        "short",
        "supertrend direction=-1 (downtrend)",
        [
            "supertrend rule: short while direction == -1 (active band is the upper band)",
        ],
    )


def _ema_stack_read(bars: Sequence[Bar]) -> Read:
    closes = [b.close for b in bars]
    ema_s = _last(ind.ema(closes, EMA_SHORT))
    ema_l = _last(ind.ema(closes, EMA_LONG))
    if ema_s is None or ema_l is None:
        return (
            "flat",
            f"ema-stack undefined (needs >= {EMA_LONG} bars)",
            [
                f"ema{EMA_SHORT}/ema{EMA_LONG} not both defined yet — flat",
            ],
        )
    close = closes[-1]
    state = f"ema{EMA_SHORT}={ema_s:.4g} vs ema{EMA_LONG}={ema_l:.4g}, close={close:.4g}"
    if ema_s > ema_l and close >= ema_s:
        return (
            "long",
            f"bullish stack ({state})",
            [
                f"ema-stack rule: long when ema{EMA_SHORT} > ema{EMA_LONG} "
                f"and close >= ema{EMA_SHORT}",
            ],
        )
    if ema_s < ema_l and close <= ema_s:
        return (
            "short",
            f"bearish stack ({state})",
            [
                f"ema-stack rule: short when ema{EMA_SHORT} < ema{EMA_LONG} "
                f"and close <= ema{EMA_SHORT}",
            ],
        )
    return (
        "flat",
        f"mixed stack ({state})",
        [
            "ema-stack rule: flat when the stack and close do not agree on a side",
        ],
    )


def _macd_read(bars: Sequence[Bar]) -> Read:
    mv = _last(ind.macd([b.close for b in bars]))
    if mv is None:
        return (
            "flat",
            "macd undefined (too little history)",
            [
                "macd histogram not defined yet — flat",
            ],
        )
    hist = mv.histogram
    state = f"histogram={hist:.4g}"
    if hist > 0:
        return (
            "long",
            f"bullish momentum ({state})",
            [
                "macd rule: long when histogram > 0",
            ],
        )
    if hist < 0:
        return (
            "short",
            f"bearish momentum ({state})",
            [
                "macd rule: short when histogram < 0",
            ],
        )
    return (
        "flat",
        f"flat momentum ({state})",
        [
            "macd rule: flat when histogram == 0",
        ],
    )


def _ichimoku_read(bars: Sequence[Bar]) -> Read:
    series = ind.ichimoku(bars)
    i = len(bars) - 1
    cur = series[i] if i >= 0 else None
    cloud_idx = i - ICHIMOKU_DISPLACEMENT
    cloud = series[cloud_idx] if cloud_idx >= 0 else None
    if cur is None or cloud is None:
        return (
            "flat",
            "ichimoku undefined (cloud or line not defined yet)",
            [
                "ichimoku cloud/line not both defined yet — flat",
            ],
        )
    close = bars[i].close
    cloud_high = max(cloud.senkou_a, cloud.senkou_b)
    cloud_low = min(cloud.senkou_a, cloud.senkou_b)
    tk = (
        "tenkan > kijun"
        if cur.tenkan > cur.kijun
        else ("tenkan < kijun" if cur.tenkan < cur.kijun else "tenkan == kijun")
    )
    if close > cloud_high and cur.tenkan > cur.kijun:
        return (
            "long",
            f"price above cloud, {tk}",
            [
                "ichimoku rule: long when close > cloud (displaced) and tenkan > kijun",
            ],
        )
    if close < cloud_low and cur.tenkan < cur.kijun:
        return (
            "short",
            f"price below cloud, {tk}",
            [
                "ichimoku rule: short when close < cloud (displaced) and tenkan < kijun",
            ],
        )
    return (
        "flat",
        f"price in/against cloud, {tk}",
        [
            "ichimoku rule: flat when price is inside the cloud "
            "or TK disagrees with the cloud side",
        ],
    )


# Curated regime→direction registry (ADR-0068). `ichimoku` is registered only when
# the `ichimoku()` function exists (Plan 0073 phase 1) — it does today, so all four
# are present; the guard keeps the plan's staged-availability contract honest.
_REGISTRY: dict[str, _Rule] = {
    "supertrend": _supertrend_read,
    "ema_stack": _ema_stack_read,
    "macd": _macd_read,
}
if hasattr(ind, "ichimoku"):
    _REGISTRY["ichimoku"] = _ichimoku_read


def eligible_indicators() -> tuple[str, ...]:
    """The curated indicator ids available for a technical read, in a stable order."""

    return tuple(sorted(_REGISTRY))


def technical_read(
    *,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    indicator_id: str,
) -> TechnicalRead:
    """The single-indicator technical read (ADR-0068).

    Computes ``indicator_id``'s trailing regime over ``bars`` and maps it to a
    direction by the curated mechanical rule, as of the **last closed bar**. Returns
    a `TechnicalRead` with direction + regime_state + rationale and **no** conviction
    or levels. Raises `ValueError` on an unknown ``indicator_id`` (boundary
    validation, listing the known set) or on empty ``bars`` (no as-of bar to read).
    """

    rule = _REGISTRY.get(indicator_id)
    if rule is None:
        raise ValueError(
            f"unknown indicator_id {indicator_id!r}; "
            f"known technical-read indicators: {list(eligible_indicators())}"
        )
    if not bars:
        raise ValueError("technical_read requires at least one bar")

    direction, regime_state, rationale = rule(bars)
    return TechnicalRead(
        symbol=symbol,
        timeframe=timeframe,
        as_of_bar_ts=bars[-1].event_ts,
        indicator_id=cast(IndicatorId, indicator_id),
        direction=direction,
        regime_state=regime_state,
        rationale=rationale,
    )


__all__ = ["eligible_indicators", "technical_read"]
