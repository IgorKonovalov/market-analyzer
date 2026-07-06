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


class SeriesInput(BaseModel):
    """Provenance for one exogenous metric series a forecast consumed (Plan
    0059, ADR-0054): the registered ``series_id`` and the timestamp of the
    freshest point the lag-1 join actually read (``None`` when the series was
    registered as an input but had no observable point — all-NaN column)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    last_point_ts: int | None


class ForecastProvenance(BaseModel):
    """The audit trail that makes a forecast reproducible and traceable to its
    exact model (ADR-0040 §4). ``series_inputs`` (Plan 0059, ADR-0054) names
    every exogenous series the feature set consumed — empty for a v1 model,
    whose features are derived from the target symbol's own bars only.
    ``fallback_reason`` (Plan 0061, ADR-0056) says *why* a v1 set was used when
    the v2 set was the goal — the store was unwired, or wired but too starved
    for the requested walk-forward — and is ``None`` (wire-absent under the
    bus's ``exclude_none`` dump) when the v2 set genuinely ran."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_version: str
    feature_set_id: str
    training_cutoff: datetime
    seed: int
    lib_versions: dict[str, str]
    # Appended after lib_versions to keep the wire-stable field order (the
    # ForecastResult edge_margin precedent); defaulted so pre-0059 constructors
    # stay valid.
    series_inputs: tuple[SeriesInput, ...] = ()
    # Appended after series_inputs, same wire-stability discipline; defaulted
    # so pre-0061 constructors stay valid and the v2 path's dumps do not move.
    fallback_reason: str | None = None


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


class HorizonForecast(BaseModel):
    """One horizon's independently-validated forecast block (Plan 0059,
    ADR-0054 rule 2). ``prob_*`` are ``None`` when this horizon did not beat
    baseline out-of-sample; the ``validation`` basis always travels with the
    block so a per-horizon no-edge reads as exactly that. ``provenance`` is
    ``None`` only when the horizon had nothing to train on at all (e.g. every
    row dropped during exogenous warm-up) — then no model exists to version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int
    prob_up: float | None
    prob_down: float | None
    prob_flat: float | None
    validation: ForecastValidation
    edge_margin: float | None
    edge_strength: EdgeStrength
    provenance: ForecastProvenance | None


class MultiHorizonForecastResult(BaseModel):
    """The `forecast` tool's Plan 0059 response: one block per requested
    horizon, each trained, walk-forward-validated, and baseline-gated
    **independently** (no shared verdict — ADR-0054 rejected the multi-output
    model). ``feature_set_id`` names the frozen feature set the whole call
    used; each block's provenance repeats it alongside that block's own
    ``model_version``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime
    feature_set_id: str
    horizons: list[HorizonForecast]


__all__ = [
    "EdgeStrength",
    "ForecastProvenance",
    "ForecastResult",
    "HorizonForecast",
    "MultiHorizonForecastResult",
    "SeriesInput",
]
