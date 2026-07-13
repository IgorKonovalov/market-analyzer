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
(e) unbooked kinds (liquidation / unclassified) and any incomplete position
    force honest `None` wallet totals, never a partial sum (`swap` is booked as
    of Plan 0084; `custody_move` is a no-op).

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

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from market_analyser.data.adapters.defillama import token_key
from market_analyser.defi.models import Chain, DefiPosition, PositionToken
from market_analyser.defi.pnl import compute_wallet_pnl
from market_analyser.defi.pnl_events import PositionEvent, map_events
from market_analyser.defi.tx_models import DecodedTx, TxTransfer

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


def _decoded_tx(
    tx_hash: str,
    operation_type: str,
    block: int,
    mined_at: datetime,
    legs: list[dict[str, Any]],
    *,
    act_type: str = "execute",
    method: str | None = None,
) -> DecodedTx:
    """A DecodedTx joined to `_LP` via its pool contract — for the end-to-end
    map_events → engine pipeline golden."""
    return DecodedTx.model_validate(
        {
            "chain": "base",
            "hash": tx_hash,
            "operation_type": operation_type,
            "mined_at": mined_at,
            "mined_at_block": block,
            "in_block_index": 0,
            "status": "confirmed",
            "transfers": legs,
            "acts": [
                {
                    "act_id": f"{tx_hash}-a",
                    "type": act_type,
                    "contract_address": _LP.pool_address,
                    "method_name": method,
                }
            ],
        }
    )


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
        now=_AS_OF,
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


def test_position_carries_chain_and_pool_address_for_explorer_links() -> None:
    # The renderer deep-links a position to its pool on the chain's block explorer,
    # so the per-position P&L must carry the source position's chain + on-chain pool
    # address through the replay (the position_id's trailing segment is a Zerion
    # group id, NOT an address, so it can't be linked).
    complete = _lp_pnl().positions[0]
    assert complete.incomplete is False
    assert complete.chain == "base"
    assert complete.pool_address == _LP.pool_address

    # An incomplete (unpriceable) position carries them too — the link does not
    # depend on the figures being reconstructable.
    events = [
        *_LP_EVENTS,
        _event("fee_claim", _LP, _TS3, 400, [_leg("in", "GHST", _GHST, 5.0)]),
    ]
    incomplete = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=events,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    ).positions[0]
    assert incomplete.incomplete is True
    assert incomplete.chain == "base"
    assert incomplete.pool_address == _LP.pool_address


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
        wallet=_WALLET,
        positions=[_LP],
        events=events,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    position = result.positions[0]
    assert position.incomplete is True
    assert position.realized_usd is None, "a missing price must never appear as 0.0"
    assert position.unrealized_usd is None
    assert position.cost_basis_usd is None
    assert any(f"base:{_GHST}" in note and str(_epoch(_TS3)) in note for note in position.notes)
    # Plan 0088 / ADR-0082: the incomplete position is EXCLUDED from the total (a
    # sum over the complete positions — here none), flagged, never nulled.
    assert result.incomplete is True
    assert result.partial is True
    assert result.incomplete_position_count == 1
    assert result.realized_usd == 0.0
    assert result.unrealized_usd == 0.0


# -- Plan 0093 / ADR-0085: user-attested dust-token override ----------------------
#
# The GHST fee_claim leg is unpriceable (absent from _PRICES), so by default it
# marks the position incomplete (the loud-failure test above). Attesting GHST as
# dust values it at $0 in the price path: the position completes with a disclosing
# note, its figures equal the dust-free _LP (a $0 fee claim books nothing), and the
# ADR-0036 default is untouched for the same token when it is NOT listed.

_GHST_KEY = f"base:{_GHST}"
_DUST_EVENTS = [
    *_LP_EVENTS,
    _event("fee_claim", _LP, _TS3, 400, [_leg("in", "GHST", _GHST, 5.0)]),
]


def _dust_pnl(dust_tokens: frozenset[str]) -> Any:
    return compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=_DUST_EVENTS,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
        dust_tokens=dust_tokens,
    )


def test_attested_dust_token_completes_at_zero_with_a_disclosing_note() -> None:
    result = _dust_pnl(frozenset({_GHST_KEY}))
    position = result.positions[0]
    assert position.incomplete is False
    # GHST valued at $0, so the figures equal the dust-free _LP (realized 70, basis
    # 700, unrealized 100) — the $0 leg is booked, never a fabricated non-zero.
    assert position.realized_usd == 70.0
    assert position.cost_basis_usd == 700.0
    assert position.unrealized_usd == 100.0
    # The zero is disclosed, never silent.
    assert any(_GHST_KEY in note and "$0" in note for note in position.notes)
    # The now-complete position counts toward the wallet total — not partial.
    assert result.partial is False
    assert result.incomplete_position_count == 0
    assert result.realized_usd == 70.0


def test_dust_override_leaves_loud_failure_the_default_for_unlisted_tokens() -> None:
    # Same position, GHST NOT attested: ADR-0036 default holds — figures null, the
    # offending leg named, never a fabricated $0.
    position = _dust_pnl(frozenset()).positions[0]
    assert position.incomplete is True
    assert position.realized_usd is None
    assert any(_GHST_KEY in note and str(_epoch(_TS3)) in note for note in position.notes)


def test_dust_override_is_case_insensitive_and_deterministic() -> None:
    # A mixed-case config entry still matches (the engine lowercases to token_key
    # form), and a re-run with the same dust set is byte-identical.
    mixed = frozenset({f"base:{_GHST.upper()}"})
    assert _dust_pnl(mixed).positions[0].incomplete is False
    assert (
        _dust_pnl(frozenset({_GHST_KEY})).model_dump_json()
        == _dust_pnl(frozenset({_GHST_KEY})).model_dump_json()
    )


def test_unclassified_event_marks_the_position_incomplete() -> None:
    events = [
        *_LP_EVENTS,
        _event("unclassified", _LP, _TS3, 500, [_leg("in", "USDC", _USDC, 1.0)]),
    ]
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=events,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    position = result.positions[0]
    assert position.incomplete is True
    assert any("unclassified" in note for note in position.notes)


# -- Plan 0088 phase 1: partial wallet totals (never null-everything) -------------
#
# A second position whose fee-claim leg (GHST) has no block-time price in _PRICES
# comes back incomplete; the wallet total is then the sum over the COMPLETE
# positions (the priced _LP), flagged partial, with the incomplete one excluded —
# not zeroed, not nulling the whole wallet (ADR-0082, amending ADR-0036).

_INCOMPLETE_POSITION = DefiPosition(
    position_id="base:aerodrome:lp-2",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[PositionToken(symbol="GHST", address=_GHST, amount=5.0)],
    usd_value=100.0,
    pool="GHST pool",
    pool_address="0xpool0000000000000000000000000000000000002",
)
_UNPRICEABLE_EVENT = _event(
    "fee_claim", _INCOMPLETE_POSITION, _TS3, 700, [_leg("in", "GHST", _GHST, 5.0)]
)


def test_partial_total_sums_over_complete_positions_only() -> None:
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP, _INCOMPLETE_POSITION],
        events=[*_LP_EVENTS, _UNPRICEABLE_EVENT],
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    assert result.partial is True
    assert result.incomplete is True
    assert result.incomplete_position_count == 1
    # The incomplete GHST position is excluded; the total is exactly the complete
    # _LP's figures — no fabricated 0 for the position we couldn't price.
    assert result.realized_usd == 70.0
    assert result.unrealized_usd == 100.0
    # The incomplete position is still reported per-position, honestly null.
    excluded = next(
        p for p in result.positions if p.position_id == _INCOMPLETE_POSITION.position_id
    )
    assert excluded.incomplete is True
    assert excluded.realized_usd is None


def test_incomplete_position_contributes_nothing_to_the_total() -> None:
    """Removing the incomplete position from a complete wallet leaves the total
    unchanged — it is excluded, not zeroed (ADR-0082 done-when)."""
    with_incomplete = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP, _INCOMPLETE_POSITION],
        events=[*_LP_EVENTS, _UNPRICEABLE_EVENT],
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    without = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=_LP_EVENTS,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    assert with_incomplete.realized_usd == without.realized_usd
    assert with_incomplete.unrealized_usd == without.unrealized_usd
    # The all-complete wallet is not flagged partial and reports the same totals
    # as before this change (no regression to the fully-complete path).
    assert without.partial is False
    assert without.incomplete is False
    assert without.incomplete_position_count == 0


# -- Plan 0084 phase 3: swap booking + custody-move no-op ------------------------
#
# A fair swap (V_in == V_out at block time) realizes ~0 and leaves `basis`
# untouched — value is reshuffled within the position, not added or removed. An
# unfair swap realizes its execution delta. `liquidation` / `unclassified` still
# fail loud; `custody_move` is a no-op.

_SWAP_EVENTS = [
    _event(
        "add_liquidity",
        _LP,
        _TS1,
        100,
        [_leg("out", "WETH", _WETH, 0.2), _leg("out", "USDC", _USDC, 700.0)],
    ),
    # 350 USDC @1 (V_out 350) -> 0.0875 WETH @4000 (V_in 350): a fair swap.
    _event(
        "swap",
        _LP,
        _TS2,
        200,
        [_leg("out", "USDC", _USDC, 350.0), _leg("in", "WETH", _WETH, 0.0875)],
    ),
]


def test_swap_books_as_a_basis_preserving_conversion() -> None:
    """A fair swap completes the position (not incomplete), realizes ~0, and does
    not touch cost basis — the invariant that keeps a swap from corrupting basis."""
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=_SWAP_EVENTS,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    position = result.positions[0]
    assert position.incomplete is False
    assert position.realized_usd == 0.0, "a fair atomic swap realizes ~0"
    assert position.cost_basis_usd == 1400.0, "basis is unchanged by a swap"
    assert result.realized_usd == 0.0


def test_unfair_swap_realizes_its_block_time_execution_delta() -> None:
    """Poor execution (V_in < V_out) surfaces as a realized loss of exactly the
    delta — 350 USDC out, only 0.08 WETH (=$320) in -> realized -30."""
    events = [
        _SWAP_EVENTS[0],
        _event(
            "swap",
            _LP,
            _TS2,
            200,
            [_leg("out", "USDC", _USDC, 350.0), _leg("in", "WETH", _WETH, 0.08)],
        ),
    ]
    position = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=events,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    ).positions[0]
    assert position.incomplete is False
    assert position.realized_usd == -30.0


def test_swap_inclusive_replay_is_byte_identical() -> None:
    def _run() -> Any:
        return compute_wallet_pnl(
            wallet=_WALLET,
            positions=[_LP],
            events=_SWAP_EVENTS,
            price_source=_PRICES,
            as_of=_AS_OF,
            now=_AS_OF,
        )

    assert _run().model_dump_json() == _run().model_dump_json()


def test_custody_move_is_a_noop() -> None:
    """Staking the LP receipt into the gauge changes nothing: the result matches
    the same history without the custody move."""
    with_custody = [
        _SWAP_EVENTS[0],
        _event("custody_move", _LP, _TS2, 200, [_leg("out", "vAMM", _WETH, 1.0)]),
    ]
    without = [_SWAP_EVENTS[0]]
    a = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=with_custody,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    ).positions[0]
    b = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=without,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    ).positions[0]
    assert a.incomplete is False
    assert (a.realized_usd, a.cost_basis_usd, a.unrealized_usd) == (
        b.realized_usd,
        b.cost_basis_usd,
        b.unrealized_usd,
    )


def test_aggregator_swap_reconstructs_end_to_end_and_is_byte_identical() -> None:
    """Plan 0087 smoke follow-up: an aggregator swap (operation_type "execute" + a
    "trade" act) flows the full pipeline — map_events classifies it `swap`, the
    engine books it as an average-cost conversion — and the position reconstructs
    cleanly (non-None realized, incomplete=False), byte-identical on re-run. This is
    the path that leaves position 87f522… complete once its aggregator swap is
    recognized."""
    deposit = _decoded_tx(
        "0xdep",
        "deposit",
        100,
        _TS1,
        [_leg("out", "WETH", _WETH, 0.2), _leg("out", "USDC", _USDC, 700.0)],
    )
    # Sell 0.1 WETH (@4000 = 400) for 450 USDC → realizes +50, routed as an "execute".
    agg_swap = _decoded_tx(
        "0x1cbbb89c",
        "execute",
        200,
        _TS2,
        [_leg("out", "WETH", _WETH, 0.1), _leg("in", "USDC", _USDC, 450.0)],
        act_type="trade",
        method="Execute",
    )
    events = map_events([deposit, agg_swap], [_LP])
    assert [e.kind for e in events] == ["add_liquidity", "swap"]

    def _run() -> Any:
        return compute_wallet_pnl(
            wallet=_WALLET,
            positions=[_LP],
            events=events,
            price_source=_PRICES,
            as_of=_AS_OF,
            now=_AS_OF,
        )

    position = _run().positions[0]
    assert position.incomplete is False
    assert position.realized_usd == 50.0
    assert _run().model_dump_json() == _run().model_dump_json()


def test_liquidation_still_fails_loud() -> None:
    events = [
        *_LP_EVENTS,
        _event("liquidation", _LP, _TS3, 600, [_leg("out", "USDC", _USDC, 10.0)]),
    ]
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_LP],
        events=events,
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    assert result.positions[0].incomplete is True
    assert any("unbooked liquidation" in note for note in result.positions[0].notes)


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
        now=_AS_OF,
    )
    position = result.positions[0]
    assert position.incomplete is False
    assert position.realized_usd == -5.0, "borrowed 100, repaid 105: the -5 is interest paid"
    assert position.cost_basis_usd == 0.0
    assert position.unrealized_usd == 0.0
    assert position.vs_hodl_usd is None, "vs-HODL is an LP-only fact"


# -- Plan 0088 phase 2: per-position rolling-window realized P&L (exact) ----------
#
# Four income events dated 3 / 20 / 60 / 120 days before a FIXED `now`, so each
# window's realized figure is the hand-summed subset of deltas. Worked on paper:
#   7d  = 10            (only the 3-day-old claim)
#   30d = 10 + 20 = 30  (adds the 20-day-old claim)
#   90d = 10 + 20 + 40 = 70
#   all = 10 + 20 + 40 + 80 = 150   (== the position's all-time realized)

_WIN_NOW = datetime(2025, 6, 1, tzinfo=UTC)


def _days_before(days: int) -> datetime:
    return _WIN_NOW - timedelta(days=days)


_WINDOW_POSITION = DefiPosition(
    position_id="base:aerodrome:windowed",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[PositionToken(symbol="USDC", address=_USDC, amount=100.0)],
    usd_value=100.0,
    pool="USDC pool",
    pool_address="0xpool0000000000000000000000000000000000003",
)
_WINDOW_EVENTS = [
    _event(
        "add_liquidity", _WINDOW_POSITION, _days_before(200), 1, [_leg("out", "USDC", _USDC, 100.0)]
    ),
    _event("fee_claim", _WINDOW_POSITION, _days_before(3), 2, [_leg("in", "USDC", _USDC, 10.0)]),
    _event("fee_claim", _WINDOW_POSITION, _days_before(20), 3, [_leg("in", "USDC", _USDC, 20.0)]),
    _event("fee_claim", _WINDOW_POSITION, _days_before(60), 4, [_leg("in", "USDC", _USDC, 40.0)]),
    _event("fee_claim", _WINDOW_POSITION, _days_before(120), 5, [_leg("in", "USDC", _USDC, 80.0)]),
]
_WINDOW_PRICES = _TablePriceSource(
    {
        (f"base:{_USDC}", _epoch(ts)): 1.0
        for ts in (
            _days_before(200),
            _days_before(3),
            _days_before(20),
            _days_before(60),
            _days_before(120),
            _WIN_NOW,
        )
    }
)


def _windowed_result() -> Any:
    return compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_WINDOW_POSITION],
        events=_WINDOW_EVENTS,
        price_source=_WINDOW_PRICES,
        as_of=_WIN_NOW,
        now=_WIN_NOW,
    )


def test_windowed_realized_buckets_deltas_by_mined_at() -> None:
    position = _windowed_result().positions[0]
    windows = {w.window: w.realized_usd for w in position.windows}
    assert windows == {"7d": 10.0, "30d": 30.0, "90d": 70.0, "all": 150.0}
    # The `all` window reproduces the position's all-time realized figure exactly.
    assert windows["all"] == position.realized_usd


def test_windowed_realized_is_byte_identical_with_a_fixed_now() -> None:
    assert _windowed_result().model_dump_json() == _windowed_result().model_dump_json()


def test_engine_never_reads_the_wall_clock() -> None:
    """The rolling windows anchor to the injected `now`; the engine must never read
    the clock itself (Plan 0088 done-when — `now` flows only as an argument)."""
    import market_analyser.defi.pnl as pnl_module

    source = Path(pnl_module.__file__).read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utcnow" not in source
    assert "time.time" not in source


def test_incomplete_position_has_no_windows() -> None:
    """An incomplete position carries no reconstructable figures, so its window
    list is empty (never a fabricated zero-filled set)."""
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_INCOMPLETE_POSITION],
        events=[_UNPRICEABLE_EVENT],
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    position = result.positions[0]
    assert position.incomplete is True
    assert position.windows == []


# -- Plan 0088 phase 3: per-window estimated total return (labeled) ---------------
#
# total_return = realized_in_window + (unrealized_now - unrealized_at_window_start),
# where the window-start mark values the contributed lots at window-start prices
# minus the basis then. Worked for a single-USDC LP (usd_value 130, basis 100 →
# unrealized_now 30), add_liquidity 100 days ago, a 10-USDC fee 10 days ago:
#   7d : start = after both events (basis 100, held 100 → unreal_start 0),
#        realized 0  → TR = 0 + (30 - 0) = 30
#   30d: start = after the deposit only (basis 100, held 100 → unreal_start 0),
#        realized 10 → TR = 10 + (30 - 0) = 40
#   all: no start mark → realized 10 + unrealized_now 30 = 40


class _ConstUsdcPrice:
    """USDC priced at 1.0 at every timestamp (so window-start marks resolve);
    every other token is unpriceable."""

    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return 1.0 if address == _USDC else None


_CONST_USDC = _ConstUsdcPrice()

_TR_POSITION = DefiPosition(
    position_id="base:aerodrome:total-return",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[PositionToken(symbol="USDC", address=_USDC, amount=130.0)],
    usd_value=130.0,
    pool="USDC pool",
    pool_address="0xpool0000000000000000000000000000000000004",
)
_TR_EVENTS = [
    _event(
        "add_liquidity", _TR_POSITION, _days_before(100), 1, [_leg("out", "USDC", _USDC, 100.0)]
    ),
    _event("fee_claim", _TR_POSITION, _days_before(10), 2, [_leg("in", "USDC", _USDC, 10.0)]),
]


def _tr_result() -> Any:
    return compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_TR_POSITION],
        events=_TR_EVENTS,
        price_source=_CONST_USDC,
        as_of=_WIN_NOW,
        now=_WIN_NOW,
    )


def test_windowed_total_return_estimates_realized_plus_unrealized_drift() -> None:
    position = _tr_result().positions[0]
    assert position.incomplete is False
    by_window = {w.window: w for w in position.windows}
    assert by_window["7d"].total_return_usd == 30.0
    assert by_window["30d"].total_return_usd == 40.0
    assert by_window["all"].total_return_usd == 40.0
    # The exact realized figures are the headline and are unchanged by the estimate.
    assert by_window["7d"].realized_usd == 0.0
    assert by_window["30d"].realized_usd == 10.0
    # Every total-return figure is labeled an estimate.
    assert all(w.estimated is True for w in position.windows)


def test_windowed_total_return_is_byte_identical_with_a_fixed_now() -> None:
    assert _tr_result().model_dump_json() == _tr_result().model_dump_json()


# A two-token LP whose GHST leg is priceable at its block time and at `as_of`
# (so the replay + vs-HODL complete) but NOT at the finite window-start marks.

_TR_GAP_POSITION = DefiPosition(
    position_id="base:aerodrome:tr-gap",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="GHST", address=_GHST, amount=50.0),
        PositionToken(symbol="USDC", address=_USDC, amount=50.0),
    ],
    usd_value=150.0,
    pool="GHST / USDC",
    pool_address="0xpool0000000000000000000000000000000000005",
)
_TR_GAP_EVENTS = [
    _event(
        "add_liquidity",
        _TR_GAP_POSITION,
        _days_before(100),
        1,
        [_leg("out", "GHST", _GHST, 50.0), _leg("out", "USDC", _USDC, 50.0)],
    ),
    _event("fee_claim", _TR_GAP_POSITION, _days_before(10), 2, [_leg("in", "USDC", _USDC, 10.0)]),
]
_TR_GAP_PRICES = _TablePriceSource(
    {
        # GHST priced at the deposit block and at `as_of`, absent at every window cutoff.
        (f"base:{_GHST}", _epoch(_days_before(100))): 2.0,
        (f"base:{_GHST}", _epoch(_WIN_NOW)): 2.0,
        (f"base:{_USDC}", _epoch(_days_before(100))): 1.0,
        (f"base:{_USDC}", _epoch(_days_before(10))): 1.0,
        (f"base:{_USDC}", _epoch(_WIN_NOW)): 1.0,
    }
)


def test_unpriceable_window_start_reports_none_total_return_without_marking_incomplete() -> None:
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_TR_GAP_POSITION],
        events=_TR_GAP_EVENTS,
        price_source=_TR_GAP_PRICES,
        as_of=_WIN_NOW,
        now=_WIN_NOW,
    )
    position = result.positions[0]
    # The per-window pricing gap must NOT null the position.
    assert position.incomplete is False
    by_window = {w.window: w for w in position.windows}
    # GHST can't be priced at the 30d window start → an honest per-window gap...
    assert by_window["30d"].total_return_usd is None
    # ...that leaves the exact realized figure and the estimate label intact.
    assert by_window["30d"].realized_usd == 10.0
    assert by_window["30d"].estimated is True
    # `all` needs no window-start mark, so it still reports a value.
    assert by_window["all"].total_return_usd is not None


# -- Plan 0088 phase 4: is_lp + LP-first ordering + non-LP never suppresses --------
#
# A non-LP "Wanderers"-shape position holding an unpriceable exotic token (GHST is
# absent from _PRICES at the supply block) comes back incomplete, listed FIRST in
# discovery order — yet the LP position leads the report and its figures + the
# partial wallet total are untouched.

_WANDERERS = DefiPosition(
    position_id="base:wanderers:exotic",
    chain="base",
    protocol="wanderers",
    kind="staking",
    tokens=[PositionToken(symbol="WNDR", address=_GHST, amount=100.0)],
    usd_value=500.0,
    pool_address="0xpool0000000000000000000000000000000000006",
)
_WANDERERS_EVENT = _event("supply", _WANDERERS, _TS1, 10, [_leg("out", "WNDR", _GHST, 100.0)])


def test_lp_positions_lead_and_a_non_lp_incomplete_does_not_suppress_them() -> None:
    result = compute_wallet_pnl(
        wallet=_WALLET,
        positions=[_WANDERERS, _LP],  # the non-LP exotic is first in discovery order
        events=[_WANDERERS_EVENT, *_LP_EVENTS],
        price_source=_PRICES,
        as_of=_AS_OF,
        now=_AS_OF,
    )
    # LP-first: the LP position leads despite being second in discovery order.
    assert [p.is_lp for p in result.positions] == [True, False]
    lp = result.positions[0]
    assert lp.position_id == _LP.position_id
    assert lp.is_lp is True
    assert lp.incomplete is False
    assert lp.realized_usd == 70.0  # the LP figures are intact
    assert lp.unrealized_usd == 100.0
    wanderers = result.positions[1]
    assert wanderers.is_lp is False
    assert wanderers.incomplete is True
    # The partial wallet total reflects only the complete LP position.
    assert result.partial is True
    assert result.incomplete_position_count == 1
    assert result.realized_usd == 70.0
    assert result.unrealized_usd == 100.0


def test_rerun_on_the_same_inputs_is_byte_identical() -> None:
    first = _lp_pnl()
    second = _lp_pnl()
    assert first.model_dump_json() == second.model_dump_json()


def test_position_without_events_has_full_value_as_unrealized() -> None:
    """No history reconstructable (but nothing contradictory either): zero
    basis, so the whole current value is unrealized gain over nothing."""
    result = compute_wallet_pnl(
        wallet=_WALLET, positions=[_LP], events=[], price_source=_PRICES, as_of=_AS_OF, now=_AS_OF
    )
    position = result.positions[0]
    assert position.incomplete is False
    assert position.realized_usd == 0.0
    assert position.cost_basis_usd == 0.0
    assert position.unrealized_usd == 800.0
