"""Forecasting subsystem (Plan 0036, ADR-0030 / ADR-0040).

A causal, leakage-free, deterministic next-/N-bar **direction** forecaster
(up / down / flat as a calibrated probability) built over the already-trailing
`analysis/` indicator surface (ADR-0023). The subsystem ships *validated edge or
nothing* (ADR-0030 invariant 3) and never emits a price level or a
recommendation.

This package root re-exports only the **pure** feature API (`features`), which
depends on nothing beyond `analysis/` + the standard library. The model layer
(`model`) pulls in `scikit-learn` and is imported directly by its consumers so
that merely importing `forecast` does not drag the ML stack into every caller.
"""

from __future__ import annotations

from market_analyser.forecast.features import (
    FEATURE_NAMES,
    FEATURE_SET_ID,
    FeatureRow,
    build_feature_rows,
    feature_names,
)

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SET_ID",
    "FeatureRow",
    "build_feature_rows",
    "feature_names",
]
