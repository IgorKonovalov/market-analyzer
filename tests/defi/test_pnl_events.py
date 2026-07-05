"""Plan 0035 phase 5 done-when: the `DecodedTx` → `PositionEvent` taxonomy map.

Representative fixtures map to the right kinds (an Aerodrome add-liquidity, a
reward claim, a swap, a lending borrow/repay); an unrecognized shape yields
exactly one `unclassified` event — not a drop, not a crash; the mapping is
pure and deterministic (same inputs → same events, twice); joins are
precision-first (act contract, then a single-candidate token fallback — an
ambiguous fallback joins nothing).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.defi.models import DefiPosition, PositionToken
from market_analyser.defi.pnl_events import map_events
from market_analyser.defi.tx_models import DecodedTx

_POOL = "0xAER0dr0mePool000000000000000000000000001"
_AAVE = "0xAavePool00000000000000000000000000000002"
_WETH = "0x4200000000000000000000000000000000000006"
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_AERO = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"

_LP_POSITION = DefiPosition(
    position_id="base:aerodrome:lp-1",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="WETH", address=_WETH, amount=0.5),
        PositionToken(symbol="USDC", address=_USDC, amount=1500.0),
    ],
    usd_value=3200.0,
    pool="WETH / USDC",
    pool_address=_POOL,
)

_LENDING_POSITION = DefiPosition(
    position_id="base:aave-v3:supply-usdc",
    chain="base",
    protocol="aave-v3",
    kind="lending_supply",
    tokens=[PositionToken(symbol="USDC", address=_USDC, amount=1000.0)],
    usd_value=1000.0,
    pool_address=_AAVE,
)

_POSITIONS = [_LP_POSITION, _LENDING_POSITION]


def _tx(
    tx_hash: str,
    operation_type: str,
    *,
    block: int = 100,
    index: int = 0,
    contract: str | None = _POOL,
    act_type: str = "execute",
    method: str | None = None,
    transfers: list[dict[str, Any]] | None = None,
    status: str = "confirmed",
) -> DecodedTx:
    acts = []
    if contract is not None:
        acts.append(
            {
                "act_id": f"{tx_hash}-act",
                "type": act_type,
                "contract_address": contract,
                "method_name": method,
            }
        )
    return DecodedTx.model_validate(
        {
            "chain": "base",
            "hash": tx_hash,
            "operation_type": operation_type,
            "mined_at": datetime(2025, 9, 1, tzinfo=UTC),
            "mined_at_block": block,
            "in_block_index": index,
            "status": status,
            "transfers": transfers if transfers is not None else [],
            "acts": acts,
        }
    )


def _leg(direction: str, symbol: str, address: str | None, amount: float) -> dict[str, Any]:
    return {"direction": direction, "symbol": symbol, "address": address, "amount": amount}


def test_aerodrome_deposit_maps_to_add_liquidity() -> None:
    tx = _tx(
        "0xadd",
        "deposit",
        transfers=[_leg("out", "WETH", _WETH, 0.2), _leg("out", "USDC", _USDC, 700.0)],
    )
    events = map_events([tx], _POSITIONS)
    assert len(events) == 1
    assert events[0].kind == "add_liquidity"
    assert events[0].position_id == _LP_POSITION.position_id
    assert [leg.symbol for leg in events[0].legs] == ["WETH", "USDC"]


def test_lp_withdraw_maps_to_remove_liquidity() -> None:
    tx = _tx(
        "0xrm",
        "withdraw",
        transfers=[_leg("in", "WETH", _WETH, 0.2), _leg("in", "USDC", _USDC, 700.0)],
    )
    assert map_events([tx], _POSITIONS)[0].kind == "remove_liquidity"


def test_lending_deposit_and_withdraw_map_to_supply_kinds() -> None:
    deposit = _tx("0xsup", "deposit", contract=_AAVE, transfers=[_leg("out", "USDC", _USDC, 500.0)])
    withdraw = _tx(
        "0xwsup", "withdraw", contract=_AAVE, transfers=[_leg("in", "USDC", _USDC, 500.0)]
    )
    events = map_events([deposit, withdraw], _POSITIONS)
    assert [e.kind for e in events] == ["supply", "withdraw_supply"]
    assert {e.position_id for e in events} == {_LENDING_POSITION.position_id}


def test_lending_borrow_and_repay_map_directly() -> None:
    borrow = _tx("0xbor", "borrow", contract=_AAVE, transfers=[_leg("in", "USDC", _USDC, 200.0)])
    repay = _tx("0xrep", "repay", contract=_AAVE, transfers=[_leg("out", "USDC", _USDC, 200.0)])
    events = map_events([borrow, repay], _POSITIONS)
    assert [e.kind for e in events] == ["borrow", "repay"]


def test_trade_against_the_pool_maps_to_swap() -> None:
    tx = _tx(
        "0xswp",
        "trade",
        transfers=[_leg("out", "USDC", _USDC, 100.0), _leg("in", "WETH", _WETH, 0.028)],
    )
    assert map_events([tx], _POSITIONS)[0].kind == "swap"


def test_emissions_claim_maps_to_reward_claim() -> None:
    """An AERO stream to a WETH/USDC gauge position: inbound token from outside
    the pool set → reward, via the getReward method hint."""
    tx = _tx(
        "0xrew",
        "execute",
        method="getReward",
        transfers=[_leg("in", "AERO", _AERO, 12.5)],
    )
    event = map_events([tx], _POSITIONS)[0]
    assert event.kind == "reward_claim"
    assert event.position_id == _LP_POSITION.position_id


def test_inbound_non_pool_token_without_hints_is_still_a_reward_claim() -> None:
    tx = _tx("0xrw2", "receive", transfers=[_leg("in", "AERO", _AERO, 3.0)])
    assert map_events([tx], _POSITIONS)[0].kind == "reward_claim"


def test_collect_of_pool_tokens_maps_to_fee_claim() -> None:
    tx = _tx(
        "0xfee",
        "execute",
        method="collect",
        transfers=[_leg("in", "WETH", _WETH, 0.01), _leg("in", "USDC", _USDC, 35.0)],
    )
    assert map_events([tx], _POSITIONS)[0].kind == "fee_claim"


def test_inbound_pool_tokens_without_hints_are_a_fee_claim() -> None:
    tx = _tx(
        "0xfe2",
        "receive",
        transfers=[_leg("in", "USDC", _USDC, 12.0)],
    )
    assert map_events([tx], _POSITIONS)[0].kind == "fee_claim"


def test_liquidation_call_maps_to_liquidation() -> None:
    tx = _tx(
        "0xliq",
        "execute",
        contract=_AAVE,
        method="liquidationCall",
        transfers=[_leg("out", "USDC", _USDC, 400.0)],
    )
    assert map_events([tx], _POSITIONS)[0].kind == "liquidation"


def test_unrecognized_shape_yields_exactly_one_unclassified_event() -> None:
    """A mixed-direction zap through the pool contract fits no taxonomy kind:
    it must surface as exactly one `unclassified` event — never a silent drop,
    never a crash."""
    tx = _tx(
        "0xzap",
        "unknown",
        act_type="zap",
        transfers=[_leg("out", "USDC", _USDC, 50.0), _leg("in", "WETH", _WETH, 0.01)],
    )
    events = map_events([tx], _POSITIONS)
    assert [e.kind for e in events] == ["unclassified"]
    assert events[0].tx_hash == "0xzap"


def test_transaction_joining_no_position_produces_no_event() -> None:
    tx = _tx(
        "0xother",
        "trade",
        contract="0xSomeUnrelatedRouter0000000000000000000009",
        transfers=[_leg("out", "DAI", "0xdai0000000000000000000000000000000000001", 10.0)],
    )
    assert map_events([tx], _POSITIONS) == []


def test_token_fallback_joins_only_a_single_unambiguous_candidate() -> None:
    # WETH moves: only the LP position holds WETH → unambiguous join, no act
    # contract needed.
    weth_only = _tx("0xtf1", "deposit", contract=None, transfers=[_leg("out", "WETH", _WETH, 0.1)])
    events = map_events([weth_only], _POSITIONS)
    assert len(events) == 1
    assert events[0].position_id == _LP_POSITION.position_id
    # USDC moves: BOTH positions hold USDC → ambiguous, joins nothing.
    usdc_only = _tx("0xtf2", "deposit", contract=None, transfers=[_leg("out", "USDC", _USDC, 10.0)])
    assert map_events([usdc_only], _POSITIONS) == []


def test_failed_transaction_is_skipped() -> None:
    tx = _tx(
        "0xfail",
        "deposit",
        status="failed",
        transfers=[_leg("out", "USDC", _USDC, 10.0)],
    )
    assert map_events([tx], _POSITIONS) == []


def test_mapping_is_pure_and_deterministic() -> None:
    txs = [
        _tx("0xadd", "deposit", transfers=[_leg("out", "WETH", _WETH, 0.2)]),
        _tx(
            "0xrew",
            "execute",
            block=101,
            method="getReward",
            transfers=[_leg("in", "AERO", _AERO, 1.0)],
        ),
        _tx(
            "0xzap",
            "unknown",
            block=102,
            act_type="zap",
            transfers=[_leg("in", "AERO", _AERO, 1.0), _leg("out", "USDC", _USDC, 5.0)],
        ),
    ]
    first = map_events(txs, _POSITIONS)
    second = map_events(txs, _POSITIONS)
    assert first == second
    assert [e.tx_hash for e in first] == ["0xadd", "0xrew", "0xzap"], "input order preserved"
    assert [e.kind for e in first] == ["add_liquidity", "reward_claim", "unclassified"]


def test_event_carries_the_pricing_coordinates() -> None:
    """The engine prices legs at block time: every event must carry mined_at,
    block, in-block index, and chain."""
    tx = _tx("0xadd", "deposit", block=123, index=2, transfers=[_leg("out", "WETH", _WETH, 0.2)])
    event = map_events([tx], _POSITIONS)[0]
    assert event.mined_at == datetime(2025, 9, 1, tzinfo=UTC)
    assert (event.block, event.in_block_index, event.chain) == (123, 2, "base")
