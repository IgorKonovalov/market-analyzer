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

from market_analyser.forecast.exogenous import (
    ExogenousColumns,
    MetricAsOfLookup,
    build_exogenous_columns,
)
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    EXOGENOUS_SERIES_IDS_V2_DEEP,
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_NAMES_V2_DEEP,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    FEATURE_SET_ID_V2_DEEP,
    FeatureRow,
    build_feature_rows,
    build_feature_rows_v2,
    build_feature_rows_v2_deep,
    feature_names,
)

__all__ = [
    "EXOGENOUS_SERIES_IDS_V2",
    "EXOGENOUS_SERIES_IDS_V2_DEEP",
    "FEATURE_NAMES",
    "FEATURE_NAMES_V2",
    "FEATURE_NAMES_V2_DEEP",
    "FEATURE_SET_ID",
    "FEATURE_SET_ID_V2",
    "FEATURE_SET_ID_V2_DEEP",
    "ExogenousColumns",
    "FeatureRow",
    "MetricAsOfLookup",
    "build_exogenous_columns",
    "build_feature_rows",
    "build_feature_rows_v2",
    "build_feature_rows_v2_deep",
    "feature_names",
]
