"""Fixture stub for duplicate-id discovery test (paired with two.py)."""

from market_analyser.contracts import StrategyMeta

META = StrategyMeta(
    id="dup",
    name="One",
    description="",
    version="0.0.0",
    timeframes=("1d",),
)
