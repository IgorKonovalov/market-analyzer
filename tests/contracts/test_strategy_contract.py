"""Phase 1 done-when: the strategy contract surface.

Asserts each load-bearing property of `Signal`, `BaseParams`, `StrategyMeta`,
and `StrategyProtocol` so a future contributor cannot quietly weaken them
without a test failing.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
    StrategyProtocol,
)
from market_analyser.strategies import rsi


def test_signal_kind_values_are_stable() -> None:
    # Persisted signals on disk will key on these strings; flipping a value
    # would silently invalidate old trade logs.
    assert SignalKind.ENTER_LONG.value == "enter_long"
    assert SignalKind.EXIT_LONG.value == "exit_long"


def test_signal_is_frozen() -> None:
    sig = Signal(bar_index=3, kind=SignalKind.ENTER_LONG)
    with pytest.raises(ValidationError):
        sig.bar_index = 4

def test_signal_default_reason_is_none() -> None:
    sig = Signal(bar_index=0, kind=SignalKind.EXIT_LONG)
    assert sig.reason is None


def test_strategy_meta_is_frozen() -> None:
    meta = StrategyMeta(
        id="x",
        name="X",
        description="",
        version="0.0.1",
        timeframes=("1d",),
    )
    with pytest.raises(ValidationError):
        meta.id = "y"

def test_base_params_rejects_extras() -> None:
    class P(BaseParams):
        period: int = 14

    with pytest.raises(ValidationError):
        P(period=14, not_a_field=99)  # type: ignore[call-arg]


def test_base_params_is_frozen() -> None:
    class P(BaseParams):
        period: int = 14

    p = P()
    with pytest.raises(ValidationError):
        p.period = 5

def test_rsi_module_has_meta_params_generate_signals() -> None:
    # The contract is a module shape, not a class. `discover()` checks the
    # same three attributes at runtime — this test mirrors that check on the
    # one reference strategy so the contract is exercised end-to-end.
    assert isinstance(rsi.META, StrategyMeta)
    assert rsi.META.id == "rsi"
    assert issubclass(rsi.Params, BaseParams)
    assert callable(rsi.generate_signals)


def test_rsi_satisfies_static_protocol() -> None:
    # `@runtime_checkable` works on modules for class-attribute Protocols;
    # this confirms the static signature matches.
    assert isinstance(rsi, StrategyProtocol)


def test_rsi_params_emits_json_schema() -> None:
    schema = rsi.Params.model_json_schema()
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"period", "oversold", "overbought"}


def test_generate_signals_accepts_empty_bars() -> None:
    bars: Sequence[Bar] = []
    out = rsi.generate_signals(bars, rsi.Params())
    assert list(out) == []
