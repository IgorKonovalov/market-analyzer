"""Plan 0054 phase 2: backtest + walk-forward validation of `chart_pattern_breakout`.

This exercises the Plan 0053 flat/long/short engine end-to-end through the
`chart_pattern_breakout` strategy (no engine change). Done-when coverage:

- A backtest on a fixture carrying BOTH a confirmed bullish and a confirmed
  bearish pattern produces a deterministic `BacktestResult` (re-run
  byte-identical modulo run provenance — `run_id` / `started_at` / `finished_at`,
  per ADR-0018) that contains BOTH a `long` and a `short` `Trade`.
- A walk-forward run reports per-fold + aggregate metrics without error.

The fixtures reuse the exact constructed paths the detector's own test pins
(`tests/analysis/test_chart_patterns.py`), so the geometry is known-good; the
mixed fixture stitches a bullish inverse-H&S into a bearish H&S so both
directions confirm in one series. Trade directions are read off the engine's
`BacktestResult.trades`, the single source of truth for what executed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from market_analyser.backtest import BacktestResult, run, walk_forward
from market_analyser.data.types import Bar
from market_analyser.strategies import chart_pattern_breakout as cpb

# Exclude run provenance per ADR-0018 (the documented determinism exceptions).
_PROVENANCE = {"run_id", "started_at", "finished_at"}


def _bars_from_path(anchors: list[tuple[int, float]], *, symbol: str = "MIX") -> list[Bar]:
    """The detector test's piecewise-linear fixture builder: high/low straddle
    the base by 1.0, open/close on it, so the only swing pivots are the interior
    anchor extremes."""

    n = anchors[-1][0] + 1
    bases: list[float] = []
    for i in range(n):
        for (x1, p1), (x2, p2) in pairwise(anchors):
            if x1 <= i <= x2:
                bases.append(p1 + (p2 - p1) * (i - x1) / (x2 - x1))
                break
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
            open=base,
            high=base + 1.0,
            low=base - 1.0,
            close=base,
            volume=1000.0,
            source="synthetic",
        )
        for i, base in enumerate(bases)
    ]


# A bullish inverse-H&S (breaks UP near bar 35) flowing into a bearish H&S
# (breaks DOWN near the tail). Both halves reuse the detector-verified geometry:
# the inverse-H&S is the `_INVERSE_HS_ANCHORS` shape; the H&S is the `_HS_ANCHORS`
# shape, shifted to sit on the ~120 plateau the inverse-H&S breakout reaches and
# offset in bar-index so its pivots are distinct. The long fills off the upside
# break, the short off the downside break, in one series.
_MIXED_ANCHORS = [
    # --- inverse head & shoulders (bullish) ---
    (0, 120.0),
    (6, 110.0),
    (10, 120.0),
    (14, 100.0),
    (18, 119.0),
    (22, 109.5),
    (35, 142.0),  # upside breakout -> confirmed bullish
    # --- head & shoulders (bearish) on the higher plateau ---
    (42, 132.0),  # left shoulder peak (high = 133)
    (46, 122.0),  # trough
    (50, 142.0),  # head peak (high = 143), strictly above both shoulders
    (54, 123.0),  # trough
    (58, 132.5),  # right shoulder peak (high = 133.5), within symmetry tol
    (72, 100.0),  # downside breakout -> confirmed bearish
]


def _mixed_bars() -> list[Bar]:
    return _bars_from_path(_MIXED_ANCHORS)


def test_backtest_on_mixed_fixture_has_both_a_long_and_a_short_trade() -> None:
    bars = _mixed_bars()
    result = run(cpb, bars, cpb.Params(), timeframe="1d")
    assert isinstance(result, BacktestResult)
    kinds = [t.kind for t in result.trades]
    assert "long" in kinds, f"expected a long trade; got {kinds}"
    assert "short" in kinds, f"expected a short trade; got {kinds}"
    # The long opens before the short (the bullish break precedes the bearish one).
    first_long = next(t for t in result.trades if t.kind == "long")
    first_short = next(t for t in result.trades if t.kind == "short")
    assert first_long.entry_bar_index < first_short.entry_bar_index


def test_backtest_is_deterministic_modulo_run_provenance() -> None:
    bars = _mixed_bars()
    a = run(cpb, bars, cpb.Params(), timeframe="1d", commission_bps=5.0, slippage_bps=5.0)
    b = run(cpb, bars, cpb.Params(), timeframe="1d", commission_bps=5.0, slippage_bps=5.0)
    dump_a = a.model_dump(mode="json", exclude=_PROVENANCE)
    dump_b = b.model_dump(mode="json", exclude=_PROVENANCE)
    assert dump_a == dump_b
    # The provenance fields are the *only* ones allowed to differ.
    assert a.run_id != b.run_id or a.started_at != b.started_at  # at minimum run_id varies


def test_walk_forward_reports_per_fold_and_aggregate_metrics() -> None:
    bars = _mixed_bars()
    wf = walk_forward(cpb, bars, cpb.Params(), timeframe="1d", n_splits=3)
    assert wf.strategy_id == "chart_pattern_breakout"
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
    bars = _mixed_bars()
    a = walk_forward(cpb, bars, cpb.Params(), timeframe="1d", n_splits=3)
    b = walk_forward(cpb, bars, cpb.Params(), timeframe="1d", n_splits=3)
    # WalkForwardResult stores no run_id / timestamps, so dumps are equal outright.
    assert a.model_dump() == b.model_dump()
