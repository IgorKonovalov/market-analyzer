"""Advisor data shapes (Plan 0038 phase 1, ADR-0029).

The `Recommendation` is the one sanctioned crossing of the "conditions are
facts, decisions are the user's" line: an explicit, labeled advisory artifact.
ADR-0029's containment rules are enforced *structurally* here, not by
convention:

* every recommendation is labeled ``advisory`` (a `Literal` — no other value
  can be constructed);
* a recommendation without a basis is a `ValidationError`, not a soft warning
  ("an unexplained or basis-free recommendation is a review finding");
* a **directional** call (long/short) must additionally carry a non-empty
  rationale, its backtested basis, its forecast basis, and complete
  entry/stop/target levels;
* a **flat** call carries no levels and zero conviction — the honest
  "no actionable edge" shape can never masquerade as a trade ticket.

Both models are frozen + ``extra="forbid"`` and hold only deterministic
values, so two identical fusions dump byte-identically (no run ids, no
wall-clock fields — `as_of_bar_ts` is bar time, ADR-0023's anti-lookahead
grain).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The JSON-representable value grain of a basis summary. Basis dicts summarise
# the analyst outputs that backed the call (walk-forward stats, forecast
# probabilities + provenance) — flat scalars only, never nested model dumps,
# so the recommendation stays small on the MCP wire (ADR-0046 posture).
BasisValue = float | int | str | bool | None


class ReasonCode(BaseModel):
    """One structured, translatable reason (Plan 0069 phase 4, ADR-0063).

    A ``{code, params}`` pair the renderer localizes: ``code`` is a stable wire
    identifier (e.g. ``"blocker.forecast_no_edge"``, ``"gate.signal_live_vote"``)
    the renderer keys off verbatim; ``params`` are the raw values it
    interpolates — numbers stay numbers, so the renderer formats them ``en-US``
    (ADR-0063). Reason-codes ride *beside* — never replace — the English
    ``rationale``/``basis`` prose, which stays authoritative for the agent/MCP
    consumer. Frozen + ``extra="forbid"`` + deterministic contents, so two
    identical fusions dump byte-identically."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    params: dict[str, float | int | str] = Field(default_factory=dict)


class FusionCheck(BaseModel):
    """One recorded gate of the fusion trace (Plan 0063, ADR-0058).

    ``leg`` names which analyst leg the gate belongs to; ``check`` is the
    human-readable gate name; ``threshold`` and ``actual`` carry the real
    values the gate compared (scalars only — the ADR-0046 small-wire grain;
    ``threshold`` is ``None`` for a recorded fact that has no pass bar, e.g.
    an individual signal vote); ``passed`` is the outcome. The full ordered
    tuple of these makes any verdict — directional or flat — replayable line
    by line: the verdict is directional exactly when every check passed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    leg: Literal["forecast", "signal", "backtest", "conditions", "alignment"]
    check: str
    threshold: BasisValue
    actual: BasisValue
    passed: bool


class RecommendationBasis(BaseModel):
    """What backed the call — ADR-0029's mandatory basis.

    `conditions` names the condition facts that were read (ADR-0023 snapshot:
    trend/momentum/volume/patterns). `signals` names each live strategy
    evaluation consumed (Plan 0026). `backtest` summarises the walk-forward
    edge (ADR-0024) and `forecast` the calibrated forecast (Plan 0036) —
    either may be ``None`` only for a *flat* recommendation (enforced by
    `Recommendation`, which sees the direction). An entirely empty basis is
    invalid at construction: there is no such thing as a groundless
    recommendation, flat included.

    `checks` (Plan 0063, ADR-0058) is the structured fusion trace: every gate
    `fuse()` evaluated, in a fixed deterministic order, with the real
    threshold-vs-actual values behind each pass/fail. It is the numeric
    superset of the rationale strings and travels on directional and flat
    verdicts alike (defaulted so pre-0063 constructors stay valid; it does
    not count toward basis non-emptiness).

    `condition_codes` / `signal_codes` (Plan 0069 phase 4b, ADR-0063) are the
    translatable mirrors of the `conditions` / `signals` prose lists: one
    `ReasonCode` per line, index-aligned to the prose, co-generated in lockstep
    so counts and order can never drift. Every condition/signal enum value is a
    *closed* vocabulary (`Trend`, `MomentumStance`, `VolumeStance`, pattern
    `Direction`, `current_position`, the fixed candlestick pattern names), so the
    value rides as a raw token in ``params`` and the renderer translates it via
    an enum-label catalog — no prose-parsing (ADR-0063's "the sidecar ships
    facts, the renderer owns wording"). The English prose lists above stay
    authoritative for the agent/MCP consumer, untouched; the codes are additive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    conditions: list[str]
    signals: list[str]
    backtest: dict[str, BasisValue] | None
    forecast: dict[str, BasisValue] | None
    # Appended after forecast to keep the wire-stable field order (the
    # ForecastProvenance appended-fields precedent) — Plan 0063's deliberate,
    # versioned move of the ADR-0029 field-set pins.
    checks: tuple[FusionCheck, ...] = ()
    # Appended after checks, same wire-stable append discipline; defaulted so
    # pre-0069 constructors stay valid (Plan 0069 phase 4b).
    condition_codes: tuple[ReasonCode, ...] = ()
    signal_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _require_non_empty(self) -> RecommendationBasis:
        if (
            not self.conditions
            and not self.signals
            and self.backtest is None
            and self.forecast is None
        ):
            raise ValueError(
                "a recommendation basis must not be empty — every recommendation "
                "carries what backed it (ADR-0029)"
            )
        return self


class Recommendation(BaseModel):
    """A labeled advisory trade recommendation (ADR-0029).

    ``direction`` is the call; ``entry_zone`` is the ``(low, high)`` price band
    the call considers actionable, ``stop`` the invalidation level, ``targets``
    the objective(s) — all ``None``/empty when flat. ``conviction`` is *derived*
    (forecast probability x backtested edge — the documented monotone mapping
    in `fusion.py`), never invented, and is ``0.0`` when flat. ``rationale`` is
    the human-readable "why" — which facts fired. The app recommends; the user
    acts. Order placement, trade keys, and auto-action are ADR-0025's domain
    and structurally absent here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    direction: Literal["long", "short", "flat"]
    entry_zone: tuple[float, float] | None
    stop: float | None
    targets: list[float]
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: list[str]
    basis: RecommendationBasis
    label: Literal["advisory"]
    as_of_bar_ts: datetime  # the decision saw bars[0..=this] only (anti-lookahead)
    # Appended after as_of_bar_ts to keep the wire-stable field order (the
    # RecommendationBasis.checks precedent); defaulted so pre-0069 constructors
    # stay valid. The finite authored surface the renderer localizes (Plan 0069,
    # ADR-0063): one code per `rationale` line (1:1, same order), then one code
    # per gate (1:1 with `basis.checks`, same order). The English prose above is
    # unchanged and stays authoritative for the agent/MCP consumer.
    reason_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _enforce_advisory_shape(self) -> Recommendation:
        if self.direction == "flat":
            if self.entry_zone is not None or self.stop is not None or self.targets:
                raise ValueError(
                    "a flat recommendation carries no entry/stop/target levels — "
                    "'no actionable edge' must not look like a trade ticket"
                )
            if self.conviction != 0.0:
                raise ValueError("a flat recommendation has zero conviction by definition")
            return self

        # Directional call — ADR-0029's three containment rules, structurally.
        if not self.rationale:
            raise ValueError(
                "a directional recommendation must carry a non-empty rationale (ADR-0029)"
            )
        if self.basis.backtest is None:
            raise ValueError(
                "a directional recommendation must carry its backtested basis (ADR-0029)"
            )
        if self.basis.forecast is None:
            raise ValueError(
                "a directional recommendation must carry its forecast basis (ADR-0029)"
            )
        if self.entry_zone is None or self.stop is None or not self.targets:
            raise ValueError(
                "a directional recommendation must carry an entry zone, a stop, "
                "and at least one target"
            )
        low, high = self.entry_zone
        if low > high:
            raise ValueError(
                f"entry_zone must be (low, high) with low <= high, got ({low}, {high})"
            )
        return self


__all__ = ["BasisValue", "FusionCheck", "ReasonCode", "Recommendation", "RecommendationBasis"]
