"""Public contract surface for `market_analyser`.

Strategies, the backtester, and the UI all import from this module — never
directly from internal submodules. `Bar` is re-exported from
`market_analyser.data.types` so a strategy needs only a single import root.
"""

from __future__ import annotations

from market_analyser.contracts.strategy import (
    BaseParams,
    DuplicateStrategyError,
    Signal,
    SignalKind,
    StrategyMeta,
    StrategyProtocol,
    discover,
)
from market_analyser.data.types import Bar

__all__ = [
    "Bar",
    "BaseParams",
    "DuplicateStrategyError",
    "Signal",
    "SignalKind",
    "StrategyMeta",
    "StrategyProtocol",
    "discover",
]
