"""Unit tests for the pure `run()` orchestrator.

Each test isolates one acceptance criterion from Plan 0008 phase 2's
done-when list:

- Happy path: returns a `BacktestResult` whose identity / spec fields are
  populated from the strategy module, bars, and arguments.
- Boundary failure: missing META / Params / generate_signals raises
  `StrategyContractError`.
- Boundary failure: invalid params raise pydantic `ValidationError` at the
  `strategy.Params(**params)` call site, not after.
- Boundary failure: empty bars raise `ValueError`.
- Boundary failure: unknown timeframe raises `UnknownTimeframeError` (via
  the helpers — `run()` does not silently guess annualization).
- Purity: `run()` performs no filesystem or network I/O.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.backtest import (
    BacktestResult,
    ENGINE_VERSION,
    StrategyContractError,
    UnknownTimeframeError,
    bars_hash,
    run,
)
from market_analyser.contracts.strategy import BaseParams
from market_analyser.data.types import Bar
from market_analyser.strategies import rsi as rsi_strategy


def _bars(closes: Sequence[float], symbol: str = "TEST") -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    prev_close = closes[0]
    for i, c in enumerate(closes):
        bar_open = prev_close
        high = max(bar_open, c)
        low = min(bar_open, c)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=start + timedelta(days=i),
                open=bar_open,
                high=high,
                low=low,
                close=c,
                volume=0.0,
                source="fixture",
            )
        )
        prev_close = c
    return bars


def _rsi_friendly_closes() -> list[float]:
    """30 closes designed to give RSI(14) at least one cross-down + cross-up."""

    import math

    return [
        round(100.0 + math.sin(i / 4.0) * 12.0, 4)
        for i in range(30)
    ]


def test_happy_path_populates_identity_and_spec() -> None:
    bars = _bars(_rsi_friendly_closes(), symbol="AAPL")
    result = run(
        rsi_strategy,
        bars,
        {"period": 14, "oversold": 30.0, "overbought": 70.0},
        timeframe="1d",
    )
    assert isinstance(result, BacktestResult)
    assert result.strategy_id == "rsi"
    assert result.strategy_version == rsi_strategy.META.version
    assert result.symbol == "AAPL"
    assert result.timeframe == "1d"
    assert result.range_start == bars[0].event_ts
    assert result.range_end == bars[-1].event_ts
    assert result.bars_hash == bars_hash(bars)
    assert result.engine_version == ENGINE_VERSION
    assert len(result.equity_curve) == len(bars)
    assert result.sizing == "fixed_fraction"
    assert result.initial_capital == 10_000.0
    assert result.costs == {"commission_bps": 0.0, "slippage_bps": 0.0}
    assert result.params == {"period": 14, "oversold": 30.0, "overbought": 70.0}
    assert isinstance(result.run_id, str) and len(result.run_id) == 32
    assert result.finished_at >= result.started_at


def test_costs_threaded_through_to_costs_dict() -> None:
    bars = _bars(_rsi_friendly_closes())
    result = run(
        rsi_strategy,
        bars,
        {"period": 14, "oversold": 30.0, "overbought": 70.0},
        timeframe="1d",
        commission_bps=10.0,
        slippage_bps=5.0,
    )
    assert result.costs == {"commission_bps": 10.0, "slippage_bps": 5.0}


def _make_module_missing(attr: str) -> ModuleType:
    """Return a SimpleNamespace masquerading as a strategy module, missing one attr."""

    fake = SimpleNamespace(
        __name__="fake_strategy",
        META=rsi_strategy.META,
        Params=rsi_strategy.Params,
        generate_signals=rsi_strategy.generate_signals,
    )
    delattr(fake, attr)
    return fake  # type: ignore[return-value]


def test_missing_meta_raises_strategy_contract_error() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    with pytest.raises(StrategyContractError) as excinfo:
        run(_make_module_missing("META"), bars, {}, timeframe="1d")
    assert "META" in str(excinfo.value)


def test_missing_params_raises_strategy_contract_error() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    with pytest.raises(StrategyContractError) as excinfo:
        run(_make_module_missing("Params"), bars, {}, timeframe="1d")
    assert "Params" in str(excinfo.value)


def test_missing_generate_signals_raises_strategy_contract_error() -> None:
    bars = _bars([100.0, 101.0, 102.0])
    with pytest.raises(StrategyContractError) as excinfo:
        run(_make_module_missing("generate_signals"), bars, {}, timeframe="1d")
    assert "generate_signals" in str(excinfo.value)


def test_invalid_params_raise_validation_error_at_boundary() -> None:
    bars = _bars(_rsi_friendly_closes())
    # RSI's Params constrains period >= 2; period=1 must raise at construction.
    with pytest.raises(ValidationError):
        run(
            rsi_strategy,
            bars,
            {"period": 1, "oversold": 30.0, "overbought": 70.0},
            timeframe="1d",
        )


def test_empty_bars_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        run(
            rsi_strategy,
            [],
            {"period": 14, "oversold": 30.0, "overbought": 70.0},
            timeframe="1d",
        )
    assert "bars" in str(excinfo.value).lower()


def test_unknown_timeframe_raises_unknown_timeframe_error() -> None:
    bars = _bars(_rsi_friendly_closes())
    with pytest.raises(UnknownTimeframeError) as excinfo:
        run(
            rsi_strategy,
            bars,
            {"period": 14, "oversold": 30.0, "overbought": 70.0},
            timeframe="5m",
        )
    assert "5m" in str(excinfo.value)


def test_accepts_baseparams_instance_directly() -> None:
    bars = _bars(_rsi_friendly_closes())
    params = rsi_strategy.Params(period=14, oversold=30.0, overbought=70.0)
    result = run(rsi_strategy, bars, params, timeframe="1d")
    assert result.params == {"period": 14, "oversold": 30.0, "overbought": 70.0}


def test_purity_no_filesystem_or_network_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run()` does no I/O — `open`/`Path.write_*`/`httpx`/`requests` calls fail loudly."""

    import builtins
    import pathlib

    real_open = builtins.open

    def fail_open(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"run() opened a file: args={args!r}")

    def fail_write_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"run() wrote to {self!r}")

    def fail_write_bytes(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"run() wrote bytes to {self!r}")

    monkeypatch.setattr(builtins, "open", fail_open)
    monkeypatch.setattr(pathlib.Path, "write_text", fail_write_text)
    monkeypatch.setattr(pathlib.Path, "write_bytes", fail_write_bytes)

    # httpx + requests: stub out at the module level if importable. The engine
    # never imports them, so this is belt-and-suspenders.
    for name in ("httpx", "requests"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue

        def fail_request(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"run() called {name} HTTP")

        for verb in ("get", "post", "put", "delete", "request"):
            if hasattr(mod, verb):
                monkeypatch.setattr(mod, verb, fail_request)

    bars = _bars(_rsi_friendly_closes())
    result = run(
        rsi_strategy,
        bars,
        {"period": 14, "oversold": 30.0, "overbought": 70.0},
        timeframe="1d",
    )
    # Restore real open so the test framework can clean up.
    monkeypatch.setattr(builtins, "open", real_open)
    assert isinstance(result, BacktestResult)
