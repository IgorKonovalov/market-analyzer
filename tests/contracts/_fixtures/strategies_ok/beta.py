"""Fixture stub strategy — META only, no Params or generate_signals.

`discover()` keys by `META.id`. Authored in alphabetical-id order opposite to
filename to confirm the discover() result is sorted by id, not by filename.
"""

from market_analyser.contracts import StrategyMeta

META = StrategyMeta(
    id="alpha",
    name="Alpha (fixture)",
    description="",
    version="0.0.0",
    timeframes=("1d",),
)
