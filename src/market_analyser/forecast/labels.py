"""Direction labels for the forecast target (Plan 0036 phase 2, ADR-0030).

The label at bar ``i`` is the **direction of the N-bar-ahead return**:
``up`` / ``down`` / ``flat``, where ``flat`` is the band ``|fwd_return| <=
flat_band`` around zero. Computing the label *looks forward* to ``close[i +
horizon]`` — that is the whole point of a target — but the label is **never** a
feature at or before ``i`` (the feature pipeline in ``features.py`` reads only
``bars[0..=i]``). Keeping the forward-looking target strictly out of the causal
feature matrix is the no-label-leakage discipline ADR-0030 invariant 1 demands;
``tests/forecast/test_model.py`` pins it by perturbing a future bar and asserting
the label moves while the feature row does not.

The flat band is the plan's resolved open question: a configurable parameter with
a documented default (``flat_band``, ±0.1% next-bar return) so "flat" is an
explicit, tunable definition rather than an accident of equality.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.types import Bar


class Direction(StrEnum):
    """The forecast target's three classes. Ordered deterministically by value so
    the model's class order is reproducible (``DOWN`` < ``FLAT`` < ``UP``)."""

    DOWN = "down"
    FLAT = "flat"
    UP = "up"


class LabelParams(BaseModel):
    """Parameters of the labelling rule. ``horizon_bars`` is how far ahead the
    direction is measured; ``flat_band`` is the half-width of the dead zone that
    counts as ``flat`` (a fractional return, e.g. 0.001 = ±0.1%)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int = Field(default=1, ge=1)
    flat_band: float = Field(default=0.001, ge=0.0)


def build_labels(bars: Sequence[Bar], params: LabelParams | None = None) -> list[Direction | None]:
    """Build the per-bar direction label aligned to ``bars``.

    Entry ``i`` is the direction of ``close[i + horizon] / close[i] - 1`` against
    ``flat_band``, or ``None`` for the trailing ``horizon`` bars that have no
    future bar to look at (and where ``close[i]`` is zero, which cannot yield a
    well-defined return). The forward look is confined to the label; no caller
    should ever join entry ``i`` onto a feature at index ``> i``.
    """

    p = params if params is not None else LabelParams()
    horizon = p.horizon_bars
    closes = [b.close for b in bars]
    n = len(bars)
    out: list[Direction | None] = [None] * n
    for i in range(n - horizon):
        base = closes[i]
        if base == 0.0:
            continue
        fwd_return = closes[i + horizon] / base - 1.0
        if fwd_return > p.flat_band:
            out[i] = Direction.UP
        elif fwd_return < -p.flat_band:
            out[i] = Direction.DOWN
        else:
            out[i] = Direction.FLAT
    return out


__all__ = ["Direction", "LabelParams", "build_labels"]
