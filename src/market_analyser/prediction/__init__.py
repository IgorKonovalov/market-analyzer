"""Prediction-market analysis on top of the Plan 0040 read layer (Plan 0078).

The convergence screener finds markets nearing resolution with a near-certain
outcome and surfaces the edge **with its risk context attached** (resolution risk,
liquidity caution, capital-lockup note) — facts, never a buy call (ADR-0029; the
buying is the deferred ADR-0072 execution pillar).
"""

from __future__ import annotations

from market_analyser.prediction.convergence import (
    CAPITAL_LOCKUP_NOTE,
    ConvergenceParams,
    screen_convergence,
)
from market_analyser.prediction.models import (
    ConvergenceOpportunity,
    ResolutionRisk,
    ResolutionRiskLevel,
)

__all__ = [
    "CAPITAL_LOCKUP_NOTE",
    "ConvergenceOpportunity",
    "ConvergenceParams",
    "ResolutionRisk",
    "ResolutionRiskLevel",
    "screen_convergence",
]
