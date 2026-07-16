"""Plan 0099 phase 3 — the pure DeFi LP rebalance fusion (ADR-0029/0093).

Done-when claims pinned here:
(a) an out-of-range position context yields a labeled recommendation with a
    direction (recenter/widen/exit), a non-empty rationale, and a numeric
    basis;
(b) an in-range / healthy position yields an honest "hold — no action";
(c) missing on-chain detail yields "hold — insufficient basis", never a
    guessed direction;
plus the advisory-shape invariants: label is structurally "advisory", no
size/route/execution field can be expressed (extra="forbid"), and the
fusion is deterministic (same context + as_of → byte-identical dump).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.advisor.rebalance import (
    EXIT_MIN_EXCURSION_RATIO,
    WIDEN_MAX_EXCURSION_RATIO,
    LpPositionContext,
    RebalanceRecommendation,
    recommend_rebalance,
)

MASKED_WALLET = "0x1234…abcd"
POOL = "0x" + "cd" * 20
AS_OF = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)


def _context(**overrides: Any) -> LpPositionContext:
    kwargs: dict[str, Any] = {
        "wallet": MASKED_WALLET,
        "chain": "base",
        "pool_address": POOL,
        "nft_token_id": 42,
        "in_range": False,
        "tick_lower": -100,
        "tick_upper": 100,
        "current_tick": 150,
        "hours_out": 7.5,
        "dwell_hours": 6.0,
        "uncollected_fees": {"USDC": 1.25},
    }
    kwargs.update(overrides)
    return LpPositionContext(**kwargs)


class TestDirectionHeuristic:
    def test_shallow_excursion_recommends_widen(self) -> None:
        # width 200, 50 beyond the upper bound -> 0.25 range-widths (<= 0.5).
        rec = recommend_rebalance(_context(current_tick=150), as_of=AS_OF)
        assert rec.action == "widen"
        assert rec.label == "advisory"
        assert rec.rationale
        assert rec.basis["excursion_range_widths"] == pytest.approx(0.25)

    def test_moderate_excursion_recommends_recenter(self) -> None:
        # 200 beyond the upper bound -> 1.0 range-widths (0.5 < r <= 1.5).
        rec = recommend_rebalance(_context(current_tick=300), as_of=AS_OF)
        assert rec.action == "recenter"
        assert any("impermanent loss" in line for line in rec.rationale)

    def test_deep_excursion_recommends_exit(self) -> None:
        # 400 beyond the upper bound -> 2.0 range-widths (> 1.5).
        rec = recommend_rebalance(_context(current_tick=500), as_of=AS_OF)
        assert rec.action == "exit"

    def test_below_range_side_is_symmetric(self) -> None:
        # 50 below the lower bound -> 0.25 range-widths, widen; rationale says below.
        rec = recommend_rebalance(_context(current_tick=-150), as_of=AS_OF)
        assert rec.action == "widen"
        assert any("below" in line for line in rec.rationale)

    def test_thresholds_are_inclusive_boundaries(self) -> None:
        width = 200
        at_widen_max = 100 + int(WIDEN_MAX_EXCURSION_RATIO * width)
        assert recommend_rebalance(_context(current_tick=at_widen_max), as_of=AS_OF).action == (
            "widen"
        )
        at_exit_min = 100 + int(EXIT_MIN_EXCURSION_RATIO * width)
        assert recommend_rebalance(_context(current_tick=at_exit_min), as_of=AS_OF).action == (
            "recenter"
        )


class TestHonestHolds:
    def test_in_range_position_yields_hold_no_action(self) -> None:
        rec = recommend_rebalance(
            _context(
                in_range=True,
                tick_lower=None,
                tick_upper=None,
                current_tick=None,
                hours_out=None,
                uncollected_fees=None,
            ),
            as_of=AS_OF,
        )
        assert rec.action == "hold"
        assert any("no action" in line for line in rec.rationale)

    def test_out_of_range_without_tick_detail_yields_hold_insufficient_basis(self) -> None:
        rec = recommend_rebalance(
            _context(tick_lower=None, tick_upper=None, current_tick=None),
            as_of=AS_OF,
        )
        assert rec.action == "hold"
        assert any("insufficient basis" in line for line in rec.rationale)


class TestAdvisoryShape:
    def test_basis_carries_the_facts(self) -> None:
        rec = recommend_rebalance(_context(), as_of=AS_OF)
        assert rec.basis["hours_out"] == 7.5
        assert rec.basis["dwell_hours"] == 6.0
        assert rec.basis["tick_lower"] == -100
        assert rec.basis["uncollected_fee_USDC"] == 1.25

    def test_label_is_structurally_advisory(self) -> None:
        with pytest.raises(ValidationError):
            RebalanceRecommendation(
                wallet=MASKED_WALLET,
                chain="base",
                pool_address=POOL,
                nft_token_id=None,
                action="exit",
                rationale=["r"],
                basis={"in_range": False},
                label="executed",  # type: ignore[arg-type]
                as_of=AS_OF,
            )

    def test_no_execution_shaped_field_can_be_expressed(self) -> None:
        for field in ("size", "amount", "route", "transaction", "calldata", "slippage"):
            with pytest.raises(ValidationError):
                RebalanceRecommendation(
                    wallet=MASKED_WALLET,
                    chain="base",
                    pool_address=POOL,
                    nft_token_id=None,
                    action="exit",
                    rationale=["r"],
                    basis={"in_range": False},
                    label="advisory",
                    as_of=AS_OF,
                    **{field: 1},
                )

    def test_empty_rationale_and_empty_basis_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="rationale"):
            RebalanceRecommendation(
                wallet=MASKED_WALLET,
                chain="base",
                pool_address=POOL,
                nft_token_id=None,
                action="hold",
                rationale=[],
                basis={"in_range": True},
                label="advisory",
                as_of=AS_OF,
            )
        with pytest.raises(ValidationError, match="basis"):
            RebalanceRecommendation(
                wallet=MASKED_WALLET,
                chain="base",
                pool_address=POOL,
                nft_token_id=None,
                action="hold",
                rationale=["r"],
                basis={},
                label="advisory",
                as_of=AS_OF,
            )

    def test_deterministic_dump(self) -> None:
        first = recommend_rebalance(_context(), as_of=AS_OF)
        second = recommend_rebalance(_context(), as_of=AS_OF)
        assert first.model_dump_json() == second.model_dump_json()


class TestContextBoundary:
    def test_partial_tick_detail_rejected(self) -> None:
        with pytest.raises(ValidationError, match="all present or all absent"):
            _context(tick_lower=None)

    def test_inverted_ticks_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tick_lower"):
            _context(tick_lower=100, tick_upper=-100)
