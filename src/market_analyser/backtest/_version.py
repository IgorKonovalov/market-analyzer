"""Engine-output fingerprint.

Lives in its own module so `engine.py` and `__init__.py` can both import
it without a circular dependency. The canonical re-export is from the
package's `__init__.py`; downstream code should import `ENGINE_VERSION`
from `market_analyser.backtest`, not from this private module.

Bump on any output-affecting change to the four helpers (`_apply_costs`,
`_build_equity_curve`, `_calc_metrics`, `_buy_and_hold_return`) or the
`run()` orchestrator's composition order. The Plan 0008 phase-2 golden
fixture is the secondary defence: changes that alter outputs break the
golden test and force a deliberate fixture regen + version bump.
"""

from __future__ import annotations

ENGINE_VERSION = "0.1.0"

__all__ = ["ENGINE_VERSION"]
