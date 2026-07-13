"""Pure, trailing technical indicators (Plan 0018 phase 1, ADR-0023).

Ten indicators — EMA, SMA, RSI (Wilder), Bollinger Bands, MACD, ATR, Supertrend,
Donchian channel, ADX, Ichimoku — each returning a series aligned to the input
length with `None` for the leading bars where the indicator is mathematically
undefined. No
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
class KeltnerValue:
    """One Keltner reading: the EMA middle line and the ± `multiplier * ATR`
    envelope. Mirrors `BollingerValue`'s shape; the band is ATR-based rather than
    standard-deviation-based, which is what makes the TTM squeeze (Bollinger inside
    Keltner) meaningful (ADR-0083)."""

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


@dataclass(frozen=True)
class IchimokuValue:
    """One Ichimoku reading, every field **as computed at bar `i` from
    `bars[0..=i]`** — purely trailing, like every other value object here.

    Ichimoku is the one indicator whose *plotted* position differs from its
    *computed* bar, so the as-computed-vs-as-plotted split (mirroring the Donchian
    inclusive-window note) is explicit and load-bearing: displacement is applied by
    the *consumer/renderer*, never baked into the series. A chart plots
    `senkou_a`/`senkou_b` at `i + displacement` and `chikou` at `i - displacement`;
    the cloud sitting *under* bar `i` is `senkou_*[i - displacement]` — the trailing
    read ADR-0067 pins for trend classification. Keeping the series trailing is what
    makes `ichimoku(bars[:k])[k-1] == ichimoku(bars)[k-1]` (anti-lookahead)."""

    tenkan: float  # midpoint of the trailing `conversion`-bar high/low
    kijun: float  # ...over `base` bars
    senkou_a: float  # (tenkan + kijun) / 2 — PLOTTED at i + displacement
    senkou_b: float  # midpoint over `span_b` bars — PLOTTED at i + displacement
    chikou: float  # close[i] — PLOTTED at i - displacement


@dataclass(frozen=True)
class StochasticValue:
    """One Stochastic reading: the fast `%K` line and its `%D` smoothing (an SMA of
    `%K`). Both are 0-100. Emitted only once both are defined, so the object never
    holds a `None` field (Plan 0091 phase 1)."""

    k: float
    d: float


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


def bollinger_bandwidth(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> list[float | None]:
    """Bollinger band-width `(upper - lower) / middle` — the canonical squeeze /
    expansion measure (ADR-0083). Derived from the same `bollinger()` values, so it
    is trailing and length-aligned identically. `None` for `i < period - 1` (band
    undefined) and `None` where `middle == 0` (no divide-by-zero on a zero-mean
    window)."""

    out: list[float | None] = []
    for band in bollinger(closes, period, num_std):
        if band is None or band.middle == 0.0:
            out.append(None)
        else:
            out.append((band.upper - band.lower) / band.middle)
    return out


def keltner(
    bars: Sequence[Bar],
    period: int = 20,
    atr_period: int = 20,
    multiplier: float = 1.5,
) -> list[KeltnerValue | None]:
    """Keltner channel: EMA middle band ± `multiplier * ATR` envelope.

    `middle = ema(close, period)`, `upper/lower = middle ± multiplier * atr(bars,
    atr_period)` — reusing the module's `ema` and Wilder `atr`. The value object is
    emitted only once *both* the EMA and the ATR are defined (from index
    `max(period - 1, atr_period)`), so it never holds a `None` field. Trailing by
    construction — `result[i]` reads only `bars[0..=i]`. The 20 / 20 / 1.5 defaults
    are the TTM-squeeze convention (ADR-0083); `multiplier` must be > 0.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if atr_period < 1:
        raise ValueError(f"atr_period must be >= 1, got {atr_period}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")
    closes = [b.close for b in bars]
    ema_series = ema(closes, period)
    atr_series = atr(bars, atr_period)
    out: list[KeltnerValue | None] = [None] * len(bars)
    for i, (mid, a) in enumerate(zip(ema_series, atr_series, strict=True)):
        if mid is None or a is None:
            continue
        out[i] = KeltnerValue(upper=mid + multiplier * a, middle=mid, lower=mid - multiplier * a)
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


def _hl_midpoint(bars: Sequence[Bar], i: int, period: int) -> float:
    """Midpoint of the highest high and lowest low over the trailing `period` bars
    inclusive of bar `i` — the shared Ichimoku/Donchian line convention."""

    window = bars[i - period + 1 : i + 1]
    return (max(b.high for b in window) + min(b.low for b in window)) / 2


def ichimoku(
    bars: Sequence[Bar],
    conversion: int = 9,
    base: int = 26,
    span_b: int = 52,
    displacement: int = 26,
) -> list[IchimokuValue | None]:
    """Ichimoku Kinkō Hyō — Tenkan, Kijun, Senkou A/B, Chikou — all TRAILING.

    Each field is computed at bar `i` from `bars[0..=i]`, classic 9/26/52/26
    defaults:

    * `tenkan[i]` = midpoint of the trailing `conversion`-bar high/low;
    * `kijun[i]`  = midpoint over `base`;
    * `senkou_a[i]` = `(tenkan[i] + kijun[i]) / 2`;
    * `senkou_b[i]` = midpoint over `span_b`;
    * `chikou[i]` = `close[i]`.

    Displacement is **not** baked into the series — it is a consumption/display
    concern (the chart plots Senkou at `i + displacement`, Chikou at
    `i - displacement`; the cloud under bar `i` is `senkou_*[i - displacement]`,
    ADR-0067). Validating it here (`>= 1`) keeps the eventual consumer honest, but
    it does not shift the defined-from index — that is set by the widest trailing
    *computed* window (`span_b` with the classic defaults, `max(conversion, base,
    span_b)` in general). The value object is emitted only once every component is
    defined, so it never holds a `None` field; earlier bars are `None`.
    """

    if conversion < 1 or base < 1 or span_b < 1 or displacement < 1:
        raise ValueError(
            "conversion, base, span_b, displacement must all be >= 1, got "
            f"{conversion}, {base}, {span_b}, {displacement}"
        )
    n = len(bars)
    out: list[IchimokuValue | None] = [None] * n
    defined_from = max(conversion, base, span_b) - 1
    for i in range(defined_from, n):
        tenkan = _hl_midpoint(bars, i, conversion)
        kijun = _hl_midpoint(bars, i, base)
        out[i] = IchimokuValue(
            tenkan=tenkan,
            kijun=kijun,
            senkou_a=(tenkan + kijun) / 2,
            senkou_b=_hl_midpoint(bars, i, span_b),
            chikou=bars[i].close,
        )
    return out


# --------------------------------------------------------------------------- #
# Momentum oscillators (Plan 0091 phase 1)                                     #
# --------------------------------------------------------------------------- #


def stochastic(
    bars: Sequence[Bar], k_period: int = 14, d_period: int = 3
) -> list[StochasticValue | None]:
    """Fast Stochastic oscillator over the trailing window inclusive of bar `i`.

    Raw `%K = 100 * (close - lowest_low) / (highest_high - lowest_low)` over the
    trailing `k_period` bars; `%D` is the `d_period`-SMA of `%K`. A flat window
    (`highest_high == lowest_low`) leaves `%K` undefined — `None`, never a
    divide-by-zero (matching the `vwap`/`relative_volume` guards). The value object
    is emitted only once both lines are defined — from index `(k_period - 1) +
    (d_period - 1)` on a series with no flat windows; earlier bars are `None`.
    """

    if k_period < 1:
        raise ValueError(f"k_period must be >= 1, got {k_period}")
    if d_period < 1:
        raise ValueError(f"d_period must be >= 1, got {d_period}")
    n = len(bars)
    raw_k: list[float | None] = [None] * n
    for i in range(k_period - 1, n):
        window = bars[i - k_period + 1 : i + 1]
        hh = max(b.high for b in window)
        ll = min(b.low for b in window)
        rng = hh - ll
        if rng == 0.0:
            continue  # flat window — %K undefined
        raw_k[i] = 100.0 * (bars[i].close - ll) / rng

    out: list[StochasticValue | None] = [None] * n
    for i in range(d_period - 1, n):
        k = raw_k[i]
        if k is None:
            continue
        dwin = raw_k[i - d_period + 1 : i + 1]
        if any(v is None for v in dwin):
            continue
        d = sum(v for v in dwin if v is not None) / d_period
        out[i] = StochasticValue(k=k, d=d)
    return out


def stochastic_rsi(
    closes: Sequence[float], rsi_period: int = 14, stoch_period: int = 14
) -> list[float | None]:
    """Stochastic RSI — the Stochastic %K formula applied to the RSI series rather
    than price, scaled 0-100.

    `stoch_rsi[i] = 100 * (rsi[i] - min(rsi)) / (max(rsi) - min(rsi))` over the
    trailing `stoch_period` RSI values. A flat RSI window (`max == min`) is `None`,
    never a divide-by-zero. Defined from index `rsi_period + stoch_period - 1` on a
    series with no flat RSI windows; earlier bars are `None`. Trailing because the
    underlying `rsi` is trailing.
    """

    if stoch_period < 1:
        raise ValueError(f"stoch_period must be >= 1, got {stoch_period}")
    rsi_series = rsi(closes, rsi_period)  # validates rsi_period
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(stoch_period - 1, n):
        cur = rsi_series[i]
        if cur is None:
            continue
        window = rsi_series[i - stoch_period + 1 : i + 1]
        if any(v is None for v in window):
            continue
        defined = [v for v in window if v is not None]
        hi, lo = max(defined), min(defined)
        rng = hi - lo
        if rng == 0.0:
            continue  # flat RSI window — undefined
        out[i] = 100.0 * (cur - lo) / rng
    return out


def cci(bars: Sequence[Bar], period: int = 20) -> list[float | None]:
    """Commodity Channel Index over the trailing `period` bars inclusive of `i`.

    Typical price `TP = (high + low + close) / 3`, `CCI = (TP - SMA(TP)) / (0.015 *
    mean_deviation)` where the mean deviation is the average absolute deviation of
    `TP` from its SMA over the window. A zero mean deviation (a flat `TP` window)
    is `None`, never a divide-by-zero. `None` for `i < period - 1`.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(bars)
    tp = [(b.high + b.low + b.close) / 3 for b in bars]
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = tp[i - period + 1 : i + 1]
        sma_tp = sum(window) / period
        mean_dev = sum(abs(x - sma_tp) for x in window) / period
        if mean_dev == 0.0:
            continue  # flat typical-price window — undefined
        out[i] = (tp[i] - sma_tp) / (0.015 * mean_dev)
    return out


def williams_r(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    """Williams %R over the trailing `period` bars inclusive of `i`, ranged -100..0.

    `%R = -100 * (highest_high - close) / (highest_high - lowest_low)`. A flat
    window (`highest_high == lowest_low`) is `None`, never a divide-by-zero. `None`
    for `i < period - 1`.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(bars)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = bars[i - period + 1 : i + 1]
        hh = max(b.high for b in window)
        ll = min(b.low for b in window)
        rng = hh - ll
        if rng == 0.0:
            continue  # flat window — undefined
        out[i] = -100.0 * (hh - bars[i].close) / rng
    return out


def roc(closes: Sequence[float], period: int = 12) -> list[float | None]:
    """Rate of Change: percent change from `period` bars ago, `100 * (close[i] -
    close[i - period]) / close[i - period]`.

    `None` for `i < period` (no reference bar) and `None` where the reference close
    is exactly `0` (no divide-by-zero).
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        prev = closes[i - period]
        if prev == 0.0:
            continue  # zero reference — undefined
        out[i] = 100.0 * (closes[i] - prev) / prev
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
    "IchimokuValue",
    "KeltnerValue",
    "MacdValue",
    "StochasticValue",
    "SupertrendValue",
    "adx",
    "atr",
    "bollinger",
    "bollinger_bandwidth",
    "cci",
    "donchian",
    "ema",
    "ichimoku",
    "keltner",
    "macd",
    "roc",
    "rsi",
    "sma",
    "stochastic",
    "stochastic_rsi",
    "supertrend",
    "williams_r",
]
