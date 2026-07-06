"""Phase-4 done-when (tool half) for Plan 0036, extended by Plan 0059 phase 3:
the `forecast` MCP tool.

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
- the tool is wired into the live MCP server (registration assertion).

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
    FORECAST_DESCRIPTION,
    _classify_edge,
    _compute_forecast,
    _compute_multi_horizon_forecast,
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
from market_analyser.events import EventBus
from market_analyser.forecast import validation as validation_module
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
)
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.result import MultiHorizonForecastResult
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
    result (feature_set_id + empty series_inputs), never silent (ADR-0054)."""

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
