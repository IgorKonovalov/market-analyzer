"""Phase 1 done-when: RSI reference strategy.

The fixture is a 60-bar deterministic price series — 30 down-bars (close
decreasing by 1) followed by 30 up-bars (close increasing by 1). With default
`Params(period=14, oversold=40, overbought=60)`, Wilder's RSI produces:

- RSI[14] = 0 (after 14 consecutive losses), which crosses below `oversold`
  on its first defined reading. Expected ENTER_LONG at `bar_index = 14`.
- The up-run starts at bar 30 with RSI ≈ 7.14. RSI = 100 * (1 - (13/14)^(k+1))
  where `k` is bars into the up-run; RSI first exceeds 60 at k=12, i.e.
  `bar_index = 42` (RSI[41] ≈ 58.65, RSI[42] ≈ 61.61). Expected EXIT_LONG
  at `bar_index = 42`.

These two signal positions are computed by hand from the Wilder recurrence,
not by reading the implementation's output. They are the acceptance criteria.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.contracts import Bar
from market_analyser.strategies import rsi


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


def _down_then_up_fixture() -> list[Bar]:
    """30 bars of close-1 declines, then 30 bars of close+1 advances."""

    closes = [100.0 - i for i in range(30)] + [71.0 + i for i in range(1, 31)]
    return _bars(closes)


def test_rsi_emits_enter_long_then_exit_long_at_hand_computed_indices() -> None:
    bars = _down_then_up_fixture()
    signals = list(rsi.generate_signals(bars, rsi.Params()))
    assert [(s.bar_index, s.kind.value) for s in signals] == [
        (14, "enter_long"),
        (42, "exit_long"),
    ]


def test_rsi_emits_no_signals_when_fewer_than_period_plus_one_bars() -> None:
    # 14 bars produce 13 changes — not enough to seed the Wilder average,
    # so RSI is undefined everywhere and no signals fire.
    bars = _bars([100.0 - i for i in range(14)])
    assert list(rsi.generate_signals(bars, rsi.Params())) == []


def test_rsi_emits_no_signals_on_flat_prices() -> None:
    bars = _bars([100.0] * 60)
    # All changes are 0 → avg_gain = avg_loss = 0 → RSI = 100. RSI stays
    # above oversold throughout, and we never enter, so EXIT is unreachable.
    signals = list(rsi.generate_signals(bars, rsi.Params()))
    # RSI = 100 on the first defined bar (bar 14) crosses above overbought
    # but we're flat, so no EXIT is emitted. No ENTER either. Zero signals.
    assert signals == []


def test_rsi_is_deterministic() -> None:
    bars = _down_then_up_fixture()
    a = list(rsi.generate_signals(bars, rsi.Params()))
    b = list(rsi.generate_signals(bars, rsi.Params()))
    assert a == b


def test_rsi_has_no_lookahead() -> None:
    # A signal at bar_index = k must depend only on bars[0..=k]. Truncating
    # the input after each k must yield the same prefix of signals.
    bars = _down_then_up_fixture()
    full = list(rsi.generate_signals(bars, rsi.Params()))
    for sig in full:
        prefix = list(rsi.generate_signals(bars[: sig.bar_index + 1], rsi.Params()))
        # The signal at sig.bar_index must appear in the prefix run; signals
        # at later indices may or may not (they can't be present, because
        # those bars are absent).
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_rsi_params_validates_field_constraints() -> None:
    # period must be >= 2; oversold/overbought in [0, 100]. ValidationError
    # raises at construction — strategies cannot be instantiated with
    # nonsense params.
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        rsi.Params(period=1)
    with pytest.raises(ValidationError):
        rsi.Params(oversold=-1.0)
    with pytest.raises(ValidationError):
        rsi.Params(overbought=200.0)
