"""Phase-1 done-when for Plan 0018: the nine indicators in `analysis/indicators.py`.

Strategy of the suite:

* **Per-indicator correctness** is pinned two ways — against an *independent*
  reference computation (``statistics`` / naive loops, written differently from
  the implementation) on the committed 120-bar fixture, and against closed-form
  limit values on degenerate fixtures (a constant series, a strict uptrend) where
  the right answer is provable by hand. RSI is additionally asserted byte-equal
  to ``strategies/rsi._wilder_rsi`` (the contract pin).
* **Anti-lookahead** (the load-bearing test) — for every indicator, truncating the
  series to ``bars[0..=k]`` leaves every value at ``i <= k`` unchanged.
* **Undefined prefix + length** — each series is the input length with a leading
  run of ``None`` (the undefined region) and no interior gaps.
* **Determinism** — two calls on the same input return equal results.
"""

from __future__ import annotations

import dataclasses
import json
import statistics
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from market_analyser.analysis import indicators as ind
from market_analyser.data.types import Bar
from market_analyser.strategies.rsi import _wilder_rsi

_FIXTURE = Path(__file__).parent / "fixtures" / "ohlcv_120.json"
_TOL = 1e-9


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _load_fixture() -> list[Bar]:
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return [Bar.model_validate(row) for row in rows]


def _bar(i: int, *, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        symbol="X",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000.0,
        source="synthetic",
    )


def _constant_bars(n: int, price: float) -> list[Bar]:
    return [_bar(i, o=price, h=price, low=price, c=price) for i in range(n)]


def _points_uptrend_bars(n: int) -> list[Bar]:
    """Zero-range rising points (o == h == low == c == base). Every bar's true range
    equals the up-step and the down-move is negative, so +DM == TR == step and
    -DM == 0: the directional system saturates exactly (ADX -> 100, +DI -> 100,
    -DI -> 0)."""

    return [
        _bar(i, o=100.0 + 5.0 * i, h=100.0 + 5.0 * i, low=100.0 + 5.0 * i, c=100.0 + 5.0 * i)
        for i in range(n)
    ]


def _strong_uptrend_bars(n: int) -> list[Bar]:
    """Steady rise that closes at the high each bar with a tight range, so a
    `multiplier=1` Supertrend's lower band trails just under price and the
    direction locks into the uptrend (+1)."""

    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + 1.0 * i
        bars.append(_bar(i, o=base - 0.2, h=base + 0.2, low=base - 0.2, c=base + 0.2))
    return bars


BARS = _load_fixture()
CLOSES = [b.close for b in BARS]
N = len(BARS)


# --------------------------------------------------------------------------- #
# Independent reference computations (deliberately not the implementation)     #
# --------------------------------------------------------------------------- #


def _ref_ema(values: Sequence[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    first = next((j for j, v in enumerate(values) if v is not None), None)
    if first is None or first + period - 1 >= len(values):
        return out
    seed_end = first + period - 1
    window = values[first : seed_end + 1]
    if any(v is None for v in window):
        return out
    prev = statistics.fmean(v for v in window if v is not None)
    out[seed_end] = prev
    alpha = 2.0 / (period + 1)
    for i in range(seed_end + 1, len(values)):
        v = values[i]
        if v is None:
            break
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def _ref_atr(bars: Sequence[Bar], period: int) -> list[float | None]:
    tr: list[float | None] = [None]
    for i in range(1, len(bars)):
        pc = bars[i - 1].close
        tr.append(max(bars[i].high - bars[i].low, abs(bars[i].high - pc), abs(bars[i].low - pc)))
    out: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return out
    prev = statistics.fmean(t for t in tr[1 : period + 1] if t is not None)
    out[period] = prev
    for i in range(period + 1, len(bars)):
        t = tr[i]
        assert t is not None
        prev = (prev * (period - 1) + t) / period
        out[i] = prev
    return out


# --------------------------------------------------------------------------- #
# Per-indicator correctness — independent reference on the realistic fixture   #
# --------------------------------------------------------------------------- #

_INDICES = (60, 90, 119)


def test_sma_matches_independent_mean() -> None:
    series = ind.sma(CLOSES, 10)
    for i in _INDICES:
        expected = statistics.fmean(CLOSES[i - 9 : i + 1])
        assert series[i] is not None
        assert abs(series[i] - expected) < _TOL  # type: ignore[operator]


def test_ema_matches_independent_reference() -> None:
    series = ind.ema(CLOSES, 12)
    ref = _ref_ema(CLOSES, 12)
    for i in _INDICES:
        assert series[i] is not None and ref[i] is not None
        assert abs(series[i] - ref[i]) < _TOL  # type: ignore[operator]


def test_rsi_matches_strategy_wilder() -> None:
    """Contract pin (ADR-0023 / plan risk note): the analysis RSI is byte-for-byte
    the strategy module's Wilder RSI on the same closes."""

    assert ind.rsi(CLOSES, 14) == _wilder_rsi(CLOSES, 14)


def test_bollinger_matches_independent_stats() -> None:
    series = ind.bollinger(CLOSES, 20, 2.0)
    for i in _INDICES:
        window = CLOSES[i - 19 : i + 1]
        mean = statistics.fmean(window)
        sd = statistics.pstdev(window)
        val = series[i]
        assert val is not None
        assert abs(val.middle - mean) < _TOL
        assert abs(val.upper - (mean + 2.0 * sd)) < _TOL
        assert abs(val.lower - (mean - 2.0 * sd)) < _TOL


def test_macd_matches_independent_reference() -> None:
    fast = _ref_ema(CLOSES, 12)
    slow = _ref_ema(CLOSES, 26)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast, slow, strict=True)
    ]
    sig = _ref_ema(macd_line, 9)
    series = ind.macd(CLOSES)
    for i in _INDICES:
        val = series[i]
        assert val is not None and macd_line[i] is not None and sig[i] is not None
        assert abs(val.macd - macd_line[i]) < _TOL  # type: ignore[operator]
        assert abs(val.signal - sig[i]) < _TOL  # type: ignore[operator]
        assert abs(val.histogram - (macd_line[i] - sig[i])) < _TOL  # type: ignore[operator]


def test_atr_matches_independent_reference() -> None:
    series = ind.atr(BARS, 14)
    ref = _ref_atr(BARS, 14)
    for i in _INDICES:
        assert series[i] is not None and ref[i] is not None
        assert abs(series[i] - ref[i]) < _TOL  # type: ignore[operator]


def test_donchian_matches_independent_window() -> None:
    series = ind.donchian(BARS, 20)
    for i in _INDICES:
        window = BARS[i - 19 : i + 1]
        upper = max(b.high for b in window)
        lower = min(b.low for b in window)
        val = series[i]
        assert val is not None
        assert abs(val.upper - upper) < _TOL
        assert abs(val.lower - lower) < _TOL
        assert abs(val.middle - (upper + lower) / 2) < _TOL


# --------------------------------------------------------------------------- #
# Per-indicator correctness — closed-form pins on degenerate fixtures          #
# --------------------------------------------------------------------------- #


def test_constant_series_closed_forms() -> None:
    const = _constant_bars(60, 50.0)
    closes = [b.close for b in const]
    for i in (30, 45, 59):
        assert abs(ind.sma(closes, 10)[i] - 50.0) < _TOL  # type: ignore[operator]
        assert abs(ind.ema(closes, 12)[i] - 50.0) < _TOL  # type: ignore[operator]
        assert ind.rsi(closes, 14)[i] == 100.0  # no losses -> RSI 100
        boll = ind.bollinger(closes, 20)[i]
        assert boll is not None and boll.upper == boll.middle == boll.lower == 50.0
        assert abs(ind.atr(const, 14)[i] - 0.0) < _TOL  # type: ignore[operator]
        st = ind.supertrend(const, 10)[i]
        assert st is not None and abs(st.value - 50.0) < _TOL and st.direction == -1
        adx = ind.adx(const, 14)[i]
        assert adx is not None and adx.adx == 0.0 and adx.plus_di == 0.0 and adx.minus_di == 0.0
    for i in (45, 59):  # MACD is first defined at index (slow-1)+(signal-1) = 33
        mac = ind.macd(closes)[i]
        assert mac is not None and abs(mac.macd) < _TOL and abs(mac.histogram) < _TOL


def test_uptrend_saturates_adx() -> None:
    up = _points_uptrend_bars(80)
    for i in (50, 65, 79):
        adx = ind.adx(up, 14)[i]
        assert adx is not None
        assert abs(adx.adx - 100.0) < _TOL
        assert abs(adx.plus_di - 100.0) < _TOL
        assert abs(adx.minus_di - 0.0) < _TOL


def test_strong_uptrend_locks_supertrend_up() -> None:
    up = _strong_uptrend_bars(60)
    series = ind.supertrend(up, period=10, multiplier=1.0)
    for i in (40, 50, 59):
        st = series[i]
        assert st is not None and st.direction == 1  # locked into the uptrend
        assert st.value < up[i].close  # lower band trails below price


# --------------------------------------------------------------------------- #
# Generic properties across all nine indicators                                #
# --------------------------------------------------------------------------- #


def _floats(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return (float(value),)
    return tuple(float(x) for x in dataclasses.astuple(value))  # type: ignore[call-overload]


# (name, callable bars -> normalized series, expected first-defined index)
_INDICATORS: list[tuple[str, Callable[[Sequence[Bar]], list[tuple[float, ...] | None]], int]] = [
    ("sma10", lambda b: [_floats(v) for v in ind.sma([x.close for x in b], 10)], 9),
    ("ema12", lambda b: [_floats(v) for v in ind.ema([x.close for x in b], 12)], 11),
    ("rsi14", lambda b: [_floats(v) for v in ind.rsi([x.close for x in b], 14)], 14),
    ("bollinger20", lambda b: [_floats(v) for v in ind.bollinger([x.close for x in b], 20)], 19),
    ("macd", lambda b: [_floats(v) for v in ind.macd([x.close for x in b])], 33),
    ("atr14", lambda b: [_floats(v) for v in ind.atr(b, 14)], 14),
    ("supertrend10", lambda b: [_floats(v) for v in ind.supertrend(b, 10)], 10),
    ("donchian20", lambda b: [_floats(v) for v in ind.donchian(b, 20)], 19),
    ("adx14", lambda b: [_floats(v) for v in ind.adx(b, 14)], 27),
]


def _series_close(
    a: list[tuple[float, ...] | None], b: list[tuple[float, ...] | None], upto: int
) -> None:
    for i in range(upto + 1):
        if a[i] is None or b[i] is None:
            assert a[i] is None and b[i] is None, f"defined-ness diverged at {i}"
            continue
        av, bv = a[i], b[i]
        assert av is not None and bv is not None
        assert len(av) == len(bv)
        for x, y in zip(av, bv, strict=True):
            assert abs(x - y) < _TOL, f"value diverged at {i}: {x} vs {y}"


def test_anti_lookahead_truncation_invariance() -> None:
    """The load-bearing property: computing an indicator on bars[0..=k] yields, at
    every i <= k, the same value as the full-series computation. Truncating the
    future never changes the past."""

    for name, fn, _ in _INDICATORS:
        full = fn(BARS)
        for k in (40, 70, 100, N - 1):
            truncated = fn(BARS[: k + 1])
            assert len(truncated) == k + 1, name
            _series_close(full, truncated, k)


def test_undefined_prefix_and_length() -> None:
    for name, fn, first_defined in _INDICATORS:
        series = fn(BARS)
        assert len(series) == N, name
        assert all(series[i] is None for i in range(first_defined)), f"{name}: prefix not None"
        assert series[first_defined] is not None, f"{name}: not defined at {first_defined}"
        # No interior gaps once the indicator starts.
        assert all(series[i] is not None for i in range(first_defined, N)), f"{name}: interior gap"


def test_determinism() -> None:
    for name, fn, _ in _INDICATORS:
        assert fn(BARS) == fn(BARS), name
