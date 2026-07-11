"""In-house OHLCV resampler (Plan 0025 phase 2 / ADR-0028; Plan 0081 / ADR-0076).

A source that serves a timeframe natively is fetched; one that does not has it
**derived on read** from a native base by this aggregator. The aggregation is
**trailing by construction** (an output bar reads only the base bars inside its
own closed window, never future bars), so it carries no lookahead — the
project-wide non-negotiable for any financially-meaningful path.

Three targets are built, one bucketing rule each:

- **`4h ← 1h`** — a **fixed UTC grid**: buckets start at `00:00, 04:00, 08:00,
  12:00, 16:00, 20:00 UTC`. This does not match any single exchange session — a
  deliberate trade of session-exactness for determinism and venue-independence,
  since we have no exchange-calendar data (ADR-0028). (Yahoo has no 4h interval;
  Coinbase serves 15m/1h/1d and derives 4h from 1h.)
- **`1w ← 1d`** — the **ISO calendar week**: buckets start Monday 00:00 UTC.
- **`1mo ← 1d`** — the **calendar month**: buckets start on the 1st, 00:00 UTC.

The weekly/monthly rules exist for Coinbase's coarse timeframes (ADR-0076): 24/7
crypto has gap-free daily bars, so calendar bucketing is deterministic and
trailing, materially reducing the variable-month concern ADR-0047 raised for
Yahoo's closure-laden series. Yahoo still fetches 1w/1mo natively (the provider
only derives when `source_resampled_from` says so), so this aggregator being able
to bucket them changes no Yahoo behaviour.

Bars are timestamped at their bucket open, consistent with how native bars are
timestamped.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from market_analyser.data.timeframes import bar_duration, timeframe_spec
from market_analyser.data.types import Bar

# The timeframes this aggregator knows how to bucket (Plan 0081): each has a
# derivation somewhere in the per-source registry (`source_resampled_from`). A
# target outside this set is either native (nothing to aggregate) or unknown.
_RESAMPLE_TARGETS: frozenset[str] = frozenset({"4h", "1w", "1mo"})


def _bucket_start(ts: datetime, target: str) -> datetime:
    """Floor `ts` onto `target`'s trailing bucket grid (see the module docstring):
    calendar week (Monday 00:00 UTC) for `1w`, calendar month (1st 00:00 UTC) for
    `1mo`, else the fixed sub-daily UTC grid of width `bar_duration(target)`
    anchored at 00:00 UTC (`4h`)."""
    if target == "1w":
        day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())
    if target == "1mo":
        return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    width = bar_duration(target)
    day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    n = (ts - day_start) // width
    return day_start + n * width


def resample_ohlcv(bars: Sequence[Bar], target: str = "4h") -> list[Bar]:
    """Aggregate `bars` (native base-timeframe bars) into `target` bars on
    `target`'s trailing bucket grid. Each output bar takes open = first base
    bar's open, high = max high, low = min low, close = last base bar's close,
    volume = sum.

    A partial final bucket (fewer base bars than a full window at the series end)
    is still emitted from the bars present — never dropped, never forward-padded —
    which keeps the result anti-lookahead-safe (it uses only `bars[0..=i]`).

    Deterministic: the input is sorted by `event_ts` and buckets are emitted in
    chronological order, with no set/dict-iteration-order dependence. `target`
    must be a resampled timeframe (`4h`, `1w`, or `1mo`); a native timeframe (or
    an unknown one) raises `ValueError`."""
    if target not in _RESAMPLE_TARGETS:
        timeframe_spec(target)  # raises ValueError for an unregistered timeframe
        raise ValueError(f"timeframe {target!r} is native, not resampled — nothing to aggregate")

    ordered = sorted(bars, key=lambda b: b.event_ts)
    buckets: dict[datetime, list[Bar]] = {}
    for bar in ordered:
        buckets.setdefault(_bucket_start(bar.event_ts, target), []).append(bar)

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
