"""Phase 4 done-when: Bollinger band reversion produces a hand-computed `Trade`.

Fixture (period=3, num_std=1.0), OHLC = close throughout:

    closes = [10]*6 + [5] + [10]*5 + [15] + [10]*3

- Bars 2..5: rolling window is [10,10,10] → mean=10, stdev=0 → upper=lower=10.
  close=10 is not strictly < lower or > upper. No signals.
- Bar 6 close=5: window=[10,10,5] → mean=25/3, stdev=5*sqrt(2)/3 ≈ 2.357 →
  lower ≈ 5.976. close=5 < 5.976. Prev (bar 5) close=10 was at lower=10
  (not strictly below). CROSS DOWN through lower → ENTER_LONG.
- Bars 7..11: stdev stays bounded, close stays inside the band → no signal.
- Bar 12 close=15: window=[10,10,15] → mean=35/3, stdev=5*sqrt(2)/3 →
  upper ≈ 14.024. close=15 > 14.024. Prev (bar 11) close=10 was at
  upper=10. CROSS UP through upper → EXIT_LONG.

Adapter executes at OPEN[i+1] = close[i+1] in this fixture, so
entry_price = close[7] = 10.0 and exit_price = close[13] = 10.0. P&L is zero,
which is fine — the test verifies indices, kind, and adapter integration, not
trade economics.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import Trade, signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import bollinger


def _bars(closes: Sequence[float]) -> list[Bar]:
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
    closes = [10.0] * 6 + [5.0] + [10.0] * 5 + [15.0] + [10.0] * 3
    return _bars(closes)


def test_bollinger_produces_one_hand_computed_trade() -> None:
    bars = _fixture()
    params = bollinger.Params(period=3, num_std=1.0)
    signals = list(bollinger.generate_signals(bars, params))
    trades = signals_to_trades(bars, signals)
    assert trades == [
        Trade(
            entry_bar_index=7,
            exit_bar_index=13,
            entry_price=10.0,
            exit_price=10.0,
            kind="long",
        )
    ]


def test_bollinger_is_deterministic() -> None:
    bars = _fixture()
    params = bollinger.Params(period=3, num_std=1.0)
    a = list(bollinger.generate_signals(bars, params))
    b = list(bollinger.generate_signals(bars, params))
    assert a == b


def test_bollinger_has_no_lookahead() -> None:
    bars = _fixture()
    params = bollinger.Params(period=3, num_std=1.0)
    full = list(bollinger.generate_signals(bars, params))
    for sig in full:
        prefix = list(bollinger.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_bollinger_emits_no_signals_when_fewer_than_period_bars() -> None:
    bars = _bars([10.0, 10.0])  # period=3 requires 3 bars before first band
    assert list(bollinger.generate_signals(bars, bollinger.Params(period=3, num_std=1.0))) == []


def test_bollinger_params_validates_field_constraints() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        bollinger.Params(period=1)
    with pytest.raises(ValidationError):
        bollinger.Params(num_std=0.0)
    with pytest.raises(ValidationError):
        bollinger.Params(num_std=-1.0)
