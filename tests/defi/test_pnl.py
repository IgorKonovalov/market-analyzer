"""Plan 0035 phase 6 done-when: the average-cost replay engine.

Pinned claims:
(a) a hand-built multi-event position (deposit → partial withdraw → fee
    claim) yields the hand-computed average-cost realized + unrealized
    figures — worked below, every number derived on paper first;
(b) a position with a missing block-time price comes back `incomplete` with
    the offending leg named, and its realized figure is None — asserted NOT
    `0.0`;
(c) an LP position reports a vs-HODL delta;
(d) re-running the engine on the same cached inputs is byte-identical
    (`model_dump_json` equality — no provenance inside the engine, nothing
    to exclude);
(e) unbooked kinds (swap / liquidation / unclassified) and any incomplete
    position force honest `None` wallet totals, never a partial sum.

Hand-worked case (a):
  ts1: add 0.2 WETH @ 3500 + 700 USDC @ 1        → basis 1400
  ts2: remove 0.1 WETH @ 4000 + 350 USDC @ 1     → extracted 750;
       holdings before = 0.2*4000 + 700*1 = 1500 → f = 0.5, released 700
       realized += 750 - 700 = 50; basis 700
  ts3: fee claim 20 USDC @ 1                     → realized 70
  current usd_value 800                          → unrealized 100
  as_of: WETH 4200, USDC 1 → HODL(0.1, 350) = 770
       vs_hodl = (800 + 20) - 770 = 50
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.data.adapters.defillama import token_key
from market_analyser.defi.models import Chain, DefiPosition, PositionToken
from market_analyser.defi.pnl import compute_wallet_pnl
from market_analyser.defi.pnl_events import PositionEvent
from market_analyser.defi.tx_models import TxTransfer

_WALLET = "0x2222222222222222222222222222222222222222"
_WETH = "0x4200000000000000000000000000000000000006"
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_GHST = "0xcd2f22236dd9dfe2356d7c543161d4d260fd9bcb"

_TS1 = datetime(2025, 1, 1, tzinfo=UTC)
_TS2 = datetime(2025, 2, 1, tzinfo=UTC)
_TS3 = datetime(2025, 3, 1, tzinfo=UTC)
_AS_OF = datetime(2025, 4, 1, tzinfo=UTC)


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


class _TablePriceSource:
    """Deterministic price table keyed (token_key, ts); absent = no coverage."""

    def __init__(self, table: dict[tuple[str, int], float]) -> None:
        self._table = table

    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return self._table.get((token_key(chain, address), ts))


_PRICES = _TablePriceSource(
    {
        (f"base:{_WETH}", _epoch(_TS1)): 3500.0,
        (f"base:{_USDC}", _epoch(_TS1)): 1.0,
        (f"base:{_WETH}", _epoch(_TS2)): 4000.0,
        (f"base:{_USDC}", _epoch(_TS2)): 1.0,
        (f"base:{_USDC}", _epoch(_TS3)): 1.0,
        (f"base:{_WETH}", _epoch(_AS_OF)): 4200.0,
        (f"base:{_USDC}", _epoch(_AS_OF)): 1.0,
    }
)

_LP = DefiPosition(
    position_id="base:aerodrome:lp-1",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="WETH", address=_WETH, amount=0.1),
        PositionToken(symbol="USDC", address=_USDC, amount=350.0),
    ],
    usd_value=800.0,
    pool="WETH / USDC",
    pool_address="0xpool0000000000000000000000000000000000001",
)


def _event(
    kind: str,
    position: DefiPosition,
    mined_at: datetime,
    block: int,
    legs: list[dict[str, Any]],
) -> PositionEvent:
    return PositionEvent(
        kind=kind,  # type: ignore[arg-type]  # tests exercise the closed Literal directly
        position_id=position.position_id,
        chain=position.chain,
        tx_hash=f"0x{kind}-{block}",
        mined_at=mined_at,
        block=block,
        in_block_index=0,
        legs=[TxTransfer.model_validate(leg) for leg in legs],
    )


def _leg(direction: str, symbol: str, address: str | None, amount: float) -> dict[str, Any]:
    return {"direction": direction, "symbol": symbol, "address": address, "amount": amount}


_LP_EVENTS = [
    _event(
        "add_liquidity",
        _LP,
        _TS1,
        100,
        [_leg("out", "WETH", _WETH, 0.2), _leg("out", "USDC", _USDC, 700.0)],
    ),
    _event(
        "remove_liquidity",
        _LP,
        _TS2,
        200,
        [_leg("in", "WETH", _WETH, 0.1), _leg("in", "USDC", _USDC, 350.0)],
    ),
    _event("fee_claim", _LP, _TS3, 300, [_leg("in", "USDC", _USDC, 20.0)]),
]


def _lp_pnl() -> Any:
    return compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=_LP_EVENTS,
        price_source=_PRICES,
        as_of=_AS_OF,
    )


def test_hand_computed_average_cost_figures() -> None:
    result = _lp_pnl()
    position = result.positions[0]
    assert position.incomplete is False
    assert position.realized_usd == 70.0
    assert position.cost_basis_usd == 700.0
    assert position.unrealized_usd == 100.0
    assert result.realized_usd == 70.0
    assert result.unrealized_usd == 100.0
    assert result.incomplete is False


def test_lp_position_reports_vs_hodl_delta() -> None:
    position = _lp_pnl().positions[0]
    assert position.vs_hodl_usd == 50.0


def test_wallet_is_masked_in_the_result() -> None:
    result = _lp_pnl()
    assert result.wallet != _WALLET
    assert "…" in result.wallet


def test_missing_price_marks_the_position_incomplete_with_the_leg_named() -> None:
    events = [
        *_LP_EVENTS,
        _event("fee_claim", _LP, _TS3, 400, [_leg("in", "GHST", _GHST, 5.0)]),
    ]
    result = compute_wallet_pnl(
        wallet=_WALLET, positions=[_LP], events=events, price_source=_PRICES, as_of=_AS_OF
    )
    position = result.positions[0]
    assert position.incomplete is True
    assert position.realized_usd is None, "a missing price must never appear as 0.0"
    assert position.unrealized_usd is None
    assert position.cost_basis_usd is None
    assert any(f"base:{_GHST}" in note and str(_epoch(_TS3)) in note for note in position.notes)
    # The wallet total is honest too: None, not a partial sum.
    assert result.incomplete is True
    assert result.realized_usd is None
    assert result.unrealized_usd is None


def test_unclassified_event_marks_the_position_incomplete() -> None:
    events = [
        *_LP_EVENTS,
        _event("unclassified", _LP, _TS3, 500, [_leg("in", "USDC", _USDC, 1.0)]),
    ]
    result = compute_wallet_pnl(
        wallet=_WALLET, positions=[_LP], events=events, price_source=_PRICES, as_of=_AS_OF
    )
    position = result.positions[0]
    assert position.incomplete is True
    assert any("unclassified" in note for note in position.notes)


def test_swap_and_liquidation_are_unbooked_in_v1_not_invented() -> None:
    events = [
        *_LP_EVENTS,
        _event(
            "swap",
            _LP,
            _TS3,
            600,
            [_leg("out", "USDC", _USDC, 10.0), _leg("in", "WETH", _WETH, 0.002)],
        ),
    ]
    result = compute_wallet_pnl(
        wallet=_WALLET, positions=[_LP], events=events, price_source=_PRICES, as_of=_AS_OF
    )
    assert result.positions[0].incomplete is True
    assert any("unbooked swap" in note for note in result.positions[0].notes)


def test_borrow_and_repay_realize_interest_as_the_debt_closes() -> None:
    borrow_position = DefiPosition(
        position_id="base:aave-v3:borrow-usdc",
        chain="base",
        protocol="aave-v3",
        kind="lending_borrow",
        tokens=[PositionToken(symbol="USDC", address=_USDC, amount=1.0)],
        usd_value=0.0,
        pool_address="0xaave000000000000000000000000000000000002",
    )
    events = [
        _event("borrow", borrow_position, _TS1, 100, [_leg("in", "USDC", _USDC, 100.0)]),
        _event("repay", borrow_position, _TS2, 200, [_leg("out", "USDC", _USDC, 105.0)]),
    ]
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[borrow_position],
        events=events,
        price_source=_PRICES,
        as_of=_AS_OF,
    )
    position = result.positions[0]
    assert position.incomplete is False
    assert position.realized_usd == -5.0, "borrowed 100, repaid 105: the -5 is interest paid"
    assert position.cost_basis_usd == 0.0
    assert position.unrealized_usd == 0.0
    assert position.vs_hodl_usd is None, "vs-HODL is an LP-only fact"


def test_rerun_on_the_same_inputs_is_byte_identical() -> None:
    first = _lp_pnl()
    second = _lp_pnl()
    assert first.model_dump_json() == second.model_dump_json()


def test_position_without_events_has_full_value_as_unrealized() -> None:
    """No history reconstructable (but nothing contradictory either): zero
    basis, so the whole current value is unrealized gain over nothing."""
    result = compute_wallet_pnl(
        wallet=_WALLET, positions=[_LP], events=[], price_source=_PRICES, as_of=_AS_OF
    )
    position = result.positions[0]
    assert position.incomplete is False
    assert position.realized_usd == 0.0
    assert position.cost_basis_usd == 0.0
    assert position.unrealized_usd == 800.0
