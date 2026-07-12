"""Plan 0078 phase 1 — the convergence-screener core (ADR-0041 / ADR-0029).

Done-when claims pinned here, against a fixture of markets (a near-certain binary
near close, a thin-book near-certain, a multi-outcome ambiguous, a far-from-close
control, plus a below-confidence and a closed market to exercise both filters):

(a) the screener returns exactly the markets passing BOTH filters (time-to-close
    within the window AND top-outcome probability at/above the floor), ranked stably;
(b) the edge math (`implied_return_if_right = (1 - p) / p`) matches hand-computed
    values;
(c) `time_to_resolution` and `capital_lockup_note` are populated from `closes_at`;
(d) the thin-book market carries a `liquidity_caution` (and a deep-book market does
    not);
(e) the multi-outcome / ambiguous market carries an ELEVATED `resolution_risk` with
    its reason, and a clean binary carries the baseline `low`;
(f) the filters are configurable (loosening them admits the controls);
(g) a re-run with the same `now` is byte-identical (determinism);
(h) a word-boundary grep asserts NO output string carries buy/sell/act advice
    (the ADR-0029 pattern used by Plan 0041) — and a real polymarket.com URL is in
    the screened output, proving the provenance link carries no advice tokens;
(i) each opportunity copies the market's `market_url` provenance link through, present
    (as the URL, or null) in the JSON dump so the renderer mirror sees the key
    (Plan 0089).

All pure — no network, no wall-clock (the only time input is the injected `now`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.data.types import MarketOutcome, PredictionMarket
from market_analyser.prediction.convergence import (
    CAPITAL_LOCKUP_NOTE,
    ConvergenceParams,
    screen_convergence,
)
from market_analyser.prediction.models import ConvergenceOpportunity

_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _binary(prob: float) -> list[MarketOutcome]:
    """A binary market's outcomes with the near-certain side at `prob`."""
    return [
        MarketOutcome(label="Yes", implied_probability=round(1.0 - prob, 6)),
        MarketOutcome(label="No", implied_probability=prob),
    ]


def _market(
    *,
    market_id: str,
    question: str = "Will it rain tomorrow?",
    outcomes: list[MarketOutcome] | None = None,
    closed: bool = False,
    closes_in: timedelta | None = timedelta(days=3),
    volume_usd: float | None = 5_000_000.0,
    market_url: str | None = None,
) -> PredictionMarket:
    return PredictionMarket(
        market_id=market_id,
        question=question,
        outcomes=outcomes if outcomes is not None else _binary(0.95),
        closed=closed,
        closes_at=(_NOW + closes_in) if closes_in is not None else None,
        volume_usd=volume_usd,
        liquidity_usd=None,
        queried_at=_NOW,
        source="polymarket",
        market_url=market_url,
    )


# --- the plan's named fixture set ------------------------------------------------

NEAR_CERTAIN = _market(
    market_id="near",
    closes_in=timedelta(days=3),
    volume_usd=5_000_000.0,
    market_url="https://polymarket.com/event/will-it-rain-tomorrow",
)
THIN_BOOK = _market(
    market_id="thin",
    outcomes=_binary(0.96),
    closes_in=timedelta(days=2),
    volume_usd=10_000.0,  # < 50k default -> thin
)
MULTI_AMBIGUOUS = _market(
    market_id="multi",
    question="Which candidate wins the disputed election?",
    outcomes=[
        MarketOutcome(label="A", implied_probability=0.93),
        MarketOutcome(label="B", implied_probability=0.04),
        MarketOutcome(label="C", implied_probability=0.02),
        MarketOutcome(label="D", implied_probability=0.01),
    ],
    closes_in=timedelta(days=5),
    volume_usd=8_000.0,  # thin
)
FAR_FROM_CLOSE = _market(market_id="far", closes_in=timedelta(days=60), volume_usd=5_000_000.0)
LOW_CONFIDENCE = _market(market_id="low", outcomes=_binary(0.70), closes_in=timedelta(days=1))
CLOSED = _market(market_id="closed", closed=True, closes_in=timedelta(days=1))

ALL_MARKETS = [
    NEAR_CERTAIN,
    THIN_BOOK,
    MULTI_AMBIGUOUS,
    FAR_FROM_CLOSE,
    LOW_CONFIDENCE,
    CLOSED,
]


def _screen(markets: list[PredictionMarket], **overrides: object) -> list[ConvergenceOpportunity]:
    return screen_convergence(
        markets,
        params=ConvergenceParams(**overrides),
        now=_NOW,  # type: ignore[arg-type]
    )


# --- (a) both filters + stable ranking -------------------------------------------


def test_returns_exactly_the_markets_passing_both_filters_ranked() -> None:
    opportunities = _screen(ALL_MARKETS)

    # far-from-close (window), low-confidence (floor) and closed are all excluded.
    ids = [o.market_id for o in opportunities]
    assert set(ids) == {"near", "thin", "multi"}
    # Ranked by gross return descending: multi (0.075) > near (0.053) > thin (0.042).
    assert ids == ["multi", "near", "thin"]


# --- (b) edge math ---------------------------------------------------------------


def test_edge_math_matches_hand_computed_values() -> None:
    by_id = {o.market_id: o for o in _screen(ALL_MARKETS)}
    assert by_id["near"].implied_return_if_right == pytest.approx(0.05 / 0.95)
    assert by_id["thin"].implied_return_if_right == pytest.approx(0.04 / 0.96)
    assert by_id["multi"].implied_return_if_right == pytest.approx(0.07 / 0.93)
    # The near-certain outcome (max probability) is the one selected.
    assert by_id["near"].outcome_label == "No"
    assert by_id["near"].implied_probability == 0.95
    assert by_id["multi"].outcome_label == "A"


# --- (c) time-to-resolution + lockup from closes_at ------------------------------


def test_time_to_resolution_and_capital_lockup_populated() -> None:
    by_id = {o.market_id: o for o in _screen(ALL_MARKETS)}
    assert by_id["near"].time_to_resolution == timedelta(days=3)
    assert by_id["thin"].time_to_resolution == timedelta(days=2)
    assert by_id["near"].closes_at == _NOW + timedelta(days=3)
    # The lockup note is the fixed, non-empty labeled caveat on every opportunity.
    for opportunity in by_id.values():
        assert opportunity.capital_lockup_note == CAPITAL_LOCKUP_NOTE
        assert opportunity.capital_lockup_note.strip()


# --- (d) liquidity caution -------------------------------------------------------


def test_thin_book_carries_a_liquidity_caution_deep_book_does_not() -> None:
    by_id = {o.market_id: o for o in _screen(ALL_MARKETS)}
    assert by_id["thin"].liquidity_caution is not None
    assert "thin book" in by_id["thin"].liquidity_caution.lower()
    assert by_id["near"].liquidity_caution is None  # 5M volume -> deep


def test_unknown_volume_is_treated_as_thin_never_as_a_clean_book() -> None:
    unknown = _market(market_id="unknown", closes_in=timedelta(days=1), volume_usd=None)
    (opportunity,) = _screen([unknown])
    assert opportunity.liquidity_caution is not None
    assert "unknown" in opportunity.liquidity_caution.lower()
    # An absent number is honest uncertainty -> it also lifts resolution risk.
    assert opportunity.resolution_risk.level == "medium"


# --- (e) resolution-risk heuristic -----------------------------------------------


def test_multi_outcome_ambiguous_carries_elevated_resolution_risk_with_reason() -> None:
    by_id = {o.market_id: o for o in _screen(ALL_MARKETS)}
    risk = by_id["multi"].resolution_risk
    assert risk.level == "high"  # dispute wording + multi-outcome + thin
    assert risk.reasons  # non-empty
    joined = " ".join(risk.reasons).lower()
    assert "dispute" in joined  # the wording reason is spelled out
    assert "multi-outcome" in joined


def test_clean_binary_deep_book_carries_baseline_low_risk() -> None:
    by_id = {o.market_id: o for o in _screen(ALL_MARKETS)}
    risk = by_id["near"].resolution_risk
    assert risk.level == "low"
    # Even low carries the standing not-zero caveat, never a bare label.
    assert len(risk.reasons) == 1
    assert "never zero" in risk.reasons[0].lower()


def test_thin_only_binary_is_medium_risk() -> None:
    by_id = {o.market_id: o for o in _screen(ALL_MARKETS)}
    assert by_id["thin"].resolution_risk.level == "medium"


# --- (f) configurable filters ----------------------------------------------------


def test_loosening_the_filters_admits_the_controls() -> None:
    opportunities = _screen(
        ALL_MARKETS,
        max_time_to_close=timedelta(days=90),
        min_confidence=0.60,
    )
    ids = {o.market_id for o in opportunities}
    # far-from-close (now in the 90d window) and low-confidence (now above a 0.60
    # floor) join; the closed market is still excluded (it is not "near resolution").
    assert {"far", "low"} <= ids
    assert "closed" not in ids


# --- (g) determinism -------------------------------------------------------------


def test_rerun_with_same_now_is_byte_identical() -> None:
    first = [o.model_dump(mode="json") for o in _screen(ALL_MARKETS)]
    second = [o.model_dump(mode="json") for o in _screen(ALL_MARKETS)]
    assert first == second


# --- (h) no advice, structurally -------------------------------------------------


def test_output_carries_no_advice_language() -> None:
    """ADR-0029 / ADR-0041: opportunities are facts with risks attached, never a
    buy call. No direction/size/action field, no advice language in any string."""
    opportunities = _screen(ALL_MARKETS)
    blob = json.dumps([o.model_dump(mode="json") for o in opportunities]).lower()
    for token in (
        "recommend",
        "recommendation",
        "buy",
        "sell",
        "hold",
        "short",
        "conviction",
        "entry",
        "stop",
        "target",
        "should",
    ):
        assert not re.search(rf"\b{token}\b", blob), f"advice token {token!r} leaked"
    # And no direction/size/action field on the model.
    fields = set(opportunities[0].model_dump().keys())
    for forbidden in ("direction", "side", "size", "action", "signal"):
        assert forbidden not in fields
    # The screened output actually contains a real polymarket.com provenance URL, so
    # the grep above proved a market_url carries none of the advice tokens.
    assert "polymarket.com/event/" in blob


# --- (i) market_url provenance (Plan 0089) --------------------------------------


def test_opportunity_carries_market_url_from_the_market() -> None:
    (opportunity,) = _screen([NEAR_CERTAIN])
    assert opportunity.market_url == "https://polymarket.com/event/will-it-rain-tomorrow"
    # Serialized present (matching the other optional provenance fields' posture).
    dumped = opportunity.model_dump(mode="json")
    assert dumped["market_url"] == "https://polymarket.com/event/will-it-rain-tomorrow"


def test_opportunity_market_url_is_null_when_market_has_none() -> None:
    no_url = _market(market_id="nourl", closes_in=timedelta(days=1), market_url=None)
    (opportunity,) = _screen([no_url])
    assert opportunity.market_url is None
    # Present-as-null in the JSON dump (no exclude_none), so the renderer mirror sees
    # the key rather than a silent omission.
    assert opportunity.model_dump(mode="json")["market_url"] is None
