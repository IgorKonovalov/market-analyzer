"""Strategy contract — `Signal`, `BaseParams`, `StrategyMeta`, `StrategyProtocol`.

Per [ADR-0004](../../docs/architecture/adrs/0004-strategy-interface.md) and
[Plan 0002](../../docs/architecture/plans/0002-strategy-interface.md):
a strategy is a Python module that exports `META`, `Params` (subclass of
`BaseParams`), and `generate_signals(bars, params)`. The contract is the entire
public surface; positions, capital, costs, equity, and metrics live in the
backtest engine, not here.

Strategies must be pure: same `bars + params` in, same signals out. No
module-level state, no I/O. A decision at `bar_index = i` may only see
`bars[0..=i]` — see `best-practices.md` (no-lookahead rule).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Sequence
from enum import StrEnum
from types import ModuleType
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from market_analyser.data.types import Bar


class SignalKind(StrEnum):
    """The vocabulary of strategy decisions. Short signals reserved, not implemented."""

    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"


class Signal(BaseModel):
    """A strategy decision emitted at a specific bar.

    `bar_index` is the closing bar's index in the input series. The backtest
    engine simulates execution at the OPEN of `bar_index + 1` to avoid
    lookahead. Strategies MUST NOT produce signals that depend on data beyond
    `bar_index`.
    """

    model_config = ConfigDict(frozen=True)

    bar_index: int
    kind: SignalKind
    reason: str | None = None


class StrategyMeta(BaseModel):
    """Identity card for a strategy module. `id` is the discovery key."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    version: str
    timeframes: tuple[str, ...]


class BaseParams(BaseModel):
    """Base class for strategy `Params` models.

    Named `BaseParams` (not `Params`) so each strategy module can declare its
    own `class Params(BaseParams):` without shadowing the imported symbol.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# `@runtime_checkable` is here for static-type-checker friendliness; strategies
# are *modules*, not classes, so `isinstance(mod, StrategyProtocol)` is not
# reliable for class-attribute protocols at runtime. `discover()` validates
# `META`, `Params`, and `generate_signals` on each module via `getattr` +
# explicit type checks instead.
@runtime_checkable
class StrategyProtocol(Protocol):
    """The shape every strategy module must satisfy."""

    META: StrategyMeta
    Params: type[BaseParams]

    def generate_signals(self, bars: Sequence[Bar], params: BaseParams) -> Sequence[Signal]: ...


class DuplicateStrategyError(RuntimeError):
    """Two discovered modules declared the same `META.id`."""


def discover(package: str = "market_analyser.strategies") -> dict[str, ModuleType]:
    """Walk `package` and return every strategy module keyed by `META.id`.

    Returns a `dict` in ascending `META.id` order — Python 3.7+ preserves
    insertion order, so callers can rely on the iteration order being
    deterministic across runs.

    Raises `DuplicateStrategyError` if two discovered modules share an
    `id`. Submodules whose name starts with `_` are skipped (private
    helpers). Import errors in strategy modules propagate — a broken file
    should fail loudly at startup, not be silently skipped.
    """

    pkg = importlib.import_module(package)
    pkg_path = pkg.__path__

    found: dict[str, ModuleType] = {}
    for module_info in pkgutil.iter_modules(pkg_path):
        name = module_info.name
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f"{package}.{name}")
        meta = getattr(mod, "META", None)
        if not isinstance(meta, StrategyMeta):
            continue
        if meta.id in found:
            prior = found[meta.id]
            raise DuplicateStrategyError(
                f"strategy id '{meta.id}' declared by both {prior.__name__!r} and {mod.__name__!r}"
            )
        found[meta.id] = mod

    return dict(sorted(found.items()))


__all__ = [
    "BaseParams",
    "DuplicateStrategyError",
    "Signal",
    "SignalKind",
    "StrategyMeta",
    "StrategyProtocol",
    "discover",
]
