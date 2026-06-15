"""`btc_cycle_snapshot` MCP tool — Plan 0055 phase 4; MVRV added by Plan 0057.

One call answering "where are we in the BTC cycle": the halving clock
(`days_since_halving`, estimated days to the next, phase fraction), Mayer
Multiple and 200-week-MA distance computed from cached daily BTC-USD bars
(`analysis/cycles.py` — pure, trailing math), the latest Fear & Greed and
BTC dominance from the metric store with 7/30-day deltas, plus on-chain MVRV
(market value / realized value) with its trailing full-history percentile — the
deep cost-basis lens alongside the price-only Mayer/200W reads (ADR-0053).

Trailing-only by construction: every store read goes through `as_of`/`range`
bounded at the snapshot's own `as_of` instant, so a point timestamped in the
future can never appear (ADR-0051's anti-lookahead read is the only join
primitive used). Insufficient history is an honest `None` — `mayer_multiple`
under 200 daily bars, `dist_200w_ma` under 1400, deltas while the dominance
accrual warms up — never a fabricated or silently-shortened value.

The bar read goes through the provider Protocol (`get_ohlcv`, live mode), so it
serves cached bars and fills gaps through the normal cache path (ADR-0007); the
cycle math itself adds no external source.

Offline by default (Plan 0057 phase 5): the snapshot reads only stored data.
Passing `refresh=true` first backfills/updates the `coinmetrics.btc.mvrv` series
from the wired MVRV source (the `derivatives_snapshot` refresh precedent) — the
one path that touches the network, and only when explicitly asked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.analysis.cycles import (
    NEXT_HALVING_DATE_EST,
    SMA_200W_DAYS,
    days_since_halving,
    days_to_next_halving_est,
    dist_200w_ma,
    halving_phase,
    mayer_multiple,
)
from market_analyser.data.metric_series import (
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_COINMETRICS_BTC_MVRV,
    SERIES_FNG_VALUE,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import MetricSeriesSource
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_SYMBOL = "BTC-USD"
_TIMEFRAME = "1d"

# Bar window: enough calendar days to hold the 1400 daily closes the 200W MA
# needs, with margin for the occasional missing day. Crypto trades every day,
# so 1500 calendar days comfortably covers 1400 bars.
_BARS_WINDOW_DAYS = SMA_200W_DAYS + 100

_DAY_SECONDS = 86_400

BTC_CYCLE_SNAPSHOT_DESCRIPTION = (
    "Get the current BTC cycle picture in one call: days since the 2024-04-19 "
    "halving, ESTIMATED days to the next (the next-halving date is an estimate, "
    "hence the _est suffix), the cycle phase fraction (0.0 just after a halving, "
    "1.0 at the estimated next), Mayer Multiple (close / 200-day SMA) and "
    "distance to the 200-week MA (close / SMA1400 - 1) from cached daily "
    "BTC-USD bars, plus the latest Fear & Greed and BTC dominance with 7/30-day "
    "deltas from the stored metric series, plus on-chain MVRV (market value / "
    "realized value) with its trailing full-history percentile. Reads are "
    "local-only by default; pass refresh=true to first backfill/update the MVRV "
    "series from the network, then read. Fields are null when history is "
    "insufficient (fewer than 200 / 1400 daily bars for the moving averages; "
    "the dominance series accrues from deployment, so its deltas stay null "
    "until it warms up; mvrv and mvrv_percentile are null until the MVRV series "
    "is backfilled). Conditions, not advice."
)


class BtcCycleSnapshotInput(BaseModel):
    """MCP-boundary input. BTC-only in v1; `extra="forbid"` rejects any
    argument an agent supplies by mistake. The lone field is the opt-in
    `refresh` flag — default-off keeps the snapshot an offline read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    refresh: bool = Field(
        default=False,
        description=(
            "When true, backfill/update the MVRV series from the network before "
            "reading; default is offline (local store only)"
        ),
    )


class BtcCycleSnapshot(BaseModel):
    """The snapshot payload (Plan 0055 data shape; Plan 0057 adds the MVRV
    fields). `*_est` fields are labeled estimates; `None` means insufficient
    history, never zero.

    Conditional fields (null until their backing series has data):
    `mayer_multiple` (< 200 daily bars), `dist_200w_ma` (< 1400 daily bars),
    the `*_delta_*` deltas (until the series spans the lookback), and
    `mvrv` / `mvrv_percentile` (until the `coinmetrics.btc.mvrv` series is
    backfilled)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    days_since_halving: int
    days_to_next_halving_est: int
    next_halving_date_est: str
    halving_phase: float
    mayer_multiple: float | None
    dist_200w_ma: float | None
    bars_available: int
    fng: float | None
    fng_delta_7d: float | None
    fng_delta_30d: float | None
    btc_dominance: float | None
    dominance_delta_7d: float | None
    dominance_delta_30d: float | None
    mvrv: float | None
    mvrv_percentile: float | None


def _latest_and_deltas(
    store: MetricPointsRepository,
    series_id: str,
    as_of_ts: int,
) -> tuple[float | None, float | None, float | None]:
    """The latest stored value at or before `as_of_ts` plus its 7d/30d deltas,
    each delta anchored on the latest point's own timestamp. `as_of` never
    returns a later point, so a future-timestamped row cannot leak in."""
    latest = store.as_of(series_id, as_of_ts)
    if latest is None:
        return None, None, None
    deltas: list[float | None] = []
    for days in (7, 30):
        earlier = store.as_of(series_id, latest.ts - days * _DAY_SECONDS)
        deltas.append(latest.value - earlier.value if earlier is not None else None)
    return latest.value, deltas[0], deltas[1]


def _mvrv_and_percentile(
    store: MetricPointsRepository,
    as_of_ts: int,
) -> tuple[float | None, float | None]:
    """The latest MVRV at or before `as_of_ts` and its trailing percentile rank
    over the full stored history up to that instant.

    The percentile is rank-inclusive (0-100): the share of stored observations
    with `ts <= as_of_ts` whose value is at or below the current MVRV — a
    cycle-position read (a fresh all-time high reads ~100, a deep trough reads
    near 0). Trailing-only by construction: both reads are bounded at `as_of_ts`
    (`as_of` never returns a later point; `range` stops at it), so a
    future-timestamped point cannot shift either field (ADR-0051 anti-lookahead).
    `None` when the series has no point at or before the bound."""
    latest = store.as_of(SERIES_COINMETRICS_BTC_MVRV, as_of_ts)
    if latest is None:
        return None, None
    history = store.range(SERIES_COINMETRICS_BTC_MVRV, 0, as_of_ts)
    at_or_below = sum(1 for point in history if point.value <= latest.value)
    return latest.value, 100.0 * at_or_below / len(history)


def _refresh_mvrv(
    source: MetricSeriesSource,
    store: MetricPointsRepository,
    as_of_ts: int,
) -> None:
    """The explicit network path (the `derivatives_snapshot` `_refresh`
    precedent): fetch MVRV from the latest stored point forward — the full
    2011-12-29→ history when the series is empty (first refresh doubles as the
    backfill), an incremental tail otherwise — and upsert. A re-fetched
    same-value point is a repository no-op; one that *changed* upstream raises
    `MetricPointConflictError` (ADR-0051 immutability), surfaced not absorbed."""
    latest = store.as_of(SERIES_COINMETRICS_BTC_MVRV, as_of_ts)
    fetched = source.fetch_series(
        SERIES_COINMETRICS_BTC_MVRV, start=latest.ts if latest is not None else None
    )
    if fetched:
        store.upsert_points(list(fetched))


def _build_snapshot(
    provider: MarketDataProvider,
    store: MetricPointsRepository,
    now: datetime,
) -> BtcCycleSnapshot:
    """Assemble the snapshot at instant `now` (passed in, not read here, so the
    trailing-only property is testable with a pinned clock)."""
    bars = provider.get_ohlcv(_SYMBOL, _TIMEFRAME, now - timedelta(days=_BARS_WINDOW_DAYS), now)
    closes = [bar.close for bar in bars]
    as_of_ts = int(now.timestamp())
    fng, fng_7d, fng_30d = _latest_and_deltas(store, SERIES_FNG_VALUE, as_of_ts)
    dom, dom_7d, dom_30d = _latest_and_deltas(store, SERIES_COINGECKO_BTC_DOMINANCE, as_of_ts)
    mvrv, mvrv_percentile = _mvrv_and_percentile(store, as_of_ts)
    today = now.date()
    return BtcCycleSnapshot(
        as_of=now,
        days_since_halving=days_since_halving(today),
        days_to_next_halving_est=days_to_next_halving_est(today),
        next_halving_date_est=NEXT_HALVING_DATE_EST.isoformat(),
        halving_phase=halving_phase(today),
        mayer_multiple=mayer_multiple(closes),
        dist_200w_ma=dist_200w_ma(closes),
        bars_available=len(closes),
        fng=fng,
        fng_delta_7d=fng_7d,
        fng_delta_30d=fng_30d,
        btc_dominance=dom,
        dominance_delta_7d=dom_7d,
        dominance_delta_30d=dom_30d,
        mvrv=mvrv,
        mvrv_percentile=mvrv_percentile,
    )


def register_btc_cycle_snapshot(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    metric_points_repository: MetricPointsRepository,
    mvrv_source: MetricSeriesSource | None = None,
) -> None:
    """Bind the `btc_cycle_snapshot` tool to `server`. Provider, store, and the
    optional MVRV source are captured by closure. The default path is offline;
    `refresh=true` touches the network only when `mvrv_source` is wired —
    unwired (the legacy/test default), `refresh=true` is a harmless no-op."""

    @server.tool(description=BTC_CYCLE_SNAPSHOT_DESCRIPTION)
    async def btc_cycle_snapshot(params: BtcCycleSnapshotInput) -> dict[str, Any]:
        def _run() -> BtcCycleSnapshot:
            now = datetime.now(tz=UTC)
            if params.refresh and mvrv_source is not None:
                _refresh_mvrv(mvrv_source, metric_points_repository, int(now.timestamp()))
            return _build_snapshot(provider, metric_points_repository, now)

        snapshot = await asyncio.to_thread(_run)
        return snapshot.model_dump(mode="json")


__all__ = [
    "BTC_CYCLE_SNAPSHOT_DESCRIPTION",
    "BtcCycleSnapshot",
    "BtcCycleSnapshotInput",
    "_build_snapshot",
    "register_btc_cycle_snapshot",
]
