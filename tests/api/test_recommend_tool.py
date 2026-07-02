"""Phase-2 done-when for Plan 0038: the `recommend` MCP tool.

Covered:
- an aligned set of live inputs yields a directional `Recommendation`
  explicitly labeled advisory and carrying all four basis components;
- conviction maps from the forecast probability + backtested edge — varying
  either moves it (not a constant);
- a no-edge forecast (and any other failed leg) yields the honest flat
  "no actionable edge" verdict, never a fabricated call;
- boundary validation (symbol / timeframe / strategy / params / knobs / bars);
- **no trade-permissioned secret, no order, no network write path** exists in
  the advisor package or this tool (a source-level assertion, per ADR-0029);
- registration lives in `tests/api/test_mcp_tools.py` alongside the other
  toolset assertions.

The directional/flat branches are exercised by stubbing the three expensive
legs (live signal, walk-forward, forecast) at the module seams — their own
correctness is pinned by `tests/backtest/` and `tests/forecast/`; the
condition snapshot runs for real over synthetic bars. The fusion logic itself
is pinned by `tests/advisor/test_fusion.py`.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

import market_analyser.advisor
from market_analyser.api.mcp_tools import recommend as recommend_tool
from market_analyser.api.mcp_tools.forecast import (
    EdgeStrength,
    ForecastProvenance,
    ForecastResult,
)
from market_analyser.api.mcp_tools.recommend import (
    RECOMMEND_DESCRIPTION,
    _recommend_response,
)
from market_analyser.backtest.result import BacktestMetrics
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.data.types import Bar
from market_analyser.forecast.validation import ForecastValidation
from tests.forecast._synthetic import synthetic_bars

BARS = synthetic_bars(220)
# The synthetic series is daily from 2025-01-01; a NOW far past the last bar
# keeps every bar closed, so the closed-bar filter is the identity here.
NOW = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_START = datetime(2025, 1, 1, tzinfo=UTC)
LAST_TS = BARS[-1].event_ts


class _StubProvider:
    """Returns a fixed bar list on get_ohlcv; everything else is unused."""

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[Bar]:
        return self._bars


def _signal_evaluation(
    position: Literal["flat", "long", "short"],
) -> SignalEvaluation:
    return SignalEvaluation(
        strategy_id="rsi",
        symbol="SYN",
        timeframe="1d",
        evaluated_through_ts=LAST_TS,
        closed_bar_count=len(BARS),
        latest_bar_excluded_as_forming=False,
        current_position=position,
        fresh_signal=position != "flat",
    )


def _walk_forward_result(sharpe_mean: float) -> WalkForwardResult:
    metrics = BacktestMetrics(
        total_return=0.2,
        sharpe=1.1,
        max_drawdown=-0.1,
        max_drawdown_duration_bars=12,
        win_rate=0.6,
        trade_count=20,
        buy_and_hold_return=0.15,
    )
    return WalkForwardResult(
        strategy_id="rsi",
        symbol="SYN",
        timeframe="1d",
        n_splits=5,
        folds=[],
        aggregate={
            "total_return_mean": 0.04,
            "total_return_std": 0.01,
            "sharpe_mean": sharpe_mean,
            "sharpe_std": 0.2,
        },
        full_run_baseline=metrics,
    )


def _forecast_result(
    *,
    prob_up: float | None,
    prob_down: float | None,
    prob_flat: float | None,
    beats_baseline: bool,
    edge_strength: EdgeStrength,
) -> ForecastResult:
    return ForecastResult(
        symbol="SYN",
        timeframe="1d",
        as_of_bar_ts=LAST_TS,
        horizon_bars=1,
        prob_up=prob_up,
        prob_down=prob_down,
        prob_flat=prob_flat,
        validation=ForecastValidation(
            horizon_bars=1,
            n_splits=5,
            n_scored=120,
            skill=0.58 if beats_baseline else 0.35,
            baseline_skill=0.50,
            persistence_skill=0.50,
            majority_skill=0.44,
            beats_baseline=beats_baseline,
            folds=[],
        ),
        provenance=ForecastProvenance(
            model_version="deadbeef",
            feature_set_id="fs-v1",
            training_cutoff=LAST_TS,
            seed=1729,
            lib_versions={"scikit-learn": "1.8.0"},
        ),
        edge_margin=0.08 if beats_baseline else -0.15,
        edge_strength=edge_strength,
    )


def _patch_legs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    position: Literal["flat", "long", "short"] = "long",
    sharpe_mean: float = 0.8,
    prob_up: float | None = 0.60,
    prob_down: float | None = 0.25,
    prob_flat: float | None = 0.15,
    beats_baseline: bool = True,
    edge_strength: EdgeStrength = "clear",
) -> None:
    """Stub the three expensive legs at the module seams; the condition
    snapshot stays real."""

    monkeypatch.setattr(
        recommend_tool,
        "evaluate_signals_core",
        lambda *a, **kw: _signal_evaluation(position),
    )
    monkeypatch.setattr(
        recommend_tool,
        "walk_forward",
        lambda *a, **kw: _walk_forward_result(sharpe_mean),
    )
    monkeypatch.setattr(
        recommend_tool,
        "_compute_forecast",
        lambda **kw: _forecast_result(
            prob_up=prob_up,
            prob_down=prob_down,
            prob_flat=prob_flat,
            beats_baseline=beats_baseline,
            edge_strength=edge_strength,
        ),
    )


def _run(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "provider": _StubProvider(list(BARS)),
        "coordinator": None,
        "models_dir": None,
        "strategy_id": "rsi",
        "symbol": "SYN",
        "timeframe": "1d",
        "range_start": RANGE_START,
        "params": None,
        "horizon_bars": 1,
        "flat_band": 0.001,
        "n_splits": 5,
        "seed": 1729,
        "now": NOW,
    }
    kwargs.update(overrides)
    return asyncio.run(_recommend_response(**kwargs))


class TestAdvisoryOutput:
    def test_aligned_inputs_return_labeled_directional_recommendation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch)
        rec = _run()
        assert rec.label == "advisory"
        assert rec.direction == "long"
        assert rec.symbol == "SYN" and rec.timeframe == "1d"
        # All four basis components travel with the call (phase-2 done-when).
        assert rec.basis.conditions  # ADR-0023 snapshot facts (computed for real)
        assert rec.basis.signals  # Plan 0026 live signal
        assert rec.basis.backtest is not None  # ADR-0024 walk-forward edge
        assert rec.basis.forecast is not None  # Plan 0036 forecast
        assert rec.rationale
        assert rec.entry_zone is not None and rec.stop is not None and rec.targets
        assert rec.as_of_bar_ts == LAST_TS  # the shared as-of bar

    def test_conviction_moves_with_forecast_probability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch, prob_up=0.52, prob_down=0.33)
        low = _run().conviction
        _patch_legs(monkeypatch, prob_up=0.72, prob_down=0.13)
        high = _run().conviction
        assert high > low  # not a constant — maps from the forecast probability

    def test_conviction_moves_with_backtested_edge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_legs(monkeypatch, sharpe_mean=0.3)
        thin = _run().conviction
        _patch_legs(monkeypatch, sharpe_mean=0.9)
        strong = _run().conviction
        assert strong > thin  # maps from the walk-forward edge


class TestHonestFlat:
    def test_no_edge_forecast_yields_flat_no_actionable_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(
            monkeypatch,
            prob_up=None,
            prob_down=None,
            prob_flat=None,
            beats_baseline=False,
            edge_strength="no_edge",
        )
        rec = _run()
        assert rec.direction == "flat"
        assert rec.label == "advisory"
        assert rec.conviction == 0.0
        assert rec.entry_zone is None and rec.stop is None and rec.targets == []
        assert rec.rationale[0] == "no actionable edge"
        # The basis still travels — an honest flat is not a bare shrug.
        assert rec.basis.forecast is not None
        assert rec.basis.forecast["beats_baseline"] is False

    def test_signal_conflicting_with_forecast_yields_flat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_legs(monkeypatch, position="short")  # forecast says long
        rec = _run()
        assert rec.direction == "flat"
        assert any("disagree" in line for line in rec.rationale)


class TestBoundaryValidation:
    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown strategy_id"):
            _run(strategy_id="nope")

    def test_timeframe_unsupported_by_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="not supported by strategy"):
            _run(timeframe="15m")  # rsi supports 1h/1d

    def test_empty_symbol_raises(self) -> None:
        with pytest.raises(ValueError, match="symbol"):
            _run(symbol="")

    def test_unknown_param_key_rejected_at_boundary(self) -> None:
        with pytest.raises(Exception, match=r"extra_forbidden|unexpected"):
            _run(params={"not_a_param": 1})

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("horizon_bars", 0, "horizon_bars"),
            ("n_splits", 1, "n_splits"),
            ("flat_band", -0.1, "flat_band"),
        ],
    )
    def test_bad_knobs_raise(self, field: str, value: float, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            _run(**{field: value})

    def test_no_bars_raises_with_backfill_hint(self) -> None:
        with pytest.raises(ValueError, match="backfill via get_ohlcv"):
            _run(provider=_StubProvider([]))

    def test_all_forming_bars_raise(self) -> None:
        with pytest.raises(ValueError, match="no closed bars"):
            _run(now=BARS[0].event_ts + timedelta(hours=1))


def test_description_labels_the_tool_advisory() -> None:
    assert "ADVISORY" in RECOMMEND_DESCRIPTION
    assert "no order" in RECOMMEND_DESCRIPTION


def test_advisor_holds_no_key_and_no_order_path() -> None:
    """Phase-2 done-when (ADR-0029 / ADR-0025 boundary): no trade-permissioned
    secret, no order placement, and no network write path exist anywhere in
    the advisor package or the `recommend` tool. Source-level, so a future
    'just submit it' accretion fails here before it ships."""

    package_file = market_analyser.advisor.__file__
    assert package_file is not None
    sources = sorted(Path(package_file).parent.glob("*.py"))
    sources.append(Path(recommend_tool.__file__))

    forbidden_tokens = (
        "place_order",
        "create_order",
        "new_order",
        "submit_order",
        "x-mbx-apikey",
        "hmac",
        "api_key",
        "apikey",
        "trade_key",
        "private_key",
    )
    forbidden_imports = (
        "httpx",
        "requests",
        "urllib",
        "market_analyser.data._http",
        "market_analyser.persistence.secrets",
    )

    for source in sources:
        text = source.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in forbidden_tokens:
            assert token not in lowered, f"{source.name} contains forbidden token {token!r}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                for banned in forbidden_imports:
                    assert not name.startswith(banned), (
                        f"{source.name} imports {name!r} — the advisor surface "
                        "must not reach the network or any secret store"
                    )
