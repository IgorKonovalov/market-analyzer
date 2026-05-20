"""Phase 4 done-when: MACD crossover produces a hand-computed `Trade` list.

Fixture (fast=2, slow=4, signal=2), OHLC = close throughout:

    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92,
              95, 98, 101, 104, 107, 110, 113,
              110, 105, 100, 95]

The down-leg (bars 0..8) holds fast_ema == slow_ema - 1 → MACD ≡ -1, and the
signal EMA seeds at -1 too → MACD == signal throughout. Cross-up requires
MACD > signal *strictly*, so no signal fires in this region.

Up-leg starts at bar 9 (close=95 vs prev 92). Fast EMA accelerates faster than
slow EMA → MACD turns positive at i=9 (≈ +0.067), pulling above the signal
line (≈ -0.289). CROSS UP → ENTER_LONG at i=9.

The peak is at bar 15 (close=113). After that closes fall back; fast EMA falls
faster → MACD drops. At i=15 MACD ≈ 2.833 > signal ≈ 2.755 (still above); at
i=16 MACD ≈ 1.299 < signal ≈ 1.784 → CROSS DOWN → EXIT_LONG at i=16.

Adapter executes at OPEN[i+1] = close[i+1] in this fixture:
    entry_price = close[10] = 98.0
    exit_price  = close[17] = 105.0
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import Trade, signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import macd


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
    closes = [
        100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0,
        95.0, 98.0, 101.0, 104.0, 107.0, 110.0, 113.0,
        110.0, 105.0, 100.0, 95.0,
    ]
    return _bars(closes)


def test_macd_produces_one_hand_computed_trade() -> None:
    bars = _fixture()
    params = macd.Params(fast=2, slow=4, signal=2)
    signals = list(macd.generate_signals(bars, params))
    trades = signals_to_trades(bars, signals)
    assert trades == [
        Trade(
            entry_bar_index=10,
            exit_bar_index=17,
            entry_price=98.0,
            exit_price=105.0,
            kind="long",
        )
    ]


def test_macd_is_deterministic() -> None:
    bars = _fixture()
    params = macd.Params(fast=2, slow=4, signal=2)
    a = list(macd.generate_signals(bars, params))
    b = list(macd.generate_signals(bars, params))
    assert a == b


def test_macd_has_no_lookahead() -> None:
    bars = _fixture()
    params = macd.Params(fast=2, slow=4, signal=2)
    full = list(macd.generate_signals(bars, params))
    for sig in full:
        prefix = list(macd.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_macd_emits_no_signals_when_signal_line_never_seeds() -> None:
    # slow=4 + signal=2 needs at least 5 bars before signal line seeds.
    # 4 bars: slow EMA seeds at i=3, MACD defined only at i=3, signal needs 2
    # MACD values to seed → never seeds. No signals possible.
    bars = _bars([100.0, 99.0, 98.0, 97.0])
    assert list(macd.generate_signals(bars, macd.Params(fast=2, slow=4, signal=2))) == []


def test_macd_params_rejects_fast_not_less_than_slow() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        macd.Params(fast=12, slow=12)
    with pytest.raises(ValidationError):
        macd.Params(fast=26, slow=12)
