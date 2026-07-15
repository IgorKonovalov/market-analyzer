"""Phase-1 done-when for Plan 0101: the composite quality scorer (ADR-0096).

Pins the five properties the plan calls out:
  (a) the factor contributions sum to the composite (liquid and capped alike);
  (b) monotonicity — a strictly better trend / momentum sub-score raises that
      factor's contribution and the composite;
  (c) the liquidity gate flags a thin name and caps its score;
  (d) no-lookahead via truncation-invariance — the as-of-k reading ignores bars
      after k, yet the tail genuinely changes the as-of-end reading;
  (e) the result model carries NO call-shaped field (the ADR-0029 boundary guard).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis.quality import (
    _LIQUIDITY_FAIL_CAP,
    _asset_class,
    _QualityFactors,
    composite_score,
    score_quality,
)
from market_analyser.analysis.types import QualityScore
from market_analyser.data.types import Bar

_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _bars(symbol: str, closes: Sequence[float], *, volume: float = 1_000_000.0) -> list[Bar]:
    """Daily bars ending today from an explicit close series; high/low bracket the
    close by +/-0.5 so ADX/ATR are well-defined. Volume defaults high so the
    liquidity gate passes unless a test deliberately lowers it."""

    n = len(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=closes[i] + 0.5,
            low=closes[i] - 0.5,
            close=closes[i],
            volume=volume,
            source="fixture",
        )
        for i in range(n)
    ]


def _uptrend(symbol: str, n: int = 160, *, volume: float = 1_000_000.0) -> list[Bar]:
    """Monotonic rise -> RSI high, trend up (a high-quality-looking setup)."""

    return _bars(symbol, [100.0 + i for i in range(n)], volume=volume)


# --- (a) contributions sum to the composite --------------------------------- #


def test_factor_contributions_sum_to_the_composite_when_liquid() -> None:
    qs = score_quality(_uptrend("AAA"), "1d")
    assert isinstance(qs, QualityScore)
    assert qs.liquidity_ok
    assert set(qs.factors) == {"trend", "momentum", "volume", "volatility"}
    assert sum(qs.factors.values()) == pytest.approx(qs.score)


def test_factor_contributions_sum_to_the_composite_when_capped() -> None:
    # A strong setup but a thin book -> the gate caps the score; the decomposition
    # invariant must survive the cap (contributions scaled, still summing to score).
    qs = score_quality(_uptrend("BBB", volume=1.0), "1d")
    assert isinstance(qs, QualityScore)
    assert not qs.liquidity_ok
    assert sum(qs.factors.values()) == pytest.approx(qs.score)
    assert qs.score <= _LIQUIDITY_FAIL_CAP + 1e-9


# --- (b) monotonicity ------------------------------------------------------- #


def test_composite_is_monotonic_in_trend_and_momentum() -> None:
    base = _QualityFactors(trend=0.4, momentum=0.4, volume=0.4, volatility=0.4)
    base_score, base_contrib = composite_score(base, liquidity_ok=True)

    better_trend = base.model_copy(update={"trend": 0.9})
    t_score, t_contrib = composite_score(better_trend, liquidity_ok=True)
    assert t_contrib["trend"] > base_contrib["trend"]
    assert t_score > base_score

    better_momentum = base.model_copy(update={"momentum": 0.9})
    m_score, m_contrib = composite_score(better_momentum, liquidity_ok=True)
    assert m_contrib["momentum"] > base_contrib["momentum"]
    assert m_score > base_score


def test_extracted_trend_factor_orders_uptrend_above_downtrend() -> None:
    # Extraction-level sanity: a clean uptrend earns a higher trend contribution
    # than a clean downtrend (a strictly better trend input -> higher contribution).
    up = score_quality(_uptrend("UP"), "1d")
    down = score_quality(_bars("DOWN", [260.0 - i for i in range(160)]), "1d")
    assert isinstance(up, QualityScore) and isinstance(down, QualityScore)
    assert up.factors["trend"] > down.factors["trend"]


# --- (c) liquidity gate ----------------------------------------------------- #


def test_liquidity_gate_flags_and_caps_a_thin_name() -> None:
    liquid = score_quality(_uptrend("LIQ", volume=1_000_000.0), "1d")
    thin = score_quality(_uptrend("THIN", volume=1.0), "1d")
    assert isinstance(liquid, QualityScore) and isinstance(thin, QualityScore)

    assert liquid.liquidity_ok
    assert liquid.liquidity_note is None
    assert liquid.score > _LIQUIDITY_FAIL_CAP  # the same setup, liquid, ranks high

    assert not thin.liquidity_ok
    assert thin.liquidity_note is not None
    assert thin.score <= _LIQUIDITY_FAIL_CAP + 1e-9  # ...but capped when thin
    assert thin.score < liquid.score


def test_asset_class_selects_the_per_class_floor() -> None:
    assert _asset_class("AAPL") == "equity"
    assert _asset_class("BTC-USD") == "crypto"
    assert _asset_class("BTCUSDT") == "crypto"
    # A crypto name whose notional clears the (lower) crypto floor but would miss
    # the equity floor is liquidity_ok — proof the per-class split is applied.
    crypto = score_quality(_bars("ETH-USD", [100.0 + i for i in range(160)], volume=7_000.0), "1d")
    assert isinstance(crypto, QualityScore)
    assert crypto.liquidity_ok  # 7_000 * ~260 ~= $1.8M > $500k crypto floor


# --- (d) no-lookahead / truncation-invariance ------------------------------- #


def test_score_quality_is_trailing_no_lookahead() -> None:
    prefix_closes = [100.0 + i for i in range(131)]  # bars 0..130, a clean uptrend
    reversal = [prefix_closes[-1] - 5.0 * i for i in range(1, 31)]  # sharp drop after k
    full = _bars("X", prefix_closes + reversal)
    k = 130

    as_of_k = score_quality(full[: k + 1], "1d")
    standalone_prefix = score_quality(_bars("X", prefix_closes), "1d")
    # The as-of-k reading is a function of bars[0..k] only: it equals the score of
    # the standalone prefix, unaffected by the reversal tail that follows bar k.
    assert as_of_k == standalone_prefix

    # ...and the tail is genuinely read at as-of-end (guards against a scorer that
    # ignores its input tail, which would make the equality above vacuous).
    at_end = score_quality(full, "1d")
    assert at_end != as_of_k


# --- (e) no call-shaped field ---------------------------------------------- #


def test_result_model_has_no_call_shaped_field() -> None:
    fields = set(QualityScore.model_fields)
    for forbidden in (
        "action",
        "signal",
        "recommendation",
        "grade",
        "buy",
        "sell",
        "direction",
        "conviction",
        "entry",
        "stop",
        "target",
    ):
        assert forbidden not in fields, f"call-shaped field {forbidden!r} on QualityScore"


def test_result_model_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        QualityScore(
            symbol="X",
            score=50.0,
            factors={},
            liquidity_ok=True,
            grade="Strong",  # type: ignore[call-arg]
        )


def test_serialized_output_carries_no_advice_language() -> None:
    qs = score_quality(_uptrend("X"), "1d")
    assert isinstance(qs, QualityScore)
    blob = json.dumps(qs.model_dump(mode="json")).lower()
    for token in (
        "recommend",
        "buy",
        "sell",
        "short",
        "hold",
        "action",
        "grade",
        "conviction",
        "entry",
        "stop",
        "target",
        "should",
    ):
        assert not re.search(rf"\b{token}\b", blob), f"advice token {token!r} leaked"
