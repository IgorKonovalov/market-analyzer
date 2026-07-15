"""Composite technical-quality screening rank (Plan 0101, ADR-0096).

A pure, trailing, deterministic scorer that fuses a symbol's trailing condition
snapshot (`analysis/snapshot.py`) into a single normalized 0..100 composite,
decomposed into four named factor contributions — trend, momentum, volume,
volatility — that **sum to the composite** (transparent, not a black-box number),
plus a per-asset-class liquidity gate that flags and caps thin names.

Each factor is normalized to a 0..1 sub-score where 1.0 is "best" (strongest
bullish alignment / calmest volatility) and 0.0 is "worst"; the composite is the
weighted sum of those sub-scores scaled to 0..100. Where a factor draws on a
distribution-relative reading (volume / volatility percentile) it is normalized
against the symbol's *own trailing* distribution, not an absolute constant, so
crypto and equity names rank comparably despite different absolute scales
(ADR-0096 cross-asset-normalization risk).

Conditions only — ADR-0096 keeps this strictly on the ADR-0029 conditions side:
`QualityScore` has NO action / signal / recommendation / grade / buy / sell field.
It is a screening rank, never a call; a call goes through `advisor` / `recommend`.
The `advisor` may *consume* the rank as one more input, but the rank never advises.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.analysis.scanners import SCAN_SKIP, _ScanSkip
from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import ConditionSnapshot, QualityScore, Trend
from market_analyser.data.types import Bar

# --- Composite weights (opinionated, documented, tunable) ------------------- #
# The four factor weights sum to 1.0; a factor's contribution to the 0..100
# composite is `weight * sub_score * 100`, so the contributions sum to the score.
# Changing a weight is a visible edit here, never hidden behaviour (ADR-0096).
_WEIGHTS: Final[dict[str, float]] = {
    "trend": 0.35,
    "momentum": 0.30,
    "volume": 0.20,
    "volatility": 0.15,
}

# Momentum sub-score blend: RSI level (primary) refined by MACD-histogram sign.
# The two weights sum to 1.0 so the momentum sub-score stays in [0, 1].
_MOM_RSI_WEIGHT: Final = 0.7
_MOM_MACD_WEIGHT: Final = 0.3

# ADX at/above which directional trend strength saturates (sub-score reaches its
# directional extreme). A convention we own and may re-tune, not a law.
_ADX_FULL: Final = 40.0

# Liquidity gate: latest-bar notional (close x volume) against a per-asset-class
# floor (ADR-0096 — crypto and equity dollar-volume scales differ). A name below
# its floor is flagged illiquid and its composite is capped at `_LIQUIDITY_FAIL_CAP`
# so it cannot rank as high-quality. Documented, tunable module constants,
# calibrated to a daily bar (finer timeframes carry less per-bar volume and will
# flag more readily — an honest conservatism, not a bug).
_EQUITY_MIN_NOTIONAL: Final = 1_000_000.0  # $/bar dollar-volume floor for equities
_CRYPTO_MIN_NOTIONAL: Final = 500_000.0  # $/bar dollar-volume floor for crypto
_LIQUIDITY_FAIL_CAP: Final = 49.0  # a gate-failing name cannot rank as "high quality"

# Crypto quote-currency markers for the liquidity gate's asset-class split. This is
# a *display/gate heuristic* over the symbol's shape, NOT the data-source routing
# authority (that is membership-based, in `default_provider._ohlcv_route`); it only
# picks which notional floor applies and never affects data provenance.
_CRYPTO_DASH_SUFFIXES: Final = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")
_CRYPTO_CONCAT_SUFFIXES: Final = ("USDT", "USDC", "BUSD", "FDUSD")


def _asset_class(symbol: str) -> Literal["crypto", "equity"]:
    """Classify a symbol as crypto or equity for the liquidity floor (ADR-0096).

    Crypto names carry a quote currency: a dashed pair (`BTC-USD`, `ETH-USDT`) or a
    Binance-style concatenated pair (`BTCUSDT`). Equities are bare tickers, which
    never end in a fiat/stable quote suffix. A gate heuristic only — see the module
    note; it never decides where bars are fetched."""

    s = symbol.upper()
    if any(s.endswith(suffix) for suffix in _CRYPTO_DASH_SUFFIXES):
        return "crypto"
    if "-" not in s and len(s) > 5 and any(s.endswith(q) for q in _CRYPTO_CONCAT_SUFFIXES):
        return "crypto"
    return "equity"


class _QualityFactors(BaseModel):
    """The four normalized [0, 1] factor sub-scores extracted from a snapshot —
    an internal intermediate, not a wire model. 1.0 = strongest bullish alignment /
    calmest volatility, 0.0 = weakest; the composite is their weighted sum x100."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trend: float = Field(ge=0.0, le=1.0)
    momentum: float = Field(ge=0.0, le=1.0)
    volume: float = Field(ge=0.0, le=1.0)
    volatility: float = Field(ge=0.0, le=1.0)


def _clip01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _extract_factors(snapshot: ConditionSnapshot) -> _QualityFactors | None:
    """Map a trailing condition snapshot to the four normalized factor sub-scores,
    or ``None`` when the history is too short to score (no defined RSI — the marker
    of insufficient bars, matching the sibling scanners' skip discipline).

    Trend: signed direction (up/side/down) scaled by ADX strength → higher for a
    stronger uptrend. Momentum: RSI level refined by MACD-histogram sign → higher
    for stronger up-momentum. Volume: the latest bar's *trailing* volume percentile
    → higher for more participation. Volatility: inverse of the *trailing* ATR
    percentile → higher (better) for a calmer, more orderly tape. Every input is
    read from `bars[0..=last]` via the snapshot, so the extraction is trailing."""

    ind = snapshot.indicators
    rsi = ind.get("rsi")
    if rsi is None:
        return None

    # Trend: signed directional strength, ADX-scaled, mapped from [-1, 1] to [0, 1].
    trend_dir = {Trend.UP: 1.0, Trend.SIDEWAYS: 0.0, Trend.DOWN: -1.0}[snapshot.trend]
    adx = ind.get("adx")
    adx_norm = _clip01(adx / _ADX_FULL) if adx is not None else 0.0
    trend_sub = (trend_dir * adx_norm + 1.0) / 2.0

    # Momentum: RSI level (0..100 → 0..1) blended with the MACD-histogram sign.
    macd_hist = ind.get("macd_hist")
    if macd_hist is None:
        macd_dir = 0.0
    else:
        macd_dir = 1.0 if macd_hist > 0 else (-1.0 if macd_hist < 0 else 0.0)
    momentum_sub = _MOM_RSI_WEIGHT * _clip01(rsi / 100.0) + _MOM_MACD_WEIGHT * (
        (macd_dir + 1.0) / 2.0
    )

    # Volume: trailing volume percentile (participation). Neutral 0.5 when undefined.
    vol_pct90 = ind.get("vol_pct90")
    volume_sub = _clip01(vol_pct90 / 100.0) if vol_pct90 is not None else 0.5

    # Volatility: inverse trailing ATR percentile — a calmer tape scores higher.
    atr_pct90 = ind.get("atr_pct90")
    volatility_sub = 1.0 - _clip01(atr_pct90 / 100.0) if atr_pct90 is not None else 0.5

    return _QualityFactors(
        trend=trend_sub,
        momentum=momentum_sub,
        volume=volume_sub,
        volatility=_clip01(volatility_sub),
    )


def composite_score(
    factors: _QualityFactors, *, liquidity_ok: bool
) -> tuple[float, dict[str, float]]:
    """The pure composite: weighted factor contributions summing to a 0..100 score.

    Each contribution is `weight * sub_score * 100`; the score is their sum. When
    `liquidity_ok` is `False` and the raw score exceeds `_LIQUIDITY_FAIL_CAP`, every
    contribution is scaled by the same factor so the score is capped at the cap
    **while the contributions still sum to it** (the decomposition invariant is
    preserved through the cap). Monotonic: raising any factor's sub-score raises its
    contribution and the composite (before the cap engages)."""

    contributions = {
        name: weight * getattr(factors, name) * 100.0 for name, weight in _WEIGHTS.items()
    }
    raw = sum(contributions.values())
    if not liquidity_ok and raw > _LIQUIDITY_FAIL_CAP:
        scale = _LIQUIDITY_FAIL_CAP / raw
        contributions = {name: value * scale for name, value in contributions.items()}
    return sum(contributions.values()), contributions


def score_quality(bars: Sequence[Bar], timeframe: str) -> QualityScore | _ScanSkip:
    """Score one symbol's composite technical quality from its trailing snapshot.

    Reuses `condition_snapshot` so trend / RSI / MACD / volume / ATR are defined in
    exactly one place (ADR-0023) — the rank can never disagree with `analyze_symbol`.
    Returns `SCAN_SKIP` when the history is too short to score (no defined RSI):
    scanned, uncomputable, routed into the scan's `skipped`, never a crash and never
    a match on a partial read. Trailing by construction — every input is
    `bars[0..=last]`. Conditions only (ADR-0096): the returned `QualityScore` carries
    no call-shaped field."""

    snapshot = condition_snapshot(bars, timeframe)
    factors = _extract_factors(snapshot)
    if factors is None:
        return SCAN_SKIP

    last = bars[-1]
    notional = last.close * last.volume
    asset_class = _asset_class(last.symbol)
    floor = _CRYPTO_MIN_NOTIONAL if asset_class == "crypto" else _EQUITY_MIN_NOTIONAL
    liquidity_ok = notional >= floor
    liquidity_note = (
        None
        if liquidity_ok
        else (
            f"thin: latest-bar notional ${notional:,.0f} below the "
            f"${floor:,.0f} {asset_class} floor; score capped at {_LIQUIDITY_FAIL_CAP:.0f}"
        )
    )

    score, contributions = composite_score(factors, liquidity_ok=liquidity_ok)
    return QualityScore(
        symbol=snapshot.symbol,
        score=score,
        factors=contributions,
        liquidity_ok=liquidity_ok,
        liquidity_note=liquidity_note,
    )


__all__ = ["composite_score", "score_quality"]
