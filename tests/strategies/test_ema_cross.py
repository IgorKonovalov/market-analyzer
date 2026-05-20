"""Phase 4 done-when: EMA crossover produces a hand-computed `Trade` list.

The fixture is a 20-bar synthetic series chosen so that with `fast=3, slow=5`
the two EMAs cross exactly twice — once up, once down. The crossover bar
indices are derived from the SMA seed + `alpha = 2/(N+1)` recurrence (not by
reading the implementation's output); they are the acceptance criterion.

Fast EMA (period=3, alpha=0.5), seeded at i=2 by `SMA(closes[0..2]) = 99.0`:
    i=10: 92.0   i=11: 93.5
Slow EMA (period=5, alpha=1/3), seeded at i=4 by `SMA(closes[0..4]) = 98.0`:
    i=10: 92.666...   i=11: 93.444...
At i=10 fast < slow; at i=11 fast > slow → CROSS UP → ENTER_LONG.

Symmetrically, at i=17 fast > slow (118.96 vs 117.06) and at i=18 fast < slow
(109.48 vs 111.37) → CROSS DOWN → EXIT_LONG.

The adapter executes each signal at the OPEN of `bar_index + 1`; this fixture
sets `open == high == low == close`, so entry/exit prices equal `close` at the
next bar.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import Trade, signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import ema_cross


def _bars(closes: Sequence[float]) -> list[Bar]:
    """Build a deterministic Bar list from a close-price sequence."""

    start = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Bar] = []
    for i, close in enumerate(closes):
        out.append(
            Bar(
                symbol="TEST",
                timeframe="1d",
                event_ts=start + timedelta(days=i),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=0.0,
                source="fixture",
            )
        )
    return out


def _fixture() -> list[Bar]:
    closes = [
        100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0,
        92.0, 95.0, 100.0, 110.0, 120.0, 130.0, 125.0, 115.0, 100.0, 85.0,
    ]
    return _bars(closes)


def test_ema_cross_produces_one_hand_computed_trade() -> None:
    bars = _fixture()
    params = ema_cross.Params(fast=3, slow=5)
    signals = list(ema_cross.generate_signals(bars, params))
    trades = signals_to_trades(bars, signals)
    assert trades == [
        Trade(
            entry_bar_index=12,
            exit_bar_index=19,
            entry_price=100.0,
            exit_price=85.0,
            kind="long",
        )
    ]


def test_ema_cross_is_deterministic() -> None:
    bars = _fixture()
    params = ema_cross.Params(fast=3, slow=5)
    a = list(ema_cross.generate_signals(bars, params))
    b = list(ema_cross.generate_signals(bars, params))
    assert a == b


def test_ema_cross_has_no_lookahead() -> None:
    bars = _fixture()
    params = ema_cross.Params(fast=3, slow=5)
    full = list(ema_cross.generate_signals(bars, params))
    for sig in full:
        prefix = list(ema_cross.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_ema_cross_emits_no_signals_when_fewer_than_slow_bars() -> None:
    bars = _bars([100.0, 99.0, 98.0, 97.0])  # slow=5 requires 5 bars
    params = ema_cross.Params(fast=3, slow=5)
    assert list(ema_cross.generate_signals(bars, params)) == []


def test_ema_cross_params_rejects_fast_not_less_than_slow() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ema_cross.Params(fast=12, slow=12)
    with pytest.raises(ValidationError):
        ema_cross.Params(fast=26, slow=12)
