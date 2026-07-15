"""Shared watchlist-scan fan-out harness + condition scorers (Plan 0100, ADR-0095).

Every multi-symbol watchlist scanner needs the same safety-critical loop: validate
a capped, non-empty symbol list, read cached bars per symbol through the
`MarketDataProvider` Protocol (ADR-0007), honour `as_of` so the window truncates to
`event_ts <= as_of` (the anti-lookahead guarantee), offload each read with
`asyncio.to_thread`, skip empty/errored symbols into a `skipped` list, sort the
matches deterministically, and stamp `scanned_at`. Copying that loop per scanner is
exactly where the cap, the anti-lookahead contract, and the skip discipline
silently drift apart (ADR-0095) — so `_scan_symbols` owns it once, parameterised by
a pure per-symbol scoring callable and a sort key.

A scorer maps one symbol's trailing bars to one of three outcomes: a typed match, a
`SCAN_SKIP` sentinel (scanned but uncomputable — e.g. too few bars for a trailing
percentile — routed into `skipped`), or ``None`` (computed fine, simply not a match
— dropped). The three condition scorers live here beside the harness; each MCP tool
supplies only its scorer + sort key and wraps the result in its own frozen response.

Conditions only — every match model is chart geometry / a condition fact, never a
buy/sell call (the analyst non-negotiable, ADR-0029).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.annotations.types import SUPPORTED_TIMEFRAMES
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history
from market_analyser.data.types import Bar

logger = logging.getLogger(__name__)

# Bar fan-out bound: one cached read per symbol, so cap the list (Plan 0021 risk
# mitigation, promoted to a single shared constant by ADR-0095).
MAX_SCAN_SYMBOLS: Final = 25

# Fetch window per symbol: the timeframe's feed-limited history, or a generous
# default for the unbounded cadences — wide enough for any scorer's trailing
# windows (percentiles, relative volume, RSI).
_DEFAULT_WINDOW: Final = timedelta(days=5 * 365)


class _ScanSkip:
    """Sentinel a scorer returns to route a *scanned-but-uncomputable* symbol
    (too few bars for a trailing percentile, a single bar with no prior close)
    into `skipped` — distinct from returning ``None`` (scanned and computed,
    simply not a match, so dropped) and from a fetch error (owned by the
    harness). One skip vocabulary, decided by the scorer, applied in one place."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "SCAN_SKIP"


SCAN_SKIP: Final = _ScanSkip()


def _require_supported_timeframe(timeframe: str) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"timeframe {timeframe!r} not supported (supported: {sorted(SUPPORTED_TIMEFRAMES)})",
        )


def _require_scan_list(symbols: list[str]) -> None:
    if not symbols:
        raise ValueError("symbols must be a non-empty list")
    if len(symbols) > MAX_SCAN_SYMBOLS:
        raise ValueError(f"symbols list of {len(symbols)} exceeds the cap of {MAX_SCAN_SYMBOLS}")
    for symbol in symbols:
        if not symbol:
            raise ValueError("symbol must be a non-empty string")


async def _scan_symbols[MatchT: BaseModel](
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    score: Callable[[Sequence[Bar]], MatchT | _ScanSkip | None],
    sort_key: Callable[[MatchT], Any],
    as_of: datetime | None,
    tool_name: str,
) -> tuple[list[MatchT], list[str], datetime]:
    """The shared watchlist-scan fan-out (ADR-0095).

    Validates the symbol list (cap + non-empty) and the timeframe, then reads
    cached bars per symbol through the provider — the read is offloaded with
    `asyncio.to_thread` so a slow fetch cannot stall the loop, and `as_of` is
    passed through so the provider truncates to `event_ts <= as_of` (the
    anti-lookahead guarantee). A symbol with no bars or a fetch error is skipped
    into `skipped`; it never fails the whole scan. Each surviving symbol's bars
    go to `score`, which returns a typed match (kept), `SCAN_SKIP` (routed into
    `skipped`), or ``None`` (dropped — computed, not a match). Matches are sorted
    by `sort_key` and returned with the `scanned_at` provenance stamp.

    Returns `(matches, skipped, scanned_at)`; each tool wraps that in its own
    frozen response model.
    """

    _require_scan_list(symbols)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)

    matches: list[MatchT] = []
    skipped: list[str] = []
    for symbol in symbols:
        try:
            bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
        except Exception:  # one bad symbol must not fail the whole scan
            logger.warning("%s: fetch failed for %s %s; skipping", tool_name, symbol, timeframe)
            skipped.append(symbol)
            continue
        if not bars:
            skipped.append(symbol)
            continue
        outcome = score(list(bars))
        if isinstance(outcome, _ScanSkip):
            skipped.append(symbol)
            continue
        if outcome is None:
            continue
        matches.append(outcome)

    matches.sort(key=sort_key)
    return matches, skipped, datetime.now(tz=UTC)


# --------------------------------------------------------------------------- #
# squeeze scorer (Plan 0100 phase 1)                                            #
# --------------------------------------------------------------------------- #


class SqueezeScanMatch(BaseModel):
    """One symbol's squeeze reading in a `squeeze_scan` result (Plan 0100, ADR-0083).

    The ADR-0083 squeeze trio, read from the symbol's trailing condition snapshot
    (the single definition of the trio, so the scan can never drift from
    `analyze_symbol`): `bb_width` is the latest Bollinger band-width — the canonical
    compression metric — `bb_width_pct90` its trailing 90-window percentile rank
    (*lower = tighter coil*, the field the scan ranks ascending), and `squeeze_on`
    the TTM Bollinger-inside-Keltner flag on the latest bar. Conditions only — a
    squeeze is chart geometry, never a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    bb_width: float
    bb_width_pct90: float  # lower = tighter coil; the scan ranks ascending
    squeeze_on: bool


def score_squeeze(bars: Sequence[Bar], timeframe: str) -> SqueezeScanMatch | _ScanSkip:
    """Score one symbol's squeeze from its trailing condition snapshot.

    Reuses `condition_snapshot` so the trio is defined in exactly one place
    (ADR-0083) — the scan and `analyze_symbol` can never disagree. Returns
    `SCAN_SKIP` when any leg of the trio is undefined over the available bars (a
    short history yields a `None` percentile): scanned, uncomputable, reported in
    `skipped` — never a crash, never a match on a partial trio."""

    snapshot = condition_snapshot(bars, timeframe)
    indicators = snapshot.indicators
    bb_width = indicators.get("bb_width")
    bb_width_pct90 = indicators.get("bb_width_pct90")
    squeeze_on = indicators.get("squeeze_on")
    if bb_width is None or bb_width_pct90 is None or squeeze_on is None:
        return SCAN_SKIP
    return SqueezeScanMatch(
        symbol=snapshot.symbol,
        bb_width=bb_width,
        bb_width_pct90=bb_width_pct90,
        squeeze_on=bool(squeeze_on),
    )


# --------------------------------------------------------------------------- #
# return scorer (Plan 0100 phase 2)                                             #
# --------------------------------------------------------------------------- #


class GainersLosersMatch(BaseModel):
    """One symbol's move in a `gainers_losers` result (Plan 0100).

    `change_pct` is the signed close-to-close percentage change over one timeframe
    window — the latest bar's close against the immediately-prior bar's close (a
    +5.0 is a 5% gain, a -3.0 a 3% loss). `direction` is the coarse sign: ``up``
    when the change is non-negative, ``down`` when negative. Conditions only — a
    raw move is a fact, never a buy/sell call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    change_pct: float  # signed, latest close vs the prior close, in percent
    direction: Literal["up", "down"]


def score_return(bars: Sequence[Bar]) -> GainersLosersMatch | _ScanSkip:
    """Score one symbol's trailing close-to-close move over one timeframe window.

    Returns `SCAN_SKIP` when there is no prior close to measure against — a single
    bar, or a zero prior close (no defined return off a zero base): scanned,
    uncomputable, reported in `skipped`, never a divide-by-zero. Trailing by
    construction — reads only the last two bars (both at-or-before the scan's
    `as_of`), so no future bar is involved."""

    if len(bars) < 2:
        return SCAN_SKIP
    prior_close = bars[-2].close
    if prior_close == 0:
        return SCAN_SKIP
    change_pct = (bars[-1].close - prior_close) / prior_close * 100.0
    direction: Literal["up", "down"] = "up" if change_pct >= 0 else "down"
    return GainersLosersMatch(symbol=bars[-1].symbol, change_pct=change_pct, direction=direction)


__all__ = [
    "MAX_SCAN_SYMBOLS",
    "SCAN_SKIP",
    "GainersLosersMatch",
    "SqueezeScanMatch",
    "_require_scan_list",
    "_require_supported_timeframe",
    "_scan_symbols",
    "score_return",
    "score_squeeze",
]
