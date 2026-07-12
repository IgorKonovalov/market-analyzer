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
_AAVE_TOKEN = "0x63706e401c06ac8513145b7687a14804d17f814b"  # the AAVE ERC-20 on Base

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


def test_approval_moving_no_assets_is_skipped() -> None:
    """Plan 0084 phase-6 regression: an ERC-20 approve (no transfers) whose contract
    is a position's pool/gauge must NOT join and surface as a spurious `unclassified`
    that nulls the position — it moves no assets, so it is skipped like a failed tx."""
    approve = _tx("0xapprove", "approve", method="Approve", transfers=[])
    assert map_events([approve], _POSITIONS) == []


def test_bare_send_of_a_position_token_is_a_custody_move() -> None:
    """Plan 0087 / ADR-0081: a plain outbound transfer of a single position token
    (operation_type `send`, one leg, no lifecycle method) is a wallet-to-wallet
    custody move — booked as a no-op `custody_move`, not a spurious `unclassified`
    that nulls the position. Motivating live cases: the wallet's two residual
    *unclassified* transfers `0x1cbbb89c…` (pos `87f522…`) and `0x303f8366…` (pos
    `37023f…`) are outbound sends (an inbound receive is never unclassified — see
    the inbound-claim tests above); raw payloads live in the wallet's tx cache,
    not the repo, so this pins the shape."""
    for tx_hash in ("0x303f8366", "0x1cbbb89c"):
        send = _tx(tx_hash, "send", contract=None, transfers=[_leg("out", "WETH", _WETH, 0.25)])
        events = map_events([send], _POSITIONS)
        assert len(events) == 1
        assert events[0].kind == "custody_move"
        assert events[0].position_id == _LP_POSITION.position_id


def test_bare_receive_keeps_its_plan_0035_claim_classification() -> None:
    """The custody shortcut is scoped to outbound `send`: a bare inbound `receive`
    of a single position token is unchanged — a pool-token receipt stays a
    `fee_claim` (Plan 0035), never reclassified to a no-op custody move. This pins
    the narrowing (send-only) that avoids reversing the inbound-claim heuristic."""
    receive = _tx("0xrecv", "receive", transfers=[_leg("in", "USDC", _USDC, 12.0)])
    assert map_events([receive], _POSITIONS)[0].kind == "fee_claim"


def test_two_transfer_send_is_not_a_custody_move() -> None:
    """The single-leg requirement keeps a two-token movement (e.g. a plain
    withdrawal of both pool legs) out of the custody shortcut — it stays an honest
    `unclassified`, never silently a no-op."""
    send = _tx(
        "0xsend2",
        "send",
        contract=None,
        transfers=[_leg("out", "WETH", _WETH, 0.25), _leg("out", "USDC", _USDC, 750.0)],
    )
    events = map_events([send], _POSITIONS)
    assert len(events) == 1
    assert events[0].kind == "unclassified"


def test_send_carrying_a_lifecycle_hint_is_not_a_custody_move() -> None:
    """A lifecycle hint blocks the custody shortcut: a `send` bearing an `unstake`
    method is not a plain custody move, so it stays an honest `unclassified` rather
    than being silently booked as a no-op — precision over the default."""
    send = _tx(
        "0xsend-unstake",
        "send",
        method="unstake",
        transfers=[_leg("out", "WETH", _WETH, 0.25)],
    )
    assert map_events([send], _POSITIONS)[0].kind != "custody_move"


def test_aggregator_execute_swap_with_a_trade_act_is_a_swap() -> None:
    """Plan 0087 smoke follow-up: an aggregator/router-routed swap arrives as
    operation_type "execute" (NOT "trade") but carries a "trade" act and a clean
    two-sided transfer — recognized as `swap` despite the op_type. Motivating live
    case: 0x1cbbb89c… on position 87f522… (WETH out 1.9 → AAVE in 37.6)."""
    swap = _tx(
        "0x1cbbb89c",
        "execute",
        act_type="trade",
        method="Execute",
        transfers=[_leg("out", "WETH", _WETH, 1.9), _leg("in", "AAVE", _AAVE_TOKEN, 37.6)],
    )
    events = map_events([swap], _POSITIONS)
    assert len(events) == 1
    assert events[0].kind == "swap"
    assert events[0].position_id == _LP_POSITION.position_id


def test_zap_execute_with_a_trade_act_plus_an_add_hint_stays_unclassified() -> None:
    """The load-bearing precision guard: an aggregator `execute` that swaps AND adds
    liquidity (a zap-in) carries a "trade" act but ALSO an add-liquidity hint — it
    must NOT book as a pure swap (that would corrupt basis, ADR-0079), so it stays
    honest `unclassified`."""
    zap = _tx(
        "0xzap",
        "execute",
        act_type="trade",
        method="addLiquidity",
        transfers=[_leg("out", "WETH", _WETH, 1.0), _leg("in", "AERO", _AERO, 50.0)],
    )
    assert map_events([zap], _POSITIONS)[0].kind == "unclassified"


def test_execute_with_a_trade_act_but_three_transfers_stays_unclassified() -> None:
    """A clean swap is exactly one out-leg and one in-leg; a "trade" act with THREE
    transfers (two swap legs + an extra LP leg) is a compound op, not a pure swap —
    stays `unclassified` rather than mis-booking a basis."""
    compound = _tx(
        "0xcompound",
        "execute",
        act_type="trade",
        transfers=[
            _leg("out", "WETH", _WETH, 1.0),
            _leg("in", "AERO", _AERO, 50.0),
            _leg("in", "USDC", _USDC, 100.0),
        ],
    )
    assert map_events([compound], _POSITIONS)[0].kind == "unclassified"


def test_trade_act_one_sided_or_same_token_is_not_a_swap() -> None:
    """The 1-in / 1-out-of-DIFFERENT-tokens guard: a "trade" act with only outbound
    legs, or with the same token in and out, is not a clean conversion — not a swap."""
    one_sided = _tx(
        "0x1side", "execute", act_type="trade", transfers=[_leg("out", "WETH", _WETH, 1.0)]
    )
    assert map_events([one_sided], _POSITIONS)[0].kind != "swap"
    same_token = _tx(
        "0xsame",
        "execute",
        act_type="trade",
        transfers=[_leg("out", "WETH", _WETH, 1.0), _leg("in", "WETH", _WETH, 0.9)],
    )
    assert map_events([same_token], _POSITIONS)[0].kind != "swap"


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


# -- Plan 0084 phase 2: gauge-aware classification -------------------------------
#
# The wallet holds three positions, and AERO is contained by TWO of them
# (AERO/WETH and USDC/AERO), so a gauge `getReward` paying AERO cannot be
# attributed by the token fallback — it is ambiguous by construction (ADR-0079).
# Each pool's emissions come through a distinct per-pool gauge; the gauge→pool map
# is what disambiguates.

_AAVE_TOKEN = "0xba100000625a3754423978a60c9317c58a424e3d"
_POOL_AERO_WETH = "0xAER0WETHpool00000000000000000000000000a1"
_POOL_USDC_AERO = "0xUSDCAER0pool00000000000000000000000000b2"
_POOL_AAVE_WETH = "0xAAVEWETHpool00000000000000000000000000c3"
_GAUGE_AERO_WETH = "0x9564" + "0" * 32 + "88f1"
_GAUGE_AAVE_WETH = "0x33ab" + "0" * 32 + "cd12"

_AERO_WETH = DefiPosition(
    position_id="base:aerodrome:aero-weth",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="AERO", address=_AERO, amount=1000.0),
        PositionToken(symbol="WETH", address=_WETH, amount=0.5),
    ],
    usd_value=3200.0,
    pool="AERO / WETH",
    pool_address=_POOL_AERO_WETH,
)
_USDC_AERO = DefiPosition(
    position_id="base:aerodrome:usdc-aero",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="USDC", address=_USDC, amount=1500.0),
        PositionToken(symbol="AERO", address=_AERO, amount=800.0),
    ],
    usd_value=3000.0,
    pool="USDC / AERO",
    pool_address=_POOL_USDC_AERO,
)
_AAVE_WETH = DefiPosition(
    position_id="base:aerodrome:aave-weth",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="AAVE", address=_AAVE_TOKEN, amount=10.0),
        PositionToken(symbol="WETH", address=_WETH, amount=0.5),
    ],
    usd_value=2000.0,
    pool="AAVE / WETH",
    pool_address=_POOL_AAVE_WETH,
)
_GAUGE_POSITIONS = [_AERO_WETH, _USDC_AERO, _AAVE_WETH]
_GAUGE_MAP = {
    _GAUGE_AERO_WETH.lower(): _POOL_AERO_WETH.lower(),
    _GAUGE_AAVE_WETH.lower(): _POOL_AAVE_WETH.lower(),
}


def test_gauge_getreward_attributes_to_the_specific_pool_not_an_ambiguous_one() -> None:
    """The core ADR-0079 fix: a getReward on the AERO/WETH gauge must book a
    `reward_claim` on the AERO/WETH position specifically — not the USDC/AERO one
    that also holds AERO, and not the AAVE/WETH one."""
    tx = _tx(
        "0xrewAW",
        "execute",
        contract=_GAUGE_AERO_WETH,
        method="getReward",
        transfers=[_leg("in", "AERO", _AERO, 2830.0)],
    )
    events = map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)
    assert len(events) == 1
    assert events[0].kind == "reward_claim"
    assert events[0].position_id == _AERO_WETH.position_id


def test_a_second_gauge_attributes_the_same_token_to_a_different_pool() -> None:
    """Attribution is by gauge, not token: an AERO getReward on the AAVE/WETH
    gauge books against AAVE/WETH, proving the map (not the token) decides."""
    tx = _tx(
        "0xrewVW",
        "execute",
        contract=_GAUGE_AAVE_WETH,
        method="getReward",
        transfers=[_leg("in", "AERO", _AERO, 34.2)],
    )
    event = map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)[0]
    assert event.kind == "reward_claim"
    assert event.position_id == _AAVE_WETH.position_id


def test_without_the_gauge_map_the_reward_is_unattributable() -> None:
    """Establishes the map is *what fixes it*: the identical getReward tx, with no
    gauge map, joins nothing — the gauge is not a pool and AERO is ambiguous — so
    the reward is lost (the pre-0084 failure this plan closes)."""
    tx = _tx(
        "0xrewAW",
        "execute",
        contract=_GAUGE_AERO_WETH,
        method="getReward",
        transfers=[_leg("in", "AERO", _AERO, 2830.0)],
    )
    assert map_events([tx], _GAUGE_POSITIONS) == []
    assert map_events([tx], _GAUGE_POSITIONS, {}) == []


def test_gauge_stake_is_a_custody_move_not_a_contribution() -> None:
    """Staking the LP token out to the gauge is custody, not added capital:
    `custody_move`, joined to the pool, so the engine changes no basis."""
    tx = _tx(
        "0xstake",
        "deposit",
        contract=_GAUGE_AERO_WETH,
        method="stake",
        transfers=[_leg("out", "vAMM-AERO/WETH", _POOL_AERO_WETH, 12.0)],
    )
    event = map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)[0]
    assert event.kind == "custody_move"
    assert event.position_id == _AERO_WETH.position_id


def test_gauge_unstake_is_a_custody_move_not_a_reward() -> None:
    """The regression the custody-method check prevents: an unstake brings the LP
    token *in*, which the bare inbound heuristic would miscount as reward income —
    the `unstake` method keeps it a `custody_move`."""
    tx = _tx(
        "0xunstake",
        "withdraw",
        contract=_GAUGE_AERO_WETH,
        method="unstake",
        transfers=[_leg("in", "vAMM-AERO/WETH", _POOL_AERO_WETH, 12.0)],
    )
    assert map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)[0].kind == "custody_move"


def test_no_gauge_interaction_is_left_unclassified_on_the_fixture() -> None:
    """The done-when count check: across a realistic gauge-interaction fixture
    (stake → claim → claim → unstake), zero events are `unclassified`."""
    txs = [
        _tx(
            "0xg1",
            "deposit",
            block=1,
            contract=_GAUGE_AERO_WETH,
            method="stake",
            transfers=[_leg("out", "vAMM", _POOL_AERO_WETH, 12.0)],
        ),
        _tx(
            "0xg2",
            "execute",
            block=2,
            contract=_GAUGE_AERO_WETH,
            method="getReward",
            transfers=[_leg("in", "AERO", _AERO, 100.0)],
        ),
        _tx(
            "0xg3",
            "execute",
            block=3,
            contract=_GAUGE_AAVE_WETH,
            method="getReward",
            transfers=[_leg("in", "AERO", _AERO, 5.0)],
        ),
        _tx(
            "0xg4",
            "withdraw",
            block=4,
            contract=_GAUGE_AERO_WETH,
            method="unstake",
            transfers=[_leg("in", "vAMM", _POOL_AERO_WETH, 12.0)],
        ),
    ]
    events = map_events(txs, _GAUGE_POSITIONS, _GAUGE_MAP)
    assert len(events) == 4
    assert not any(e.kind == "unclassified" for e in events)
    assert [e.kind for e in events] == [
        "custody_move",
        "reward_claim",
        "reward_claim",
        "custody_move",
    ]


def test_gauge_mapping_is_pure_and_deterministic() -> None:
    tx = _tx(
        "0xrewAW",
        "execute",
        contract=_GAUGE_AERO_WETH,
        method="getReward",
        transfers=[_leg("in", "AERO", _AERO, 2830.0)],
    )
    first = map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)
    second = map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)
    assert first == second


def test_a_direct_pool_match_still_wins_when_gauge_map_is_present() -> None:
    """The gauge path is additive: a tx whose act contract is the pool itself
    still joins directly (via_gauge False), classified by the normal taxonomy."""
    tx = _tx(
        "0xdirect",
        "deposit",
        contract=_POOL_AERO_WETH,
        transfers=[_leg("out", "AERO", _AERO, 100.0), _leg("out", "WETH", _WETH, 0.05)],
    )
    event = map_events([tx], _GAUGE_POSITIONS, _GAUGE_MAP)[0]
    assert event.kind == "add_liquidity"
    assert event.position_id == _AERO_WETH.position_id
