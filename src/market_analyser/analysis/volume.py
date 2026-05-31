"""Pure, trailing volume measures (Plan 0027 phase 1, ADR-0023).

Five trailing volume functions over `bars[0..=last]` plus a composed
`volume_summary`. No pandas/numpy (consistent with ADR-0023). Every series is the
input length with a leading run of `None` where the measure is mathematically
undefined, exactly the convention `analysis/indicators.py` uses — `result[i]`
reads only `bars[0..=i]`, so truncating the future never changes the past (the
load-bearing anti-lookahead property tested in `tests/analysis/test_volume.py`).

`vwap` here is a **rolling trailing N-period** volume-weighted average of the
typical price `(high + low + close) / 3`, **not** session VWAP. Our bars are
predominantly daily and we don't carry intraday session boundaries, so a session
reset is ill-defined; a rolling trailing window is deterministic and well-defined
on any timeframe (Plan 0027 "VWAP anchoring" decision). The renderer's
`lib/volume.ts` mirrors this same approximation.

Conditions only — `volume_summary` reports how heavy volume is and whether OBV is
accumulating; never a buy/sell call.
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.types import VolumeStance, VolumeSummary
from market_analyser.data.types import Bar

# --- Tunable measure windows + stance thresholds (named constants) ---------- #
VOLUME_SMA_PERIOD = 20  # trailing window for the volume moving average
RELATIVE_VOLUME_PERIOD = 20  # trailing MA window for latest ÷ MA
VOLUME_PERCENTILE_WINDOW = 90  # trailing window for the volume percentile rank
OBV_SLOPE_LOOKBACK = 10  # trailing window for the signed OBV slope
VWAP_PERIOD = 20  # rolling trailing VWAP window (NOT session-anchored)
HEAVY_MULT = 1.5  # latest >= HEAVY_MULT * trailing MA -> HEAVY
LIGHT_MULT = 0.5  # latest <= LIGHT_MULT * trailing MA -> LIGHT


def _last(series: Sequence[float | None]) -> float | None:
    for v in reversed(series):
        if v is not None:
            return v
    return None


def volume_sma(bars: Sequence[Bar], period: int = VOLUME_SMA_PERIOD) -> list[float | None]:
    """Trailing simple moving average of volume, `None` for `i < period - 1`.

    Delegates to `indicators.sma` over the volume series so the convention (window
    inclusive of `i`, length-aligned, `None`-prefixed) stays identical.
    """

    return ind.sma([b.volume for b in bars], period)


def _percentile_rank(values: Sequence[float], window: int) -> float:
    """Trailing percentile rank (0..100) of the latest value among the most recent
    `window` values — the share at or below the latest. Pure and trailing."""

    sample = values[-window:]
    latest = sample[-1]
    below_or_equal = sum(1 for v in sample if v <= latest)
    return 100.0 * below_or_equal / len(sample)


def relative_volume(
    bars: Sequence[Bar], period: int = RELATIVE_VOLUME_PERIOD
) -> tuple[float | None, float | None]:
    """Latest volume ÷ its trailing `period`-bar MA, plus the trailing percentile
    rank of the latest volume over `VOLUME_PERCENTILE_WINDOW` bars.

    Returns `(None, None)` when there are fewer than `period` bars. The ratio is
    `None` (never `inf`) when the trailing MA is `0` — a degenerate zero-volume
    window from the feed must not divide-by-zero.
    """

    if len(bars) < period:
        return None, None
    volumes = [b.volume for b in bars]
    ma = volume_sma(bars, period)[-1]
    latest = volumes[-1]
    ratio = latest / ma if (ma is not None and ma != 0.0) else None
    percentile = _percentile_rank(volumes, VOLUME_PERCENTILE_WINDOW)
    return ratio, percentile


def obv(bars: Sequence[Bar]) -> list[float | None]:
    """Cumulative on-balance volume, seeded at `0.0` on the first bar.

    From `i >= 1`, the bar's volume is added when `close > prev_close`, subtracted
    when `close < prev_close`, and left unchanged on a flat close. Defined for every
    bar (length-aligned); `None` only when there are no bars.
    """

    n = len(bars)
    out: list[float | None] = [None] * n
    if n == 0:
        return out
    cumulative = 0.0
    out[0] = 0.0
    for i in range(1, n):
        if bars[i].close > bars[i - 1].close:
            cumulative += bars[i].volume
        elif bars[i].close < bars[i - 1].close:
            cumulative -= bars[i].volume
        out[i] = cumulative
    return out


def obv_slope(bars: Sequence[Bar], lookback: int = OBV_SLOPE_LOOKBACK) -> list[float | None]:
    """Signed slope of OBV over the trailing `lookback` bars — the basis for an
    accumulation (`> 0`) / distribution (`< 0`) read. `None` until index `lookback`.

    A simple finite-difference slope `(obv[i] - obv[i - lookback]) / lookback`:
    deterministic, trailing, and hand-verifiable.
    """

    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    obv_series = obv(bars)
    n = len(obv_series)
    out: list[float | None] = [None] * n
    for i in range(lookback, n):
        prev, curr = obv_series[i - lookback], obv_series[i]
        if prev is None or curr is None:
            continue
        out[i] = (curr - prev) / lookback
    return out


def vwap(bars: Sequence[Bar], period: int = VWAP_PERIOD) -> list[float | None]:
    """Rolling trailing volume-weighted average of the typical price
    `(high + low + close) / 3` over the trailing `period` bars inclusive of `i`.

    `None` for `i < period - 1`, and `None` for a window whose total volume is `0`
    (degenerate — no weighting defined, never divide-by-zero). NOT session VWAP —
    see the module docstring.
    """

    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(bars)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = bars[i - period + 1 : i + 1]
        volume_sum = sum(b.volume for b in window)
        if volume_sum == 0.0:
            continue  # degenerate zero-volume window — leave undefined
        weighted = sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in window)
        out[i] = weighted / volume_sum
    return out


def _stance(ratio: float | None) -> VolumeStance:
    if ratio is None:
        return VolumeStance.NORMAL
    if ratio >= HEAVY_MULT:
        return VolumeStance.HEAVY
    if ratio <= LIGHT_MULT:
        return VolumeStance.LIGHT
    return VolumeStance.NORMAL


def volume_summary(bars: Sequence[Bar]) -> VolumeSummary:
    """Compose the latest trailing volume measures into a frozen `VolumeSummary`.

    On too-few bars (or none) the undefined measures are `None` and `stance` falls
    back to `NORMAL` — the call never raises.
    """

    if not bars:
        return VolumeSummary(
            latest_volume=None,
            volume_sma=None,
            relative_volume=None,
            volume_percentile=None,
            obv=None,
            obv_slope=None,
            vwap=None,
            stance=VolumeStance.NORMAL,
        )
    ratio, percentile = relative_volume(bars)
    return VolumeSummary(
        latest_volume=bars[-1].volume,
        volume_sma=_last(volume_sma(bars)),
        relative_volume=ratio,
        volume_percentile=percentile,
        obv=_last(obv(bars)),
        obv_slope=_last(obv_slope(bars)),
        vwap=_last(vwap(bars)),
        stance=_stance(ratio),
    )


__all__ = [
    "HEAVY_MULT",
    "LIGHT_MULT",
    "OBV_SLOPE_LOOKBACK",
    "RELATIVE_VOLUME_PERIOD",
    "VOLUME_PERCENTILE_WINDOW",
    "VOLUME_SMA_PERIOD",
    "VWAP_PERIOD",
    "obv",
    "obv_slope",
    "relative_volume",
    "volume_sma",
    "volume_summary",
    "vwap",
]
