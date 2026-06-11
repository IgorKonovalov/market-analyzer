"""The four pure metric helpers Plan 0008 phase 1 ships.

Each function is pure: same inputs → byte-identical outputs, no I/O, no
module-level state, no wall-clock reads, no unseeded randomness. The
`ENGINE_VERSION` constant bumps whenever any helper's output changes for
identical inputs.

Sharpe annualization uses `_TIMEFRAME_BARS_PER_YEAR`, a hand-maintained
dict whose keys equal the data layer's `SUPPORTED_TIMEFRAMES`
(`15m/1h/4h/1d/1w`). Adding a timeframe means appending to the dict;
unknown timeframes raise rather than silently picking a wrong factor — the
engine does not guess annualization.

Fixed-fraction-at-100% sizing per
[ADR-0018](../../../docs/architecture/adrs/0018-backtest-result-schema.md),
generalized to flat/long/short per
[ADR-0050](../../../docs/architecture/adrs/0050-short-selling-strategy-backtest.md):
while flat, equity equals cash; while in a long position, equity equals
`units * bar.close` where `units = entry_cash / entry_price` was fixed at
the trade's entry-bar open; while in a short position, equity equals
`entry_cash + units * (entry_price - bar.close)` — the exact mirror, with
no borrow or financing cost (frictionless v1).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from market_analyser.backtest.result import BacktestMetrics, EquityPoint
from market_analyser.backtest.types import Trade
from market_analyser.data.types import Bar

# Bars per year on the existing annualization basis: 252 trading days/year,
# 24 hours/day. Every value is `252 * 24 * 60 / (minutes per bar)`, so the set
# is internally consistent and re-derives uniformly:
#   15m = 252*24*4, 1h = 252*24, 4h = 252*6, 1d = 252, 1w = 252/7.
# Weekly is 252/7 = 36 (not the textbook 52) to stay consistent with this
# table's own 7-day-week / 24h-day basis. Keys equal the data-layer
# `SUPPORTED_TIMEFRAMES`; unknown timeframes raise rather than guess a factor.
_TIMEFRAME_BARS_PER_YEAR: dict[str, int] = {
    "15m": 252 * 24 * 4,
    "1h": 252 * 24,
    "4h": 252 * 6,
    "1d": 252,
    "1w": 252 // 7,
}


class UnknownTimeframeError(ValueError):
    """Raised when a timeframe lacks an entry in `_TIMEFRAME_BARS_PER_YEAR`."""


def _apply_costs(
    trades: Sequence[Trade],
    *,
    commission_bps: float,
    slippage_bps: float,
) -> list[Trade]:
    """Adjust each trade's entry/exit prices by the per-side bps cost.

    Costs always hurt, in either direction. Long: the entry buy fills
    higher (`* (1 + f)`), the exit sell receives less (`* (1 - f)`).
    Short: the entry sell receives less (`* (1 - f)`), the exit buy fills
    higher (`* (1 + f)`) — the same per-side bps a long pays (ADR-0050).
    One signed code path: `sign` is `+1` for long, `-1` for short, and
    multiplying by `+1.0` is exact in IEEE 754, so the long path is
    bit-identical to the pre-short engine. Dangling trades
    (`exit_price is None`) keep `exit_price` untouched — they have not yet
    executed an exit, so no exit cost applies.
    """

    total_bps = commission_bps + slippage_bps
    factor = total_bps / 10_000.0
    out: list[Trade] = []
    for trade in trades:
        sign = 1.0 if trade.kind == "long" else -1.0
        adjusted_entry = trade.entry_price * (1.0 + sign * factor)
        adjusted_exit = (
            trade.exit_price * (1.0 - sign * factor) if trade.exit_price is not None else None
        )
        out.append(
            Trade(
                entry_bar_index=trade.entry_bar_index,
                exit_bar_index=trade.exit_bar_index,
                entry_price=adjusted_entry,
                exit_price=adjusted_exit,
                kind=trade.kind,
            )
        )
    return out


def _position_value(trade: Trade, units: float, entry_cash: float, price: float) -> float:
    """Value an open position at `price`, signed by the trade's direction.

    Long: `units * price` — kept in this exact form (not the algebraically
    equal `entry_cash + units * (price - entry)`) so the long path stays
    bit-identical to the pre-short engine; IEEE 754 would not guarantee
    that for the rearranged expression. Short: the mirror,
    `entry_cash + units * (entry - price)` — equity rises as price falls,
    no borrow cost (frictionless v1 per ADR-0050).
    """

    if trade.kind == "long":
        return units * price
    return entry_cash + units * (trade.entry_price - price)


def _build_equity_curve(
    bars: Sequence[Bar],
    trades: Sequence[Trade],
    initial_capital: float,
) -> list[EquityPoint]:
    """Per-bar mark-to-market equity curve, direction-aware via
    `_position_value` (one state machine for long and short).

    Trades are consumed in input order; the adapter emits them in entry
    order, so callers may rely on that. At each bar, exits are processed
    before entries (so a same-bar close-then-open chain — e.g. ADR-0050's
    exit-long-then-enter-short at the same next open — works).
    """

    cash = initial_capital
    units = 0.0
    entry_cash = 0.0
    in_position = False
    trade_iter = iter(trades)
    current_trade: Trade | None = next(trade_iter, None)
    equity_curve: list[EquityPoint] = []

    for i, bar in enumerate(bars):
        if current_trade is not None and in_position and current_trade.exit_bar_index == i:
            assert current_trade.exit_price is not None
            cash = _position_value(current_trade, units, entry_cash, current_trade.exit_price)
            units = 0.0
            in_position = False
            current_trade = next(trade_iter, None)

        if current_trade is not None and not in_position and current_trade.entry_bar_index == i:
            entry_cash = cash
            units = cash / current_trade.entry_price
            cash = 0.0
            in_position = True

        if in_position:
            assert current_trade is not None
            equity = _position_value(current_trade, units, entry_cash, bar.close)
        else:
            equity = cash
        equity_curve.append(EquityPoint(ts=bar.event_ts, equity=equity))

    return equity_curve


def _max_drawdown_and_duration(equities: Sequence[float]) -> tuple[float, int]:
    """Return (max_drawdown_as_negative_fraction, max_below_peak_bars).

    Drawdown depth: minimum of `(equity - running_max) / running_max` across
    all bars. Returns `0.0` if the equity curve never dips.

    Duration: the longest contiguous stretch (in bars) where equity sits
    strictly below the running max. A stretch that runs to the end of the
    series counts up to its last bar (per
    [ADR-0018](../../../docs/architecture/adrs/0018-backtest-result-schema.md):
    "or end-of-series if never recovered").
    """

    if not equities:
        return 0.0, 0

    running_max = equities[0]
    max_dd = 0.0
    max_dd_duration = 0
    dd_start: int | None = None

    for i, eq in enumerate(equities):
        if eq > running_max:
            if dd_start is not None:
                duration = i - dd_start
                if duration > max_dd_duration:
                    max_dd_duration = duration
                dd_start = None
            running_max = eq
        elif eq == running_max:
            if dd_start is not None:
                duration = i - dd_start
                if duration > max_dd_duration:
                    max_dd_duration = duration
                dd_start = None
        else:
            if dd_start is None:
                dd_start = i
            drawdown = (eq - running_max) / running_max if running_max > 0 else 0.0
            if drawdown < max_dd:
                max_dd = drawdown

    if dd_start is not None:
        duration = len(equities) - dd_start
        if duration > max_dd_duration:
            max_dd_duration = duration

    return max_dd, max_dd_duration


def _sortino(returns: Sequence[float], bars_per_year: int) -> float:
    """Annualized Sortino ratio per [ADR-0024].

    `mean(returns) / downside_deviation * sqrt(bars_per_year)`, where
    `downside_deviation = stdev({min(r, 0) for r in returns})` (target/MAR
    = 0, sample stdev ddof=1). Sharpe-family, so it keeps the `0.0`
    collapse: fewer than two returns, or no downside (every clamped value
    is 0 so the stdev is 0), yields `0.0` — never NaN.
    """

    if len(returns) < 2:
        return 0.0
    downside = [min(r, 0.0) for r in returns]
    downside_deviation = statistics.stdev(downside)
    if downside_deviation <= 0.0:
        return 0.0
    return statistics.fmean(returns) / downside_deviation * math.sqrt(bars_per_year)


def _calmar(
    total_return: float,
    max_drawdown: float,
    bars_per_year: int,
    n_bars: int,
) -> float | None:
    """Calmar ratio per [ADR-0024]: `annualized_total_return / abs(max_drawdown)`.

    `annualized_total_return = (1 + total_return) ** (bars_per_year / n_bars) - 1`.
    `n_bars` is the bar count of the equity series. When the curve never
    dipped (`max_drawdown == 0.0`) Calmar is undefined (division by zero)
    and returns `None`, not `0.0`. Long positions keep equity (and so
    `1 + total_return`) positive, but a frictionless short whose price more
    than doubles can drive equity to or below zero (ADR-0050 models no
    margin call); the geometric annualization is then undefined, so Calmar
    is `None` rather than a complex-valued power.
    """

    if max_drawdown == 0.0:
        return None
    if 1.0 + total_return <= 0.0:
        return None
    # `float ** float` is typed `Any` (it can go complex for a negative base);
    # pin it back to float — the base is always positive here.
    annualized_total_return: float = (1.0 + total_return) ** (bars_per_year / n_bars) - 1.0
    return annualized_total_return / abs(max_drawdown)


def _profit_factor(per_trade_returns: Sequence[float]) -> float | None:
    """Gross profit / gross loss over closed trades, per [ADR-0024].

    Gross profit/loss sum the positive / negative per-trade returns
    (gross loss as a positive magnitude). Undefined — and so `None`, never
    `inf` or `0.0` — when there are no closed trades or no losing trade to
    divide by (`gross_loss == 0`).
    """

    if not per_trade_returns:
        return None
    gross_profit = sum(r for r in per_trade_returns if r > 0.0)
    gross_loss = -sum(r for r in per_trade_returns if r < 0.0)
    if gross_loss == 0.0:
        return None
    return gross_profit / gross_loss


def _calc_metrics(
    trades: Sequence[Trade],
    equity_curve: Sequence[EquityPoint],
    initial_capital: float,
    timeframe: str,
    *,
    buy_and_hold_return: float = 0.0,
) -> BacktestMetrics:
    """Compute the extended `BacktestMetrics` summary.

    Sharpe is per-bar mean / per-bar sample std (`statistics.stdev`,
    ddof=1), then annualized by `sqrt(bars_per_year[timeframe])`. NaN-safe:
    zero std (flat curve) collapses to 0.0, never NaN. Zero closed trades
    collapses `win_rate` to 0.0, also never NaN.

    The six extended metrics (Calmar, Sortino, profit factor, expectancy,
    best/worst trade) follow the definitions and degenerate-value
    convention pinned by
    [ADR-0024](../../../docs/architecture/adrs/0024-extended-backtest-metrics.md):
    genuinely-undefined ratio / per-trade metrics are `None`, except
    Sortino, which is Sharpe-family and keeps the `0.0` collapse. Per-trade
    returns are computed on the cost-adjusted trade prices this helper
    receives (`exit_price / entry_price - 1` for each closed long trade,
    negated for a short — ADR-0050).

    `buy_and_hold_return` is passed through from `_buy_and_hold_return`;
    the engine computes it from the same `bars` `_build_equity_curve` saw
    and threads it in. Defaulting to 0.0 keeps direct-call tests simple.

    Raises `UnknownTimeframeError` if `timeframe` is not in
    `_TIMEFRAME_BARS_PER_YEAR`. The engine does not guess annualization.
    """

    if timeframe not in _TIMEFRAME_BARS_PER_YEAR:
        raise UnknownTimeframeError(
            f"unknown timeframe {timeframe!r}; known timeframes: {sorted(_TIMEFRAME_BARS_PER_YEAR)}"
        )
    bars_per_year = _TIMEFRAME_BARS_PER_YEAR[timeframe]

    equities = [point.equity for point in equity_curve]
    final_equity = equities[-1] if equities else initial_capital
    total_return = (final_equity / initial_capital) - 1.0

    if len(equities) >= 2:
        returns = [(equities[i] / equities[i - 1]) - 1.0 for i in range(1, len(equities))]
    else:
        returns = []

    if len(returns) >= 2:
        mean_return = statistics.fmean(returns)
        std_return = statistics.stdev(returns)
        sharpe = mean_return / std_return * math.sqrt(bars_per_year) if std_return > 0.0 else 0.0
    else:
        sharpe = 0.0

    max_dd, max_dd_duration = _max_drawdown_and_duration(equities)

    sortino = _sortino(returns, bars_per_year)
    calmar = _calmar(total_return, max_dd, bars_per_year, len(equities))

    closed_trades = [t for t in trades if t.exit_price is not None]
    trade_count = len(closed_trades)

    # Per-trade fractional returns on the cost-adjusted prices (ADR-0024),
    # signed by direction (ADR-0050): a short's return on capital under
    # fixed-fraction sizing is `(entry - exit) / entry`, the exact negation
    # of the long formula, so negating keeps one code path. The long branch
    # is untouched (bit-identical to the pre-short engine).
    per_trade_returns: list[float] = []
    for t in closed_trades:
        assert t.exit_price is not None  # closed by construction; narrows for mypy
        long_return = t.exit_price / t.entry_price - 1.0
        per_trade_returns.append(long_return if t.kind == "long" else -long_return)

    # A win is a positive return in the trade's own direction. For longs this
    # is the same `exit > entry` predicate the long-only engine used.
    win_count = sum(1 for r in per_trade_returns if r > 0.0)
    win_rate = win_count / trade_count if trade_count > 0 else 0.0

    profit_factor = _profit_factor(per_trade_returns)
    expectancy = statistics.fmean(per_trade_returns) if per_trade_returns else None
    best_trade_return = max(per_trade_returns) if per_trade_returns else None
    worst_trade_return = min(per_trade_returns) if per_trade_returns else None

    return BacktestMetrics(
        total_return=total_return,
        sharpe=sharpe,
        max_drawdown=max_dd,
        max_drawdown_duration_bars=max_dd_duration,
        win_rate=win_rate,
        trade_count=trade_count,
        buy_and_hold_return=buy_and_hold_return,
        calmar=calmar,
        sortino=sortino,
        profit_factor=profit_factor,
        expectancy=expectancy,
        best_trade_return=best_trade_return,
        worst_trade_return=worst_trade_return,
    )


def _buy_and_hold_return(bars: Sequence[Bar], initial_capital: float) -> float:
    """Return `(last_close / first_close) - 1` for the bar series.

    `initial_capital` is accepted for symmetry with the other helpers; the
    buy-and-hold return is a ratio and does not depend on capital. Empty
    bars raise — the engine validates `bars` non-empty before calling.
    """

    if not bars:
        raise ValueError("bars must not be empty")
    _ = initial_capital  # accepted for symmetry; unused
    return (bars[-1].close / bars[0].close) - 1.0


__all__ = [
    "UnknownTimeframeError",
    "_apply_costs",
    "_build_equity_curve",
    "_buy_and_hold_return",
    "_calc_metrics",
    "_calmar",
    "_profit_factor",
    "_sortino",
]
