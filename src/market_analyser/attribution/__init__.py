"""Recommendation outcome attribution — Plan 0080 (ADR-0075).

The advisor's live track record: scoring each recorded `recommend` call against
what price actually did over its horizon, path-dependently and honestly (every
call, baseline-relative, calibrated, no lookahead). This package holds the pure
scoring engine (`scoring`), its result shape (`models`), the scheduled scorer
(`scoring_job`, phase 3), and the aggregation (`track_record`, phase 4).
"""

from __future__ import annotations

from market_analyser.attribution.models import Outcome, OutcomeClass
from market_analyser.attribution.scoring import score_recommendation

__all__ = ["Outcome", "OutcomeClass", "score_recommendation"]
