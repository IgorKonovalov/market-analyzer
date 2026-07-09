"""The timeframe set the backtest tools accept at the MCP boundary.

The data-registry-supported set (Plan 0025), now that metrics annualize all of
it (Plan 0050 phase 1). The unsupported `1m` (no such data timeframe) is
dropped. `run_backtest`, `compare_strategies`, and `walk_forward_backtest` all
import this one alias so the three tools cannot drift; a test pins
`get_args(BACKTEST_TIMEFRAME)` equal to `SUPPORTED_TIMEFRAMES` (the data
registry's canonical set). Moved to `_shared` in Plan 0072 phase 3 so the two
sibling tools stop importing it out of `run_backtest`.
"""

from __future__ import annotations

from typing import Literal

BACKTEST_TIMEFRAME = Literal["15m", "1h", "4h", "1d", "1w"]

__all__ = ["BACKTEST_TIMEFRAME"]
