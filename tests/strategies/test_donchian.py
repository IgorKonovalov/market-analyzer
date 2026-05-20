"""Phase 4 done-when: Donchian breakout produces a hand-computed `Trade` list.

Fixture (period=4), with OHLC = close throughout:

- Bars 0..7: close = 10.0 → the 4-bar prior-high window is always [10,10,10,10]
  → upper channel = 10.0 → close (10) is not strictly greater. No entries.
- Bar 8 close = 15.0 → upper = max(prev 4) = 10.0; 15.0 > 10.0 → ENTER_LONG.
- Bars 9..13 close in {14, 13, 13, 13, 13}: stay inside both channels (lower
  bottoms at 13 once the window holds a 13). No signal.
- Bar 14 close = 5.0 → lower = min([13,13,13,13]) = 13.0; 5.0 < 13.0 → EXIT_LONG.

Adapter executes at `OPEN[i + 1]`, which equals `close[i + 1]` in this fixture:
entry_price = close[9] = 14.0; exit_price = close[15] = 5.0.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import Trade, signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import donchian


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
    closes = [10.0] * 8 + [15.0, 14.0, 13.0, 13.0, 13.0, 13.0, 5.0, 5.0, 5.0, 5.0]
    return _bars(closes)


def test_donchian_produces_one_hand_computed_trade() -> None:
    bars = _fixture()
    params = donchian.Params(period=4)
    signals = list(donchian.generate_signals(bars, params))
    trades = signals_to_trades(bars, signals)
    assert trades == [
        Trade(
            entry_bar_index=9,
            exit_bar_index=15,
            entry_price=14.0,
            exit_price=5.0,
            kind="long",
        )
    ]


def test_donchian_is_deterministic() -> None:
    bars = _fixture()
    params = donchian.Params(period=4)
    a = list(donchian.generate_signals(bars, params))
    b = list(donchian.generate_signals(bars, params))
    assert a == b


def test_donchian_has_no_lookahead() -> None:
    bars = _fixture()
    params = donchian.Params(period=4)
    full = list(donchian.generate_signals(bars, params))
    for sig in full:
        prefix = list(donchian.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_donchian_emits_no_signals_when_fewer_than_period_plus_one_bars() -> None:
    # period=4 requires at least 5 bars before the first decision (i=4 reads
    # bars[0..3]). A 4-bar series is short by one.
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    assert list(donchian.generate_signals(bars, donchian.Params(period=4))) == []


def test_donchian_params_validates_field_constraints() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        donchian.Params(period=1)
    with pytest.raises(ValidationError):
        donchian.Params(period=0)
