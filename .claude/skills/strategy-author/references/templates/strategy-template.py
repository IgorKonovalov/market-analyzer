"""Strategy template — copy this and fill in the blanks.

Replace every <PLACEHOLDER> below. Keep the structure: three top-level names
(META, Params, generate_signals) and nothing else exported publicly.

Read docs/architecture/adrs/0004-strategy-interface.md before editing the
shape of this file — the contract wins on any conflict with this template.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from market_analyser.contracts.strategy import Bar, Signal, StrategyMeta

# Import the indicators this strategy needs. Drop the ones you don't use.
# Indicators are written in-house per ADR-0009; verify analysis/indicators.py exists first.
from market_analyser.analysis.indicators import (
    calc_rsi,
    # calc_bollinger, calc_macd, calc_ema, calc_supertrend, calc_donchian
)


META = StrategyMeta(
    id="<slug>",                          # lowercase, hyphen-separated; must match filename
    name="<Human-readable name>",
    description="<One sentence on what this strategy does and when it might work>",
    version=1,                            # bump on behavior-changing edits
    timeframes=["1h", "4h", "1d"],        # which bar intervals this strategy supports
)


class Params(BaseModel):
    """Tunable parameters for this strategy.

    Every field needs a type, a default, and ideally a Field(ge=..., le=...) range
    so the UI can render a bounded form. Field descriptions become tooltips in the UI.
    """
    period: int = Field(default=14, ge=2, le=200, description="<what this controls>")
    threshold: float = Field(default=30.0, ge=0.0, le=100.0, description="<what this controls>")
    # Add more fields as needed. Be conservative — 2-4 params is usually right.

    model_config = {"frozen": True}        # immutable params == deterministic backtests


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    """Emit signals for this strategy.

    PURE function. No I/O, no module-level state, no time.time(), no random.
    Reads bars only up to and including the current index — no lookahead.

    Returns a list (or any Sequence) of Signal events. The backtester applies
    them at the next bar's open price; you do not own execution timing.
    """
    if len(bars) < params.period + 1:
        return []

    closes = [b.close for b in bars]
    # Example: RSI-bound entries/exits. Replace with your strategy's logic.
    rsi = calc_rsi(closes, params.period)

    signals: list[Signal] = []
    in_position = False  # local variable, NOT instance state — recomputed each call

    for i in range(1, len(bars)):
        if rsi[i] is None:
            continue
        # Decision at bar i uses only data 0..=i. Anything past i is a bug.
        if not in_position and rsi[i] < params.threshold:
            signals.append(Signal(kind="enter_long", bar_index=i))
            in_position = True
        elif in_position and rsi[i] > (100 - params.threshold):
            signals.append(Signal(kind="exit_long", bar_index=i))
            in_position = False

    return signals
