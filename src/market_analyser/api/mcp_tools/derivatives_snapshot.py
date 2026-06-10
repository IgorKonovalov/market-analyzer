"""`derivatives_snapshot` MCP tool — Plan 0056 phase 4.

One call answering "how is <SYMBOL> positioned on Binance USDⓈ-M": the current
funding rate with the estimated next funding instant, the trailing-7-day
funding mean, and the latest open interest with 24h/7d deltas — all read from
the ADR-0051 metric store via its two read shapes (`as_of` for the latest /
delta anchors, `range` for the mean window), bounded at the snapshot's own
`as_of` instant so a future-timestamped point can never leak in.

**Offline by default.** The tool reads only accrued/backfilled local data;
only an explicit `refresh=true` touches the network, and then it does exactly
two things before reading: fetch funding prints from the latest stored one
forward (full backfill when the series is empty) and accrue one open-interest
snapshot through the phase-3 write-through path.

Funding cadence comes from the points' actual spacing — the gap between the
two latest stored prints — never a hardcoded 8h (Plan 0056 risk note: some
contracts fund every 4h). Warm-up honesty: every field is `None` (never zero)
while its series lacks the points to compute it — no funding print yet, no
print before the delta anchor, an empty 7d window.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.metric_series import (
    MetricPoint,
    is_registered,
    registered_series,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_FUNDING_SERIES_PREFIX = "binance.funding_rate."
_OI_SERIES_PREFIX = "binance.open_interest."

_DAY_SECONDS = 86_400
_WEEK_SECONDS = 7 * 86_400


class DerivativesSource(Protocol):
    """The slice of the Binance derivatives adapter the `refresh=true` path
    needs: incremental funding fetch (`MetricSeriesSource` shape, ADR-0051)
    plus one open-interest accrual sample. The default-path snapshot never
    calls either — offline reads are the contract, asserted by a spy in the
    tool's tests."""

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> Sequence[MetricPoint]: ...

    def accrue_open_interest(self, series_id: str) -> int: ...


DERIVATIVES_SNAPSHOT_DESCRIPTION = (
    "Get the Binance USDS-M derivatives picture for one contract symbol "
    "(e.g. BTCUSDT, ETHUSDT) from the locally stored metric series: the "
    "latest funding rate (decimal per funding interval, e.g. 0.0001 = 1bp) "
    "with next_funding_ts estimated from the actual spacing of the stored "
    "prints (not an assumed 8h), the mean funding rate over the trailing 7 "
    "days, and the latest open interest (base-asset units) with its 24h and "
    "7d deltas. Reads are local-only by default; pass refresh=true to first "
    "fetch new funding prints and accrue one open-interest sample from the "
    "network. Fields are null when the stored series cannot support them "
    "(no funding print yet, the open-interest accrual still warming up, no "
    "point at the delta anchor) — null means insufficient history, never "
    "zero. Conditions, not advice."
)


class DerivativesSnapshotInput(BaseModel):
    """MCP-boundary input. The symbol must name a registered Binance
    derivatives series pair; `extra="forbid"` rejects stray arguments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(
        min_length=1,
        description="Binance USDS-M contract symbol, e.g. BTCUSDT",
    )
    refresh: bool = Field(
        default=False,
        description=(
            "When true, fetch new funding prints and accrue one open-interest "
            "sample before reading; default is offline (local store only)"
        ),
    )


class DerivativesSnapshot(BaseModel):
    """The snapshot payload (Plan 0056 data shape). `None` means the stored
    series cannot support the field yet, never zero."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    as_of: datetime
    funding_rate: float | None
    next_funding_ts: int | None
    funding_mean_7d: float | None
    open_interest: float | None
    oi_delta_24h: float | None
    oi_delta_7d: float | None


def _series_ids(symbol: str) -> tuple[str, str]:
    """Map a contract symbol to its `(funding, open-interest)` series ids,
    rejecting symbols the registry does not carry (the registry is the schema
    — ADR-0051); the error lists what is available."""
    funding_id = f"{_FUNDING_SERIES_PREFIX}{symbol}"
    oi_id = f"{_OI_SERIES_PREFIX}{symbol}"
    if not (is_registered(funding_id) and is_registered(oi_id)):
        available = sorted(
            series.removeprefix(_FUNDING_SERIES_PREFIX)
            for series in registered_series()
            if series.startswith(_FUNDING_SERIES_PREFIX)
        )
        raise ValueError(
            f"no registered Binance derivatives series for symbol {symbol!r}; "
            f"available: {', '.join(available) or '(none)'}",
        )
    return funding_id, oi_id


def _build_snapshot(
    store: MetricPointsRepository,
    symbol: str,
    now: datetime,
) -> DerivativesSnapshot:
    """Assemble the snapshot at instant `now` (passed in, not read here, so
    the trailing-only property is testable with a pinned clock). Every store
    read is bounded at `now` through `as_of`/`range` — the anti-lookahead
    primitives are the only join used (ADR-0051)."""
    funding_id, oi_id = _series_ids(symbol)
    as_of_ts = int(now.timestamp())

    funding_latest = store.as_of(funding_id, as_of_ts)
    funding_rate = funding_latest.value if funding_latest is not None else None
    next_funding_ts: int | None = None
    if funding_latest is not None:
        previous = store.as_of(funding_id, funding_latest.ts - 1)
        if previous is not None:
            # Cadence read from the data: the spacing of the two latest prints
            # (8h for majors, 4h for some alts) — never assumed.
            next_funding_ts = funding_latest.ts + (funding_latest.ts - previous.ts)

    window = store.range(funding_id, max(as_of_ts - _WEEK_SECONDS, 0), as_of_ts)
    funding_mean_7d = sum(point.value for point in window) / len(window) if window else None

    oi_latest = store.as_of(oi_id, as_of_ts)
    open_interest = oi_latest.value if oi_latest is not None else None
    oi_deltas: list[float | None] = []
    for delta_seconds in (_DAY_SECONDS, _WEEK_SECONDS):
        if oi_latest is None:
            oi_deltas.append(None)
            continue
        # Anchored on the latest point's own timestamp (the cycle-snapshot
        # delta convention): `as_of` never returns a later point, and a
        # missing anchor is an honest None, never zero.
        earlier = store.as_of(oi_id, oi_latest.ts - delta_seconds)
        oi_deltas.append(oi_latest.value - earlier.value if earlier is not None else None)

    return DerivativesSnapshot(
        symbol=symbol,
        as_of=now,
        funding_rate=funding_rate,
        next_funding_ts=next_funding_ts,
        funding_mean_7d=funding_mean_7d,
        open_interest=open_interest,
        oi_delta_24h=oi_deltas[0],
        oi_delta_7d=oi_deltas[1],
    )


def _refresh(
    source: DerivativesSource,
    store: MetricPointsRepository,
    funding_id: str,
    oi_id: str,
    as_of_ts: int,
) -> None:
    """The explicit network path: funding prints from the latest stored one
    forward (the full history when the series is empty — first refresh doubles
    as the backfill), then one open-interest accrual sample. A re-fetched
    latest print with the same value is a repository no-op; one that *changed*
    upstream raises `MetricPointConflictError` (ADR-0051 immutability) —
    surfaced, not absorbed."""
    latest = store.as_of(funding_id, as_of_ts)
    fetched = source.fetch_series(funding_id, start=latest.ts if latest is not None else None)
    if fetched:
        store.upsert_points(list(fetched))
    source.accrue_open_interest(oi_id)


def register_derivatives_snapshot(
    server: FastMCP,
    *,
    metric_points_repository: MetricPointsRepository,
    derivatives_source: DerivativesSource,
) -> None:
    """Bind the `derivatives_snapshot` tool to `server`. Store and source are
    captured by closure; the source is touched only on `refresh=true`."""

    @server.tool(description=DERIVATIVES_SNAPSHOT_DESCRIPTION)
    async def derivatives_snapshot(params: DerivativesSnapshotInput) -> dict[str, Any]:
        def _run() -> DerivativesSnapshot:
            now = datetime.now(tz=UTC)
            if params.refresh:
                funding_id, oi_id = _series_ids(params.symbol)
                _refresh(
                    derivatives_source,
                    metric_points_repository,
                    funding_id,
                    oi_id,
                    int(now.timestamp()),
                )
            return _build_snapshot(metric_points_repository, params.symbol, now)

        snapshot = await asyncio.to_thread(_run)
        return snapshot.model_dump(mode="json")


__all__ = [
    "DERIVATIVES_SNAPSHOT_DESCRIPTION",
    "DerivativesSnapshot",
    "DerivativesSnapshotInput",
    "DerivativesSource",
    "_build_snapshot",
    "register_derivatives_snapshot",
]
