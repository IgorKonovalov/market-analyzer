"""Outcome-attribution data shapes — Plan 0080 phase 2 (ADR-0075).

The result of scoring one recorded advisory recommendation against what price
actually did over its horizon. `Outcome` is the pure scoring engine's return
value; the phase-3 scorer persists its fields onto the ledger row and the phase-4
aggregation reads them back.

The design honours ADR-0075's honesty-by-construction:

* `outcome_class` scores the *ticket the call actually gave* — `stopped` even if
  price later rose (the anecdote-killer), `target_hit` when a target was reached
  first, `timeout` when neither fired within the horizon, `pending` when the
  horizon has not matured (no lookahead).
* `directional_correct` is a *separate* axis — did price end the horizon in the
  called direction — so a call can be `directional_correct=True` yet score a
  `stopped` loss. Keeping the two apart is the whole point.

Frozen + ``extra="forbid"`` and holding only deterministic values, so re-scoring
a matured row dumps byte-identically (`scored_at` is the documented run-provenance
exception, the ADR-0018 posture).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

OutcomeClass = Literal["target_hit", "stopped", "timeout", "pending"]


class Outcome(BaseModel):
    """How one recommendation resolved against realized price.

    A `pending` outcome carries no measurements (the horizon has not matured and
    nothing was read past it); every other class carries a `realized_return`
    (the trade's P&L as a signed fraction of entry — positive is profit for the
    call's own direction), a `realized_r` (that return over the initial
    risk-to-stop, so a stop-out is about -1R), and a `directional_correct` flag.
    `prob_for_calibration` echoes the forecast probability the call staked on its
    direction (the input to the calibration read) — ``None`` when the call
    carried no directional forecast.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome_class: OutcomeClass
    realized_return: float | None
    realized_r: float | None
    directional_correct: bool | None
    prob_for_calibration: float | None
    scored_at: datetime | None


__all__ = ["Outcome", "OutcomeClass"]
