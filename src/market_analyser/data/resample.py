"""In-house OHLCV resampler (Plan 0025 phase 2 / ADR-0028).

Yahoo serves `1h` natively but has no `4h` interval, so `4h` bars are aggregated
in-house from native `1h` bars. The aggregation is **trailing by construction**
(an output bar reads only the base bars inside its own closed window, never
future bars), so it carries no lookahead — the project-wide non-negotiable for
any financially-meaningful path.

The grid is **fixed and UTC-aligned**: 4h buckets start at `00:00, 04:00, 08:00,
12:00, 16:00, 20:00 UTC`. This does not match any single exchange session — a
deliberate trade of session-exactness for determinism and venue-independence,
since we have no exchange-calendar data (ADR-0028). Bars are timestamped at their
bucket open, consistent with how native Yahoo bars are timestamped.

Only `1h -> 4h` is built; a general N-hour aggregator is a follow-up if a later
plan needs `2h`/`8h` (the registry already carries the per-timeframe duration the
aggregation reads).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from market_analyser.data.timeframes import bar_duration, resampled_from
from market_analyser.data.types import Bar


def _bucket_start(ts: datetime, bucket: timedelta) -> datetime:
    """Floor `ts` onto the UTC-aligned grid of width `bucket` anchored at 00:00
    UTC. `bucket` (4h) divides a day evenly, so anchoring per-day reproduces the
    continuous 00/04/08/12/16/20 grid."""
    day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    n = (ts - day_start) // bucket
    return day_start + n * bucket


def resample_ohlcv(bars: Sequence[Bar], target: str = "4h") -> list[Bar]:
    """Aggregate `bars` (native base-timeframe bars) into `target` bars on the
    fixed UTC grid. Each output bar over `[bucket_start, bucket_start + width)`
    takes open = first base bar's open, high = max high, low = min low,
    close = last base bar's close, volume = sum.

    A partial final bucket (fewer base bars than a full window at the series end)
    is still emitted from the bars present — never dropped, never forward-padded —
    which keeps the result anti-lookahead-safe (it uses only `bars[0..=i]`).

    Deterministic: the input is sorted by `event_ts` and buckets are emitted in
    chronological order, with no set/dict-iteration-order dependence. `target`
    must be a resampled timeframe (e.g. `4h`); a native timeframe raises
    `ValueError`."""
    base = resampled_from(target)
    if base is None:
        raise ValueError(f"timeframe {target!r} is native, not resampled — nothing to aggregate")
    width = bar_duration(target)

    ordered = sorted(bars, key=lambda b: b.event_ts)
    buckets: dict[datetime, list[Bar]] = {}
    for bar in ordered:
        buckets.setdefault(_bucket_start(bar.event_ts, width), []).append(bar)

    out: list[Bar] = []
    for bucket_ts in sorted(buckets):
        group = buckets[bucket_ts]
        out.append(
            Bar(
                symbol=group[0].symbol,
                timeframe=target,
                event_ts=bucket_ts,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum(b.volume for b in group),
                source=group[0].source,
            ),
        )
    return out


__all__ = ["resample_ohlcv"]
