"""DeFi LP rebalance advisory (Plan 0099 phase 3, ADR-0029/0093).

The advisor-layer consumer of the position monitor's out-of-range facts: a
pure fusion that turns one LP position's health context (in/out of range,
how far, for how long, what fee flow is idle) into a **labeled advisory
rebalance recommendation** — recenter / widen / exit — or an honest
``hold`` ("no action" for a healthy position, "insufficient basis" when the
on-chain detail is missing). This is the one sanctioned crossing of the
conditions→advice line for DeFi positions (ADR-0029): the alert itself
stays a condition fact (ADR-0093); the directive exists only here,
labeled, with rationale and basis.

**Advisory only, structurally.** Like `TechnicalRead`, the honesty is by
*omission*: `RebalanceRecommendation` has no size, no transaction, no
route, no slippage tolerance — nothing an execution layer could consume.
On-chain rebalancing is barred (ADR-0072 BA-1; ADR-0025's untaken line);
the sibling AST test pins that no order/key/network path exists in this
module. The module is pure and deterministic: no clock reads, no I/O —
`as_of` is supplied by the tool boundary.

The direction heuristic is deliberately simple and stated in the rationale
(the numbers ride in the basis so the reader can disagree):

- **in range** → ``hold`` — the position is earning; there is nothing to fix.
- **out, tick detail missing** → ``hold`` — no grounded direction without
  knowing where price sits relative to the range ("insufficient basis").
- **out, shallow** (excursion ≤ ``WIDEN_MAX_EXCURSION_RATIO`` of the range
  width) → ``widen`` — price is just beyond the boundary; widening re-earns
  through chop without committing to a new center.
- **out, moderate** (≤ ``EXIT_MIN_EXCURSION_RATIO``) → ``recenter`` — price
  has clearly moved and dwelled; re-centering on it resumes fee flow at the
  cost of realizing the excursion's IL.
- **out, deep** (beyond ``EXIT_MIN_EXCURSION_RATIO`` range-widths) →
  ``exit`` — price has left the neighbourhood entirely; the position is
  single-sided inventory and a recenter would chase a full range-width move.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_analyser.advisor.models import BasisValue

RebalanceAction = Literal["recenter", "widen", "exit", "hold"]

# Excursion depth is measured in range-widths beyond the nearer bound
# (0.0 = sitting on the boundary). The two thresholds split widen / recenter
# / exit as documented in the module docstring.
WIDEN_MAX_EXCURSION_RATIO = 0.5
EXIT_MIN_EXCURSION_RATIO = 1.5


class LpPositionContext(BaseModel):
    """One LP position's health facts as the rebalance fusion consumes them —
    assembled by the tool boundary from a fired `DefiPositionAlert` (and the
    watch's current dwell state), never fabricated here. `wallet` is an
    opaque display string (the boundary passes it masked). Tick fields are
    `None` when the on-chain detail is unknown — the fusion answers "hold /
    insufficient basis" rather than guessing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str = Field(min_length=1)
    chain: str = Field(min_length=1)
    pool_address: str = Field(min_length=1)
    nft_token_id: int | None = None
    in_range: bool
    tick_lower: int | None = None
    tick_upper: int | None = None
    current_tick: int | None = None
    hours_out: float | None = None  # sustained out-of-range hours; None in range
    dwell_hours: float | None = None  # the watch threshold that qualified the alert
    uncollected_fees: dict[str, float] | None = None  # symbol -> amount at fire

    @model_validator(mode="after")
    def _ticks_consistent(self) -> LpPositionContext:
        ticks = (self.tick_lower, self.tick_upper, self.current_tick)
        present = [t for t in ticks if t is not None]
        if present and len(present) != 3:
            raise ValueError("tick_lower/tick_upper/current_tick must be all present or all absent")
        if (
            self.tick_lower is not None
            and self.tick_upper is not None
            and self.tick_lower >= self.tick_upper
        ):
            raise ValueError("tick_lower must be strictly less than tick_upper")
        return self


class RebalanceRecommendation(BaseModel):
    """A labeled advisory DeFi rebalance recommendation (ADR-0029).

    ``action`` is the call; ``rationale`` states why in words; ``basis``
    carries the numeric facts the call rests on (excursion depth, dwell,
    fee context — flat scalars, the ADR-0046 small-wire grain). The app
    recommends; the user acts. There is deliberately **no** size, route,
    transaction, or execution field of any kind — on-chain rebalancing is
    out of scope (ADR-0072 BA-1 / ADR-0025) and structurally impossible to
    express here (``extra="forbid"``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str
    chain: str
    pool_address: str
    nft_token_id: int | None
    action: RebalanceAction
    rationale: list[str]
    basis: dict[str, BasisValue]
    label: Literal["advisory"]
    as_of: datetime

    @model_validator(mode="after")
    def _enforce_advisory_shape(self) -> RebalanceRecommendation:
        if not self.rationale:
            raise ValueError(
                "a rebalance recommendation must carry a non-empty rationale (ADR-0029)"
            )
        if not self.basis:
            raise ValueError("a rebalance recommendation must carry a non-empty basis (ADR-0029)")
        return self


def _excursion_ratio(context: LpPositionContext) -> float:
    """How far beyond the nearer range bound the current tick sits, in
    range-widths (0.0 = on the boundary). Caller guarantees ticks present."""
    assert (
        context.tick_lower is not None
        and context.tick_upper is not None
        and context.current_tick is not None
    )
    width = context.tick_upper - context.tick_lower
    if context.current_tick >= context.tick_upper:
        distance = context.current_tick - context.tick_upper
    else:
        distance = context.tick_lower - context.current_tick
    return distance / width


def recommend_rebalance(context: LpPositionContext, *, as_of: datetime) -> RebalanceRecommendation:
    """Fuse one position's health context into a labeled advisory rebalance
    call, or an honest hold. Pure and deterministic: same context + `as_of`
    → byte-identical recommendation."""
    basis: dict[str, BasisValue] = {
        "in_range": context.in_range,
        "hours_out": context.hours_out,
        "dwell_hours": context.dwell_hours,
        "tick_lower": context.tick_lower,
        "tick_upper": context.tick_upper,
        "current_tick": context.current_tick,
    }
    if context.uncollected_fees:
        for symbol, amount in context.uncollected_fees.items():
            basis[f"uncollected_fee_{symbol}"] = amount

    def _build(action: RebalanceAction, rationale: list[str]) -> RebalanceRecommendation:
        return RebalanceRecommendation(
            wallet=context.wallet,
            chain=context.chain,
            pool_address=context.pool_address,
            nft_token_id=context.nft_token_id,
            action=action,
            rationale=rationale,
            basis=basis,
            label="advisory",
            as_of=as_of,
        )

    if context.in_range:
        return _build(
            "hold",
            [
                "position is inside its tick range and earning fees - no action",
            ],
        )

    if context.tick_lower is None or context.tick_upper is None or context.current_tick is None:
        return _build(
            "hold",
            [
                "position is out of range but the on-chain tick detail is unavailable - "
                "insufficient basis to ground a rebalance direction",
            ],
        )

    ratio = _excursion_ratio(context)
    basis["excursion_range_widths"] = round(ratio, 4)
    side = "above" if context.current_tick >= context.tick_upper else "below"
    dwell_note = (
        f"out of range for {context.hours_out:.1f}h"
        + (f" (dwell threshold {context.dwell_hours:.1f}h)" if context.dwell_hours else "")
        if context.hours_out is not None
        else "out of range"
    )

    if ratio <= WIDEN_MAX_EXCURSION_RATIO:
        return _build(
            "widen",
            [
                f"price sits {side} the range but shallow ({ratio:.2f} range-widths "
                f"beyond the bound, <= {WIDEN_MAX_EXCURSION_RATIO}); {dwell_note}",
                "widening the range around the current center re-earns fees through "
                "chop without committing to a new center",
            ],
        )
    if ratio <= EXIT_MIN_EXCURSION_RATIO:
        return _build(
            "recenter",
            [
                f"price has moved {side} the range materially ({ratio:.2f} range-widths "
                f"beyond the bound); {dwell_note}",
                "recentering the range on current price resumes fee flow; note it "
                "realizes the excursion's impermanent loss",
            ],
        )
    return _build(
        "exit",
        [
            f"price is far {side} the range ({ratio:.2f} range-widths beyond the bound, "
            f"> {EXIT_MIN_EXCURSION_RATIO}); {dwell_note}",
            "the position is single-sided idle inventory and a recenter would chase "
            "a full range-width move - consider exiting and redeploying deliberately",
        ],
    )


__all__ = [
    "EXIT_MIN_EXCURSION_RATIO",
    "WIDEN_MAX_EXCURSION_RATIO",
    "LpPositionContext",
    "RebalanceAction",
    "RebalanceRecommendation",
    "recommend_rebalance",
]
