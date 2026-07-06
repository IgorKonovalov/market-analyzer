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
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from market_analyser.analysis import cycles
from market_analyser.analysis import indicators as ind
from market_analyser.data.metric_series import (
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_COINMETRICS_BTC_MVRV,
    SERIES_FNG_VALUE,
)
from market_analyser.data.types import Bar
from market_analyser.forecast.exogenous import ExogenousColumns

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

# --- Feature-set v2 (Plan 0059 phase 2, ADR-0054) ---------------------------- #
# v1's 16 OHLCV features + 4 cycle features (constants + cached bars — ordinary
# trailing features, no lag needed) + 7 exogenous features read lag-1 as-of from
# the metric store. The delta features are derived **in-pipeline** from the
# joined columns (the plan's resolved open question: the registry stays
# raw-observations-only). v1 stays frozen and reproducible under its own id.
DELTA_LOOKBACK = 7  # bars between the two endpoints of the *_delta_7 features

FEATURE_NAMES_V2: tuple[str, ...] = (
    *FEATURE_NAMES,
    "halving_phase",  # fraction of the halving cycle elapsed, [0, 1]
    "days_since_halving",  # days since the last halving at the bar's UTC date
    "mayer_multiple",  # close / SMA200(daily closes)
    "dist_200w_ma",  # close / SMA1400(daily closes) - 1
    "fng_value",  # Fear & Greed index (0-100), lag-1
    "fng_delta_7",  # fng_value[i] - fng_value[i-7] (both lag-1)
    "btc_dominance",  # BTC dominance percent (0-100), lag-1
    "dominance_delta_7",  # btc_dominance[i] - btc_dominance[i-7]
    "funding_rate",  # Binance BTCUSDT perp funding rate, lag-1
    "oi_delta_7",  # open_interest[i] / open_interest[i-7] - 1 (fractional)
    "mvrv",  # CoinMetrics BTC MVRV ratio, lag-1
)

FEATURE_SET_ID_V2: str = _compute_feature_set_id(FEATURE_NAMES_V2)

# The exogenous series the v2 set consumes, in column order. BTC legs only —
# the ETH funding/OI series exist in the registry but are not v2 inputs.
EXOGENOUS_SERIES_IDS_V2: tuple[str, ...] = (
    SERIES_FNG_VALUE,
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
    SERIES_COINMETRICS_BTC_MVRV,
)

# --- Feature-set v2-deep (Plan 0062 phase 1, ADR-0057) ----------------------- #
# The deep-history tier of the ADR-0057 ladder: v2-full minus the three features
# fed by the accrual-only series (`btc_dominance`, `dominance_delta_7`,
# `oi_delta_7`), whose empty pre-deployment history vetoes every historical bar
# under the conjunctive row-drop rule. The remaining exogenous series backfill
# to 2018-02 / 2019-09 / 2011-12, so this set actually trains today. Its own
# frozen tuple and id; v1's and v2-full's ids do not move.
FEATURE_NAMES_V2_DEEP: tuple[str, ...] = (
    *FEATURE_NAMES,
    "halving_phase",  # fraction of the halving cycle elapsed, [0, 1]
    "days_since_halving",  # days since the last halving at the bar's UTC date
    "mayer_multiple",  # close / SMA200(daily closes)
    "dist_200w_ma",  # close / SMA1400(daily closes) - 1
    "fng_value",  # Fear & Greed index (0-100), lag-1
    "fng_delta_7",  # fng_value[i] - fng_value[i-7] (both lag-1)
    "funding_rate",  # Binance BTCUSDT perp funding rate, lag-1
    "mvrv",  # CoinMetrics BTC MVRV ratio, lag-1
)

FEATURE_SET_ID_V2_DEEP: str = _compute_feature_set_id(FEATURE_NAMES_V2_DEEP)

# The exogenous series the deep set consumes — the three with deep history.
EXOGENOUS_SERIES_IDS_V2_DEEP: tuple[str, ...] = (
    SERIES_FNG_VALUE,
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_COINMETRICS_BTC_MVRV,
)


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


def _finite_or_none(value: float) -> float | None:
    """NaN (the exogenous missing marker) → ``None`` so the v2 row assembly
    collapses a not-yet-warm series into a dropped row, mirroring v1's
    undefined-indicator handling. Never coerces to zero (ADR-0054)."""

    return None if math.isnan(value) else value


def _cycle_features_at(
    i: int, bars: Sequence[Bar], closes: Sequence[float]
) -> tuple[float | None, float | None, float | None, float | None]:
    """The four cycle features at bar ``i`` — trailing by construction: dates
    from the bar's own UTC timestamp, moving averages over ``closes[0..=i]``.
    All ``None`` before the first halving (no cycle is defined there)."""

    bar_date = bars[i].event_ts.date()
    if bar_date < cycles.HALVING_DATES[0]:
        return None, None, None, None
    return (
        cycles.halving_phase(bar_date),
        float(cycles.days_since_halving(bar_date)),
        cycles.mayer_multiple(closes[: i + 1]),
        cycles.dist_200w_ma(closes[: i + 1]),
    )


def _exogenous_features_at(i: int, exogenous: ExogenousColumns) -> tuple[float | None, ...]:
    """The seven exogenous features at bar ``i`` from the lag-1 columns, in
    FEATURE_NAMES_V2 order. A NaN endpoint makes the feature ``None``; the
    delta features additionally need the ``i - DELTA_LOOKBACK`` endpoint."""

    fng = exogenous.columns[SERIES_FNG_VALUE]
    dom = exogenous.columns[SERIES_COINGECKO_BTC_DOMINANCE]
    funding = exogenous.columns[SERIES_BINANCE_FUNDING_RATE_BTCUSDT]
    oi = exogenous.columns[SERIES_BINANCE_OPEN_INTEREST_BTCUSDT]
    mvrv = exogenous.columns[SERIES_COINMETRICS_BTC_MVRV]

    fng_value = _finite_or_none(fng[i])
    dominance = _finite_or_none(dom[i])
    funding_rate = _finite_or_none(funding[i])
    mvrv_value = _finite_or_none(mvrv[i])

    fng_delta_7: float | None = None
    dominance_delta_7: float | None = None
    oi_delta_7: float | None = None
    if i >= DELTA_LOOKBACK:
        fng_prev = _finite_or_none(fng[i - DELTA_LOOKBACK])
        if fng_value is not None and fng_prev is not None:
            fng_delta_7 = fng_value - fng_prev
        dom_prev = _finite_or_none(dom[i - DELTA_LOOKBACK])
        if dominance is not None and dom_prev is not None:
            dominance_delta_7 = dominance - dom_prev
        oi_now = _finite_or_none(oi[i])
        oi_prev = _finite_or_none(oi[i - DELTA_LOOKBACK])
        if oi_now is not None and oi_prev is not None:
            oi_delta_7 = _safe_ratio(oi_now, oi_prev)
            if oi_delta_7 is not None:
                oi_delta_7 -= 1.0

    return (
        fng_value,
        fng_delta_7,
        dominance,
        dominance_delta_7,
        funding_rate,
        oi_delta_7,
        mvrv_value,
    )


def _exogenous_features_at_deep(i: int, exogenous: ExogenousColumns) -> tuple[float | None, ...]:
    """The four deep-tier exogenous features at bar ``i`` from the lag-1 columns,
    in FEATURE_NAMES_V2_DEEP order — the v2-full extraction minus the features
    fed by the accrual-only series (ADR-0057). Same NaN → ``None`` semantics."""

    fng = exogenous.columns[SERIES_FNG_VALUE]
    funding = exogenous.columns[SERIES_BINANCE_FUNDING_RATE_BTCUSDT]
    mvrv = exogenous.columns[SERIES_COINMETRICS_BTC_MVRV]

    fng_value = _finite_or_none(fng[i])
    funding_rate = _finite_or_none(funding[i])
    mvrv_value = _finite_or_none(mvrv[i])

    fng_delta_7: float | None = None
    if i >= DELTA_LOOKBACK:
        fng_prev = _finite_or_none(fng[i - DELTA_LOOKBACK])
        if fng_value is not None and fng_prev is not None:
            fng_delta_7 = fng_value - fng_prev

    return (fng_value, fng_delta_7, funding_rate, mvrv_value)


def _build_exogenous_feature_rows(
    bars: Sequence[Bar],
    exogenous: ExogenousColumns,
    *,
    feature_names: tuple[str, ...],
    series_ids: tuple[str, ...],
    exogenous_at: Callable[[int, ExogenousColumns], tuple[float | None, ...]],
) -> list[FeatureRow | None]:
    """The one exogenous-tier row builder (Plan 0062 phase 1): v1 rows + cycle
    features + a tier's exogenous extraction, with the lag-1 join and the
    NaN-drops-row semantics identical across tiers — parameterized so the tiers
    cannot drift apart. ``exogenous`` may carry more series than the tier reads
    (the ladder builds one column set for all tiers); only ``series_ids`` are
    required and validated."""

    missing = [s for s in series_ids if s not in exogenous.columns]
    if missing:
        raise ValueError(f"exogenous columns missing required series: {missing}")
    for series_id in series_ids:
        if len(exogenous.columns[series_id]) != len(bars):
            raise ValueError(
                f"exogenous column {series_id!r} has {len(exogenous.columns[series_id])} "
                f"entries for {len(bars)} bars — columns must align to the bar list",
            )

    v1_rows = build_feature_rows(bars)
    closes = [b.close for b in bars]

    rows: list[FeatureRow | None] = [None] * len(bars)
    for i, v1_row in enumerate(v1_rows):
        if v1_row is None:
            continue
        extras: tuple[float | None, ...] = (
            *_cycle_features_at(i, bars, closes),
            *exogenous_at(i, exogenous),
        )
        values = (*v1_row.values, *extras)
        assert len(values) == len(feature_names)  # order/length lock, as in v1
        if any(v is None for v in extras):
            continue
        rows[i] = FeatureRow(
            bar_index=i,
            event_ts=bars[i].event_ts,
            values=tuple(v for v in values if v is not None),
        )
    return rows


def build_feature_rows_v2(
    bars: Sequence[Bar], exogenous: ExogenousColumns
) -> list[FeatureRow | None]:
    """Build the v2 per-bar feature matrix, aligned to ``bars``.

    ``exogenous`` must carry lag-1 columns (see `exogenous.build_exogenous_columns`)
    for every series in `EXOGENOUS_SERIES_IDS_V2`, aligned to the same bars. Entry
    ``i`` is a `FeatureRow` ordered exactly as `FEATURE_NAMES_V2` once **every**
    feature is defined there, or ``None`` otherwise — a missing exogenous value
    (series not yet warm) drops the row from the matrix, it is never zero-filled
    (ADR-0054 row policy). Entry ``i`` reads only ``bars[0..=i]`` plus metric
    points strictly before bar ``i``'s open, so the v1 anti-lookahead property
    carries over.
    """

    return _build_exogenous_feature_rows(
        bars,
        exogenous,
        feature_names=FEATURE_NAMES_V2,
        series_ids=EXOGENOUS_SERIES_IDS_V2,
        exogenous_at=_exogenous_features_at,
    )


def build_feature_rows_v2_deep(
    bars: Sequence[Bar], exogenous: ExogenousColumns
) -> list[FeatureRow | None]:
    """Build the v2-deep per-bar feature matrix, aligned to ``bars`` (ADR-0057).

    Identical contract to `build_feature_rows_v2` over the deep tier: columns for
    every series in `EXOGENOUS_SERIES_IDS_V2_DEEP` (extra series in ``exogenous``
    are ignored, so the ladder can share one column set), rows ordered exactly as
    `FEATURE_NAMES_V2_DEEP`, missing exogenous value drops the row, never
    zero-filled. Because the accrual-only series are not read here, bars they
    would have vetoed under v2-full survive whenever the three deep series are
    observable.
    """

    return _build_exogenous_feature_rows(
        bars,
        exogenous,
        feature_names=FEATURE_NAMES_V2_DEEP,
        series_ids=EXOGENOUS_SERIES_IDS_V2_DEEP,
        exogenous_at=_exogenous_features_at_deep,
    )


__all__ = [
    "DELTA_LOOKBACK",
    "EXOGENOUS_SERIES_IDS_V2",
    "EXOGENOUS_SERIES_IDS_V2_DEEP",
    "FEATURE_NAMES",
    "FEATURE_NAMES_V2",
    "FEATURE_NAMES_V2_DEEP",
    "FEATURE_SET_ID",
    "FEATURE_SET_ID_V2",
    "FEATURE_SET_ID_V2_DEEP",
    "FeatureRow",
    "build_feature_rows",
    "build_feature_rows_v2",
    "build_feature_rows_v2_deep",
    "feature_names",
]
