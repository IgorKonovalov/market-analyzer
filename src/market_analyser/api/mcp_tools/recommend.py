"""`recommend` MCP tool — Plan 0038 phase 2 (ADR-0029).

The advisor layer's agent-facing surface: assemble the four live analyst
inputs for one symbol/timeframe and run the pure fusion (phase 1) into a
single labeled advisory `Recommendation`. The flow:

    validate inputs (symbol / timeframe / strategy / params / knobs)
        -> now = datetime.now(UTC)          (the only wall-clock read)
        -> fetch bars [range_start, now]    (fetch-on-miss via coordinator)
        -> closed bars only                 (same rule as the live evaluator)
        -> condition snapshot (ADR-0023)  + live signal (Plan 0026)
         + walk-forward edge (ADR-0024)   + forecast (Plan 0036)
        -> fuse() -> Recommendation | honest "no actionable edge"
        -> bus.publish("recommendation.completed v1", {recommendation})

All four inputs are computed from the SAME closed-bar series, so the
recommendation's `as_of_bar_ts` is the one bar the whole basis saw last —
no leg peeks past another (anti-lookahead, ADR-0023 grain).

**Advisory only, structurally** (ADR-0029): the tool consumes no secret
store, opens no network write path, and returns an artifact whose `label`
can only be `"advisory"`. Order placement is ADR-0025's separate, untaken
decision — a test greps this module and the advisor package for key/order
paths and fails if any appear.

The CPU-bound assembly (walk-forward + forecast training) is offloaded with
`asyncio.to_thread`; the body is factored into `_recommend_response` with an
injectable `now` so tests run on a fixed instant without a live MCP server.

The `recommendation.completed v1` envelope (Plan 0039) is published exactly
once on success and not at all on failure — any raise above the publish
leaves the bus untouched, the same discipline as `signal.evaluated`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.advisor.fusion import fuse
from market_analyser.advisor.models import Recommendation
from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import ConditionSnapshot
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.api.mcp_tools.forecast import (
    _compute_forecast,
    _fs_safe,
    _write_explanation_artifact,
)
from market_analyser.backtest import evaluate_signals as evaluate_signals_core
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward import walk_forward
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.contracts.strategy import BaseParams, discover
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label, timeframe_spec
from market_analyser.data.types import Bar
from market_analyser.events import EventBus, RecommendationCompletedPayloadV1
from market_analyser.forecast.model import DEFAULT_SEED
from market_analyser.forecast.result import ForecastResult

RECOMMEND_DESCRIPTION = (
    "ADVISORY ONLY — fuse the four analyst outputs for one symbol into a single "
    "labeled trade recommendation: the technical condition snapshot, the named "
    "strategy's live signal on the current bar, its walk-forward out-of-sample "
    "edge, and the calibrated direction forecast. Returns a Recommendation "
    "(direction long/short/flat, entry zone, stop, target(s), conviction, "
    "rationale, and the full basis that backed the call) — or an honest "
    "'no actionable edge' flat verdict when any leg disagrees or shows no edge. "
    "A directional call requires the forecast, the live signal, and a positive "
    "backtested edge to all agree; conviction is DERIVED (forecast probability "
    "x backtested edge), never invented, so a marginal edge reads as low "
    "conviction. Every result is labeled 'advisory': the app recommends, the "
    "user decides and acts. This tool holds no trade key, places no order, and "
    "moves no money. Publishes a `recommendation.completed v1` event so a "
    "connected viewer renders the advisory call live. "
    "`range_start` is the warm-up lookback — request enough "
    "history for indicator warm-up, walk-forward folds, and forecast training "
    "(several hundred bars). Bars are fetched on miss where the data layer "
    f"supports it. Supported timeframes: {supported_timeframes_label()}."
)


class RecommendationLegInputs(BaseModel):
    """The four analyst inputs exactly as `fuse()` consumed them (Plan 0063,
    ADR-0058) — the per-leg half of the advice explanation artifact, so a
    verdict's trace can be audited against the very inputs that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: ConditionSnapshot
    signals: tuple[SignalEvaluation, ...]
    walk_forward: WalkForwardResult | None
    forecast: ForecastResult
    last_close: float


class RecommendationExplanationArtifact(BaseModel):
    """The complete persisted explanation for one `recommend` call
    (``runs_dir/advice/<started_at>-<symbol>/explanation.json``): the fused
    verdict (whose ``basis.checks`` is the full gate trace) beside the per-leg
    inputs. ``started_at`` is run provenance — with the on-disk path, the
    documented exception to byte-identical re-runs (the ADR-0018 posture)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    strategy_id: str
    started_at: datetime
    recommendation: Recommendation
    inputs: RecommendationLegInputs


def _assemble_and_fuse(
    *,
    strategy_module: ModuleType,
    closed_bars: list[Bar],
    params_instance: BaseParams,
    timeframe: str,
    horizon_bars: int,
    flat_band: float,
    n_splits: int,
    seed: int,
    models_dir: Path | None,
    now: datetime,
) -> tuple[Recommendation, RecommendationLegInputs]:
    """The CPU-bound core: compute all four analyst inputs from the same
    closed-bar series and fuse them. Deterministic given (`closed_bars`,
    params, knobs) — `now` only re-confirms bar closedness inside the live
    evaluator and never reaches the fusion. Returns the verdict together with
    the leg inputs it was fused from (Plan 0063: the artifact records both)."""

    snapshot = condition_snapshot(closed_bars, timeframe)
    evaluation = evaluate_signals_core(strategy_module, closed_bars, params_instance, now=now)
    wf = walk_forward(
        strategy_module,
        closed_bars,
        params_instance,
        timeframe=timeframe,
        n_splits=n_splits,
    )
    forecast = _compute_forecast(
        bars=closed_bars,
        symbol=closed_bars[0].symbol,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        flat_band=flat_band,
        n_splits=n_splits,
        seed=seed,
        models_dir=models_dir,
    )
    last_close = closed_bars[-1].close
    recommendation = fuse(
        snapshot=snapshot,
        signals=[evaluation],
        walk_forward=wf,
        forecast=forecast,
        last_close=last_close,
    )
    leg_inputs = RecommendationLegInputs(
        snapshot=snapshot,
        signals=(evaluation,),
        walk_forward=wf,
        forecast=forecast,
        last_close=last_close,
    )
    return recommendation, leg_inputs


async def _recommend_response(
    *,
    provider: MarketDataProvider,
    coordinator: BackfillCoordinator | None,
    event_bus: EventBus,
    models_dir: Path | None,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    params: dict[str, Any] | None,
    horizon_bars: int,
    flat_band: float,
    n_splits: int,
    seed: int,
    now: datetime | None = None,
    runs_dir: Path | None = None,
) -> Recommendation:
    """Body of the `recommend` tool. `now` is injectable so tests run on a
    fixed instant; production passes `None` and reads `datetime.now(UTC)`
    here — the only wall-clock read on the path. Publishes the
    `recommendation.completed v1` envelope exactly once, only after a
    successful fusion.

    Plan 0063: with a ``runs_dir`` wired, the complete advice explanation
    (fused verdict + per-leg inputs) is persisted under ``runs_dir/advice/…``
    **before** the publish (a failed write leaves the bus untouched); the
    artifact's stamp reuses ``now`` — still the path's only wall-clock read.
    Without a ``runs_dir`` no artifact is written; the trace itself always
    rides the wire on ``basis.checks``."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    if horizon_bars < 1:
        raise ValueError(f"horizon_bars must be >= 1, got {horizon_bars}")
    if n_splits < 2:
        raise ValueError(f"n_splits must be >= 2, got {n_splits}")
    if flat_band < 0:
        raise ValueError(f"flat_band must be >= 0, got {flat_band}")

    strategies = discover()
    if strategy_id not in strategies:
        raise ValueError(
            f"unknown strategy_id {strategy_id!r}; known: {sorted(strategies)}",
        )
    strategy_module = strategies[strategy_id]

    supported = strategy_module.META.timeframes
    if timeframe not in supported:
        raise ValueError(
            f"timeframe {timeframe!r} not supported by strategy {strategy_id!r} "
            f"(supported: {list(supported)})",
        )

    # Validate params at the boundary against the strategy's own Params model
    # (extra="forbid" rejects unknown keys) — same discipline as evaluate_signals.
    params_instance: BaseParams = strategy_module.Params(**(params or {}))

    resolved_now = now if now is not None else datetime.now(UTC)

    if coordinator is not None:
        result = await asyncio.to_thread(
            coordinator.get_ohlcv_with_status, symbol, timeframe, range_start, resolved_now
        )
        bars = list(result.bars)
    else:
        bars = list(
            await asyncio.to_thread(
                provider.get_ohlcv, symbol, timeframe, range_start, resolved_now
            )
        )
    if not bars:
        raise ValueError(
            f"no bars for {symbol} {timeframe} over the requested window; "
            "backfill via get_ohlcv first",
        )

    # Same closedness rule as the live evaluator (Plan 0026): a bar is closed
    # once a full duration has elapsed since it opened. Every input below sees
    # this same series, so the whole basis shares one as-of bar.
    duration = timeframe_spec(timeframe).bar_duration
    closed_bars = [bar for bar in bars if bar.event_ts + duration <= resolved_now]
    if not closed_bars:
        raise ValueError(
            f"no closed bars: all {len(bars)} bar(s) are still forming relative "
            f"to now={resolved_now!r}"
        )

    recommendation, leg_inputs = await asyncio.to_thread(
        _assemble_and_fuse,
        strategy_module=strategy_module,
        closed_bars=closed_bars,
        params_instance=params_instance,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        flat_band=flat_band,
        n_splits=n_splits,
        seed=seed,
        models_dir=models_dir,
        now=resolved_now,
    )

    if runs_dir is not None:
        stamp = resolved_now.strftime("%Y%m%dT%H%M%S%fZ")
        artifact = RecommendationExplanationArtifact(
            symbol=symbol,
            timeframe=timeframe,
            strategy_id=strategy_id,
            started_at=resolved_now,
            recommendation=recommendation,
            inputs=leg_inputs,
        )
        await asyncio.to_thread(
            _write_explanation_artifact,
            artifact.model_dump_json(indent=2),
            runs_dir,
            f"advice/{stamp}-{_fs_safe(symbol)}/explanation.json",
        )

    # Publish AFTER a successful fusion — every raise above this line leaves
    # the bus untouched (zero envelopes on failure).
    event_bus.publish(
        "recommendation.completed",
        RecommendationCompletedPayloadV1(recommendation=recommendation),
    )

    return recommendation


def register_recommend(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    backfill_coordinator: BackfillCoordinator | None,
    event_bus: EventBus,
    models_dir: Path | None,
    runs_dir: Path | None = None,
) -> None:
    """Bind the `recommend` tool to `server`. Dependencies are captured by
    closure so the tool body keeps the parameter list FastMCP introspects for
    the (strict) input schema. ``runs_dir`` (Plan 0063, ADR-0058) enables the
    per-call advice explanation artifact under ``runs_dir/advice/…``; without
    it the fusion trace still rides the wire, only the full JSON is skipped."""

    @server.tool(description=RECOMMEND_DESCRIPTION)
    async def recommend(
        strategy_id: str,
        symbol: str,
        timeframe: str,
        range_start: datetime,
        params: dict[str, Any] | None = None,
        horizon_bars: int = 1,
        flat_band: float = 0.001,
        n_splits: int = 5,
        seed: int = DEFAULT_SEED,
    ) -> Recommendation:
        return await _recommend_response(
            provider=provider,
            coordinator=backfill_coordinator,
            event_bus=event_bus,
            models_dir=models_dir,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            params=params,
            horizon_bars=horizon_bars,
            flat_band=flat_band,
            n_splits=n_splits,
            seed=seed,
            runs_dir=runs_dir,
        )


__all__ = [
    "RECOMMEND_DESCRIPTION",
    "RecommendationExplanationArtifact",
    "RecommendationLegInputs",
    "_assemble_and_fuse",
    "_recommend_response",
    "register_recommend",
]
