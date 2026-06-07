"""Causal feature pipeline (Plan 0036 phase 1, ADR-0030 invariant 1).

`build_feature_rows(bars)` assembles a per-bar feature matrix from the trailing
`analysis/` indicator surface (ADR-0023). The row at bar ``i`` is computed from
``bars[0..=i]`` **only** — there is no centered indicator, no full-series
statistic (`.mean()` / normalisation over the whole series), and no label column.
Because every indicator the row is built from is itself trailing (its value at
``i`` reads only ``bars[0..=i]``), the matrix inherits the anti-lookahead property
for free: truncating the series at ``i`` and rebuilding leaves row ``i``
byte-identical. That invariant is the load-bearing leakage guard, tested per the
plan's done-when in ``tests/forecast/test_features.py``.

The feature set is **frozen and explicitly ordered** (`FEATURE_NAMES`). Every row
carries its values in that exact order; adding or reordering a feature is a
deliberate edit that must update both the tuple and the test that pins it. The
order is also hashed into `FEATURE_SET_ID`, a prediction-affecting input that
flows into the model-version provenance (ADR-0040; phases 2 & 4).

All features are stationary ratios / bounded oscillator readings rather than raw
price levels — appropriate for a model, and a second line of defence against a
feature whose scale silently encodes the (non-stationary) absolute price.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from market_analyser.analysis import indicators as ind
from market_analyser.data.types import Bar

# --- Indicator periods the feature set is built on -------------------------- #
# Chosen to match the `analysis/snapshot.py` conventions so the forecast features
# read the same indicator parameterisation the analyst surface already reports.
RSI_PERIOD = 14
BB_PERIOD = 20
ATR_PERIOD = 14
ADX_PERIOD = 14
ST_PERIOD = 10
EMA_SHORT = 20
EMA_LONG = 50
DON_PERIOD = 20
VOL_SMA_PERIOD = 20
RET_LOOKBACK = 5  # the longer trailing return horizon (ret_5)

# --- The frozen, explicitly-ordered feature set ----------------------------- #
# Order is load-bearing: it is the column order of every row AND an input to
# FEATURE_SET_ID. Changing this tuple changes the feature-set identity (and thus
# the model_version, ADR-0040) and must update the test that pins it.
FEATURE_NAMES: tuple[str, ...] = (
    "ret_1",  # close[i] / close[i-1] - 1 (trailing one-bar return)
    "ret_5",  # close[i] / close[i-5] - 1 (trailing N-bar return)
    "rsi_14",  # Wilder RSI
    "macd",  # MACD line
    "macd_signal",  # MACD signal EMA
    "macd_hist",  # MACD histogram (line - signal)
    "bb_pct_b",  # (close - lower) / (upper - lower) within the Bollinger band
    "atr_pct",  # ATR / close (volatility as a fraction of price)
    "adx",  # trend strength
    "plus_di",  # +DI
    "minus_di",  # -DI
    "supertrend_dir",  # +1 uptrend / -1 downtrend
    "ema20_dist",  # (close - EMA20) / close
    "ema50_dist",  # (close - EMA50) / close
    "donchian_pos",  # (close - lower) / (upper - lower) within the Donchian channel
    "rel_volume",  # volume[i] / SMA(volume, 20)[i]
)


def _compute_feature_set_id(names: tuple[str, ...]) -> str:
    """A stable 16-hex-char identity for the frozen feature set — the SHA-256 of
    the ordered feature names. Same set → same id; any add/reorder → new id."""

    digest = hashlib.sha256("|".join(names).encode("utf-8")).hexdigest()
    return digest[:16]


FEATURE_SET_ID: str = _compute_feature_set_id(FEATURE_NAMES)


def feature_names() -> tuple[str, ...]:
    """The frozen feature column order. The single source consumers index against."""

    return FEATURE_NAMES


@dataclass(frozen=True)
class FeatureRow:
    """One bar's feature vector. ``values`` is ordered exactly as `FEATURE_NAMES`;
    ``bar_index`` / ``event_ts`` locate the row on the source series so a label
    (phase 2) can be aligned without re-deriving the index."""

    bar_index: int
    event_ts: datetime
    values: tuple[float, ...]


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    """``numerator / denominator`` or ``None`` when the denominator is zero — a
    degenerate band/price collapses the whole row to ``None`` rather than emitting
    a div-by-zero or an infinite feature."""

    if denominator == 0.0:
        return None
    return numerator / denominator


def build_feature_rows(bars: Sequence[Bar]) -> list[FeatureRow | None]:
    """Build the per-bar feature matrix, aligned to ``bars``.

    Returns a list the same length as ``bars`` where entry ``i`` is the
    `FeatureRow` for bar ``i`` once **every** feature is defined there, or ``None``
    while any underlying indicator is still in its undefined leading run (or a
    band/price denominator is degenerate). Entry ``i`` reads only ``bars[0..=i]``.
    """

    n = len(bars)
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]

    # Each series is internally trailing: series[i] depends only on bars[0..=i].
    rsi_s = ind.rsi(closes, RSI_PERIOD)
    macd_s = ind.macd(closes)
    boll_s = ind.bollinger(closes, BB_PERIOD)
    atr_s = ind.atr(bars, ATR_PERIOD)
    adx_s = ind.adx(bars, ADX_PERIOD)
    st_s = ind.supertrend(bars, ST_PERIOD)
    ema20_s = ind.ema(closes, EMA_SHORT)
    ema50_s = ind.ema(closes, EMA_LONG)
    don_s = ind.donchian(bars, DON_PERIOD)
    vol_sma_s = ind.sma(volumes, VOL_SMA_PERIOD)

    rows: list[FeatureRow | None] = [None] * n
    for i in range(n):
        row = _row_at(
            i,
            closes=closes,
            volumes=volumes,
            rsi_s=rsi_s,
            macd_s=macd_s,
            boll_s=boll_s,
            atr_s=atr_s,
            adx_s=adx_s,
            st_s=st_s,
            ema20_s=ema20_s,
            ema50_s=ema50_s,
            don_s=don_s,
            vol_sma_s=vol_sma_s,
        )
        if row is not None:
            rows[i] = FeatureRow(bar_index=i, event_ts=bars[i].event_ts, values=row)
    return rows


def _row_at(
    i: int,
    *,
    closes: Sequence[float],
    volumes: Sequence[float],
    rsi_s: Sequence[float | None],
    macd_s: Sequence[ind.MacdValue | None],
    boll_s: Sequence[ind.BollingerValue | None],
    atr_s: Sequence[float | None],
    adx_s: Sequence[ind.AdxValue | None],
    st_s: Sequence[ind.SupertrendValue | None],
    ema20_s: Sequence[float | None],
    ema50_s: Sequence[float | None],
    don_s: Sequence[ind.DonchianValue | None],
    vol_sma_s: Sequence[float | None],
) -> tuple[float, ...] | None:
    """Assemble bar ``i``'s feature tuple, or ``None`` if any feature is undefined.

    The assembly is order-locked to `FEATURE_NAMES`: the local list is appended in
    exactly that sequence and its final length is asserted against it.
    """

    close = closes[i]

    ret_1 = _safe_ratio(close, closes[i - 1]) if i >= 1 else None
    if ret_1 is not None:
        ret_1 -= 1.0
    ret_5 = _safe_ratio(close, closes[i - RET_LOOKBACK]) if i >= RET_LOOKBACK else None
    if ret_5 is not None:
        ret_5 -= 1.0

    macd_v = macd_s[i]
    boll_v = boll_s[i]
    atr_v = atr_s[i]
    adx_v = adx_s[i]
    st_v = st_s[i]
    ema20_v = ema20_s[i]
    ema50_v = ema50_s[i]
    don_v = don_s[i]
    vol_sma_v = vol_sma_s[i]

    bb_pct_b = (
        _safe_ratio(close - boll_v.lower, boll_v.upper - boll_v.lower)
        if boll_v is not None
        else None
    )
    atr_pct = _safe_ratio(atr_v, close) if atr_v is not None else None
    ema20_dist = _safe_ratio(close - ema20_v, close) if ema20_v is not None else None
    ema50_dist = _safe_ratio(close - ema50_v, close) if ema50_v is not None else None
    donchian_pos = (
        _safe_ratio(close - don_v.lower, don_v.upper - don_v.lower) if don_v is not None else None
    )
    rel_volume = _safe_ratio(volumes[i], vol_sma_v) if vol_sma_v is not None else None

    values: list[float | None] = [
        ret_1,
        ret_5,
        rsi_s[i],
        macd_v.macd if macd_v is not None else None,
        macd_v.signal if macd_v is not None else None,
        macd_v.histogram if macd_v is not None else None,
        bb_pct_b,
        atr_pct,
        adx_v.adx if adx_v is not None else None,
        adx_v.plus_di if adx_v is not None else None,
        adx_v.minus_di if adx_v is not None else None,
        float(st_v.direction) if st_v is not None else None,
        ema20_dist,
        ema50_dist,
        donchian_pos,
        rel_volume,
    ]
    assert len(values) == len(FEATURE_NAMES)  # order/length lock against the frozen set

    if any(v is None for v in values):
        return None
    return tuple(v for v in values if v is not None)


__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SET_ID",
    "FeatureRow",
    "build_feature_rows",
    "feature_names",
]
