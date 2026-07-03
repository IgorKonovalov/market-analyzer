"""Pure fusion engine (Plan 0038 phase 1, ADR-0029).

`fuse()` maps the four analyst outputs — a condition snapshot (ADR-0023), live
strategy signal evaluations (Plan 0026), a walk-forward result (ADR-0024), and
a calibrated forecast (Plan 0036) — to one labeled advisory `Recommendation`.
It consumes those layers' *output models only* (`ConditionSnapshot`,
`SignalEvaluation`, `WalkForwardResult`, `ForecastResult`), never their
internals — guarded by an import-lint test.

**A directional call requires every leg to agree; anything less is flat.**
The forecast must ship a probability (baseline beaten, ADR-0030) whose argmax
is directional; at least one live signal must imply the same direction with
none opposing; and the walk-forward edge must be positive **and belong to one
of the agreeing strategies** (an edge for a strategy that did not vote backs
nothing). Each failed leg becomes a named blocker in the flat recommendation's
rationale — an honest "no actionable edge", never a fabricated call.

**Conviction is derived, never invented** (the plan's open question, resolved
here as the documented monotone mapping):

    conviction = P(direction) * clamp(sharpe_mean / SHARPE_FULL_CREDIT, 0, 1)

where ``P(direction)`` is the calibrated forecast probability of the called
direction and ``sharpe_mean`` the walk-forward out-of-sample aggregate. The
mapping is monotone in both inputs and zero when either shows no edge: a
marginal forecast or a thin backtest reads as low conviction by construction.

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

from market_analyser.advisor.models import BasisValue, Recommendation, RecommendationBasis
from market_analyser.analysis.types import ConditionSnapshot
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.forecast.result import ForecastResult

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

_Directional = Literal["long", "short"]


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
) -> None:
    """All inputs must describe the same symbol, timeframe, and as-of bar —
    fusing across symbols/timeframes, or a fresh snapshot with a stale leg,
    would produce a well-formed but meaningless call. (`WalkForwardResult`
    carries no as-of field; its currency is the caller's bar series.)"""

    expected = (snapshot.symbol, snapshot.timeframe)
    for name, got in (
        ("forecast", (forecast.symbol, forecast.timeframe)),
        *(((f"signals[{s.strategy_id}]"), (s.symbol, s.timeframe)) for s in signals),
        *(
            (("walk_forward", (walk_forward.symbol, walk_forward.timeframe)),)
            if walk_forward is not None
            else ()
        ),
    ):
        if got != expected:
            raise ValueError(f"inconsistent fusion inputs: snapshot is {expected}, {name} is {got}")

    for name, got_ts in (
        ("forecast.as_of_bar_ts", forecast.as_of_bar_ts),
        *(
            (f"signals[{s.strategy_id}].evaluated_through_ts", s.evaluated_through_ts)
            for s in signals
        ),
    ):
        if got_ts != snapshot.as_of:
            raise ValueError(
                f"inconsistent fusion inputs: snapshot as-of is {snapshot.as_of}, "
                f"{name} is {got_ts}"
            )


def _build_basis(
    snapshot: ConditionSnapshot,
    signals: Sequence[SignalEvaluation],
    walk_forward: WalkForwardResult | None,
    forecast: ForecastResult,
) -> RecommendationBasis:
    conditions = [
        f"trend={snapshot.trend}",
        f"momentum={snapshot.momentum}",
        f"volume={snapshot.volume_stance}",
        *(f"candlestick={hit.pattern} ({hit.direction})" for hit in snapshot.recent_patterns),
    ]
    signal_lines = [
        f"{s.strategy_id}: position={s.current_position}"
        + (", fresh_signal" if s.fresh_signal else "")
        for s in sorted(signals, key=lambda s: s.strategy_id)
    ]

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
    }

    return RecommendationBasis(
        conditions=conditions,
        signals=signal_lines,
        backtest=backtest,
        forecast=forecast_summary,
    )


def fuse(
    *,
    snapshot: ConditionSnapshot,
    signals: Sequence[SignalEvaluation],
    walk_forward: WalkForwardResult | None,
    forecast: ForecastResult,
    last_close: float,
) -> Recommendation:
    """Fuse the four analyst outputs into one advisory `Recommendation`.

    `last_close` is the close of the last bar the inputs were computed from
    (the as-of bar) — the price anchor for the level geometry. `walk_forward`
    is the out-of-sample validation of the strategy whose live signal is being
    considered; ``None`` means no backtested basis exists, which blocks any
    directional call. Raises `ValueError` on inconsistent symbol/timeframe or
    as-of inputs, or a non-positive `last_close`.
    """

    if last_close <= 0.0:
        raise ValueError(f"last_close must be positive, got {last_close}")
    _require_consistent_inputs(snapshot, signals, walk_forward, forecast)

    forecast_dir = _forecast_direction(forecast)
    signal_dir, conflict, long_ids, short_ids = _signal_votes(signals)
    sharpe_mean = _sharpe_mean(walk_forward)
    edge_factor = _edge_factor(sharpe_mean)

    # Every leg must agree for a directional call; each failed leg is a named
    # blocker so the flat verdict explains itself (ADR-0029 honest uncertainty).
    blockers: list[str] = []
    if forecast_dir is None:
        if forecast.prob_up is None:
            blockers.append("forecast shows no edge over baseline (no probability shipped)")
        else:
            blockers.append("forecast direction is flat or undecided")
    if conflict:
        blockers.append(f"live signals conflict: long={long_ids}, short={short_ids}")
    elif signal_dir is None:
        blockers.append("no live strategy signal implies a direction")
    elif forecast_dir is not None and signal_dir != forecast_dir:
        blockers.append(
            f"live signals ({signal_dir}) disagree with the forecast direction ({forecast_dir})"
        )
    if walk_forward is None:
        blockers.append("no walk-forward backtest basis supplied")
    elif edge_factor <= 0.0:
        blockers.append(f"no backtested edge: walk-forward sharpe_mean={sharpe_mean}")
    elif signal_dir is not None and walk_forward.strategy_id not in (
        long_ids if signal_dir == "long" else short_ids
    ):
        blockers.append(
            f"walk-forward edge is for {walk_forward.strategy_id!r}, "
            "which is not among the agreeing signals"
        )

    basis = _build_basis(snapshot, signals, walk_forward, forecast)

    if blockers:
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
        )

    assert forecast_dir is not None and forecast_dir == signal_dir
    direction: _Directional = forecast_dir
    prob = forecast.prob_up if direction == "long" else forecast.prob_down
    assert prob is not None  # forecast_dir is directional only when probs shipped
    conviction = round(prob * edge_factor, 4)

    entry_zone, stop, targets = _levels(direction, last_close, snapshot)
    agreeing_ids = long_ids if direction == "long" else short_ids
    skill = forecast.validation.skill
    baseline = forecast.validation.baseline_skill
    rationale = [
        f"forecast: P({direction})={prob:.3f} over {forecast.horizon_bars} bar(s), "
        f"edge={forecast.edge_strength}"
        + (
            f" (out-of-sample skill {skill:.3f} vs baseline {baseline:.3f})"
            if skill is not None and baseline is not None
            else ""
        ),
        f"live signals agree ({direction}): {', '.join(agreeing_ids)}",
        f"backtested edge: walk-forward sharpe_mean={sharpe_mean:.3f} "
        f"over {walk_forward.n_splits} folds ({walk_forward.strategy_id})"
        if walk_forward is not None and sharpe_mean is not None
        else "",
        f"conditions: trend={snapshot.trend}, momentum={snapshot.momentum}, "
        f"volume={snapshot.volume_stance}",
    ]
    rationale = [line for line in rationale if line]

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
    )


__all__ = [
    "ATR_FALLBACK_FRACTION",
    "ENTRY_BAND_ATR",
    "LEVEL_BUFFER_ATR",
    "SHARPE_FULL_CREDIT",
    "STOP_ATR",
    "TARGET_ATR",
    "fuse",
]
