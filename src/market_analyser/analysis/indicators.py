"""Pure, trailing technical indicators (Plan 0018 phase 1, ADR-0023).

Nine indicators — EMA, SMA, RSI (Wilder), Bollinger Bands, MACD, ATR, Supertrend,
Donchian channel, ADX — each returning a series aligned to the input length with
`None` for the leading bars where the indicator is mathematically undefined. No
module-level state, no wall-clock, no RNG. `result[i]` reads only `values[0..=i]`
/ `bars[0..=i]`, so truncating the future never changes the past (the load-bearing
anti-lookahead property tested in `tests/analysis/test_indicators.py`).

Conventions are deliberately aligned with the inline math in the strategy modules
(`strategies/ema_cross.py`, `macd.py`, `bollinger.py`, `supertrend.py`) so the two
copies start in agreement while the duplication lasts (ADR-0023 negative
consequence; reconciliation is a tracked followup). The RSI here is asserted equal
to `strategies/rsi._wilder_rsi`. Donchian is the one intentional divergence: the
indicator channel is the trailing window *inclusive* of the current bar (the
canonical channel), whereas `strategies/donchian.py` excludes the current bar for
breakout detection.

Composite indicators (Bollinger, MACD, Supertrend, Donchian, ADX) return small
frozen value objects whose fields are all fully defined when the object is
emitted — the series carries `None` until every component is computable, so a
value object never holds a `None` field.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from market_analyser.data.types import Bar


@dataclass(frozen=True)
class BollingerValue:
    """One Bollinger reading: the SMA middle band and the ± `num_std` envelope."""

    upper: float
    middle: float
    lower: float


@dataclass(frozen=True)
class MacdValue:
    """One MACD reading: the MACD line, its signal EMA, and their difference."""

    macd: float
    signal: float
    histogram: float


@dataclass(frozen=True)
class SupertrendValue:
    """One Supertrend reading: the active band value and the trend direction
    (`+1` uptrend, `-1` downtrend)."""

    value: float
    direction: int


@dataclass(frozen=True)
class DonchianValue:
    """One Donchian channel reading: highest high, lowest low, and their midpoint
    over the trailing window inclusive of the current bar."""

    upper: float
    lower: float
    middle: float


@dataclass(frozen=True)
class AdxValue:
    """One ADX reading: the smoothed trend-strength index and the two directional
    indicators (`plus_di`, `minus_di`) it is derived from."""

    adx: float
    plus_di: float
    minus_di: float


# --------------------------------------------------------------------------- #
# Single-value indicators                                                      #
# --------------------------------------------------------------------------- #


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Simple moving average over the trailing `period` values inclusive of `i`.

    `None` for `i < period - 1`. `period` must be >= 1.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        out[i] = sum(window) / period
    return out


def ema(values: Sequence[float | None], period: int) -> list[float | None]:
    """Exponential moving average, seeded by the SMA of the first `period`
    consecutive defined values and advanced via `alpha = 2 / (period + 1)`.

    Tolerates a leading run of `None` (so the signal line can be an EMA of the
    MACD line, which has a defined prefix shorter than the full series). Assumes
    the stream is dense once it starts; collapses back to `None` on any interior
    gap. Mirrors `strategies/macd.py::_ema`.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(values)
    out: list[float | None] = [None] * n
    first_defined = next((j for j, v in enumerate(values) if v is not None), None)
    if first_defined is None:
        return out
    seed_end = first_defined + period - 1
    if seed_end >= n:
        return out
    seed_window = values[first_defined : seed_end + 1]
    if any(v is None for v in seed_window):
        return out
    seed = sum(v for v in seed_window if v is not None) / period
    out[seed_end] = seed
    alpha = 2.0 / (period + 1)
    prev = seed
    for i in range(seed_end + 1, n):
        v = values[i]
        if v is None:
            return out
        curr = alpha * v + (1 - alpha) * prev
        out[i] = curr
        prev = curr
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's smoothed RSI, `None` until index `period`.

    Byte-for-byte the algorithm in `strategies/rsi._wilder_rsi` (asserted equal in
    the tests) — the first `period` changes seed a simple average, then the Wilder
    recurrence `avg = (avg_prev * (period - 1) + value) / period` advances it.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    gains_sum = 0.0
    losses_sum = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains_sum += change
        else:
            losses_sum += -change

    avg_gain = gains_sum / period
    avg_loss = losses_sum / period
    out[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)

    return out


def _true_range(bars: Sequence[Bar]) -> list[float | None]:
    """True range, `None` at `i = 0` (no previous close)."""

    n = len(bars)
    tr: list[float | None] = [None] * n
    for i in range(1, n):
        high, low, prev_c = bars[i].high, bars[i].low, bars[i - 1].close
        tr[i] = max(high - low, abs(high - prev_c), abs(low - prev_c))
    return tr


def _atr_from_tr(tr: Sequence[float | None], period: int) -> list[float | None]:
    """Wilder-smoothed ATR over a true-range series, seeded by the SMA of
    `tr[1..period]` at index `period` (TR is undefined at `i = 0`). Mirrors
    `strategies/supertrend.py::_atr_wilder`."""

    n = len(tr)
    atr: list[float | None] = [None] * n
    if n <= period:
        return atr
    seed_values = tr[1 : period + 1]
    if any(v is None for v in seed_values):
        return atr
    seed = sum(v for v in seed_values if v is not None) / period
    atr[period] = seed
    prev = seed
    for i in range(period + 1, n):
        v = tr[i]
        assert v is not None  # TR is dense for i >= 1
        curr = (prev * (period - 1) + v) / period
        atr[i] = curr
        prev = curr
    return atr


def atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    """Wilder's Average True Range, `None` until index `period`."""

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    return _atr_from_tr(_true_range(bars), period)


def donchian(bars: Sequence[Bar], period: int = 20) -> list[DonchianValue | None]:
    """Donchian channel over the trailing `period` bars *inclusive* of the current
    bar — highest high, lowest low, midpoint. `None` for `i < period - 1`.

    Note the inclusive window: this is the canonical channel for display.
    `strategies/donchian.py` deliberately *excludes* the current bar for breakout
    detection — a different, equally-trailing choice.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(bars)
    out: list[DonchianValue | None] = [None] * n
    for i in range(period - 1, n):
        window = bars[i - period + 1 : i + 1]
        upper = max(b.high for b in window)
        lower = min(b.low for b in window)
        out[i] = DonchianValue(upper=upper, lower=lower, middle=(upper + lower) / 2)
    return out


# --------------------------------------------------------------------------- #
# Composite indicators                                                         #
# --------------------------------------------------------------------------- #


def bollinger(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> list[BollingerValue | None]:
    """Bollinger Bands: SMA middle band ± `num_std` population standard deviations.

    `None` for `i < period - 1`. Population stdev (denominator `N`) per the
    `strategies/bollinger.py` convention.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(closes)
    out: list[BollingerValue | None] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(var)
        out[i] = BollingerValue(upper=mean + num_std * sd, middle=mean, lower=mean - num_std * sd)
    return out


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[MacdValue | None]:
    """MACD: `EMA(close, fast) - EMA(close, slow)`, its `signal`-period EMA, and
    the histogram (their difference).

    The value object is emitted only once all three components are defined — i.e.
    from index `(slow - 1) + (signal - 1)` onward; earlier bars are `None`. `fast`
    must be strictly less than `slow`.
    """

    if fast < 1 or slow < 1 or signal < 1:
        raise ValueError("fast, slow, signal must all be >= 1")
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be strictly less than slow ({slow})")
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    signal_line = ema(macd_line, signal)

    out: list[MacdValue | None] = [None] * len(closes)
    for i, (m, sg) in enumerate(zip(macd_line, signal_line, strict=True)):
        if m is None or sg is None:
            continue
        out[i] = MacdValue(macd=m, signal=sg, histogram=m - sg)
    return out


def supertrend(
    bars: Sequence[Bar], period: int = 10, multiplier: float = 3.0
) -> list[SupertrendValue | None]:
    """Supertrend: Wilder ATR, basic bands `hl2 ± multiplier * ATR`, recursive
    final bands, and a direction state that flips when the close pierces the active
    band.

    Seeded "downtrend at the ATR seed bar" (index `period`), matching
    `strategies/supertrend.py`. `None` until index `period`. Direction is `+1`
    (uptrend → active band is the lower band) or `-1` (downtrend → upper band).
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")
    n = len(bars)
    out: list[SupertrendValue | None] = [None] * n
    atr_series = _atr_from_tr(_true_range(bars), period)
    if n <= period or atr_series[period] is None:
        return out

    basic_upper: list[float | None] = [None] * n
    basic_lower: list[float | None] = [None] * n
    for i in range(period, n):
        a = atr_series[i]
        assert a is not None  # ATR is dense for i >= period
        hl2 = (bars[i].high + bars[i].low) / 2
        basic_upper[i] = hl2 + multiplier * a
        basic_lower[i] = hl2 - multiplier * a

    final_upper: list[float | None] = [None] * n
    final_lower: list[float | None] = [None] * n
    final_upper[period] = basic_upper[period]
    final_lower[period] = basic_lower[period]
    for i in range(period + 1, n):
        bu, bl = basic_upper[i], basic_lower[i]
        prev_fu, prev_fl = final_upper[i - 1], final_lower[i - 1]
        assert bu is not None and bl is not None
        assert prev_fu is not None and prev_fl is not None
        prev_close = bars[i - 1].close
        final_upper[i] = bu if (bu < prev_fu or prev_close > prev_fu) else prev_fu
        final_lower[i] = bl if (bl > prev_fl or prev_close < prev_fl) else prev_fl

    # Seed direction "down" at index `period`: the active band is the upper band.
    seed_fu = final_upper[period]
    assert seed_fu is not None
    direction = -1
    out[period] = SupertrendValue(value=seed_fu, direction=direction)
    for i in range(period + 1, n):
        fu, fl = final_upper[i], final_lower[i]
        assert fu is not None and fl is not None
        c = bars[i].close
        if direction == -1 and c > fu:
            direction = 1
        elif direction == 1 and c < fl:
            direction = -1
        out[i] = SupertrendValue(value=(fl if direction == 1 else fu), direction=direction)
    return out


def adx(bars: Sequence[Bar], period: int = 14) -> list[AdxValue | None]:
    """Wilder's ADX with its `+DI` / `-DI` directional indicators.

    Directional movement and true range are Wilder-smoothed over `period`; `DX`
    is the normalised `|+DI - -DI|`, and ADX is the Wilder average of `DX`. The
    value object is emitted only once ADX itself is defined — from index
    `2 * period - 1` onward.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(bars)
    out: list[AdxValue | None] = [None] * n
    if n < 2 * period:
        return out

    tr: list[float] = [0.0] * n
    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    for i in range(1, n):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        prev_c = bars[i - 1].close
        tr[i] = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_c),
            abs(bars[i].low - prev_c),
        )

    # Wilder "sum" smoothing seeded at index `period` (sum of [1..period]); keep
    # the DI pair per index so the value objects need no second pass.
    sm_tr = sum(tr[1 : period + 1])
    sm_plus = sum(plus_dm[1 : period + 1])
    sm_minus = sum(minus_dm[1 : period + 1])

    dx: list[float | None] = [None] * n
    plus_di_series: list[float | None] = [None] * n
    minus_di_series: list[float | None] = [None] * n
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_plus = sm_plus - sm_plus / period + plus_dm[i]
            sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        plus_di = 100.0 * sm_plus / sm_tr if sm_tr != 0 else 0.0
        minus_di = 100.0 * sm_minus / sm_tr if sm_tr != 0 else 0.0
        plus_di_series[i] = plus_di
        minus_di_series[i] = minus_di
        di_sum = plus_di + minus_di
        dx[i] = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum != 0 else 0.0

    # First ADX = average of the first `period` DX values (indices period..2*period-1).
    first_adx_idx = 2 * period - 1
    adx_prev = sum(_unwrap(dx[j]) for j in range(period, first_adx_idx + 1)) / period

    for i in range(first_adx_idx, n):
        if i > first_adx_idx:
            adx_prev = (adx_prev * (period - 1) + _unwrap(dx[i])) / period
        out[i] = AdxValue(
            adx=adx_prev,
            plus_di=_unwrap(plus_di_series[i]),
            minus_di=_unwrap(minus_di_series[i]),
        )
    return out


def _unwrap(value: float | None) -> float:
    """Assert a series entry is defined and return it — narrows `float | None` to
    `float` for the dense interior of a smoothed series (mypy-strict friendly)."""

    assert value is not None
    return value


__all__ = [
    "AdxValue",
    "BollingerValue",
    "DonchianValue",
    "MacdValue",
    "SupertrendValue",
    "adx",
    "atr",
    "bollinger",
    "donchian",
    "ema",
    "macd",
    "rsi",
    "sma",
    "supertrend",
]
