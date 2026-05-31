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
from itertools import pairwise

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.types import (
    SmartVolumeHit,
    VolumeBreakout,
    VolumeConfirmation,
    VolumeStance,
    VolumeSummary,
)
from market_analyser.data.types import Bar

# --- Tunable measure windows + stance thresholds (named constants) ---------- #
VOLUME_SMA_PERIOD = 20  # trailing window for the volume moving average
RELATIVE_VOLUME_PERIOD = 20  # trailing MA window for latest ÷ MA
VOLUME_PERCENTILE_WINDOW = 90  # trailing window for the volume percentile rank
OBV_SLOPE_LOOKBACK = 10  # trailing window for the signed OBV slope
VWAP_PERIOD = 20  # rolling trailing VWAP window (NOT session-anchored)
HEAVY_MULT = 1.5  # latest >= HEAVY_MULT * trailing MA -> HEAVY
LIGHT_MULT = 0.5  # latest <= LIGHT_MULT * trailing MA -> LIGHT

# --- Scanner-condition tunables (Plan 0021 phase 2) ------------------------- #
BREAKOUT_VOL_MULTIPLE = 2.0  # relative volume at/above this is a "surge" for a breakout
BREAKOUT_PRICE_LOOKBACK = 20  # trailing bars (excl. latest) whose range price must clear
CONFIRMATION_LOOKBACK = 20  # trailing bars over which volume must back the price move
CONFIRMATION_MIN = 0.6  # confirmation score at/above this -> confirmed
SMART_VOL_MULTIPLE = 1.5  # relative volume at/above this is a surge for smart_volume
SMART_RSI_LOW = 40.0  # smart_volume RSI band lower bound (inclusive)
SMART_RSI_HIGH = 60.0  # smart_volume RSI band upper bound (inclusive)
RSI_PERIOD = 14  # RSI window for smart_volume (matches the snapshot's RSI)


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


# --------------------------------------------------------------------------- #
# Scanner-condition functions (Plan 0021 phase 2)                              #
#                                                                              #
# All three report a condition about the *latest* bar over `bars[0..=last]`    #
# (trailing): they read only the trailing window ending at the last bar and    #
# never index beyond it, so a verdict computed on a truncated series is         #
# unaffected by bars that would later be appended. They reuse the Plan 0027     #
# primitives above (relative_volume) rather than re-deriving them.             #
# --------------------------------------------------------------------------- #


def volume_breakout(
    bars: Sequence[Bar],
    vol_multiple: float = BREAKOUT_VOL_MULTIPLE,
    price_lookback: int = BREAKOUT_PRICE_LOOKBACK,
) -> VolumeBreakout:
    """Whether the latest bar broke its trailing price range on a volume surge.

    Positive only when relative volume is `>= vol_multiple` AND the latest close
    clears the trailing `price_lookback`-bar high (bullish) or low (bearish); the
    cleared extreme is reported as `broken_level`. Returns a negative result (no
    breakout, `broken_level=None`) on a drift or when there are too few bars.
    """

    symbol = bars[-1].symbol if bars else ""
    ratio, _ = relative_volume(bars)
    if ratio is None or len(bars) < price_lookback + 1:
        return VolumeBreakout(
            symbol=symbol,
            is_breakout=False,
            direction="neutral",
            volume_multiple=ratio,
            broken_level=None,
        )

    prior = bars[-(price_lookback + 1) : -1]  # the price_lookback bars before the latest
    prior_high = max(b.high for b in prior)
    prior_low = min(b.low for b in prior)
    close = bars[-1].close
    surge = ratio >= vol_multiple
    if surge and close > prior_high:
        return VolumeBreakout(
            symbol=symbol,
            is_breakout=True,
            direction="bullish",
            volume_multiple=ratio,
            broken_level=prior_high,
        )
    if surge and close < prior_low:
        return VolumeBreakout(
            symbol=symbol,
            is_breakout=True,
            direction="bearish",
            volume_multiple=ratio,
            broken_level=prior_low,
        )
    return VolumeBreakout(
        symbol=symbol,
        is_breakout=False,
        direction="neutral",
        volume_multiple=ratio,
        broken_level=None,
    )


def volume_confirmation(
    bars: Sequence[Bar], lookback: int = CONFIRMATION_LOOKBACK
) -> VolumeConfirmation:
    """How well volume backs the recent price move, as a 0..1 score.

    Over the trailing `lookback` bars, the net price direction is fixed by
    `close[-1]` vs `close[-1-lookback]`; `score` is the share of *directional*
    volume sitting on bars that moved with that direction. High when an up-move is
    carried by volume on the up-bars; low when volume concentrates on the
    counter-trend bars (a divergence). Returns a `0.0`/neutral result on a flat
    move or too few bars.
    """

    symbol = bars[-1].symbol if bars else ""
    if len(bars) < lookback + 1:
        return VolumeConfirmation(
            symbol=symbol,
            score=0.0,
            confirmed=False,
            direction="neutral",
            supportive_volume=0.0,
            opposing_volume=0.0,
        )

    window = bars[-(lookback + 1) :]  # lookback+1 bars -> lookback close-to-close deltas
    net = window[-1].close - window[0].close
    if net == 0.0:
        return VolumeConfirmation(
            symbol=symbol,
            score=0.0,
            confirmed=False,
            direction="neutral",
            supportive_volume=0.0,
            opposing_volume=0.0,
        )

    sign = 1.0 if net > 0 else -1.0
    supportive = 0.0
    opposing = 0.0
    for prev, cur in pairwise(window):
        delta = (cur.close - prev.close) * sign
        if delta > 0:
            supportive += cur.volume
        elif delta < 0:
            opposing += cur.volume
        # a flat bar backs neither direction
    total = supportive + opposing
    score = supportive / total if total > 0.0 else 0.0
    return VolumeConfirmation(
        symbol=symbol,
        score=score,
        confirmed=score >= CONFIRMATION_MIN,
        direction="bullish" if net > 0 else "bearish",
        supportive_volume=supportive,
        opposing_volume=opposing,
    )


def smart_volume(
    bars: Sequence[Bar],
    rsi_low: float = SMART_RSI_LOW,
    rsi_high: float = SMART_RSI_HIGH,
    vol_multiple: float = SMART_VOL_MULTIPLE,
) -> SmartVolumeHit:
    """A volume surge with RSI inside `[rsi_low, rsi_high]`.

    `qualifies` is true only when relative volume is `>= vol_multiple` AND the
    latest trailing RSI lies in the band. The latest figures ride along even when
    the condition fails (or is undefined over too few bars, where they are `None`).
    """

    symbol = bars[-1].symbol if bars else ""
    ratio, _ = relative_volume(bars)
    rsi_val = _last(ind.rsi([b.close for b in bars], RSI_PERIOD))
    surge = ratio is not None and ratio >= vol_multiple
    in_band = rsi_val is not None and rsi_low <= rsi_val <= rsi_high
    return SmartVolumeHit(
        symbol=symbol,
        qualifies=surge and in_band,
        volume_multiple=ratio,
        rsi=rsi_val,
    )


__all__ = [
    "BREAKOUT_PRICE_LOOKBACK",
    "BREAKOUT_VOL_MULTIPLE",
    "CONFIRMATION_LOOKBACK",
    "CONFIRMATION_MIN",
    "HEAVY_MULT",
    "LIGHT_MULT",
    "OBV_SLOPE_LOOKBACK",
    "RELATIVE_VOLUME_PERIOD",
    "RSI_PERIOD",
    "SMART_RSI_HIGH",
    "SMART_RSI_LOW",
    "SMART_VOL_MULTIPLE",
    "VOLUME_PERCENTILE_WINDOW",
    "VOLUME_SMA_PERIOD",
    "VWAP_PERIOD",
    "obv",
    "obv_slope",
    "relative_volume",
    "smart_volume",
    "volume_breakout",
    "volume_confirmation",
    "volume_sma",
    "volume_summary",
    "vwap",
]
