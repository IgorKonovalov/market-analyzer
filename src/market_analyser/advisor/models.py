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
    # Appended after passed to keep the wire-stable field order; defaulted True so
    # pre-0077 constructors (every check gated then) stay valid. `gating` (Plan
    # 0077 phase 5, ADR-0071) is whether the check *blocks*: a failed gating check
    # forces flat; an informational check (`gating=False`) is recorded and
    # replayable but never blocks. The direction-leg demotion flips the four
    # direction checks to `gating=False` below the skill-margin threshold, and the
    # non-voting vol/regime inputs ride as `gating=False` rows. The invariant
    # becomes: directional exactly when every *gating* check passed.
    gating: bool = True


class DirectionLegStatus(BaseModel):
    """The direction-forecast leg's gating status on a verdict (Plan 0077 phase 5,
    ADR-0071). The direction forecaster has near-absent edge (ADR-0070); when its
    out-of-sample skill margin (``skill - baseline_skill``) is below
    ``fusion.DIRECTION_SKILL_MARGIN`` the leg is **advisory, not gating** — it can
    neither veto a call the voting legs (conditions + backtested edge + live
    signal) corroborate nor be the sole deciding vote. ``present`` is whether a
    forecast leg was supplied; ``gating`` whether it voted this verdict;
    ``skill_margin`` its out-of-sample margin (``None`` when the forecast shipped
    no scored edge). Travels on every verdict so the demotion is auditable beside
    the ``gating=False`` flags in the ``basis.checks`` trace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: bool
    gating: bool
    skill_margin: float | None


class VolatilitySizing(BaseModel):
    """Non-voting volatility inputs to a directional advisory call (Plan 0077
    phase 5, ADR-0071). The volatility forecast (ADR-0070) is **never
    directional**: it only shrinks the size hint and widens the stop as predicted
    volatility rises — it can never turn a long into a short. ``size_factor`` is a
    bounded relative inverse-vol multiplier (``1.0`` = the reference vol, ``< 1``
    smaller, ``> 1`` larger) — an advisory number, never an order quantity
    (execution is untaken, ADR-0025). ``vol_source`` says whether the trusted
    model prediction drove it, the deterministic baseline reading did (the model
    beat no baseline — the plan's phase-4 finding), or nothing was usable
    (``none`` ⇒ neutral ``1.0``). ``vol_used`` is the per-bar RMS volatility that
    drove it; ``stop_vol_distance`` the vol-implied stop distance (in price) the
    call widened its stop to when it exceeded the level/ATR stop (``None`` when no
    volatility drove it)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    size_factor: float
    vol_used: float | None
    vol_source: Literal["model", "baseline", "none"]
    stop_vol_distance: float | None


class RegimeContext(BaseModel):
    """The non-voting regime context of a directional advisory call (Plan 0077
    phase 5, ADR-0071). The regime-transition forecast (ADR-0070) feeds
    **conviction only**, never direction: an unstable regime (one a *trusted*
    transition model expects to leave) softens conviction, bounded and
    direction-agnostic — it can never imply long or short. ``current_regime`` is
    the trailing rule-based state at the as-of bar; ``trusted`` whether the
    transition model beat its persistence baseline out-of-sample (else persistence
    is the honest default and the context is neutral); ``conviction_factor`` the
    bounded ``(0, 1]`` multiplier it applied (``1.0`` = neutral)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_regime: str | None
    trusted: bool
    conviction_factor: float


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
    # Appended after reason_codes, same wire-stable append discipline; all
    # defaulted so pre-0077 constructors stay valid (Plan 0077 phase 5, ADR-0071).
    # The non-voting forecast inputs and the demoted direction leg's status:
    # `sizing`/`regime_context` shape a directional call and are `None` on a flat
    # verdict (which has no size or conviction to shape); `direction_leg` travels
    # on every verdict so the gating decision is always auditable. None of these
    # can flip or manufacture a direction — that rests on the voting legs alone.
    sizing: VolatilitySizing | None = None
    regime_context: RegimeContext | None = None
    direction_leg: DirectionLegStatus | None = None

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
            if self.sizing is not None or self.regime_context is not None:
                raise ValueError(
                    "a flat recommendation carries no sizing or regime context — it has "
                    "no size or conviction for the non-voting inputs to shape (ADR-0071)"
                )
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


class TechnicalRead(BaseModel):
    """A single-indicator technical read — the lesser advisory tier (ADR-0068).

    A directional call (``long``/``short``/``flat``) derived from **one** curated
    regime indicator by its textbook mechanical rule. Its honesty comes from
    *structural omission*, not corroboration: unlike `Recommendation` it has **no**
    ``conviction``, ``entry_zone``, ``stop``, or ``targets`` field — ``extra="forbid"``
    makes constructing one with those a `ValidationError`, so a thin single-indicator
    basis can never be dressed as a trade ticket. The fused `recommend` tier (ADR-0029)
    is untouched; this is a sibling output, never an input to `fuse()`.

    ``indicator_id`` names the one basis; ``regime_state`` is the indicator's read in
    words (e.g. ``"supertrend direction=+1 (uptrend)"``); ``rationale`` states the
    mechanical rule that produced the direction. ``as_of_bar_ts`` is the last closed
    bar's time — the read saw ``bars[0..=this]`` only (anti-lookahead, ADR-0023).
    Frozen + ``extra="forbid"`` + deterministic contents, so two identical reads dump
    byte-identically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime  # the read saw bars[0..=this] only (anti-lookahead)
    indicator_id: Literal["supertrend", "ema_stack", "macd", "ichimoku"]
    direction: Literal["long", "short", "flat"]
    regime_state: str
    rationale: list[str]


__all__ = [
    "BasisValue",
    "DirectionLegStatus",
    "FusionCheck",
    "ReasonCode",
    "Recommendation",
    "RecommendationBasis",
    "RegimeContext",
    "TechnicalRead",
    "VolatilitySizing",
]
