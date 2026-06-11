"""Tests for the pure live-signal evaluation core (Plan 0026 phase 1).

These assert *values*, not just that the function runs — the load-bearing claim
of the plan is behavioural (the last-closed-bar signal is reported, NOT dropped
the way `signals_to_trades` drops it), so the divergence is pinned explicitly.

Two fixture styles are used deliberately:

- A real strategy (`rsi`) for the end-to-end "RSI just entered on the last
  closed bar" and "too few bars to warm up" cases — proving the core composes
  with a genuine `generate_signals`.
- Tiny fake strategy modules that emit a *known* signal stream for the
  state-machine / freshness / forming-bar cases — so the core's fold and
  closed-bar filter are tested precisely, without fighting indicator math.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from types import ModuleType

import pytest

from market_analyser.backtest import evaluate_signals, signals_to_trades
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
)
from market_analyser.data.timeframes import bar_duration
from market_analyser.strategies import rsi

# Late enough that every bar in these fixtures is closed (their first bar opens
# 2026-01-01), so the closed-bar filter keeps the whole series unless a test
# deliberately picks a `now` that leaves the latest bar forming.
_FAR_FUTURE = datetime(2030, 1, 1, tzinfo=UTC)
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class _NoParams(BaseParams):
    pass


def _bars(
    closes: Sequence[float],
    *,
    timeframe: str = "1d",
    symbol: str = "TEST",
) -> list[Bar]:
    """Build a Bar series whose closes are `closes`, spaced one bar-duration
    apart (so the closed-bar math reflects real cadence). OHLC are cloned from
    the close — the core only reads `close`/`event_ts`/`symbol`/`timeframe`."""

    step = bar_duration(timeframe)
    out: list[Bar] = []
    for i, price in enumerate(closes):
        out.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                event_ts=_EPOCH + step * i,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0.0,
                source="fixture",
            )
        )
    return out


def _make_strategy(
    generate: Callable[[Sequence[Bar], BaseParams], Sequence[Signal]],
) -> ModuleType:
    """A minimal contract-shaped strategy module emitting `generate`'s output."""

    mod = ModuleType("fake_strategy")
    setattr(
        mod,
        "META",
        StrategyMeta(
            id="fake",
            name="Fake",
            description="test double",
            version="1.0.0",
            timeframes=("15m", "1h", "1d"),
        ),
    )
    setattr(mod, "Params", _NoParams)
    setattr(mod, "generate_signals", generate)
    return mod


def _fixed(signals: Sequence[Signal]) -> ModuleType:
    """A strategy that always emits `signals`, regardless of the bars."""

    def _gen(bars: Sequence[Bar], params: BaseParams) -> Sequence[Signal]:
        return list(signals)

    return _make_strategy(_gen)


def _enter_on_last() -> ModuleType:
    """A strategy that emits ENTER_LONG on the LAST bar it is handed.

    Used by the forming-bar tests: because the core feeds only the *closed*
    bars, the emitted `bar_index` reveals exactly how many bars the strategy
    saw — if a forming bar leaked through, the index would be one too high.
    """

    def _gen(bars: Sequence[Bar], params: BaseParams) -> Sequence[Signal]:
        return [Signal(bar_index=len(bars) - 1, kind=SignalKind.ENTER_LONG)]

    return _make_strategy(_gen)


# --- Done-when 1: RSI oversold-and-just-entered on the last closed bar --------


def test_rsi_fresh_entry_on_last_closed_bar() -> None:
    # A strictly declining 15-bar series: RSI is undefined until bar 14 (period
    # 14), where a pure decline yields RSI 0, below the default oversold (40).
    # With prev RSI undefined, that is the first computable bar in-zone → a
    # fresh ENTER_LONG exactly on the last closed bar.
    bars = _bars([100.0 - i for i in range(15)])
    ev = evaluate_signals(rsi, bars, now=_FAR_FUTURE)

    assert ev.current_position == "long"
    assert ev.last_signal is not None
    assert ev.last_signal.kind is SignalKind.ENTER_LONG
    assert ev.last_signal.bar_index == 14  # the last closed bar
    assert ev.last_signal.event_ts == bars[14].event_ts
    assert ev.fresh_signal is True
    assert ev.bars_since_last_signal == 0
    assert ev.latest_bar_excluded_as_forming is False
    assert ev.closed_bar_count == 15
    assert ev.evaluated_through_ts == bars[-1].event_ts
    assert ev.strategy_id == "rsi"
    assert ev.symbol == "TEST"
    assert ev.timeframe == "1d"


# --- Done-when 2: a signal several bars back; position folds the full stream --


def test_stale_signal_folds_to_flat() -> None:
    bars = _bars([10.0] * 10)
    strat = _fixed(
        [
            Signal(bar_index=2, kind=SignalKind.ENTER_LONG),
            Signal(bar_index=5, kind=SignalKind.EXIT_LONG),
        ]
    )
    ev = evaluate_signals(strat, bars, now=_FAR_FUTURE)

    assert ev.current_position == "flat"  # enter@2 → long, exit@5 → flat
    assert ev.last_signal is not None
    assert ev.last_signal.kind is SignalKind.EXIT_LONG
    assert ev.last_signal.bar_index == 5
    assert ev.last_signal.event_ts == bars[5].event_ts
    assert ev.bars_since_last_signal == 4  # last closed index 9 - 5
    assert ev.fresh_signal is False


def test_stale_signal_folds_to_long() -> None:
    bars = _bars([10.0] * 10)
    strat = _fixed([Signal(bar_index=2, kind=SignalKind.ENTER_LONG)])
    ev = evaluate_signals(strat, bars, now=_FAR_FUTURE)

    assert ev.current_position == "long"
    assert ev.last_signal is not None
    assert ev.last_signal.bar_index == 2
    assert ev.bars_since_last_signal == 7
    assert ev.fresh_signal is False


# --- Done-when 3: a still-forming latest bar is excluded (daily + sub-daily) ---


@pytest.mark.parametrize("timeframe", ["1d", "1h", "15m"])
def test_forming_latest_bar_is_excluded(timeframe: str) -> None:
    bars = _bars([10.0] * 11, timeframe=timeframe)
    duration = bar_duration(timeframe)
    # `now` sits inside the last bar's window: the last bar has not closed
    # (event_ts + duration > now) but the previous one has.
    now = bars[-1].event_ts + duration // 2
    ev = evaluate_signals(_enter_on_last(), bars, now=now)

    assert ev.latest_bar_excluded_as_forming is True
    assert ev.closed_bar_count == 10  # the 11th bar was dropped
    assert ev.evaluated_through_ts == bars[-2].event_ts
    # The strategy emitted ENTER on the last bar it SAW; index 9 (not 10) proves
    # the forming bar never reached generate_signals.
    assert ev.last_signal is not None
    assert ev.last_signal.bar_index == 9
    assert ev.last_signal.event_ts == bars[-2].event_ts
    assert ev.fresh_signal is True


@pytest.mark.parametrize("timeframe", ["1d", "1h", "15m"])
def test_closed_latest_bar_is_kept(timeframe: str) -> None:
    bars = _bars([10.0] * 11, timeframe=timeframe)
    ev = evaluate_signals(_enter_on_last(), bars, now=_FAR_FUTURE)

    assert ev.latest_bar_excluded_as_forming is False
    assert ev.closed_bar_count == 11
    assert ev.evaluated_through_ts == bars[-1].event_ts
    assert ev.last_signal is not None
    assert ev.last_signal.bar_index == 10
    assert ev.fresh_signal is True


# --- Done-when 4: too few bars to warm up → flat / None, never raises ---------


def test_insufficient_warmup_is_flat_not_error() -> None:
    bars = _bars([100.0 - i for i in range(5)])  # 5 < RSI period 14 → no signals
    ev = evaluate_signals(rsi, bars, now=_FAR_FUTURE)

    assert ev.current_position == "flat"
    assert ev.last_signal is None
    assert ev.bars_since_last_signal is None
    assert ev.fresh_signal is False
    assert ev.closed_bar_count == 5


# --- Done-when 5: the last-closed-bar signal is REPORTED, not dropped ----------


def test_last_closed_bar_signal_is_reported_not_dropped() -> None:
    bars = _bars([10.0] * 8)
    last = len(bars) - 1
    strat = _fixed([Signal(bar_index=last, kind=SignalKind.ENTER_LONG)])
    ev = evaluate_signals(strat, bars, now=_FAR_FUTURE)

    # The evaluator keeps it: fresh long on the final closed bar.
    assert ev.current_position == "long"
    assert ev.last_signal is not None
    assert ev.last_signal.bar_index == last
    assert ev.fresh_signal is True
    assert ev.bars_since_last_signal == 0

    # The divergence the plan is built on: signals_to_trades would DROP this
    # exact signal (no bars[last+1] open to execute against), producing no
    # trade. The evaluator must not inherit that behaviour.
    assert signals_to_trades(bars, [Signal(bar_index=last, kind=SignalKind.ENTER_LONG)]) == []


# --- Done-when 6: purity / determinism + the wall-clock guards ----------------


def test_referentially_transparent() -> None:
    bars = _bars([100.0 - i for i in range(15)])
    a = evaluate_signals(rsi, bars, now=_FAR_FUTURE)
    b = evaluate_signals(rsi, bars, now=_FAR_FUTURE)
    assert a == b
    assert isinstance(a, SignalEvaluation)


def test_now_is_required_keyword_only() -> None:
    bars = _bars([10.0] * 5)
    with pytest.raises(TypeError):
        evaluate_signals(rsi, bars)  # type: ignore[call-arg]


def test_naive_now_is_rejected() -> None:
    bars = _bars([10.0] * 5)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_signals(rsi, bars, now=datetime(2030, 1, 1))  # naive on purpose


def test_empty_bars_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        evaluate_signals(rsi, [], now=_FAR_FUTURE)


def test_all_forming_is_rejected() -> None:
    bars = _bars([10.0] * 3, timeframe="1d")
    # `now` before any bar has closed: every bar is still forming.
    too_early = bars[0].event_ts
    with pytest.raises(ValueError, match="still forming"):
        evaluate_signals(_enter_on_last(), bars, now=too_early)


# --- Plan 0053 phase 3: short states (ADR-0050) --------------------------------


def test_fresh_enter_short_reports_short_position() -> None:
    # Phase 3 done-when: a strategy currently emitting `enter_short` reports a
    # `short` live state, with the signal itself surfaced as fresh.
    bars = _bars([10.0] * 8)
    last = len(bars) - 1
    strat = _fixed([Signal(bar_index=last, kind=SignalKind.ENTER_SHORT)])
    ev = evaluate_signals(strat, bars, now=_FAR_FUTURE)

    assert ev.current_position == "short"
    assert ev.last_signal is not None
    assert ev.last_signal.kind is SignalKind.ENTER_SHORT
    assert ev.last_signal.bar_index == last
    assert ev.fresh_signal is True
    assert ev.bars_since_last_signal == 0


def test_short_round_trip_folds_to_flat() -> None:
    bars = _bars([10.0] * 10)
    strat = _fixed(
        [
            Signal(bar_index=2, kind=SignalKind.ENTER_SHORT),
            Signal(bar_index=5, kind=SignalKind.EXIT_SHORT),
        ]
    )
    ev = evaluate_signals(strat, bars, now=_FAR_FUTURE)

    assert ev.current_position == "flat"
    assert ev.last_signal is not None
    assert ev.last_signal.kind is SignalKind.EXIT_SHORT
    assert ev.bars_since_last_signal == 4


def test_fold_honours_single_direction_invariants() -> None:
    # `enter_long` while short and `exit_long` while short are no-ops — the
    # short survives both (mirrors the engine adapter's state machine).
    bars = _bars([10.0] * 10)
    strat = _fixed(
        [
            Signal(bar_index=1, kind=SignalKind.ENTER_SHORT),
            Signal(bar_index=3, kind=SignalKind.ENTER_LONG),
            Signal(bar_index=5, kind=SignalKind.EXIT_LONG),
        ]
    )
    ev = evaluate_signals(strat, bars, now=_FAR_FUTURE)
    assert ev.current_position == "short"


def test_same_bar_flip_folds_exit_first_like_the_engine() -> None:
    # A same-bar exit_long + enter_short flip must fold to SHORT regardless of
    # emission order — the evaluator applies the adapter's ADR-0050 ordering
    # (exits before entries within one bar).
    bars = _bars([10.0] * 10)
    emitted_enter_first = _fixed(
        [
            Signal(bar_index=2, kind=SignalKind.ENTER_LONG),
            Signal(bar_index=6, kind=SignalKind.ENTER_SHORT),
            Signal(bar_index=6, kind=SignalKind.EXIT_LONG),
        ]
    )
    ev = evaluate_signals(emitted_enter_first, bars, now=_FAR_FUTURE)
    assert ev.current_position == "short"
    # The reported last signal is the one that ends up last in execution
    # order: the short entry, not the long exit.
    assert ev.last_signal is not None
    assert ev.last_signal.kind is SignalKind.ENTER_SHORT
    assert ev.last_signal.bar_index == 6
