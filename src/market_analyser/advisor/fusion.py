"""Pure fusion engine (Plan 0038 phase 1, ADR-0029).

`fuse()` maps the four analyst outputs — a condition snapshot (ADR-0023), live
strategy signal evaluations (Plan 0026), a walk-forward result (ADR-0024), and
a calibrated forecast (Plan 0036) — to one labeled advisory `Recommendation`.
It consumes those layers' *output models only* (`ConditionSnapshot`,
`SignalEvaluation`, `WalkForwardResult`, `ForecastResult`), never their
internals — guarded by an import-lint test.

**A directional call requires every *voting* leg to agree; anything less is
flat.** At least one live signal must imply the direction with none opposing,
and the walk-forward edge must be positive **and belong to one of the agreeing
strategies** (an edge for a strategy that did not vote backs nothing). The
direction forecast leg votes **conditionally** (ADR-0071): it gates only when
its out-of-sample skill margin clears `DIRECTION_SKILL_MARGIN`; below that it
is demoted to advisory, so it can neither veto a call the other legs
corroborate nor be the deciding vote — it still cannot manufacture one. Each
failed *gating* leg becomes a named blocker in the flat recommendation's
rationale — an honest "no actionable edge", never a fabricated call. A demoted
leg's failed checks are recorded with `gating=False` and block nothing.

**Every gate is recorded** (Plan 0063, ADR-0058): `basis.checks` carries the
structured fusion trace — leg, check, threshold, actual, outcome — in a fixed
deterministic order, on directional and flat verdicts alike, so any verdict is
replayable line by line (directional exactly when every check passed). The
trace records the decision; it never alters it.

**Conviction is derived, never invented** (the plan's open question, resolved
here as the documented monotone mapping). It has **two branches**, selected by
whether the direction leg gates (ADR-0071) — i.e. whether it shipped a
probability at all:

    edge_credit = clamp(sharpe_mean / SHARPE_FULL_CREDIT, 0, 1)

    # direction leg gating (skill margin >= DIRECTION_SKILL_MARGIN):
    conviction = P(direction) * edge_credit * regime_factor
    # direction leg demoted (no probability shipped):
    conviction =                edge_credit * regime_factor

where ``P(direction)`` is the calibrated forecast probability of the called
direction, ``sharpe_mean`` the walk-forward out-of-sample aggregate, and
``regime_factor`` the non-voting regime dampener (``1.0`` unless a *trusted*
transition model expects the current regime to break). The mapping is monotone
in each input it uses and zero when the backtested edge shows nothing: a
marginal forecast or a thin backtest reads as low conviction by construction.

The demoted branch is **not** an edge case — the direction forecaster rarely
beats baseline (ADR-0070), so it is the common path in practice. On it,
conviction is a statement about the *backtest alone*: a saturated ``1.0`` means
``sharpe_mean >= SHARPE_FULL_CREDIT``, never "certain to be right". Consumers
that narrate conviction must say which branch produced it (see the
advisory-boundary spec under ``docs/architecture/specs/``).

**Levels are chart geometry, not opinion**: the entry zone is a band of
``ENTRY_BAND_ATR`` around the last close; the stop sits beyond the nearest
opposing level (support for a long, resistance for a short) with an ATR
buffer, falling back to ``STOP_ATR`` when no level exists on that side; the
target is the nearest favouring level, falling back to ``TARGET_ATR``.

Deterministic throughout: pure function of its inputs, no clock reads, no
randomness, all iteration ordered — identical inputs produce an identical
`Recommendation` (dump-for-dump).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from market_analyser.advisor.models import (
    BasisValue,
    DirectionLegStatus,
    FusionCheck,
    ReasonCode,
    Recommendation,
    RecommendationBasis,
    RegimeContext,
    VolatilitySizing,
)
from market_analyser.analysis.types import ConditionSnapshot
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.forecast.regime import RegimeForecast
from market_analyser.forecast.result import ForecastResult
from market_analyser.forecast.volatility import VolatilityForecast

# The walk-forward sharpe_mean at (or above) which the backtested edge earns
# full credit in the conviction mapping. Below it, credit scales linearly;
# at or below zero the edge is no edge and no directional call ships.
SHARPE_FULL_CREDIT = 1.0

# Entry zone half-width, in ATRs around the last close: the band inside which
# the call considers the entry actionable (a market entry +/- a quarter ATR).
ENTRY_BAND_ATR = 0.25

# Stop / target distance in ATRs from the last close, used when no clustered
# support/resistance level exists on the relevant side of price.
STOP_ATR = 2.0
TARGET_ATR = 2.0

# Buffer beyond a support/resistance level for the stop, in ATRs — the stop
# sits past the level, not on it, so a touch-and-hold does not knock it out.
LEVEL_BUFFER_ATR = 0.1

# When the snapshot has no defined ATR (too little history), fall back to this
# fraction of the last close as the volatility unit for level geometry.
ATR_FALLBACK_FRACTION = 0.02

# ADR-0071: the direction forecast leg gates (votes) only when its out-of-sample
# beats-baseline margin (``skill - baseline_skill``) is at least this. Below it the
# leg is advisory — it can neither veto a corroborated call nor be the deciding
# vote. The direction target has near-absent edge (ADR-0070), and when it clears
# baseline it does so thinly (Plan 0062: roughly h=21 only), so a 2-percentage-point
# out-of-sample accuracy margin is the floor at which the leg earns a directional
# vote; below it is noise. Pinned with a boundary test (Plan 0077 phase 5).
DIRECTION_SKILL_MARGIN = 0.02

# Non-voting inverse-vol sizing (ADR-0071): the reference per-bar RMS volatility at
# which the advisory size factor is 1.0. A predicted vol above it shrinks size,
# below it grows size, all clamped to [SIZE_FLOOR, SIZE_CAP]. A documented reference
# for a daily crypto-first series (~3% per-bar log-return vol), not a tuned
# parameter — the factor is relative and advisory, never an order size.
REFERENCE_VOL = 0.03
SIZE_FLOOR = 0.25
SIZE_CAP = 2.0

# Non-voting vol-implied stop (ADR-0071): the stop is widened to at least this many
# predicted-vol price units from the last close, so a higher-vol forecast gives a
# wider stop (never a tighter one). Capped at STOP_VOL_CAP_FRACTION of the close so
# a degenerate vol forecast cannot produce an absurd stop distance (done-when (d)).
STOP_VOL_MULT = 2.0
STOP_VOL_CAP_FRACTION = 0.5

# Non-voting regime conviction dampening (ADR-0071): a trusted transition model that
# expects the current regime to persist leaves conviction unchanged (factor 1.0);
# one that expects a transition softens it toward this floor. Bounded and
# direction-agnostic — regime never implies a direction, only how much to trust a
# setup the voting legs already called.
REGIME_CONVICTION_FLOOR = 0.5

_Directional = Literal["long", "short"]
_VolSource = Literal["model", "baseline", "none"]


def _direction_leg_gating(forecast: ForecastResult) -> bool:
    """Whether the direction forecast leg votes (gates) this verdict: its
    out-of-sample skill margin must clear `DIRECTION_SKILL_MARGIN` (ADR-0071). A
    forecast that shipped no scored edge (`edge_margin is None`) or clears baseline
    only thinly is demoted — advisory, not gating."""

    return forecast.edge_margin is not None and forecast.edge_margin >= DIRECTION_SKILL_MARGIN


def _vol_reading(volatility: VolatilityForecast | None) -> tuple[float | None, _VolSource]:
    """The volatility reading that drives sizing/stops, and its source. Prefer the
    model prediction only when it beat a baseline out-of-sample; otherwise fall back
    to the deterministic baseline reading (the phase-4 finding: the baseline is the
    useful output). ``(None, "none")`` when no usable reading exists — neutral."""

    if volatility is None:
        return None, "none"
    if volatility.beats_baseline and volatility.predicted_vol is not None:
        return volatility.predicted_vol, "model"
    if volatility.baseline_vol is not None:
        return volatility.baseline_vol, "baseline"
    return None, "none"


def _size_factor(vol_used: float | None) -> float:
    """Bounded inverse-vol advisory size multiplier: ``REFERENCE_VOL / vol``, clamped
    to ``[SIZE_FLOOR, SIZE_CAP]``. ``1.0`` (neutral) when no vol reading — an absent
    or degenerate forecast can never blow up the size (done-when (d))."""

    if vol_used is None or vol_used <= 0.0:
        return 1.0
    return min(max(REFERENCE_VOL / vol_used, SIZE_FLOOR), SIZE_CAP)


def _widen_stop_for_vol(
    direction: _Directional, last_close: float, stop: float, vol_used: float | None
) -> tuple[float, float | None]:
    """Widen ``stop`` to at least the vol-implied distance from ``last_close`` (never
    tighter), and return ``(stop, vol_stop_distance)``. The distance is capped at
    ``STOP_VOL_CAP_FRACTION`` of the close so a degenerate vol cannot produce an
    absurd stop. ``(stop, None)`` when no vol reading — the level/ATR stop stands."""

    if vol_used is None or vol_used <= 0.0:
        return stop, None
    dist = min(STOP_VOL_MULT * vol_used * last_close, STOP_VOL_CAP_FRACTION * last_close)
    widened = last_close - dist if direction == "long" else last_close + dist
    stop = min(stop, widened) if direction == "long" else max(stop, widened)
    return stop, dist


def _regime_conviction(regime: RegimeForecast | None) -> tuple[float, RegimeContext | None]:
    """The non-voting regime conviction multiplier and its recorded context. A
    trusted transition model (beats persistence out-of-sample) that expects the
    current regime to persist leaves conviction unchanged (``1.0``); one expecting a
    transition softens it toward ``REGIME_CONVICTION_FLOOR``. Direction-agnostic and
    bounded — regime never implies long or short. ``(1.0, None)`` when no regime."""

    if regime is None:
        return 1.0, None
    current = regime.current_regime
    probs = regime.transition_probs
    trusted = regime.beats_baseline and probs is not None and current is not None
    if trusted:
        assert probs is not None and current is not None
        p_stay = probs.get(current, 0.0)
        factor = REGIME_CONVICTION_FLOOR + (1.0 - REGIME_CONVICTION_FLOOR) * p_stay
    else:
        factor = 1.0
    context = RegimeContext(
        current_regime=current.value if current is not None else None,
        trusted=bool(trusted),
        conviction_factor=round(factor, 4),
    )
    return factor, context


def _forecast_direction(forecast: ForecastResult) -> _Directional | None:
    """The forecast's directional vote: the strict argmax of the shipped
    probabilities, or ``None`` when no probability shipped (no edge), the
    argmax is flat, or up/down tie exactly (conservative tie-break)."""

    if forecast.prob_up is None or forecast.prob_down is None or forecast.prob_flat is None:
        return None
    if forecast.prob_up > forecast.prob_down and forecast.prob_up > forecast.prob_flat:
        return "long"
    if forecast.prob_down > forecast.prob_up and forecast.prob_down > forecast.prob_flat:
        return "short"
    return None


def _signal_votes(
    signals: Sequence[SignalEvaluation],
) -> tuple[_Directional | None, bool, list[str], list[str]]:
    """Fold the live evaluations into ``(direction, conflict, long_ids, short_ids)``.

    Each evaluation votes its implied ``current_position`` (Plan 0026 — the
    folded signal stream, so a fresh entry on the last closed bar is already
    reflected). Flat evaluations abstain. Opposing votes are a conflict: no
    direction, by honesty rather than majority."""

    long_ids = sorted(s.strategy_id for s in signals if s.current_position == "long")
    short_ids = sorted(s.strategy_id for s in signals if s.current_position == "short")
    if long_ids and short_ids:
        return None, True, long_ids, short_ids
    if long_ids:
        return "long", False, long_ids, short_ids
    if short_ids:
        return "short", False, long_ids, short_ids
    return None, False, long_ids, short_ids


def _sharpe_mean(walk_forward: WalkForwardResult | None) -> float | None:
    if walk_forward is None:
        return None
    return walk_forward.aggregate.get("sharpe_mean")


def _edge_factor(sharpe_mean: float | None) -> float:
    """Backtested-edge credit in [0, 1]: linear in sharpe_mean up to
    SHARPE_FULL_CREDIT, zero at or below zero (no edge, no credit)."""

    if sharpe_mean is None or sharpe_mean <= 0.0:
        return 0.0
    return min(sharpe_mean / SHARPE_FULL_CREDIT, 1.0)


def _levels(
    direction: _Directional, last_close: float, snapshot: ConditionSnapshot
) -> tuple[tuple[float, float], float, list[float]]:
    """Entry zone, stop, and target(s) from the snapshot's chart geometry."""

    atr = snapshot.indicators.get("atr")
    if atr is None or atr <= 0.0:
        atr = last_close * ATR_FALLBACK_FRACTION

    entry_zone = (last_close - ENTRY_BAND_ATR * atr, last_close + ENTRY_BAND_ATR * atr)

    support = snapshot.nearest_support
    resistance = snapshot.nearest_resistance
    if direction == "long":
        stop = last_close - STOP_ATR * atr
        if support is not None and support.price - LEVEL_BUFFER_ATR * atr < entry_zone[0]:
            stop = support.price - LEVEL_BUFFER_ATR * atr
        target = last_close + TARGET_ATR * atr
        if resistance is not None and resistance.price > entry_zone[1]:
            target = resistance.price
    else:
        stop = last_close + STOP_ATR * atr
        if resistance is not None and resistance.price + LEVEL_BUFFER_ATR * atr > entry_zone[1]:
            stop = resistance.price + LEVEL_BUFFER_ATR * atr
        target = last_close - TARGET_ATR * atr
        if support is not None and support.price < entry_zone[0]:
            target = support.price
    return entry_zone, stop, [target]


def _require_consistent_inputs(
    snapshot: ConditionSnapshot,
    signals: Sequence[SignalEvaluation],
    walk_forward: WalkForwardResult | None,
    forecast: ForecastResult,
    volatility: VolatilityForecast | None,
    regime: RegimeForecast | None,
) -> None:
    """All inputs must describe the same symbol, timeframe, and as-of bar —
    fusing across symbols/timeframes, or a fresh snapshot with a stale leg,
    would produce a well-formed but meaningless call. The non-voting volatility
    and regime forecasts (Plan 0077 phase 5) are checked on the same footing when
    present. (`WalkForwardResult` carries no as-of field; its currency is the
    caller's bar series.)"""

    expected = (snapshot.symbol, snapshot.timeframe)
    for name, got in (
        ("forecast", (forecast.symbol, forecast.timeframe)),
        *(((f"signals[{s.strategy_id}]"), (s.symbol, s.timeframe)) for s in signals),
        *(
            (("walk_forward", (walk_forward.symbol, walk_forward.timeframe)),)
            if walk_forward is not None
            else ()
        ),
        *((("volatility", (volatility.symbol, volatility.timeframe)),) if volatility else ()),
        *((("regime", (regime.symbol, regime.timeframe)),) if regime else ()),
    ):
        if got != expected:
            raise ValueError(f"inconsistent fusion inputs: snapshot is {expected}, {name} is {got}")

    for name, got_ts in (
        ("forecast.as_of_bar_ts", forecast.as_of_bar_ts),
        *(
            (f"signals[{s.strategy_id}].evaluated_through_ts", s.evaluated_through_ts)
            for s in signals
        ),
        *((("volatility.as_of_bar_ts", volatility.as_of_bar_ts),) if volatility else ()),
        *((("regime.as_of_bar_ts", regime.as_of_bar_ts),) if regime else ()),
    ):
        if got_ts != snapshot.as_of:
            raise ValueError(
                f"inconsistent fusion inputs: snapshot as-of is {snapshot.as_of}, "
                f"{name} is {got_ts}"
            )


def _build_trace(
    *,
    snapshot: ConditionSnapshot,
    signals: Sequence[SignalEvaluation],
    walk_forward: WalkForwardResult | None,
    forecast: ForecastResult,
    forecast_dir: _Directional | None,
    conflict: bool,
    signal_dir: _Directional | None,
    long_ids: Sequence[str],
    short_ids: Sequence[str],
    sharpe_mean: float | None,
    gating: bool,
    volatility: VolatilityForecast | None,
    regime: RegimeForecast | None,
) -> tuple[tuple[FusionCheck, ...], tuple[ReasonCode, ...]]:
    """The structured fusion trace (Plan 0063, ADR-0058) and, co-generated in
    lockstep, its per-gate reason-codes (Plan 0069 phase 4, ADR-0063).

    Every gate, in a fixed deterministic order, with the real
    threshold-vs-actual values — and beside each, a stable ``gate.*`` code the
    renderer localizes for the gate's label (the dynamic values ride in the
    `FusionCheck` itself). The `(FusionCheck, ReasonCode)` pairing guarantees the
    returned code tuple is index-aligned to the check tuple, so a code can never
    drift from its gate.

    The invariant the trace guarantees (pinned by the replayability test):
    **the verdict is directional exactly when every check passed** — each
    `fuse()` blocker maps to at least one failed check, and the genuinely
    unconditional facts (alignment, which raises before any verdict exists;
    the condition snapshot; individual signal votes) always pass. The
    calibrated-P(direction) gate is not unconditional: it mirrors the
    "argmax direction is directional" gate — passing iff the argmax is
    directional — so it never fails in isolation, only alongside that gate."""

    scope = f"{snapshot.symbol}/{snapshot.timeframe}"
    as_of = snapshot.as_of.isoformat()
    pairs: list[tuple[FusionCheck, ReasonCode]] = [
        # Alignment is enforced by raising before any verdict exists, so an
        # emitted trace always records it as passed — the gate ran, and only
        # aligned inputs ever reach a Recommendation.
        (
            FusionCheck(
                leg="alignment",
                check="inputs share symbol/timeframe",
                threshold=scope,
                actual=scope,
                passed=True,
            ),
            ReasonCode(code="gate.alignment_scope"),
        ),
        (
            FusionCheck(
                leg="alignment",
                check="inputs share the as-of bar",
                threshold=as_of,
                actual=as_of,
                passed=True,
            ),
            ReasonCode(code="gate.alignment_asof"),
        ),
        (
            FusionCheck(
                leg="conditions",
                check="condition snapshot read",
                threshold=None,
                actual=(
                    f"trend={snapshot.trend}, momentum={snapshot.momentum}, "
                    f"volume={snapshot.volume_stance}"
                ),
                passed=True,
            ),
            ReasonCode(code="gate.conditions_read"),
        ),
    ]

    probs_shipped = forecast.prob_up is not None
    directional_prob = (
        forecast.prob_up
        if forecast_dir == "long"
        else forecast.prob_down
        if forecast_dir == "short"
        else None
    )
    # The three direction-forecast gates and the agreement gate below are `gating`
    # only when the leg cleared the skill-margin threshold (ADR-0071). Demoted, they
    # carry their real pass/fail but `gating=False`, so they are recorded and
    # replayable yet can no longer veto a call the voting legs corroborate.
    pairs.extend(
        (
            (
                FusionCheck(
                    leg="forecast",
                    check="probabilities shipped (baseline beaten out-of-sample)",
                    threshold=True,
                    actual=probs_shipped,
                    passed=probs_shipped,
                    gating=gating,
                ),
                ReasonCode(code="gate.forecast_probs_shipped"),
            ),
            (
                FusionCheck(
                    leg="forecast",
                    check="argmax direction is directional",
                    threshold="long or short",
                    actual=forecast_dir if forecast_dir is not None else "none",
                    passed=forecast_dir is not None,
                    gating=gating,
                ),
                ReasonCode(code="gate.forecast_argmax_directional"),
            ),
            (
                FusionCheck(
                    leg="forecast",
                    check="calibrated P(direction)",
                    threshold=None,
                    actual=directional_prob,
                    passed=directional_prob is not None,
                    gating=gating,
                ),
                ReasonCode(code="gate.forecast_calibrated_prob"),
            ),
        )
    )

    pairs.extend(
        (
            FusionCheck(
                leg="signal",
                check=f"live vote: {s.strategy_id}",
                threshold=None,
                actual=s.current_position,
                passed=True,
            ),
            ReasonCode(code="gate.signal_live_vote", params={"strategy_id": s.strategy_id}),
        )
        for s in sorted(signals, key=lambda s: s.strategy_id)
    )
    agreeing_ids: Sequence[str] = (
        long_ids if signal_dir == "long" else short_ids if signal_dir == "short" else ()
    )
    pairs.extend(
        (
            (
                FusionCheck(
                    leg="signal",
                    check="no conflicting live votes",
                    threshold=False,
                    actual=conflict,
                    passed=not conflict,
                ),
                ReasonCode(code="gate.signal_no_conflict"),
            ),
            (
                FusionCheck(
                    leg="signal",
                    check="at least one directional live vote",
                    threshold="long or short",
                    actual=signal_dir if signal_dir is not None else "none",
                    passed=signal_dir is not None,
                ),
                ReasonCode(code="gate.signal_directional_vote"),
            ),
            (
                FusionCheck(
                    leg="signal",
                    check="live direction agrees with the forecast direction",
                    threshold=forecast_dir if forecast_dir is not None else "none",
                    actual=signal_dir if signal_dir is not None else "none",
                    passed=forecast_dir is not None and signal_dir == forecast_dir,
                    gating=gating,
                ),
                ReasonCode(code="gate.signal_agrees_forecast"),
            ),
            (
                FusionCheck(
                    leg="backtest",
                    check="walk-forward basis supplied",
                    threshold=True,
                    actual=walk_forward is not None,
                    passed=walk_forward is not None,
                ),
                ReasonCode(code="gate.backtest_basis_supplied"),
            ),
            (
                FusionCheck(
                    leg="backtest",
                    check="backtested edge positive (sharpe_mean > 0)",
                    threshold=0.0,
                    actual=sharpe_mean,
                    passed=sharpe_mean is not None and sharpe_mean > 0.0,
                ),
                ReasonCode(code="gate.backtest_edge_positive"),
            ),
            (
                FusionCheck(
                    leg="backtest",
                    check="walk-forward strategy among the agreeing votes",
                    threshold=walk_forward.strategy_id if walk_forward is not None else None,
                    actual=", ".join(agreeing_ids) if agreeing_ids else "none",
                    passed=(
                        walk_forward is not None
                        and signal_dir is not None
                        and walk_forward.strategy_id in agreeing_ids
                    ),
                ),
                ReasonCode(code="gate.backtest_strategy_agrees"),
            ),
        )
    )

    # Non-voting inputs (Plan 0077 phase 5, ADR-0071): recorded in the trace as
    # informational (`gating=False`) rows so a reader sees they were consumed —
    # they shape sizing/stop/conviction but never block or vote a direction. Only
    # present when supplied, so a call without them keeps the pre-0077 trace.
    if volatility is not None:
        pairs.append(
            (
                FusionCheck(
                    leg="forecast",
                    check="volatility forecast (non-voting: sizing + stop)",
                    threshold=None,
                    actual=volatility.beats_baseline,
                    passed=True,
                    gating=False,
                ),
                ReasonCode(
                    code="gate.volatility_nonvoting",
                    params={"beats_baseline": 1 if volatility.beats_baseline else 0},
                ),
            )
        )
    if regime is not None:
        pairs.append(
            (
                FusionCheck(
                    leg="forecast",
                    check="regime forecast (non-voting: conviction)",
                    threshold=None,
                    actual=regime.beats_baseline,
                    passed=True,
                    gating=False,
                ),
                ReasonCode(
                    code="gate.regime_nonvoting",
                    params={"beats_baseline": 1 if regime.beats_baseline else 0},
                ),
            )
        )

    checks = tuple(check for check, _ in pairs)
    gate_codes = tuple(code for _, code in pairs)
    return checks, gate_codes


def _blockers_from_checks(
    checks: tuple[FusionCheck, ...],
    *,
    forecast_dir: _Directional | None,
    signal_dir: _Directional | None,
    long_ids: Sequence[str],
    short_ids: Sequence[str],
    sharpe_mean: float | None,
    walk_forward: WalkForwardResult | None,
) -> tuple[list[str], list[ReasonCode]]:
    """Derive the flat verdict's named blockers from the failed trace rows
    (Plan 0072 phase 3), and — co-generated in lockstep — their translatable
    `blocker.*` reason-codes (Plan 0069 phase 4, ADR-0063).

    `_build_trace` is the single source of truth for *which* gates failed, so a
    blocker can no longer drift from its gate (finding (e)): a blocker appears
    iff its gate failed. Each leg reports its root-cause failure once — the elif
    chains mirror the trace's gate order, and a downstream gate that fails only
    because an upstream one did (the agreement gate when the forecast has no
    direction; the strategy gate when no direction was voted) is subsumed,
    exactly as the hand-written blocker chain did before. The message strings
    (which embed the failing values) are the only thing computed here; the
    decision of whether to emit is the check's `passed` flag. The code carries
    the same failing values as raw params for the renderer."""

    # Only *gating* checks block (Plan 0077 phase 5, ADR-0071): a demoted
    # direction leg's failed checks carry `gating=False`, so they no longer
    # produce a blocker — the whole point of the demotion.
    failed = {(c.leg, c.check) for c in checks if c.gating and not c.passed}
    blockers: list[str] = []
    codes: list[ReasonCode] = []

    def add(message: str, code: ReasonCode) -> None:
        blockers.append(message)
        codes.append(code)

    # Forecast: a missing probability vs a shipped-but-flat argmax.
    if ("forecast", "probabilities shipped (baseline beaten out-of-sample)") in failed:
        add(
            "forecast shows no edge over baseline (no probability shipped)",
            ReasonCode(code="blocker.forecast_no_edge"),
        )
    elif ("forecast", "argmax direction is directional") in failed:
        add(
            "forecast direction is flat or undecided",
            ReasonCode(code="blocker.forecast_flat"),
        )

    # Signal: conflict, then absence, then disagreement (only a real blocker when
    # the forecast itself has a direction to disagree with — otherwise the
    # forecast leg already blocks and the agreement gate fails only in sympathy).
    if ("signal", "no conflicting live votes") in failed:
        add(
            f"live signals conflict: long={long_ids}, short={short_ids}",
            ReasonCode(
                code="blocker.signals_conflict",
                params={"long": ", ".join(long_ids), "short": ", ".join(short_ids)},
            ),
        )
    elif ("signal", "at least one directional live vote") in failed:
        add(
            "no live strategy signal implies a direction",
            ReasonCode(code="blocker.no_directional_signal"),
        )
    elif (
        "signal",
        "live direction agrees with the forecast direction",
    ) in failed and forecast_dir is not None:
        add(
            f"live signals ({signal_dir}) disagree with the forecast direction ({forecast_dir})",
            ReasonCode(
                code="blocker.signals_disagree_forecast",
                params={
                    "signal_dir": signal_dir if signal_dir is not None else "none",
                    "forecast_dir": forecast_dir,
                },
            ),
        )

    # Backtest: missing basis, then non-positive edge, then an edge backing a
    # non-agreeing strategy (only meaningful once a direction was actually voted).
    if ("backtest", "walk-forward basis supplied") in failed:
        add(
            "no walk-forward backtest basis supplied",
            ReasonCode(code="blocker.no_walk_forward"),
        )
    elif ("backtest", "backtested edge positive (sharpe_mean > 0)") in failed:
        edge_params: dict[str, float | int | str] = {}
        if sharpe_mean is not None:
            edge_params["sharpe_mean"] = sharpe_mean
        add(
            f"no backtested edge: walk-forward sharpe_mean={sharpe_mean}",
            ReasonCode(code="blocker.no_backtested_edge", params=edge_params),
        )
    elif (
        ("backtest", "walk-forward strategy among the agreeing votes") in failed
        and signal_dir is not None
        and walk_forward is not None
    ):
        add(
            f"walk-forward edge is for {walk_forward.strategy_id!r}, "
            "which is not among the agreeing signals",
            ReasonCode(
                code="blocker.edge_nonvoting_strategy",
                params={"strategy_id": walk_forward.strategy_id},
            ),
        )

    return blockers, codes


def _build_basis(
    snapshot: ConditionSnapshot,
    signals: Sequence[SignalEvaluation],
    walk_forward: WalkForwardResult | None,
    forecast: ForecastResult,
    checks: tuple[FusionCheck, ...],
) -> RecommendationBasis:
    # Each prose line is paired with its translatable reason-code in lockstep
    # (Plan 0069 phase 4b, ADR-0063), so the code tuple is index-aligned to the
    # prose list and a code can never drift from its line. The enum values are
    # closed vocabularies, so they ride as raw string tokens in `params` — the
    # renderer localizes them via an enum-label catalog (no prose-parsing).
    condition_pairs: list[tuple[str, ReasonCode]] = [
        (
            f"trend={snapshot.trend}",
            ReasonCode(code="condition.trend", params={"value": snapshot.trend.value}),
        ),
        (
            f"momentum={snapshot.momentum}",
            ReasonCode(code="condition.momentum", params={"value": snapshot.momentum.value}),
        ),
        (
            f"volume={snapshot.volume_stance}",
            ReasonCode(code="condition.volume", params={"value": snapshot.volume_stance.value}),
        ),
        *(
            (
                f"candlestick={hit.pattern} ({hit.direction})",
                ReasonCode(
                    code="condition.candlestick",
                    params={"pattern": hit.pattern, "direction": hit.direction},
                ),
            )
            for hit in snapshot.recent_patterns
        ),
    ]
    conditions = [line for line, _ in condition_pairs]
    condition_codes = tuple(code for _, code in condition_pairs)

    signal_pairs: list[tuple[str, ReasonCode]] = [
        (
            f"{s.strategy_id}: position={s.current_position}"
            + (", fresh_signal" if s.fresh_signal else ""),
            ReasonCode(
                code="signal.vote",
                params={
                    "strategy_id": s.strategy_id,
                    "position": s.current_position,
                    "fresh": 1 if s.fresh_signal else 0,
                },
            ),
        )
        for s in sorted(signals, key=lambda s: s.strategy_id)
    ]
    signal_lines = [line for line, _ in signal_pairs]
    signal_codes = tuple(code for _, code in signal_pairs)

    backtest: dict[str, BasisValue] | None = None
    if walk_forward is not None:
        backtest = {
            "strategy_id": walk_forward.strategy_id,
            "n_splits": walk_forward.n_splits,
            "sharpe_mean": walk_forward.aggregate.get("sharpe_mean"),
            "sharpe_std": walk_forward.aggregate.get("sharpe_std"),
            "total_return_mean": walk_forward.aggregate.get("total_return_mean"),
            "total_return_std": walk_forward.aggregate.get("total_return_std"),
        }

    forecast_summary: dict[str, BasisValue] = {
        "prob_up": forecast.prob_up,
        "prob_down": forecast.prob_down,
        "prob_flat": forecast.prob_flat,
        "horizon_bars": forecast.horizon_bars,
        "skill": forecast.validation.skill,
        "baseline_skill": forecast.validation.baseline_skill,
        "beats_baseline": forecast.validation.beats_baseline,
        "edge_margin": forecast.edge_margin,
        "edge_strength": forecast.edge_strength,
        "model_version": forecast.provenance.model_version,
        # Plan 0066 (ADR-0057): the tier that backed this forecast travels on the
        # recommendation itself, not only in the persisted advice artifact — so a
        # reader can see whether the call ran on v2-full/v2-deep/v1 and why a
        # richer tier was skipped. Both open dict[str, BasisValue] scalars, so no
        # wire-pin/parity test moves (new keys, not a changed field set).
        "feature_set_id": forecast.provenance.feature_set_id,
        "fallback_reason": forecast.provenance.fallback_reason,
    }

    return RecommendationBasis(
        conditions=conditions,
        signals=signal_lines,
        backtest=backtest,
        forecast=forecast_summary,
        checks=checks,
        condition_codes=condition_codes,
        signal_codes=signal_codes,
    )


def fuse(
    *,
    snapshot: ConditionSnapshot,
    signals: Sequence[SignalEvaluation],
    walk_forward: WalkForwardResult | None,
    forecast: ForecastResult,
    last_close: float,
    volatility: VolatilityForecast | None = None,
    regime: RegimeForecast | None = None,
) -> Recommendation:
    """Fuse the analyst outputs into one advisory `Recommendation`.

    `last_close` is the close of the last bar the inputs were computed from
    (the as-of bar) — the price anchor for the level geometry. `walk_forward`
    is the out-of-sample validation of the strategy whose live signal is being
    considered; ``None`` means no backtested basis exists, which blocks any
    directional call.

    `volatility` and `regime` (Plan 0077 phase 5, ADR-0071) are **non-voting**
    inputs: volatility shapes the advisory size hint (inverse-vol) and widens the
    stop, regime softens conviction on an unstable regime. Neither can flip or
    manufacture a direction — that still rests on the voting legs agreeing. The
    direction forecast leg itself is **demoted** below `DIRECTION_SKILL_MARGIN`:
    it no longer vetoes a corroborated call nor decides one alone (ADR-0070/0071).

    Raises `ValueError` on inconsistent symbol/timeframe or as-of inputs, or a
    non-positive `last_close`.
    """

    if last_close <= 0.0:
        raise ValueError(f"last_close must be positive, got {last_close}")
    _require_consistent_inputs(snapshot, signals, walk_forward, forecast, volatility, regime)

    forecast_dir = _forecast_direction(forecast)
    signal_dir, conflict, long_ids, short_ids = _signal_votes(signals)
    sharpe_mean = _sharpe_mean(walk_forward)
    edge_factor = _edge_factor(sharpe_mean)
    gating = _direction_leg_gating(forecast)
    direction_leg = DirectionLegStatus(
        present=True, gating=gating, skill_margin=forecast.edge_margin
    )

    # The voting legs (conditions + backtested edge + live signal) must agree for a
    # directional call; the demoted direction leg contributes `gating=False` checks
    # that record but no longer block (ADR-0071). The structured trace records every
    # gate; the flat verdict's named blockers are derived FROM the failed *gating*
    # trace rows (single source of truth), so a blocker and its gate cannot drift.
    # The invariant holds: directional iff no blocker iff every gating check passed
    # (ADR-0029 honest uncertainty; ADR-0058 replayability).
    checks, gate_codes = _build_trace(
        snapshot=snapshot,
        signals=signals,
        walk_forward=walk_forward,
        forecast=forecast,
        forecast_dir=forecast_dir,
        conflict=conflict,
        signal_dir=signal_dir,
        long_ids=long_ids,
        short_ids=short_ids,
        sharpe_mean=sharpe_mean,
        gating=gating,
        volatility=volatility,
        regime=regime,
    )
    blockers, blocker_codes = _blockers_from_checks(
        checks,
        forecast_dir=forecast_dir,
        signal_dir=signal_dir,
        long_ids=long_ids,
        short_ids=short_ids,
        sharpe_mean=sharpe_mean,
        walk_forward=walk_forward,
    )
    basis = _build_basis(snapshot, signals, walk_forward, forecast, checks)

    if blockers:
        # reason_codes mirror the rationale 1:1 ("no actionable edge" + one code
        # per blocker), then the per-gate codes 1:1 with basis.checks (Plan 0069).
        return Recommendation(
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            direction="flat",
            entry_zone=None,
            stop=None,
            targets=[],
            conviction=0.0,
            rationale=["no actionable edge", *blockers],
            basis=basis,
            label="advisory",
            as_of_bar_ts=snapshot.as_of,
            reason_codes=(
                ReasonCode(code="reason.no_actionable_edge"),
                *blocker_codes,
                *gate_codes,
            ),
            direction_leg=direction_leg,
        )

    # Direction comes from the voting legs. When the forecast gates it corroborates
    # (and the agreement gate proved forecast_dir == signal_dir); when it is demoted
    # the call rests on the live signal alone, which the gating checks proved
    # directional and backtest-corroborated. Either way the voting legs decide —
    # the non-voting vol/regime inputs cannot reach this choice (ADR-0071).
    if gating:
        assert forecast_dir is not None and forecast_dir == signal_dir
        direction: _Directional = forecast_dir
    else:
        assert signal_dir is not None
        direction = signal_dir
    agreeing_ids = long_ids if direction == "long" else short_ids

    # Conviction is derived, never invented: the forecast probability of the called
    # direction (when the leg shipped one) times the backtested-edge credit, then
    # dampened by the non-voting regime factor. When the leg is demoted with no
    # probability, conviction rests on the validated backtested edge alone.
    directional_prob = forecast.prob_up if direction == "long" else forecast.prob_down
    base_conviction = (
        directional_prob * edge_factor if directional_prob is not None else edge_factor
    )
    regime_factor, regime_context = _regime_conviction(regime)
    conviction = round(min(max(base_conviction * regime_factor, 0.0), 1.0), 4)

    entry_zone, stop, targets = _levels(direction, last_close, snapshot)
    # Non-voting volatility: an inverse-vol size hint and a vol-widened stop.
    vol_used, vol_source = _vol_reading(volatility)
    stop, stop_vol_distance = _widen_stop_for_vol(direction, last_close, stop, vol_used)
    sizing = (
        VolatilitySizing(
            size_factor=round(_size_factor(vol_used), 4),
            vol_used=vol_used,
            vol_source=vol_source,
            stop_vol_distance=stop_vol_distance,
        )
        if volatility is not None
        else None
    )

    skill = forecast.validation.skill
    baseline = forecast.validation.baseline_skill

    # Each rationale line is paired with its translatable reason-code in lockstep
    # (Plan 0069 phase 4), so the code carries the same raw values the English
    # prose embeds and cannot drift from the line. Empty lines drop the pair
    # together. The forecast line branches on the leg's gating status (ADR-0071).
    if gating:
        assert directional_prob is not None  # gating implies a shipped directional prob
        forecast_params: dict[str, float | int | str] = {
            "direction": direction,
            "prob": directional_prob,
            "horizon_bars": forecast.horizon_bars,
            "edge_strength": forecast.edge_strength,
        }
        if skill is not None and baseline is not None:
            forecast_params["skill"] = skill
            forecast_params["baseline"] = baseline
        forecast_line = (
            f"forecast: P({direction})={directional_prob:.3f} over {forecast.horizon_bars} "
            f"bar(s), edge={forecast.edge_strength}"
            + (
                f" (out-of-sample skill {skill:.3f} vs baseline {baseline:.3f})"
                if skill is not None and baseline is not None
                else ""
            )
        )
        forecast_code = ReasonCode(code="reason.forecast", params=forecast_params)
    else:
        margin = forecast.edge_margin
        demotion_params: dict[str, float | int | str] = {"threshold": DIRECTION_SKILL_MARGIN}
        if margin is not None:
            demotion_params["skill_margin"] = margin
        forecast_line = (
            "direction forecast leg present but non-gating: out-of-sample skill margin "
            f"{f'{margin:.3f}' if margin is not None else 'none'} < {DIRECTION_SKILL_MARGIN} "
            "— advisory only, the call rests on the live signal and backtested edge (ADR-0071)"
        )
        forecast_code = ReasonCode(code="reason.direction_leg_nongating", params=demotion_params)

    backtest_params: dict[str, float | int | str] = {}
    if walk_forward is not None and sharpe_mean is not None:
        backtest_params = {
            "sharpe_mean": sharpe_mean,
            "n_splits": walk_forward.n_splits,
            "strategy_id": walk_forward.strategy_id,
        }

    rationale_pairs: list[tuple[str, ReasonCode]] = [
        (forecast_line, forecast_code),
        (
            f"live signals agree ({direction}): {', '.join(agreeing_ids)}",
            ReasonCode(
                code="reason.signals_agree",
                params={"direction": direction, "strategies": ", ".join(agreeing_ids)},
            ),
        ),
        (
            f"backtested edge: walk-forward sharpe_mean={sharpe_mean:.3f} "
            f"over {walk_forward.n_splits} folds ({walk_forward.strategy_id})"
            if walk_forward is not None and sharpe_mean is not None
            else "",
            ReasonCode(code="reason.backtested_edge", params=backtest_params),
        ),
        (
            f"conditions: trend={snapshot.trend}, momentum={snapshot.momentum}, "
            f"volume={snapshot.volume_stance}",
            ReasonCode(
                code="reason.conditions",
                params={
                    "trend": str(snapshot.trend),
                    "momentum": str(snapshot.momentum),
                    "volume": str(snapshot.volume_stance),
                },
            ),
        ),
    ]
    # Non-voting rationale lines, appended after the voting legs so the reason-code
    # order stays stable for a call without them (Plan 0077 phase 5).
    if sizing is not None:
        rationale_pairs.append(
            (
                f"volatility (non-voting): {vol_source} vol {vol_used:.4f} "
                f"→ size factor {sizing.size_factor:.2f}"
                + (
                    f", stop widened to {stop_vol_distance:.4f}"
                    if stop_vol_distance is not None
                    else ""
                )
                if vol_used is not None
                else "volatility (non-voting): no usable reading → neutral sizing",
                ReasonCode(
                    code="reason.sizing",
                    params={
                        "vol_source": vol_source,
                        "size_factor": sizing.size_factor,
                        **({"vol_used": vol_used} if vol_used is not None else {}),
                    },
                ),
            )
        )
    if regime_context is not None:
        rationale_pairs.append(
            (
                f"regime (non-voting): {regime_context.current_regime or 'undefined'}, "
                f"transition-trusted={regime_context.trusted} "
                f"→ conviction factor {regime_context.conviction_factor:.2f}",
                ReasonCode(
                    code="reason.regime_context",
                    params={
                        "current_regime": regime_context.current_regime or "undefined",
                        "trusted": 1 if regime_context.trusted else 0,
                        "conviction_factor": regime_context.conviction_factor,
                    },
                ),
            )
        )
    rationale = [line for line, _ in rationale_pairs if line]
    rationale_codes = [code for line, code in rationale_pairs if line]

    return Recommendation(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        direction=direction,
        entry_zone=entry_zone,
        stop=stop,
        targets=targets,
        conviction=conviction,
        rationale=rationale,
        basis=basis,
        label="advisory",
        as_of_bar_ts=snapshot.as_of,
        reason_codes=(*rationale_codes, *gate_codes),
        sizing=sizing,
        regime_context=regime_context,
        direction_leg=direction_leg,
    )


__all__ = [
    "ATR_FALLBACK_FRACTION",
    "DIRECTION_SKILL_MARGIN",
    "ENTRY_BAND_ATR",
    "LEVEL_BUFFER_ATR",
    "REFERENCE_VOL",
    "REGIME_CONVICTION_FLOOR",
    "SHARPE_FULL_CREDIT",
    "SIZE_CAP",
    "SIZE_FLOOR",
    "STOP_ATR",
    "STOP_VOL_CAP_FRACTION",
    "STOP_VOL_MULT",
    "TARGET_ATR",
    "fuse",
]
