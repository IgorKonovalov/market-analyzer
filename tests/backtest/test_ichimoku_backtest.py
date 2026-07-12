"""Plan 0075 phase 2: backtest + walk-forward validation of `ichimoku`.

This exercises the flat/long/short engine (Plan 0053) end-to-end through the
`ichimoku` strategy — no engine change. Done-when coverage:

- A backtest on a fixture carrying BOTH a bullish and a bearish Ichimoku setup
  produces a deterministic `BacktestResult` (re-run byte-identical modulo run
  provenance — `run_id` / `started_at` / `finished_at`, per ADR-0018) that
  contains BOTH a `long` and a `short` `Trade`.
- A walk-forward run reports per-fold + aggregate metrics with contiguous,
  strictly-increasing folds (anti-lookahead, ADR-0024).

The fixture is a constructed close path — decline -> rally -> pullback -> second
rally — that makes a Tenkan/Kijun cross fire in each leg: a bullish TK cross on
the first rally, a bearish one on the pullback, a bullish one on the second
rally. Small Ichimoku periods (`conversion=2, base=3, span_b=4, displacement=2`)
keep the whole thing inside a 40-bar series; cloud confirmation is turned off so
the pullback's bearish cross opens a short (the classic cloud gate would suppress
it — this test is about the engine wiring the long/short trades, not about the
strategy's default filter). No edge is claimed: the path is synthetic, so the
returns are an artifact of the fixture, not a result about Ichimoku.

Trade directions are read off the engine's `BacktestResult.trades`, the single
source of truth for what executed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from market_analyser.backtest import BacktestResult, run, walk_forward
from market_analyser.data.types import Bar
from market_analyser.strategies import ichimoku

# Exclude run provenance per ADR-0018 (the documented determinism exceptions).
_PROVENANCE = {"run_id", "started_at", "finished_at"}

# decline (0-9) -> rally (10-19, bull cross) -> pullback (20-29, bear cross) ->
# second rally (30-39, bull cross). The three crosses drive a long, a short that
# stop-and-reverses out of it, and a final long.
_CLOSES: tuple[float, ...] = (
    100.0,
    98.0,
    96.0,
    94.0,
    92.0,
    90.0,
    88.0,
    86.0,
    84.0,
    82.0,
    84.0,
    88.0,
    94.0,
    100.0,
    106.0,
    112.0,
    118.0,
    124.0,
    130.0,
    136.0,
    134.0,
    130.0,
    124.0,
    118.0,
    112.0,
    106.0,
    100.0,
    96.0,
    92.0,
    88.0,
    90.0,
    96.0,
    104.0,
    112.0,
    120.0,
    128.0,
    134.0,
    140.0,
    144.0,
    148.0,
)


def _bars(closes: Sequence[float]) -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="ICHI",
            timeframe="1d",
            event_ts=start + timedelta(days=i),
            open=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=1000.0,
            source="synthetic",
        )
        for i, close in enumerate(closes)
    ]


def _fixture() -> list[Bar]:
    return _bars(_CLOSES)


def _params() -> ichimoku.Params:
    # Small periods so both setups land inside 40 bars; cloud gate off so the
    # bearish cross opens a short (see module docstring).
    return ichimoku.Params(
        conversion=2, base=3, span_b=4, displacement=2, require_cloud_confirmation=False
    )


def test_backtest_on_mixed_fixture_has_both_a_long_and_a_short_trade() -> None:
    bars = _fixture()
    result = run(ichimoku, bars, _params(), timeframe="1d")
    assert isinstance(result, BacktestResult)
    kinds = [t.kind for t in result.trades]
    assert "long" in kinds, f"expected a long trade; got {kinds}"
    assert "short" in kinds, f"expected a short trade; got {kinds}"
    # The long opens before the short (the bullish cross precedes the bearish one).
    first_long = next(t for t in result.trades if t.kind == "long")
    first_short = next(t for t in result.trades if t.kind == "short")
    assert first_long.entry_bar_index < first_short.entry_bar_index


def test_backtest_is_deterministic_modulo_run_provenance() -> None:
    bars = _fixture()
    a = run(ichimoku, bars, _params(), timeframe="1d", commission_bps=5.0, slippage_bps=5.0)
    b = run(ichimoku, bars, _params(), timeframe="1d", commission_bps=5.0, slippage_bps=5.0)
    dump_a = a.model_dump(mode="json", exclude=_PROVENANCE)
    dump_b = b.model_dump(mode="json", exclude=_PROVENANCE)
    assert dump_a == dump_b
    # The provenance fields are the only ones allowed to differ.
    assert a.run_id != b.run_id or a.started_at != b.started_at


def test_walk_forward_reports_per_fold_and_aggregate_metrics() -> None:
    bars = _fixture()
    wf = walk_forward(ichimoku, bars, _params(), timeframe="1d", n_splits=3)
    assert wf.strategy_id == "ichimoku"
    assert wf.n_splits == 3
    assert len(wf.folds) == 3
    assert [f.fold_index for f in wf.folds] == [0, 1, 2]
    # Per-fold metrics are present (a metric object per fold).
    for fold in wf.folds:
        assert fold.metrics is not None
        assert fold.trade_count >= 0
    # Aggregate carries mean + (multi-fold) std without error.
    assert wf.aggregate["total_return_mean"] is not None
    assert wf.aggregate["sharpe_mean"] is not None
    assert wf.aggregate["total_return_std"] is not None  # 3 folds -> std defined
    assert wf.aggregate["sharpe_std"] is not None
    # Folds are contiguous and strictly increasing in time (anti-lookahead).
    for prev, nxt in pairwise(wf.folds):
        assert nxt.range_start > prev.range_end


def test_walk_forward_is_deterministic() -> None:
    bars = _fixture()
    a = walk_forward(ichimoku, bars, _params(), timeframe="1d", n_splits=3)
    b = walk_forward(ichimoku, bars, _params(), timeframe="1d", n_splits=3)
    # WalkForwardResult stores no run_id / timestamps, so dumps are equal outright.
    assert a.model_dump() == b.model_dump()
