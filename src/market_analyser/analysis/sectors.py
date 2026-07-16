"""Crypto sector-rotation momentum engine (Plan 0102 phase 2, ADR-0097).

Ranks a config-defined crypto sector taxonomy (`sector_taxonomy.py`) by
equal-weighted constituent momentum — the classic "where is capital rotating" read,
synthesized in-house because crypto has no fetchable sector index (ADR-0097). Each
constituent's trailing return is read through the shared Plan 0100 `_scan_symbols`
fan-out harness (ADR-0095), so the cap, the `as_of` anti-lookahead truncation, and the
skip-discipline are inherited unchanged — one scan per sector (every basket is well
under the harness cap). A sector's momentum is the equal-weighted mean of its priced
constituents' returns; sectors rank by that momentum, complete ones ahead of incomplete
ones; each reports its leader/laggard constituents and any skipped names.

Pure, deterministic, and trailing: `score_trailing_return` reads only `bars[-1]` and the
bar `lookback` steps earlier (both at-or-before `as_of`), so a read at `as_of=T` is
identical to the same read over bars truncated to `T` (the anti-lookahead invariant
pinned by a truncation-invariance test). Conditions only — a rotation reading is a fact,
never a buy/sell call (ADR-0029).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from market_analyser.analysis.scanners import SCAN_SKIP, _scan_symbols, _ScanSkip
from market_analyser.analysis.sector_taxonomy import MIN_PRICED_TO_RANK, SectorTaxonomy
from market_analyser.analysis.types import ConstituentReturn, SectorMomentum
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar

# How many leader/laggard constituents a sector reports, at most, on each side. Kept
# small — a rotation read wants the names that drove the sector, not the full basket.
DEFAULT_TOP_N = 3


def score_trailing_return(bars: Sequence[Bar], lookback: int) -> ConstituentReturn | _ScanSkip:
    """Score one constituent's trailing close-to-close return over `lookback` bars.

    Reads only the latest close and the close `lookback` bars earlier — both trailing,
    so no future bar is involved. Returns `SCAN_SKIP` when there are too few bars for
    the window (`< lookback + 1`) or the base close is zero (no return defined off a
    zero base): scanned, uncomputable, routed into `skipped`, never a divide-by-zero.
    """

    if len(bars) < lookback + 1:
        return SCAN_SKIP
    base = bars[-1 - lookback].close
    if base == 0:
        return SCAN_SKIP
    return_pct = (bars[-1].close - base) / base * 100.0
    return ConstituentReturn(symbol=bars[-1].symbol, return_pct=return_pct)


def _summarise_sector(
    name: str,
    matches: list[ConstituentReturn],
    skipped: list[str],
    *,
    min_priced: int,
    top_n: int,
) -> SectorMomentum:
    """Fold one sector's priced constituent returns (already sorted descending by the
    harness) into its `SectorMomentum`. `momentum` is the equal-weight mean, ``None``
    when nothing priced; leaders/laggards are the top/bottom `k = min(top_n, n // 2)`
    constituents — the `// 2` guarantees the two lists are disjoint even for a tiny
    basket (they never share a name)."""

    n_priced = len(matches)
    momentum = (sum(m.return_pct for m in matches) / n_priced) if n_priced else None
    k = min(top_n, n_priced // 2)
    leaders = matches[:k]
    laggards = list(reversed(matches[-k:])) if k else []
    return SectorMomentum(
        sector=name,
        momentum=momentum,
        n_priced=n_priced,
        complete=n_priced >= min_priced,
        leaders=leaders,
        laggards=laggards,
        skipped=sorted(skipped),
    )


def _rank_key(sector: SectorMomentum) -> tuple[int, float, str]:
    """Sort key: complete sectors first, then momentum descending, then name (a stable
    tie-break). An incomplete sector sorts last regardless of momentum, and a sector
    with no read (`momentum is None`) sorts below any incomplete sector that does have
    one — so a confident read is never displaced by a thin or empty basket."""

    return (
        0 if sector.complete else 1,
        -(sector.momentum if sector.momentum is not None else float("-inf")),
        sector.sector,
    )


async def rank_sectors(
    *,
    provider: MarketDataProvider,
    taxonomy: SectorTaxonomy,
    timeframe: str,
    lookback: int,
    as_of: datetime | None,
    min_priced: int = MIN_PRICED_TO_RANK,
    top_n: int = DEFAULT_TOP_N,
) -> tuple[list[SectorMomentum], datetime]:
    """Rank a taxonomy's sectors by equal-weighted constituent momentum, hottest first.

    Runs one `_scan_symbols` fan-out per sector (each basket is under the harness cap),
    scoring every constituent's trailing `lookback`-bar return, then folds the priced
    returns into the sector's equal-weight `SectorMomentum`. Complete sectors rank ahead
    of incomplete ones; within each group, momentum descending. Returns
    `(sectors_sorted, scanned_at)`. Trailing/`as_of`-safe and deterministic — inherited
    from the harness and the pure scorer.
    """

    if lookback < 1:
        raise ValueError(f"lookback must be >= 1 (got {lookback})")

    def _score(bars: Sequence[Bar]) -> ConstituentReturn | _ScanSkip:
        return score_trailing_return(bars, lookback)

    sectors: list[SectorMomentum] = []
    for sector in taxonomy.sectors:
        matches, skipped, _ = await _scan_symbols(
            provider=provider,
            symbols=list(sector.constituents),
            timeframe=timeframe,
            score=_score,
            sort_key=lambda m: (-m.return_pct, m.symbol),
            as_of=as_of,
            tool_name="sector_rotation",
        )
        sectors.append(
            _summarise_sector(sector.name, matches, skipped, min_priced=min_priced, top_n=top_n)
        )

    sectors.sort(key=_rank_key)
    return sectors, datetime.now(tz=UTC)


__all__ = [
    "DEFAULT_TOP_N",
    "rank_sectors",
    "score_trailing_return",
]
