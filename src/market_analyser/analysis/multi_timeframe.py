"""Multi-timeframe trend alignment (Plan 0021 phase 1, ADR-0023).

`multi_timeframe_alignment(symbol, bars_by_timeframe)` runs the Plan 0018
`condition_snapshot` for one symbol on each supplied timeframe's bars, then
summarises whether the trend agrees across the ladder: per-timeframe snapshot,
the dominant trend, and an agreement score (the fraction of *available*
timeframes whose trend matches the dominant one).

Pure and trailing — it derives nothing of its own beyond what `condition_snapshot`
already computes, so the anti-lookahead property comes for free: each timeframe's
snapshot reads only `bars[0..=last]`, and the caller truncates the per-timeframe
series at `as_of` before handing them in (the provider does this via the cache).
A timeframe with no bars becomes a `None` snapshot — an honest gap, never a crash.

Reports **conditions only** — agreement is a fact about how timeframes line up,
never a buy/sell call.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import (
    MultiTimeframeAlignment,
    TimeframeView,
    Trend,
)
from market_analyser.data.types import Bar

# Deterministic tiebreak order when two trends tie on count: prefer up, then
# down, then sideways. Fixed sequence (no set iteration) keeps the dominant
# trend reproducible across runs.
_TIEBREAK_ORDER: tuple[Trend, ...] = (Trend.UP, Trend.DOWN, Trend.SIDEWAYS)


def _dominant_trend(trends: Sequence[Trend]) -> Trend:
    """The trend held by the most timeframes; `SIDEWAYS` when there are none.

    Ties are broken by `_TIEBREAK_ORDER` so the result is deterministic.
    """

    if not trends:
        return Trend.SIDEWAYS
    counts = Counter(trends)
    return max(_TIEBREAK_ORDER, key=lambda t: (counts[t], -_TIEBREAK_ORDER.index(t)))


def multi_timeframe_alignment(
    symbol: str,
    bars_by_timeframe: Mapping[str, Sequence[Bar]],
) -> MultiTimeframeAlignment:
    """Compose per-timeframe condition snapshots into a trend-alignment summary.

    `bars_by_timeframe` maps each timeframe to its (already-windowed, already-
    truncated) bars; iteration order is preserved in the result's `timeframes`
    list. A timeframe with empty bars yields a `None` snapshot and is excluded
    from the agreement computation.
    """

    views: list[TimeframeView] = []
    trends: list[Trend] = []
    for timeframe, bars in bars_by_timeframe.items():
        snapshot = condition_snapshot(list(bars), timeframe) if bars else None
        views.append(TimeframeView(timeframe=timeframe, snapshot=snapshot))
        if snapshot is not None:
            trends.append(snapshot.trend)

    dominant = _dominant_trend(trends)
    agreement = sum(1 for t in trends if t == dominant) / len(trends) if trends else 0.0
    return MultiTimeframeAlignment(
        symbol=symbol,
        timeframes=views,
        dominant_trend=dominant,
        agreement=agreement,
    )


__all__ = ["multi_timeframe_alignment"]
