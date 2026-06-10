"""Trailing volume-by-price profile (Plan 0051 phase 2, ADR-0023).

`volume_profile(bars)` bins the volume of the trailing `window` bars (inclusive
of the last bar) across `bins` equal-width price buckets spanning the window's
`[min(low), max(high)]` range. Each bar's volume is spread *proportionally*
across the buckets its `[low, high]` range overlaps — a bar that traded across
two buckets contributes to both, weighted by overlap — so the profile is a
price-distribution of traded volume, not a close-price histogram.

Pure and trailing: the profile describes the state *as of the last input bar*
and reads only `bars[0..=last]` — truncating the series to any `k` yields the
profile an observer at bar `k` would have seen (no future bar leaks in; pinned
by the truncation test in `tests/analysis/test_volume_profile.py`). Pure
Python, no pandas/numpy, deterministic (ADR-0023).

`VolumeProfile.volume_at_price(price, band)` reads the summed volume inside the
absolute band `[price - band, price + band]`, attributing partially-overlapped
buckets proportionally. This is the level-strength input `analysis/levels.py`
(phase 3) uses: a support/resistance zone that absorbed heavy traded volume is
a stronger level than an equally-touched thin one.

This is NOT a VWAP variant — `analysis/volume.py` owns VWAP. The binning
resolution is a deliberate tradeoff (too coarse and every level looks equally
strong, too fine and the profile is noise); `VOLUME_PROFILE_BINS` /
`VOLUME_PROFILE_WINDOW` are the named constants, with a fixture pinning the
intended bucketing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from market_analyser.data.types import Bar

# --- Tunable profile resolution (named constants, see module docstring) ------ #
VOLUME_PROFILE_WINDOW = 90  # trailing bars in the profile (matches the 90-bar
# trailing convention of the snapshot's percentile windows)
VOLUME_PROFILE_BINS = 24  # equal-width price buckets across the window's range


class VolumeProfileBin(BaseModel):
    """One price bucket of a volume-by-price profile: the summed (proportionally
    attributed) volume traded inside `[low, high]` over the profile window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    low: float
    high: float
    volume: float


class VolumeProfile(BaseModel):
    """A trailing volume-by-price distribution as of `end_ts` (the last input
    bar). `bins` are contiguous, ascending, and span `[price_low, price_high]`
    (the window's min low / max high). A degenerate single-price window yields
    one zero-width bin carrying all the volume."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_ts: datetime  # first bar inside the profile window
    end_ts: datetime  # the as-of bar (last input bar)
    price_low: float
    price_high: float
    bins: list[VolumeProfileBin]

    def volume_at_price(self, price: float, band: float) -> float:
        """Summed volume inside the absolute band `[price - band, price + band]`.

        Buckets partially covered by the band contribute proportionally to the
        covered fraction of their width. A zero-width (degenerate) bucket
        contributes fully when its price lies inside the band. `band` must be
        `>= 0`; `band == 0` reads a single price point (only a degenerate bucket
        exactly at `price` can contribute).
        """

        if band < 0:
            raise ValueError(f"band must be >= 0, got {band}")
        lo, hi = price - band, price + band
        total = 0.0
        for b in self.bins:
            width = b.high - b.low
            if width <= 0.0:  # degenerate single-price bucket
                if lo <= b.low <= hi:
                    total += b.volume
                continue
            overlap = min(hi, b.high) - max(lo, b.low)
            if overlap > 0.0:
                total += b.volume * (overlap / width)
        return total


def volume_profile(
    bars: Sequence[Bar],
    window: int = VOLUME_PROFILE_WINDOW,
    bins: int = VOLUME_PROFILE_BINS,
) -> VolumeProfile:
    """Bin the trailing `window` bars' volume across `bins` price buckets.

    Trailing and pure: only `bars[-window:]` (ending at the last bar) is read,
    so the profile is exactly what an observer at the last bar could compute.
    Requires at least one bar; `window`/`bins` must be `>= 1`.
    """

    if not bars:
        raise ValueError("volume_profile requires at least one bar")
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    if bins < 1:
        raise ValueError(f"bins must be >= 1, got {bins}")

    tail = list(bars[-window:])
    price_low = min(b.low for b in tail)
    price_high = max(b.high for b in tail)

    if price_high == price_low:
        # Degenerate single-price window: one zero-width bin holds everything.
        total = sum(b.volume for b in tail)
        return VolumeProfile(
            start_ts=tail[0].event_ts,
            end_ts=tail[-1].event_ts,
            price_low=price_low,
            price_high=price_high,
            bins=[VolumeProfileBin(low=price_low, high=price_high, volume=total)],
        )

    width = (price_high - price_low) / bins
    # The last edge is pinned to price_high exactly so float drift cannot leave
    # a sliver of a bar's range outside the final bucket.
    edges = [price_low + i * width for i in range(bins)] + [price_high]
    volumes = [0.0] * bins
    for b in tail:
        span = b.high - b.low
        if span <= 0.0:
            # A single-price bar: all volume into the bucket containing it.
            idx = min(int((b.low - price_low) / width), bins - 1)
            volumes[idx] += b.volume
            continue
        for i in range(bins):
            overlap = min(b.high, edges[i + 1]) - max(b.low, edges[i])
            if overlap > 0.0:
                volumes[i] += b.volume * (overlap / span)

    return VolumeProfile(
        start_ts=tail[0].event_ts,
        end_ts=tail[-1].event_ts,
        price_low=price_low,
        price_high=price_high,
        bins=[
            VolumeProfileBin(low=edges[i], high=edges[i + 1], volume=volumes[i])
            for i in range(bins)
        ],
    )


__all__ = [
    "VOLUME_PROFILE_BINS",
    "VOLUME_PROFILE_WINDOW",
    "VolumeProfile",
    "VolumeProfileBin",
    "volume_profile",
]
