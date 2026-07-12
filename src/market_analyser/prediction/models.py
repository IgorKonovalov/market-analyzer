"""Output models for the convergence screener (Plan 0078 phase 1, ADR-0041/0029).

The screener consumes Plan 0040 `PredictionMarket` facts and emits
`ConvergenceOpportunity` records — each carrying the edge math **and** its risk
context. These are *facts with their risks attached*, never a buy call: there is
no direction / size / action field anywhere on these models (the ADR-0029
boundary; the buying is the deferred ADR-0072 execution pillar).

`ResolutionRisk` is the honesty core of the plan: a **labeled heuristic** — a
level plus its reasons — that flags the fat tail (UMA optimistic-oracle disputes,
ambiguous multi-outcome wording, thin books) the gross return-if-right does not
price. It is deliberately a coarse `low/medium/high` label with its reasons
spelled out, never a fabricated probability of dispute (that number cannot be read
from odds + metadata, so we do not pretend to — Plan 0078's single most important
constraint).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ResolutionRiskLevel = Literal["low", "medium", "high"]


class ResolutionRisk(BaseModel):
    """A **labeled heuristic** for the resolution tail of an opportunity — never a
    guarantee, never a probability. `level` is a coarse `low/medium/high`; `reasons`
    spells out every factor that set it (multi-outcome wording, thin/unknown book,
    dispute-prone question terms), and is non-empty even at `low` (it then carries
    the standing UMA-dispute caveat) so the flag is never a bare label without its
    justification (Plan 0078 / ADR-0041 honest-uncertainty discipline)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: ResolutionRiskLevel
    reasons: list[str] = Field(min_length=1)


class ConvergenceOpportunity(BaseModel):
    """One near-decided prediction-market outcome, with its edge math and the full
    risk context that must travel with it (Plan 0078).

    `implied_return_if_right = (1 - p) / p` is **gross of the resolution tail** — it
    is never expected value (no blended EV is computed; that would fake precision the
    data cannot support). The tail lives in `resolution_risk` + `liquidity_caution` +
    `capital_lockup_note`, which a consumer must weigh alongside the gross number.
    Boundary-validated; carries no direction / size / action field (ADR-0029)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    outcome_label: str = Field(min_length=1)  # the near-certain outcome
    implied_probability: float = Field(ge=0.0, le=1.0)  # its Plan 0040 price
    implied_return_if_right: float = Field(ge=0.0)  # (1 - p) / p — GROSS of the tail
    time_to_resolution: timedelta  # closes_at - the injected now
    capital_lockup_note: str = Field(min_length=1)  # close != settlement (UMA can lag/dispute)
    liquidity_caution: str | None = None  # thin/unknown-book warning from the volume hint
    resolution_risk: ResolutionRisk  # {level, reasons} — a LABELED HEURISTIC
    volume_usd: float | None = Field(default=None, ge=0.0)  # provenance for the caution
    closes_at: datetime  # the market's published close (the ttr basis)
    queried_at: datetime  # provenance (the seam-routed now the screen ran at)
    source: str = Field(min_length=1)  # "polymarket" — the selected source identity

    @field_validator("implied_return_if_right")
    @classmethod
    def _return_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("implied_return_if_right must be finite (no NaN/Inf)")
        return v

    @field_validator("closes_at", "queried_at")
    @classmethod
    def _times_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("opportunity times must be timezone-aware (UTC)")
        return v.astimezone(UTC)


__all__ = [
    "ConvergenceOpportunity",
    "ResolutionRisk",
    "ResolutionRiskLevel",
]
