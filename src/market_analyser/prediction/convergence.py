"""Convergence-screener core (Plan 0078 phase 1, ADR-0041/0029).

A pure, deterministic function over the Plan 0040 `PredictionMarket` list: it finds
markets nearing resolution whose top outcome's implied probability is near-certain,
and for each computes the **implied return if right** (gross of the tail), the
**time to resolution**, a **capital-lockup** note (close is not settlement), a
**liquidity caution** from the volume hint, and a **resolution-risk** heuristic.

**The honest number is `implied_return_if_right`, never expected value.** Every
opportunity carries its risk context (resolution risk + liquidity caution + lockup
note) so a downstream consumer can weigh the fat tail — the screener never blends
the gross edge with a guessed dispute probability (Plan 0078 risk: that would fake
precision the data cannot support).

**Facts, not a call.** No opportunity carries a direction / size / action field
(ADR-0029); the *buying* is the deferred ADR-0072 execution pillar. A test greps
the output free of advice language.

**Determinism.** The only time input is the injected `now`; there is no wall-clock
read, no set iteration, and the ranking is a stable sort. `queried_at` is `now`, so
re-running over the same markets + `now` is byte-identical (`model_dump` equal).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.data.types import MarketOutcome, PredictionMarket
from market_analyser.prediction.models import (
    ConvergenceOpportunity,
    ResolutionRisk,
    ResolutionRiskLevel,
)

# The fixed, labeled capital-lockup caveat every opportunity carries. Market close
# is not settlement: Polymarket outcomes resolve through UMA's optimistic oracle,
# which can lag hours to days and can be disputed, so capital stays locked past the
# close time this `time_to_resolution` measures (Plan 0078 open question: public
# data conflates close and settlement, so this is a standing best-effort note).
CAPITAL_LOCKUP_NOTE = (
    "Market close is not settlement — Polymarket outcomes resolve via UMA's "
    "optimistic oracle, which can lag hours to days and can be disputed. Capital "
    "stays locked until final settlement, past the close time this window measures."
)

# The standing caveat carried even by a `low` resolution-risk flag: the tail is
# never zero, only smaller.
_BASELINE_RESOLUTION_REASON = (
    "Binary market with adequate volume and no dispute-prone wording detected — but "
    "resolution risk is never zero (a UMA dispute remains possible)."
)

# Dispute-prone question stems (a LABELED HEURISTIC, not a detector of truth): word
# stems whose presence in a question correlates with subjective / contestable
# resolution. Matched with word boundaries so "count" does not fire on "country".
# Ordered so the matched-term list is deterministic.
_DISPUTE_PRONE_STEMS: tuple[tuple[str, str], ...] = (
    (r"disput\w*", "dispute"),
    (r"controvers\w*", "controversy"),
    (r"contest\w*", "contested"),
    (r"recount\w*", "recount"),
    (r"overturn\w*", "overturn"),
    (r"alleg\w*", "allege"),
    (r"accus\w*", "accuse"),
    (r"fraud\w*", "fraud"),
    (r"rig(?:s|ged|ging)?\b", "rigged"),
    (r"cheat\w*", "cheat"),
    (r"audit\w*", "audit"),
)
_DISPUTE_PRONE_RE: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{stem}", re.IGNORECASE), display) for stem, display in _DISPUTE_PRONE_STEMS
)


class ConvergenceParams(BaseModel):
    """Screener knobs (all configurable; the defaults are the Plan 0078 baseline).

    `max_time_to_close` bounds how near resolution a market must be; `min_confidence`
    is the implied-probability floor the top outcome must clear to count as
    near-certain; `thin_book_volume_usd` is the volume below which a market's book is
    flagged thin (feeding both the liquidity caution and the resolution-risk
    heuristic). A market whose reported volume is unknown (`None`) is treated as thin,
    never as adequate — an absent number is honest uncertainty, not a clean book."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_time_to_close: timedelta = timedelta(days=7)
    min_confidence: float = Field(default=0.90, ge=0.5, le=1.0)
    thin_book_volume_usd: float = Field(default=50_000.0, ge=0.0)

    @field_validator("max_time_to_close")
    @classmethod
    def _window_must_be_positive(cls, v: timedelta) -> timedelta:
        if v <= timedelta(0):
            raise ValueError("max_time_to_close must be a positive window")
        return v

    @field_validator("thin_book_volume_usd")
    @classmethod
    def _threshold_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("thin_book_volume_usd must be finite (no NaN/Inf)")
        return v


def screen_convergence(
    markets: Sequence[PredictionMarket],
    *,
    params: ConvergenceParams,
    now: datetime,
) -> list[ConvergenceOpportunity]:
    """Screen `markets` for near-decided convergence opportunities.

    A market yields an opportunity when it is still open, has a published
    `closes_at`, its time to close is within `(0, max_time_to_close]`, and its top
    outcome's implied probability is at least `min_confidence`. Results are ranked by
    gross `implied_return_if_right` descending — the edge size, NOT expected value —
    with deterministic tie-breaks (soonest resolution, then market id), so every row
    must still be read against its own risk context.

    Pure and deterministic: `now` is the only time input, there is no set iteration,
    and the sort is stable. `queried_at` is `now`, so a re-run over the same inputs is
    byte-identical.
    """
    opportunities: list[ConvergenceOpportunity] = []
    for market in markets:
        opportunity = _screen_market(market, params=params, now=now)
        if opportunity is not None:
            opportunities.append(opportunity)

    opportunities.sort(
        key=lambda o: (-o.implied_return_if_right, o.time_to_resolution, o.market_id),
    )
    return opportunities


def _screen_market(
    market: PredictionMarket,
    *,
    params: ConvergenceParams,
    now: datetime,
) -> ConvergenceOpportunity | None:
    """Apply the two filters to one market and, if it passes, build its opportunity.
    Returns `None` for a market that is closed, undated, past close, outside the
    window, or short of the confidence floor."""
    if market.closed or market.closes_at is None:
        return None

    time_to_resolution = market.closes_at - now
    if not (timedelta(0) < time_to_resolution <= params.max_time_to_close):
        return None

    top = _top_outcome(market.outcomes)
    if top.implied_probability < params.min_confidence:
        return None

    price = top.implied_probability
    # price >= min_confidence >= 0.5 > 0, so the division is always well-defined.
    implied_return_if_right = (1.0 - price) / price

    thin = market.volume_usd is None or market.volume_usd < params.thin_book_volume_usd
    liquidity_caution = _liquidity_caution(market.volume_usd, thin=thin)
    resolution_risk = _resolution_risk(market, thin=thin)

    return ConvergenceOpportunity(
        market_id=market.market_id,
        question=market.question,
        outcome_label=top.label,
        implied_probability=price,
        implied_return_if_right=implied_return_if_right,
        time_to_resolution=time_to_resolution,
        capital_lockup_note=CAPITAL_LOCKUP_NOTE,
        liquidity_caution=liquidity_caution,
        resolution_risk=resolution_risk,
        volume_usd=market.volume_usd,
        closes_at=market.closes_at,
        queried_at=now,
        source=market.source,
    )


def _top_outcome(outcomes: Sequence[MarketOutcome]) -> MarketOutcome:
    """The near-certain outcome: the highest implied probability, with a label
    tie-break for a deterministic pick when two outcomes are level."""
    return max(outcomes, key=lambda o: (o.implied_probability, o.label))


def _liquidity_caution(volume_usd: float | None, *, thin: bool) -> str | None:
    if not thin:
        return None
    if volume_usd is None:
        return (
            "Market volume is unknown — treat the implied probability as "
            "low-confidence; a thin book can be stale or moved by a single order."
        )
    return (
        f"Thin book (~${volume_usd:,.0f} reported volume) — the implied probability "
        "can be stale or move on a single order, so it is not ground truth."
    )


def _resolution_risk(market: PredictionMarket, *, thin: bool) -> ResolutionRisk:
    """The labeled resolution-risk heuristic: coarse level + spelled-out reasons."""
    multi = len(market.outcomes) > 2
    dispute_terms = _dispute_prone_terms(market.question)

    reasons: list[str] = []
    if dispute_terms:
        reasons.append(
            "Question wording contains dispute-prone term(s) "
            f"({', '.join(dispute_terms)}) — resolution may be subjective or contested."
        )
    if multi:
        reasons.append(
            f"Multi-outcome market ({len(market.outcomes)} outcomes) — resolution "
            "wording is more ambiguous than a binary yes/no."
        )
    if thin:
        reasons.append(
            "Low or unknown volume — thin books resolve less reliably and are easier "
            "to distort than deep, actively-traded ones."
        )

    if dispute_terms or (multi and thin):
        level: ResolutionRiskLevel = "high"
    elif multi or thin:
        level = "medium"
    else:
        level = "low"

    if not reasons:
        reasons.append(_BASELINE_RESOLUTION_REASON)

    return ResolutionRisk(level=level, reasons=reasons)


def _dispute_prone_terms(question: str) -> list[str]:
    """Every dispute-prone display term whose stem matches `question`, in the fixed
    heuristic order (deterministic, no set iteration)."""
    matched: list[str] = []
    for pattern, display in _DISPUTE_PRONE_RE:
        if pattern.search(question) and display not in matched:
            matched.append(display)
    return matched


__all__ = [
    "CAPITAL_LOCKUP_NOTE",
    "ConvergenceParams",
    "screen_convergence",
]
