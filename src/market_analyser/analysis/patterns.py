"""Candlestick pattern detectors (Plan 0018 phase 2, ADR-0023).

Pure, trailing detectors for the vocabulary the `market-analyst` skill names:
doji, hammer, hanging man, bullish/bearish engulfing, morning/evening star, three
white soldiers / three black crows, dark cloud cover, piercing line,
bullish/bearish harami, and marubozu. Each detector reads only `bars[0..=i]` — a
pattern reported at bar `i` never depends on `bars[i+1..]` — so truncating the
series to `bars[0..=i]` reproduces the same hit.

`detect_patterns(bars)` returns every hit sorted by `(bar_index, pattern)`, a
deterministic order. Body/shadow-ratio thresholds are named module constants
(owned and tunable): candlestick recognition is heuristic, so these encode *our*
definitions — the tests assert internal consistency, not agreement with any
external library.
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.analysis.types import Direction, PatternHit
from market_analyser.data.types import Bar

# --- Tunable thresholds (heuristic; owned by this module) ------------------- #
DOJI_BODY_RATIO = 0.1  # body <= 10% of range -> doji
HAMMER_LOWER_SHADOW_RATIO = 2.0  # lower shadow >= 2x body
HAMMER_MAX_UPPER_SHADOW_RATIO = 0.1  # upper shadow <= 10% of range
HAMMER_MAX_BODY_RATIO = 0.4  # body <= 40% of range (small body near the top)
MARUBOZU_MAX_SHADOW_RATIO = 0.05  # each shadow <= 5% of range
MARUBOZU_MIN_BODY_RATIO = 0.8  # body >= 80% of range
SMALL_BODY_RATIO = 0.3  # body <= 30% of range -> "small" (star bodies)
LARGE_BODY_RATIO = 0.6  # body >= 60% of range -> "large" (harami/star anchors)
SOLDIER_MAX_UPPER_SHADOW_RATIO = 0.3  # soldiers/crows: modest shadow into the trend
TREND_LOOKBACK = 3  # bars of prior context for hammer vs hanging man


# --- Per-bar geometry helpers ----------------------------------------------- #
def _body(b: Bar) -> float:
    return abs(b.close - b.open)


def _range(b: Bar) -> float:
    return b.high - b.low


def _upper_shadow(b: Bar) -> float:
    return b.high - max(b.open, b.close)


def _lower_shadow(b: Bar) -> float:
    return min(b.open, b.close) - b.low


def _is_bull(b: Bar) -> bool:
    return b.close > b.open


def _is_bear(b: Bar) -> bool:
    return b.close < b.open


def _body_mid(b: Bar) -> float:
    return (b.open + b.close) / 2


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _prior_trend(bars: Sequence[Bar], i: int, lookback: int = TREND_LOOKBACK) -> str:
    """Direction of the closes strictly before bar `i` over `lookback` bars.

    Reads only `bars[0..=i-1]`. Returns ``"up"``, ``"down"``, or ``"flat"`` (also
    ``"flat"`` when there is not enough history)."""

    j = i - lookback
    if j < 0:
        return "flat"
    start, end = bars[j].close, bars[i - 1].close
    if end > start:
        return "up"
    if end < start:
        return "down"
    return "flat"


# --- Single-bar detectors --------------------------------------------------- #
def _doji(bars: Sequence[Bar], i: int) -> PatternHit | None:
    b = bars[i]
    r = _range(b)
    if r <= 0:
        return None
    if _body(b) <= DOJI_BODY_RATIO * r:
        return PatternHit(
            bar_index=i, pattern="doji", direction="neutral", strength=_clamp01(1 - _body(b) / r)
        )
    return None


def _is_hammer_shape(b: Bar) -> bool:
    r, body = _range(b), _body(b)
    if r <= 0 or body <= 0:
        return False
    return (
        _lower_shadow(b) >= HAMMER_LOWER_SHADOW_RATIO * body
        and _upper_shadow(b) <= HAMMER_MAX_UPPER_SHADOW_RATIO * r
        and body <= HAMMER_MAX_BODY_RATIO * r
    )


def _hammer(bars: Sequence[Bar], i: int) -> PatternHit | None:
    b = bars[i]
    if _is_hammer_shape(b) and _prior_trend(bars, i) == "down":
        return PatternHit(
            bar_index=i,
            pattern="hammer",
            direction="bullish",
            strength=_clamp01(_lower_shadow(b) / _range(b)),
        )
    return None


def _hanging_man(bars: Sequence[Bar], i: int) -> PatternHit | None:
    b = bars[i]
    if _is_hammer_shape(b) and _prior_trend(bars, i) == "up":
        return PatternHit(
            bar_index=i,
            pattern="hanging_man",
            direction="bearish",
            strength=_clamp01(_lower_shadow(b) / _range(b)),
        )
    return None


def _marubozu(bars: Sequence[Bar], i: int) -> PatternHit | None:
    b = bars[i]
    r = _range(b)
    if r <= 0:
        return None
    if (
        _upper_shadow(b) <= MARUBOZU_MAX_SHADOW_RATIO * r
        and _lower_shadow(b) <= MARUBOZU_MAX_SHADOW_RATIO * r
        and _body(b) >= MARUBOZU_MIN_BODY_RATIO * r
    ):
        direction: Direction = "bullish" if _is_bull(b) else "bearish"
        return PatternHit(
            bar_index=i, pattern="marubozu", direction=direction, strength=_clamp01(_body(b) / r)
        )
    return None


# --- Two-bar detectors ------------------------------------------------------ #
def _bullish_engulfing(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    if (
        _is_bear(prev)
        and _is_bull(cur)
        and cur.open <= prev.close
        and cur.close >= prev.open
        and _body(cur) > _body(prev)
    ):
        return PatternHit(
            bar_index=i,
            pattern="bullish_engulfing",
            direction="bullish",
            strength=_clamp01(_body(cur) / _range(cur)) if _range(cur) > 0 else 0.0,
        )
    return None


def _bearish_engulfing(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    if (
        _is_bull(prev)
        and _is_bear(cur)
        and cur.open >= prev.close
        and cur.close <= prev.open
        and _body(cur) > _body(prev)
    ):
        return PatternHit(
            bar_index=i,
            pattern="bearish_engulfing",
            direction="bearish",
            strength=_clamp01(_body(cur) / _range(cur)) if _range(cur) > 0 else 0.0,
        )
    return None


def _dark_cloud_cover(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    if (
        _is_bull(prev)
        and _is_bear(cur)
        and cur.open > prev.close  # opens above the prior body
        and cur.close < _body_mid(prev)  # closes below the prior midpoint
        and cur.close > prev.open  # but not all the way through
    ):
        return PatternHit(
            bar_index=i,
            pattern="dark_cloud_cover",
            direction="bearish",
            strength=_clamp01((_body_mid(prev) - cur.close) / _body(prev))
            if _body(prev) > 0
            else 0.0,
        )
    return None


def _piercing_line(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    if (
        _is_bear(prev)
        and _is_bull(cur)
        and cur.open < prev.close  # opens below the prior body
        and cur.close > _body_mid(prev)  # closes above the prior midpoint
        and cur.close < prev.open  # but not all the way through
    ):
        return PatternHit(
            bar_index=i,
            pattern="piercing_line",
            direction="bullish",
            strength=_clamp01((cur.close - _body_mid(prev)) / _body(prev))
            if _body(prev) > 0
            else 0.0,
        )
    return None


def _harami(bars: Sequence[Bar], i: int, *, bullish: bool) -> PatternHit | None:
    if i < 1:
        return None
    prev, cur = bars[i - 1], bars[i]
    prev_is = _is_bear if bullish else _is_bull
    cur_is = _is_bull if bullish else _is_bear
    if not (prev_is(prev) and cur_is(cur)):
        return None
    if _body(prev) < LARGE_BODY_RATIO * _range(prev) or _range(prev) <= 0:
        return None
    cur_top, cur_bot = max(cur.open, cur.close), min(cur.open, cur.close)
    prev_top, prev_bot = max(prev.open, prev.close), min(prev.open, prev.close)
    if cur_top <= prev_top and cur_bot >= prev_bot and _body(cur) < _body(prev):
        return PatternHit(
            bar_index=i,
            pattern="bullish_harami" if bullish else "bearish_harami",
            direction="bullish" if bullish else "bearish",
            strength=_clamp01(1 - _body(cur) / _body(prev)),
        )
    return None


def _bullish_harami(bars: Sequence[Bar], i: int) -> PatternHit | None:
    return _harami(bars, i, bullish=True)


def _bearish_harami(bars: Sequence[Bar], i: int) -> PatternHit | None:
    return _harami(bars, i, bullish=False)


# --- Three-bar detectors ---------------------------------------------------- #
def _morning_star(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 2:
        return None
    a, b, c = bars[i - 2], bars[i - 1], bars[i]
    if _range(a) <= 0 or _range(b) <= 0 or _range(c) <= 0:
        return None
    star_top = max(b.open, b.close)
    if (
        _is_bear(a)
        and _body(a) >= LARGE_BODY_RATIO * _range(a)
        and _body(b) <= SMALL_BODY_RATIO * _range(b)
        and star_top <= a.close  # star gaps below the prior (bearish) body
        and _is_bull(c)
        and c.close > _body_mid(a)  # closes back into the first body
    ):
        return PatternHit(
            bar_index=i,
            pattern="morning_star",
            direction="bullish",
            strength=_clamp01((c.close - _body_mid(a)) / _body(a)),
        )
    return None


def _evening_star(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 2:
        return None
    a, b, c = bars[i - 2], bars[i - 1], bars[i]
    if _range(a) <= 0 or _range(b) <= 0 or _range(c) <= 0:
        return None
    star_bot = min(b.open, b.close)
    if (
        _is_bull(a)
        and _body(a) >= LARGE_BODY_RATIO * _range(a)
        and _body(b) <= SMALL_BODY_RATIO * _range(b)
        and star_bot >= a.close  # star gaps above the prior (bullish) body
        and _is_bear(c)
        and c.close < _body_mid(a)  # closes back into the first body
    ):
        return PatternHit(
            bar_index=i,
            pattern="evening_star",
            direction="bearish",
            strength=_clamp01((_body_mid(a) - c.close) / _body(a)),
        )
    return None


def _three_white_soldiers(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 2:
        return None
    x, y, z = bars[i - 2], bars[i - 1], bars[i]
    if not (_is_bull(x) and _is_bull(y) and _is_bull(z)):
        return None
    if not (x.close < y.close < z.close):  # each closes higher
        return None
    # Each opens within the prior real body (bullish body interval [open, close]).
    if not (x.open < y.open < x.close and y.open < z.open < y.close):
        return None
    if _range(y) <= 0 or _range(z) <= 0:
        return None
    if _upper_shadow(y) > SOLDIER_MAX_UPPER_SHADOW_RATIO * _range(y) or _upper_shadow(
        z
    ) > SOLDIER_MAX_UPPER_SHADOW_RATIO * _range(z):
        return None
    bodies = [_body(x) / _range(x), _body(y) / _range(y), _body(z) / _range(z)]
    return PatternHit(
        bar_index=i,
        pattern="three_white_soldiers",
        direction="bullish",
        strength=_clamp01(sum(bodies) / 3),
    )


def _three_black_crows(bars: Sequence[Bar], i: int) -> PatternHit | None:
    if i < 2:
        return None
    x, y, z = bars[i - 2], bars[i - 1], bars[i]
    if not (_is_bear(x) and _is_bear(y) and _is_bear(z)):
        return None
    if not (x.close > y.close > z.close):  # each closes lower
        return None
    # Each opens within the prior real body (bearish: close < open).
    if not (x.close < y.open < x.open and y.close < z.open < y.open):
        return None
    if _range(x) <= 0 or _range(y) <= 0 or _range(z) <= 0:
        return None
    bodies = [_body(x) / _range(x), _body(y) / _range(y), _body(z) / _range(z)]
    return PatternHit(
        bar_index=i,
        pattern="three_black_crows",
        direction="bearish",
        strength=_clamp01(sum(bodies) / 3),
    )


_DETECTORS = (
    _doji,
    _hammer,
    _hanging_man,
    _marubozu,
    _bullish_engulfing,
    _bearish_engulfing,
    _dark_cloud_cover,
    _piercing_line,
    _bullish_harami,
    _bearish_harami,
    _morning_star,
    _evening_star,
    _three_white_soldiers,
    _three_black_crows,
)


def detect_patterns(bars: Sequence[Bar]) -> list[PatternHit]:
    """Run every detector over every bar and return the hits.

    Trailing: each detector at bar `i` reads only `bars[0..=i]`. The result is
    sorted by `(bar_index, pattern)` for a deterministic, stable order.
    """

    hits: list[PatternHit] = []
    for i in range(len(bars)):
        for detector in _DETECTORS:
            hit = detector(bars, i)
            if hit is not None:
                hits.append(hit)
    hits.sort(key=lambda h: (h.bar_index, h.pattern))
    return hits


__all__ = ["detect_patterns"]
