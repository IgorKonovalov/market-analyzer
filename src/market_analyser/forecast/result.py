"""Forecast output shapes (Plan 0036, ADR-0030 / ADR-0040).

The domain-level result models a forecast run produces: `ForecastResult`, its
`ForecastProvenance` audit block, and the `EdgeStrength` label. They live here —
not in the MCP tool module that first shipped them — because they are consumed
across layers: the `forecast` tool serialises them onto the wire, and the
advisor (`advisor/fusion.py`, Plan 0038) reads them as one of its four analyst
inputs. Domain code depends on this module, never on `api/`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.forecast.validation import ForecastValidation

# The edge-strength label travelling with every forecast. "no_edge" means the
# model did not beat baseline out-of-sample (prob_* are null); "marginal" / "clear"
# split a real beat by EDGE_MARGIN_THRESHOLD so a thin beat reads as thin.
EdgeStrength = Literal["no_edge", "marginal", "clear"]


class ForecastProvenance(BaseModel):
    """The audit trail that makes a forecast reproducible and traceable to its
    exact model (ADR-0040 §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_version: str
    feature_set_id: str
    training_cutoff: datetime
    seed: int
    lib_versions: dict[str, str]


class ForecastResult(BaseModel):
    """A direction forecast. ``prob_*`` are ``None`` when the model did not beat
    baseline out-of-sample (the honest no-edge verdict); the ``validation`` basis
    and ``provenance`` are always present.

    ``edge_margin`` (out-of-sample ``skill - baseline_skill``) and ``edge_strength``
    (``no_edge`` / ``marginal`` / ``clear``) make a thin beat read as thin: a high
    ``prob_*`` riding a barely-above-baseline edge is labelled ``marginal`` so it is
    not mistaken for near-certainty (ADR-0030 invariant 4 refinement)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime
    horizon_bars: int
    prob_up: float | None
    prob_down: float | None
    prob_flat: float | None
    validation: ForecastValidation
    provenance: ForecastProvenance
    # Appended after provenance to keep the wire-stable field order (ADR-0040
    # determinism contract: model_dump order is part of the byte-identical result).
    edge_margin: float | None
    edge_strength: EdgeStrength


__all__ = ["EdgeStrength", "ForecastProvenance", "ForecastResult"]
