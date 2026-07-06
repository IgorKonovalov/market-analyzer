"""Phase-4 done-when (tool half) for Plan 0036, extended by Plan 0059 phase 3
and Plan 0062 phase 2 (the ADR-0057 tier ladder): the `forecast` MCP tool.

Covered:
- the no-edge gate result ships null probabilities but keeps the validation basis
  and provenance (ADR-0030 invariant 3/4);
- an accepted result ships a valid probability distribution AND persists the model
  under the gitignored models/ root (ADR-0040 §3);
- the result is deterministic — re-running on the same bars + seed is byte-identical
  (no wall-clock field), with stable provenance;
- provenance carries the model_version + the prediction-affecting inputs, and its
  lib_versions are scikit-learn only (the prediction-affecting lib);
- Plan 0059: per-horizon independence — genuine 1-bar signal + shuffled 21-bar
  labels ships probabilities at h=1 and no-edge at h=21 **in the same call**
  (done-when a); the horizon set defaults per timeframe; the tool response
  round-trips through `MultiHorizonForecastResult` with `series_inputs`
  populated when a metric store is wired, and says v1/empty when not (done-when
  c, ADR-0054);
- the tool is wired into the live MCP server (registration assertion);
- Plan 0037 phase 1: a successful run publishes exactly one `forecast.completed
  v1` envelope carrying the full `MultiHorizonForecastResult` inline (a no-edge
  horizon travels with null probabilities — `exclude_none`-absent on the wire —
  rather than suppressing the event); nothing is published on any failure.

The accepted/no-edge *branches* of the v1 core are exercised by stubbing
`validate` so the gate state is deterministic; the gate's own correctness on real
data is pinned by `tests/forecast/test_validation.py`. The determinism,
provenance, independence, and live-server tests run the real pipeline end to end.
"""

from __future__ import annotations

import asyncio
import random
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools import forecast as forecast_tool
from market_analyser.api.mcp_tools.forecast import (
    DAILY_HORIZONS,
    EDGE_MARGIN_THRESHOLD,
    FALLBACK_REASON_UNWIRED,
    FORECAST_DESCRIPTION,
    _classify_edge,
    _compute_forecast,
    _compute_multi_horizon_forecast,
    _multi_forecast_response,
    _normalise_horizons,
    default_horizons,
)
from market_analyser.data.metric_series import MetricPoint
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.events import Envelope, EventBus
from market_analyser.forecast import validation as validation_module
from market_analyser.forecast.exogenous import build_exogenous_columns
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    EXOGENOUS_SERIES_IDS_V2_DEEP,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    FEATURE_SET_ID_V2_DEEP,
    build_feature_rows_v2_deep,
)
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.result import MultiHorizonForecastResult
from market_analyser.forecast.tiers import MIN_TIER_ROWS
from market_analyser.forecast.validation import ForecastValidation
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from tests.forecast._synthetic import synthetic_bars

RENDERER_SECRET = "renderer-test-secret"
BARS = synthetic_bars(220)


def _fake_validation(*, beats: bool) -> ForecastValidation:
    return ForecastValidation(
        horizon_bars=1,
        n_splits=5,
        n_scored=120,
        skill=0.61 if beats else 0.30,
        baseline_skill=0.40,
        persistence_skill=0.40,
        majority_skill=0.36,
        beats_baseline=beats,
        folds=[],
    )


# --------------------------------------------------------------------------- #
# Body-level tests (no live server) — fast, deterministic gate branches.       #
# --------------------------------------------------------------------------- #


def test_no_edge_result_ships_null_probabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forecast_tool, "validate", lambda bars, **kw: _fake_validation(beats=False))
    result = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    assert result.prob_up is None
    assert result.prob_down is None
    assert result.prob_flat is None
    assert result.validation.beats_baseline is False
    assert result.provenance.model_version  # provenance is always present


def test_no_edge_result_does_not_persist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forecast_tool, "validate", lambda bars, **kw: _fake_validation(beats=False))
    _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=tmp_path,
    )
    assert not any(tmp_path.iterdir())  # rejected model is not written


def test_accepted_result_ships_distribution_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(forecast_tool, "validate", lambda bars, **kw: _fake_validation(beats=True))
    result = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=tmp_path,
    )
    assert result.prob_up is not None
    assert result.prob_down is not None
    assert result.prob_flat is not None
    assert abs(result.prob_up + result.prob_down + result.prob_flat - 1.0) < 1e-9

    # The accepted model is persisted under models/<model_version>/.
    artifact_dir = tmp_path / result.provenance.model_version
    assert (artifact_dir / "model.joblib").is_file()
    assert (artifact_dir / "meta.json").is_file()


def test_result_is_deterministic() -> None:
    """Real pipeline (no stub): identical bars + seed → byte-identical result."""

    first = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    second = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_provenance_lib_versions_are_scikit_learn_only() -> None:
    result = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    prov = result.provenance
    assert prov.seed == 1729
    assert prov.feature_set_id == FEATURE_SET_ID
    assert set(prov.lib_versions) == {"scikit-learn"}  # statsmodels excluded (unused in prediction)
    assert result.as_of_bar_ts == BARS[-1].event_ts


# --------------------------------------------------------------------------- #
# Plan 0050 phase 5: edge_margin + edge_strength qualifier.                    #
# --------------------------------------------------------------------------- #


def _validation(*, skill: float | None, baseline: float | None, beats: bool) -> ForecastValidation:
    return ForecastValidation(
        horizon_bars=1,
        n_splits=5,
        n_scored=120,
        skill=skill,
        baseline_skill=baseline,
        persistence_skill=baseline,
        majority_skill=baseline,
        beats_baseline=beats,
        folds=[],
    )


def test_classify_edge_splits_clear_marginal_no_edge_and_unscored() -> None:
    from math import isclose

    # Comfortable beat → clear.
    margin, strength = _classify_edge(_validation(skill=0.61, baseline=0.40, beats=True))
    assert strength == "clear"
    assert isclose(margin or 0.0, 0.21)

    # Thin beat (below threshold) → marginal; the 2026-06-08 incident shape.
    margin, strength = _classify_edge(_validation(skill=0.490, baseline=0.488, beats=True))
    assert strength == "marginal"
    assert isclose(margin or 0.0, 0.002)

    # Exactly at the threshold → clear (>= is the boundary).
    margin, strength = _classify_edge(
        _validation(skill=0.40 + EDGE_MARGIN_THRESHOLD, baseline=0.40, beats=True)
    )
    assert strength == "clear"

    # No beat → no_edge, but the (non-positive) margin is still reported.
    margin, strength = _classify_edge(_validation(skill=0.30, baseline=0.40, beats=False))
    assert strength == "no_edge"
    assert isclose(margin or 0.0, -0.10)

    # Nothing scored → no_edge with a None margin (nothing to compare).
    margin, strength = _classify_edge(_validation(skill=None, baseline=None, beats=False))
    assert strength == "no_edge"
    assert margin is None


def test_marginal_beat_ships_probabilities_labelled_marginal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from math import isclose

    monkeypatch.setattr(
        forecast_tool,
        "validate",
        lambda bars, **kw: _validation(skill=0.490, baseline=0.488, beats=True),
    )
    result = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    # prob_* still ship (it beat baseline) but the edge is flagged thin.
    assert result.prob_up is not None
    assert result.edge_strength == "marginal"
    assert result.edge_margin is not None
    assert isclose(result.edge_margin, 0.002)


def test_clear_beat_labelled_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        forecast_tool,
        "validate",
        lambda bars, **kw: _validation(skill=0.61, baseline=0.40, beats=True),
    )
    result = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    assert result.prob_up is not None
    assert result.edge_strength == "clear"


def test_no_edge_labelled_no_edge_with_null_probabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        forecast_tool,
        "validate",
        lambda bars, **kw: _validation(skill=0.30, baseline=0.40, beats=False),
    )
    result = _compute_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizon_bars=1,
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
    )
    assert result.prob_up is None  # no-edge path unchanged
    assert result.edge_strength == "no_edge"


def test_description_documents_edge_strength() -> None:
    assert "edge_strength" in FORECAST_DESCRIPTION
    assert "edge_margin" in FORECAST_DESCRIPTION


# --------------------------------------------------------------------------- #
# Plan 0059 phase 3: multi-horizon blocks, per-horizon independence,          #
# horizon defaults, and the v1 fallback's explicit provenance.                #
# --------------------------------------------------------------------------- #


def _mean_reverting_bars(n: int, seed: int) -> list[Bar]:
    """Genuine 1-bar signal: the close alternates ±2% every bar (strong one-bar
    mean reversion, fully revealed by a trailing feature like ``ret_1``) plus
    small seeded noise so the series is not literally periodic. The persistence
    baseline is systematically wrong on it (it predicts continuation) and the
    majority baseline sits near 0.5, so a causal model genuinely beats baseline
    at h=1."""

    rng = random.Random(seed)
    start_ts = datetime(2025, 1, 1, tzinfo=UTC)
    price = 100.0
    bars: list[Bar] = []
    for i in range(n):
        direction = 1.0 if i % 2 == 0 else -1.0
        open_ = price
        price = max(1.0, price * (1.0 + direction * 0.02 + rng.gauss(0.0, 0.003)))
        close = price
        high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.002)))
        low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.002)))
        volume = 1_000_000.0 + rng.random() * 10_000.0
        bars.append(
            Bar(
                symbol="ALT",
                timeframe="1d",
                event_ts=start_ts + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                source="synthetic",
            )
        )
    return bars


def _shuffled_labels(labels: list[Direction | None], seed: int) -> list[Direction | None]:
    """A seeded shuffle of the defined labels in place of their original
    positions (``None`` alignment padding stays put). The label distribution is
    preserved; the feature→label relation is destroyed — the honest verdict on
    the shuffled target is 'no edge'."""

    defined = [label for label in labels if label is not None]
    random.Random(seed).shuffle(defined)
    it = iter(defined)
    return [next(it) if label is not None else None for label in labels]


def test_horizons_are_independent_within_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan 0059 phase 3 done-when (a): genuine 1-bar signal + shuffled 21-bar
    labels → h=1 ships probabilities while h=21 returns no-edge, in the SAME
    call. The 21-bar labels are decoupled from the features by a seeded shuffle
    injected at the label builder (for that horizon only); everything else —
    walk-forward, purge, gate, training — is the real pipeline."""

    bars = _mean_reverting_bars(400, seed=20260706)

    def _patched(bars_arg: Sequence[Bar], params: LabelParams) -> list[Direction | None]:
        labels = build_labels(bars_arg, params)
        if params.horizon_bars == 21:
            return _shuffled_labels(labels, seed=99)
        return labels

    monkeypatch.setattr(validation_module, "build_labels", _patched)
    monkeypatch.setattr(forecast_tool, "build_labels", _patched)

    result = _compute_multi_horizon_forecast(
        bars=bars,
        symbol="ALT",
        timeframe="1d",
        horizons=(1, 21),
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
        metric_lookup=None,
    )

    assert [block.horizon_bars for block in result.horizons] == [1, 21]
    h1, h21 = result.horizons

    # h=1: the model genuinely beats baseline out-of-sample and ships.
    assert h1.validation.beats_baseline is True
    assert h1.prob_up is not None
    assert h1.prob_down is not None
    assert h1.prob_flat is not None
    assert abs(h1.prob_up + h1.prob_down + h1.prob_flat - 1.0) < 1e-9

    # h=21: shuffled target → honest no-edge, null probabilities — the failed
    # horizon does not poison the passing one, nor vice versa.
    assert h21.validation.beats_baseline is False
    assert h21.prob_up is None
    assert h21.prob_down is None
    assert h21.prob_flat is None
    assert h21.edge_strength == "no_edge"

    # Both blocks keep their own validation basis (per-horizon numbers).
    assert h1.validation.horizon_bars == 1
    assert h21.validation.horizon_bars == 21
    assert h1.validation.skill is not None
    assert h21.validation.skill is not None


def test_default_horizons_per_timeframe() -> None:
    assert default_horizons("1d") == DAILY_HORIZONS == (1, 5, 21)
    for timeframe in ("1h", "15m", "4h", "1w"):
        assert default_horizons(timeframe) == (1,)


def test_normalise_horizons_dedupes_sorts_and_rejects_invalid() -> None:
    assert _normalise_horizons(None, "1d") == (1, 5, 21)
    assert _normalise_horizons(None, "1h") == (1,)
    assert _normalise_horizons([21, 1, 5, 1], "1d") == (1, 5, 21)
    with pytest.raises(ValueError, match="must not be empty"):
        _normalise_horizons([], "1d")
    with pytest.raises(ValueError, match=">= 1"):
        _normalise_horizons([1, 0], "1d")


def test_without_metric_store_result_is_explicitly_v1() -> None:
    """No metric store wired → the v1 OHLCV-only feature set, stated in the
    result (feature_set_id + empty series_inputs + the unwired fallback
    reason), never silent (ADR-0054; Plan 0061 phase 2)."""

    result = _compute_multi_horizon_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizons=(1,),
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
        metric_lookup=None,
    )
    assert result.feature_set_id == FEATURE_SET_ID
    (block,) = result.horizons
    assert block.provenance is not None
    assert block.provenance.feature_set_id == FEATURE_SET_ID
    assert block.provenance.series_inputs == ()
    assert block.provenance.fallback_reason == FALLBACK_REASON_UNWIRED


def test_wired_but_starved_store_falls_back_to_v1_with_stated_reason() -> None:
    """Plan 0061 phase 2 done-when, restated over the Plan 0062 ladder: a wired
    store with ZERO exogenous points no longer produces the vacuous no-edge
    (the 2026-07-06 production finding — `beats_baseline=False` with
    `n_scored=0` shipped as if it were an evaluated verdict). The call computes
    on the v1 set: every block carries a genuinely scored validation
    (`n_scored > 0`), and the provenance states the full ADR-0057 skip chain —
    both exogenous tiers named, each with its surviving-row count and floor."""

    engine = make_engine(":memory:")
    apply_migrations(engine)
    starved_store = MetricPointsRepository(make_session_factory(engine))
    result = _compute_multi_horizon_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizons=(1, 5),
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
        metric_lookup=starved_store,
    )
    engine.dispose()

    assert result.feature_set_id == FEATURE_SET_ID  # v1, stated
    assert len(result.horizons) == 2
    for block in result.horizons:
        # The vacuous shape is no longer producible from a starved store:
        # every block's walk-forward genuinely scored something.
        assert block.validation.n_scored > 0
        assert block.provenance is not None
        assert block.provenance.feature_set_id == FEATURE_SET_ID
        assert block.provenance.series_inputs == ()
        assert block.provenance.fallback_reason == (
            f"v2-full unavailable: 0 of {len(BARS)} bars survived the join "
            f"(floor {MIN_TIER_ROWS}); "
            f"v2-deep unavailable: 0 of {len(BARS)} bars survived the join "
            f"(floor {MIN_TIER_ROWS})"
        )


def test_v2_run_keeps_fallback_reason_absent_and_wire_stable() -> None:
    """A store with enough joined history runs v2 with `fallback_reason=None`,
    and the field is absent from the `exclude_none` wire dump — the existing
    v2 provenance dump does not move (the 0052 additive-field precedent)."""

    engine = make_engine(":memory:")
    apply_migrations(engine)
    store = MetricPointsRepository(make_session_factory(engine))
    first_open = int(BARS_V2[0].event_ts.timestamp())
    store.upsert_points(
        [
            MetricPoint(series_id=series_id, ts=first_open - 60, value=10.0 + float(i))
            for i, series_id in enumerate(EXOGENOUS_SERIES_IDS_V2)
        ]
    )
    result = _compute_multi_horizon_forecast(
        bars=list(BARS_V2),
        symbol="SYN",
        timeframe="1d",
        horizons=(1,),
        flat_band=0.001,
        n_splits=4,
        seed=1729,
        models_dir=None,
        metric_lookup=store,
    )
    engine.dispose()

    assert result.feature_set_id == FEATURE_SET_ID_V2
    (block,) = result.horizons
    assert block.provenance is not None
    assert block.provenance.fallback_reason is None
    wire = block.provenance.model_dump(mode="json", exclude_none=True)
    assert "fallback_reason" not in wire
    # The exact pre-0061 v2 wire field set, byte-for-byte unmoved.
    assert set(wire) == {
        "model_version",
        "feature_set_id",
        "training_cutoff",
        "seed",
        "lib_versions",
        "series_inputs",
    }


def test_deep_seeded_store_trains_v2_deep_and_states_the_v2_full_skip() -> None:
    """Plan 0062 phase 2 done-when (b): dominance and OI empty but the deep
    series seeded across the bar window → **v2-deep trains**. The provenance's
    `feature_set_id` is the deep id, `series_inputs` names exactly the three
    deep series, and `fallback_reason` names the v2-full skip with its
    surviving-row count — the live BTC-USD store shape, end to end through the
    tool core."""

    engine = make_engine(":memory:")
    apply_migrations(engine)
    store = MetricPointsRepository(make_session_factory(engine))
    first_open = int(BARS_V2[0].event_ts.timestamp())
    store.upsert_points(
        [
            MetricPoint(series_id=series_id, ts=first_open - 60, value=10.0 + float(i))
            for i, series_id in enumerate(EXOGENOUS_SERIES_IDS_V2_DEEP)
        ]
    )
    n_deep = sum(
        1
        for row in build_feature_rows_v2_deep(
            BARS_V2, build_exogenous_columns(BARS_V2, EXOGENOUS_SERIES_IDS_V2, store)
        )
        if row is not None
    )
    assert n_deep >= MIN_TIER_ROWS  # the fixture genuinely clears the floor

    result = _compute_multi_horizon_forecast(
        bars=list(BARS_V2),
        symbol="SYN",
        timeframe="1d",
        horizons=(1,),
        flat_band=0.001,
        n_splits=4,
        seed=1729,
        models_dir=None,
        metric_lookup=store,
    )
    engine.dispose()

    assert result.feature_set_id == FEATURE_SET_ID_V2_DEEP
    (block,) = result.horizons
    assert block.validation.n_scored > 0  # deep genuinely trained and scored
    assert block.provenance is not None
    assert block.provenance.feature_set_id == FEATURE_SET_ID_V2_DEEP
    consumed = {s.series_id: s.last_point_ts for s in block.provenance.series_inputs}
    assert set(consumed) == set(EXOGENOUS_SERIES_IDS_V2_DEEP)
    assert all(ts == first_open - 60 for ts in consumed.values())
    assert block.provenance.fallback_reason == (
        f"v2-full unavailable: 0 of {len(BARS_V2)} bars survived the join "
        f"(floor {MIN_TIER_ROWS}); trained v2-deep ({n_deep} rows)"
    )
    # The skip chain travels on the wire (exclude_none keeps a non-None reason).
    wire = block.provenance.model_dump(mode="json", exclude_none=True)
    assert wire["fallback_reason"] == block.provenance.fallback_reason
    assert [s["series_id"] for s in wire["series_inputs"]] == list(EXOGENOUS_SERIES_IDS_V2_DEEP)


def test_multi_horizon_result_is_deterministic() -> None:
    first = _compute_multi_horizon_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizons=(1, 5),
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
        metric_lookup=None,
    )
    second = _compute_multi_horizon_forecast(
        bars=list(BARS),
        symbol="SYN",
        timeframe="1d",
        horizons=(1, 5),
        flat_band=0.001,
        n_splits=5,
        seed=1729,
        models_dir=None,
        metric_lookup=None,
    )
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_description_documents_multi_horizon_surface() -> None:
    assert "horizons" in FORECAST_DESCRIPTION
    assert "series_inputs" in FORECAST_DESCRIPTION


# --------------------------------------------------------------------------- #
# Plan 0037 phase 1: `forecast.completed v1` emission.                        #
# --------------------------------------------------------------------------- #


def _run_draining_bus(**overrides: Any) -> tuple[MultiHorizonForecastResult, list[Envelope]]:
    """Run `_multi_forecast_response` with a subscription open on its bus and
    return `(result, envelopes)` — the subscription is opened before the call
    so nothing published can be missed (the `recommend` test pattern)."""

    bus = EventBus()

    async def _go() -> tuple[MultiHorizonForecastResult, list[Envelope]]:
        sub = bus.subscribe()
        try:
            kwargs: dict[str, Any] = {
                "provider": _BarsProvider(BARS),
                "event_bus": bus,
                "models_dir": None,
                "metric_lookup": None,
                "symbol": "SYN",
                "timeframe": "1d",
                "range_start": datetime(2024, 1, 1, tzinfo=UTC),
                "range_end": datetime(2025, 12, 31, tzinfo=UTC),
                "horizons": [1, 5],
                "flat_band": 0.001,
                "n_splits": 5,
                "seed": 1729,
            }
            kwargs.update(overrides)
            result = await _multi_forecast_response(**kwargs)
            envelopes: list[Envelope] = []
            try:
                while True:
                    envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.3))
            except TimeoutError:
                pass
            return result, envelopes
        finally:
            sub.close()

    return asyncio.run(_go())


class TestEventEmission:
    """Plan 0037 phase 1 done-when: running `forecast` emits exactly one
    `forecast.completed v1` envelope carrying the full
    `MultiHorizonForecastResult`; a no-edge horizon ships null probabilities in
    its block rather than suppressing the event; failures publish nothing."""

    def test_success_publishes_exactly_one_envelope_with_full_result(self) -> None:
        result, envelopes = _run_draining_bus()

        assert len(envelopes) == 1  # exactly one, not "at least one"
        envelope = envelopes[0]
        assert envelope.type == "forecast.completed"
        assert envelope.version == 1

        # The full result rides inline: the payload is byte-for-byte the bus's
        # dump of the returned artifact, blocks and provenance included.
        assert envelope.payload == {"forecast": result.model_dump(mode="json", exclude_none=True)}
        payload_forecast = envelope.payload["forecast"]
        assert payload_forecast["symbol"] == "SYN"
        assert payload_forecast["timeframe"] == "1d"
        assert [block["horizon_bars"] for block in payload_forecast["horizons"]] == [1, 5]

    def test_all_no_edge_result_still_publishes_with_null_probabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The honest no-edge verdict travels: the event fires and the failed
        horizon's block carries no `prob_*` keys on the wire (`None` values are
        `exclude_none`-stripped) — the event is never suppressed."""

        monkeypatch.setattr(
            forecast_tool, "validate", lambda bars, **kw: _fake_validation(beats=False)
        )
        result, envelopes = _run_draining_bus(horizons=[1])

        (block_model,) = result.horizons
        assert block_model.prob_up is None

        assert len(envelopes) == 1
        (block,) = envelopes[0].payload["forecast"]["horizons"]
        assert block["validation"]["beats_baseline"] is False
        assert "prob_up" not in block
        assert "prob_down" not in block
        assert "prob_flat" not in block

    @staticmethod
    def _failing_run_envelopes(match: str, **overrides: Any) -> list[Envelope]:
        """Expect `_multi_forecast_response` to raise; return whatever hit the bus."""

        bus = EventBus()

        async def _go() -> list[Envelope]:
            sub = bus.subscribe()
            try:
                kwargs: dict[str, Any] = {
                    "provider": _BarsProvider(BARS),
                    "event_bus": bus,
                    "models_dir": None,
                    "metric_lookup": None,
                    "symbol": "SYN",
                    "timeframe": "1d",
                    "range_start": datetime(2024, 1, 1, tzinfo=UTC),
                    "range_end": datetime(2025, 12, 31, tzinfo=UTC),
                    "horizons": [1],
                    "flat_band": 0.001,
                    "n_splits": 5,
                    "seed": 1729,
                }
                kwargs.update(overrides)
                with pytest.raises(ValueError, match=match):
                    await _multi_forecast_response(**kwargs)
                envelopes: list[Envelope] = []
                try:
                    while True:
                        envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.2))
                except TimeoutError:
                    pass
                return envelopes
            finally:
                sub.close()

        return asyncio.run(_go())

    def test_validation_failure_publishes_nothing(self) -> None:
        assert self._failing_run_envelopes("not supported", timeframe="3m") == []

    def test_no_bars_failure_publishes_nothing(self) -> None:
        """A failure past input validation (empty bar fetch) also leaves the
        bus untouched — the publish sits strictly after the computation."""

        assert (
            self._failing_run_envelopes("backfill via get_ohlcv", provider=_BarsProvider([])) == []
        )


# --------------------------------------------------------------------------- #
# Live-server test — registration + end-to-end call.                          #
# --------------------------------------------------------------------------- #


class _BarsProvider:
    """Returns a deterministic bar list regardless of (symbol, range)."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return self.bars

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError


@pytest.fixture
def mcp_secret(tmp_path: Path) -> str:
    return load_or_generate_mcp_secret(tmp_path / "mcp-secret.json")


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def app(mcp_secret: str, annotations_repo: AnnotationsRepository) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_BarsProvider(bars=BARS),
        annotations_repository=annotations_repo,
        event_bus=EventBus(),
    )


@contextmanager
def _uvicorn_server(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="error", access_log=False)
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("uvicorn server failed to start within 5s")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
    with _uvicorn_server(app) as url:
        yield url


@asynccontextmanager
async def _mcp_session(url: str, bearer: str) -> AsyncIterator[ClientSession]:
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=httpx.Timeout(30.0),
        ) as http_client,
        streamable_http_client(f"{url}/mcp", http_client=http_client) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def test_forecast_tool_registered_and_returns_result(live_server: str, mcp_secret: str) -> None:
    """No metric store on this server → the call computes on the v1 feature set
    and the response says so explicitly (feature_set_id + empty series_inputs)."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            tools = {t.name for t in (await session.list_tools()).tools}
            assert "forecast" in tools  # registration assertion
            result = await session.call_tool(
                "forecast",
                {
                    "symbol": "SYN",
                    "timeframe": "1d",
                    "range_start": "2025-01-01T00:00:00+00:00",
                    "range_end": "2025-12-31T00:00:00+00:00",
                    "horizons": [1],
                },
            )
            assert not result.isError, f"forecast errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    parsed = MultiHorizonForecastResult.model_validate(payload)
    assert parsed.symbol == "SYN"
    assert parsed.timeframe == "1d"
    assert parsed.feature_set_id == FEATURE_SET_ID  # v1, stated
    (block,) = parsed.horizons
    assert block.horizon_bars == 1
    assert block.provenance is not None
    assert block.provenance.model_version
    assert block.provenance.series_inputs == ()  # and not silent about it


# The v2 live-server fixture pair: a real metric store (in-memory SQLite through
# the migrations) seeded with one pre-series point per exogenous series, and a
# bar history long enough for the 200W-MA cycle feature to define v2 rows
# (~1400 daily bars) with room after the warm-up for scored walk-forward folds.
BARS_V2 = synthetic_bars(2200)


@pytest.fixture
def v2_app(mcp_secret: str) -> Iterator[FastAPI]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    session_factory = make_session_factory(engine)
    first_open = int(BARS_V2[0].event_ts.timestamp())
    MetricPointsRepository(session_factory).upsert_points(
        [
            MetricPoint(series_id=series_id, ts=first_open - 60, value=10.0 + float(i))
            for i, series_id in enumerate(EXOGENOUS_SERIES_IDS_V2)
        ]
    )
    yield create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_BarsProvider(bars=BARS_V2),
        annotations_repository=AnnotationsRepository(session_factory),
        engine=engine,
        event_bus=EventBus(),
    )
    engine.dispose()


@pytest.fixture
def v2_live_server(v2_app: FastAPI) -> Iterator[str]:
    with _uvicorn_server(v2_app) as url:
        yield url


def test_forecast_tool_round_trips_with_series_inputs(v2_live_server: str, mcp_secret: str) -> None:
    """Plan 0059 phase 3 done-when (c): the tool response round-trips through
    `MultiHorizonForecastResult` with `series_inputs` populated, and every
    horizon block carries its own beats_baseline / skill / baseline numbers.
    The default 1d horizon set (1/5/21) is exercised through the wire."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(v2_live_server, mcp_secret) as session:
            result = await session.call_tool(
                "forecast",
                {
                    "symbol": "SYN",
                    "timeframe": "1d",
                    "range_start": "2025-01-01T00:00:00+00:00",
                    "range_end": "2031-12-31T00:00:00+00:00",
                    "n_splits": 4,
                },
            )
            assert not result.isError, f"forecast errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    parsed = MultiHorizonForecastResult.model_validate(payload)

    assert parsed.feature_set_id == FEATURE_SET_ID_V2
    assert [block.horizon_bars for block in parsed.horizons] == [1, 5, 21]

    first_open = int(BARS_V2[0].event_ts.timestamp())
    for block in parsed.horizons:
        # Per-horizon validation basis, whatever the verdict says.
        assert block.validation.horizon_bars == block.horizon_bars
        assert block.validation.skill is not None
        assert block.validation.baseline_skill is not None
        assert isinstance(block.validation.beats_baseline, bool)
        # Provenance names every exogenous series consumed, with the freshest
        # point ts the lag-1 join actually read.
        assert block.provenance is not None
        assert block.provenance.feature_set_id == FEATURE_SET_ID_V2
        consumed = {s.series_id: s.last_point_ts for s in block.provenance.series_inputs}
        assert set(consumed) == set(EXOGENOUS_SERIES_IDS_V2)
        assert all(ts == first_open - 60 for ts in consumed.values())

    # Round-trip: re-validating the parsed model's own dump is lossless.
    assert MultiHorizonForecastResult.model_validate(parsed.model_dump()) == parsed
