"""Phase 4 done-when: Supertrend produces a hand-computed `Trade` list.

Fixture (period=2, multiplier=1.0), OHLC = close throughout. With high == low,
true range collapses to `|close[i] - close[i-1]|`, which makes the band math
tractable by hand.

    closes = [50, 50, 50, 50, 60, 70, 80, 90, 80, 70, 60, 50, 40]

TR series (TR[0] undefined; TR[i] = |close[i] - close[i-1]| since high=low):
    [_, 0, 0, 0, 10, 10, 10, 10, 10, 10, 10, 10, 10]

ATR Wilder, seed at i=2 with SMA(TR[1..2]) = 0:
    ATR[2..]   = [0, 0, 5, 7.5, 8.75, 9.375, 9.6875, 9.84375, 9.921875,
                  9.9609375, 9.98046875]

Basic bands (hl2 = close in this fixture; bu = close + ATR, bl = close - ATR):
    bu[4..8] = [65, 77.5, 88.75, 99.375, 89.6875]
    bl[4..8] = [55, 62.5, 71.25, 80.625, 70.3125]

Final bands (recursive rule); seeded at i=2 with basic[2] = (50, 50):
    fu[3]=50, fu[4]=50  (close[3]=50, not > 50; bu[4]=65 not < 50 → keep 50)
    fu[5]=77.5          (close[4]=60 > fu[4]=50 → take bu[5])
    fu[6]=77.5          (close[5]=70 not > 77.5; bu[6]=88.75 not < 77.5 → keep)
    fu[7]=99.375        (close[6]=80 > 77.5 → take bu[7])
    fu[8]=89.6875       (bu[8]=89.6875 < 99.375 → take)

    fl[3]=50, fl[4]=55  (bl[4]=55 > 50 → take)
    fl[5]=62.5          (bl[5] > 55 → take)
    fl[6]=71.25         (bl[6] > 62.5 → take)
    fl[7]=80.625        (bl[7] > 71.25 → take)
    fl[8]=80.625        (bl[8]=70.3125 not > 80.625; close[7]=90 not < 80.625 → keep)

Direction track (seeded "down" at i=2, evaluated from i=3):
    i=3: close=50, fu=50; 50 not > 50 → stay down.
    i=4: close=60, fu=50; 60 > 50 → flip to UP. ENTER_LONG at i=4.
    i=5..7: close grows, fl rises with it; close >= fl → stay up.
    i=8: close=80, fl=80.625; 80 < 80.625 → flip to DOWN. EXIT_LONG at i=8.

Adapter executes at OPEN[i+1] = close[i+1]:
    entry_price = close[5] = 70.0
    exit_price  = close[9] = 70.0  (round-trip P&L = 0; the test verifies
                                    indices and adapter integration, not economics)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import Trade, signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import supertrend


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
    closes = [50.0, 50.0, 50.0, 50.0, 60.0, 70.0, 80.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0]
    return _bars(closes)


def test_supertrend_produces_one_hand_computed_trade() -> None:
    bars = _fixture()
    params = supertrend.Params(period=2, multiplier=1.0)
    signals = list(supertrend.generate_signals(bars, params))
    trades = signals_to_trades(bars, signals)
    assert trades == [
        Trade(
            entry_bar_index=5,
            exit_bar_index=9,
            entry_price=70.0,
            exit_price=70.0,
            kind="long",
        )
    ]


def test_supertrend_is_deterministic() -> None:
    bars = _fixture()
    params = supertrend.Params(period=2, multiplier=1.0)
    a = list(supertrend.generate_signals(bars, params))
    b = list(supertrend.generate_signals(bars, params))
    assert a == b


def test_supertrend_has_no_lookahead() -> None:
    bars = _fixture()
    params = supertrend.Params(period=2, multiplier=1.0)
    full = list(supertrend.generate_signals(bars, params))
    for sig in full:
        prefix = list(supertrend.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_supertrend_emits_no_signals_when_fewer_than_period_plus_two_bars() -> None:
    # period=2 needs the ATR seed at i=2 plus at least one decision bar at
    # i=3. A 3-bar series can seed ATR but has no decision bar.
    bars = _bars([50.0, 50.0, 50.0])
    assert list(supertrend.generate_signals(bars, supertrend.Params(period=2, multiplier=1.0))) == []


def test_supertrend_params_validates_field_constraints() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        supertrend.Params(period=1)
    with pytest.raises(ValidationError):
        supertrend.Params(multiplier=0.0)
    with pytest.raises(ValidationError):
        supertrend.Params(multiplier=-1.0)
