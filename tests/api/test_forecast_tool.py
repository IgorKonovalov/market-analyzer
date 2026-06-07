"""Phase-4 done-when (tool half) for Plan 0036: the `forecast` MCP tool.

Covered:
- the no-edge gate result ships null probabilities but keeps the validation basis
  and provenance (ADR-0030 invariant 3/4);
- an accepted result ships a valid probability distribution AND persists the model
  under the gitignored models/ root (ADR-0040 §3);
- the result is deterministic — re-running on the same bars + seed is byte-identical
  (no wall-clock field), with stable provenance;
- provenance carries the model_version + the prediction-affecting inputs, and its
  lib_versions are scikit-learn only (the prediction-affecting lib);
- the tool is wired into the live MCP server (registration assertion).

The accepted/no-edge *branches* are exercised by stubbing `validate` so the
gate state is deterministic; the gate's own correctness on real data is pinned by
`tests/forecast/test_validation.py`. The determinism + provenance tests run the
real pipeline end to end.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
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
from market_analyser.api.mcp_tools.forecast import _compute_forecast
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
from market_analyser.forecast.features import FEATURE_SET_ID
from market_analyser.forecast.validation import ForecastValidation
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
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


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
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
                    "horizon_bars": 1,
                },
            )
            assert not result.isError, f"forecast errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    assert payload["symbol"] == "SYN"
    assert payload["timeframe"] == "1d"
    assert payload["horizon_bars"] == 1
    assert isinstance(payload["validation"], dict)
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["model_version"]
    # prob_* keys are always present (null on a no-edge verdict).
    for key in ("prob_up", "prob_down", "prob_flat"):
        assert key in payload
