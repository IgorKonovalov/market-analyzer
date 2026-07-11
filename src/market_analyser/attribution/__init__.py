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
from market_analyser.attribution.track_record import (
    MIN_TRACK_RECORD_N,
    BucketStat,
    ReliabilityBucket,
    TrackRecord,
    track_record,
)

__all__ = [
    "MIN_TRACK_RECORD_N",
    "BucketStat",
    "Outcome",
    "OutcomeClass",
    "ReliabilityBucket",
    "TrackRecord",
    "score_recommendation",
    "track_record",
]
