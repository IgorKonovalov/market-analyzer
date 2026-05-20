"""Fixture stub for duplicate-id discovery test (paired with one.py)."""

from market_analyser.contracts import StrategyMeta

META = StrategyMeta(
    id="dup",
    name="Two",
    description="",
    version="0.0.0",
    timeframes=("1d",),
)
