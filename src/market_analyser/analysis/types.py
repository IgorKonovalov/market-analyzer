"""Shared analysis types (Plan 0018).

`PatternHit` (phase 2) is defined here — the plan's data-shapes section places the
shared types in `analysis/types.py`, and `patterns.py` emits it. Phase 3 extends
this module with `ConditionSnapshot`, `Trend`, and `MomentumStance`.

All models are frozen with `extra="forbid"`: boundary-validated, trustable
downstream.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Direction = Literal["bullish", "bearish", "neutral"]


class PatternHit(BaseModel):
    """A candlestick pattern detected at a specific bar.

    `bar_index` is the index of the *latest* bar of the formation in the input
    series (the bar at which the pattern completes). `strength` is a detector-
    defined score in `[0, 1]` — relative conviction, not a probability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bar_index: int
    pattern: str
    direction: Direction
    strength: float


__all__ = ["Direction", "PatternHit"]
