"""Fixture stub strategy — paired with beta.py to verify discover() sort order."""

from market_analyser.contracts import StrategyMeta

META = StrategyMeta(
    id="zeta",
    name="Zeta (fixture)",
    description="",
    version="0.0.0",
    timeframes=("1d",),
)
