"""Smoke test template — copy this for every new strategy.

This is NOT a backtest. It's a minimum-viable check that the module loads,
the function executes, and the output has the right shape. Backtesting
(P&L, Sharpe, drawdown) is the backtester skill's job.

Replace <slug> with the strategy slug throughout.
"""

from __future__ import annotations

import math

import pytest

from market_analyser.contracts.strategy import Bar, Signal
from market_analyser.strategies import <slug> as strategy_module


# ---------------------------------------------------------------------------
# Fixture: a small deterministic bar series. NOT market data — synthetic,
# enough to exercise the strategy without being subject to data drift.
# ---------------------------------------------------------------------------

def _make_bars(n: int = 100) -> list[Bar]:
    """Generate n bars with a smooth sinusoidal close + tiny tick range.

    Deterministic — same n always produces the same bars. No random, no clock.
    """
    bars: list[Bar] = []
    for i in range(n):
        # close oscillates between ~95 and ~105
        close = 100.0 + 5.0 * math.sin(i / 5.0)
        bars.append(
            Bar(
                ts=1_700_000_000 + i * 3600,   # 1h cadence, fixed epoch start
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000.0,
            )
        )
    return bars


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_module_exports_contract() -> None:
    """The module exports META, Params, and generate_signals."""
    assert hasattr(strategy_module, "META")
    assert hasattr(strategy_module, "Params")
    assert callable(strategy_module.generate_signals)


def test_params_default_construct() -> None:
    """Params() with no arguments succeeds (every field has a default)."""
    params = strategy_module.Params()
    assert params is not None


def test_generate_signals_returns_sequence_of_signals() -> None:
    """The function runs and returns a sequence of Signal objects."""
    bars = _make_bars(100)
    params = strategy_module.Params()
    signals = strategy_module.generate_signals(bars, params)

    assert hasattr(signals, "__iter__"), "generate_signals must return an iterable"
    for sig in signals:
        assert isinstance(sig, Signal), f"non-Signal in output: {sig!r}"


def test_signal_indices_in_range() -> None:
    """No signal points at a bar index outside the input range."""
    bars = _make_bars(100)
    params = strategy_module.Params()
    signals = strategy_module.generate_signals(bars, params)

    for sig in signals:
        assert 0 <= sig.bar_index < len(bars), (
            f"signal at bar_index={sig.bar_index} is out of range [0, {len(bars)})"
        )


def test_deterministic() -> None:
    """Same inputs produce the same signals every time."""
    bars = _make_bars(100)
    params = strategy_module.Params()
    out1 = list(strategy_module.generate_signals(bars, params))
    out2 = list(strategy_module.generate_signals(bars, params))
    assert out1 == out2, "strategy is non-deterministic"


# Optional: a test with extreme params. Delete if there's no "extreme" sensible.
@pytest.mark.parametrize("field_overrides", [
    {},                                       # defaults
    # {"period": 2},                          # minimum allowed
    # {"period": 200},                        # maximum allowed
])
def test_does_not_crash_with_edge_params(field_overrides: dict) -> None:
    bars = _make_bars(100)
    params = strategy_module.Params(**field_overrides)
    list(strategy_module.generate_signals(bars, params))   # just no exception
