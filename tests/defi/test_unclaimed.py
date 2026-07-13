"""Plan 0084 phase 5: the unclaimed-reward augmentation (`augment_with_unclaimed`).

Pinned claims:
- a gauge-staked position gets its owed rewards folded in; a non-gauge position
  keeps `unclaimed_rewards=None`;
- the wallet roll-up sums by symbol, and a symbol's `usd_value` total is `None`
  the moment any part is unpriced (honest, never a partial sum masquerading);
- the replay-derived figures (realized/unrealized/basis) are untouched — the
  augmentation only ever writes the `unclaimed_rewards` fields;
- a per-position read failure is best-effort: it leaves that position `None` and
  never raises (an owed-reward gap must not null a reconstructed P&L).
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.defi.models import DefiPosition, PositionToken, RewardAmount
from market_analyser.defi.pnl import PositionPnl, WalletPnl
from market_analyser.defi.unclaimed import augment_with_unclaimed

_AERO = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
_WETH = "0x4200000000000000000000000000000000000006"


def _position(position_id: str, *, kind: str = "lp", pool: str | None = "0xpool") -> DefiPosition:
    return DefiPosition(
        position_id=position_id,
        chain="base",
        protocol="aerodrome",
        kind=kind,  # type: ignore[arg-type]
        tokens=[PositionToken(symbol="WETH", address=_WETH, amount=0.5)],
        usd_value=1000.0,
        pool_address=pool,
    )


def _pnl(position_id: str) -> PositionPnl:
    return PositionPnl(
        position_id=position_id,
        chain="base",
        is_lp=True,  # gauge-stakeable LP positions (the unclaimed-rewards context)
        realized_usd=10.0,
        unrealized_usd=20.0,
        cost_basis_usd=100.0,
        vs_hodl_usd=5.0,
        incomplete=False,
    )


def _wallet(*ids: str) -> WalletPnl:
    return WalletPnl(
        wallet="0x1234…5678",
        positions=[_pnl(i) for i in ids],
        realized_usd=10.0 * len(ids),
        unrealized_usd=20.0 * len(ids),
        incomplete=False,
    )


class _FakeSource:
    """Returns fixed rewards per position_id; raises for ids in `errors`."""

    def __init__(
        self,
        table: dict[str, list[RewardAmount]],
        errors: frozenset[str] = frozenset(),
    ) -> None:
        self._table = table
        self._errors = errors

    def fetch_unclaimed(self, *, position: DefiPosition, owner: str) -> Sequence[RewardAmount]:
        if position.position_id in self._errors:
            raise UpstreamUnavailableError("rpc down")
        return self._table.get(position.position_id, [])


def test_owed_rewards_fold_onto_the_staked_position_only() -> None:
    positions = [_position("open"), _position("bare", pool=None)]
    source = _FakeSource({"open": [RewardAmount(symbol="AERO", amount=34.2, usd_value=18.0)]})
    result = augment_with_unclaimed(_wallet("open", "bare"), positions, source, owner="0xowner")
    by_id = {p.position_id: p for p in result.positions}
    assert by_id["open"].unclaimed_rewards == [
        RewardAmount(symbol="AERO", amount=34.2, usd_value=18.0)
    ]
    assert by_id["bare"].unclaimed_rewards is None


def test_wallet_rollup_sums_by_symbol() -> None:
    positions = [_position("p1"), _position("p2")]
    source = _FakeSource(
        {
            "p1": [RewardAmount(symbol="AERO", amount=10.0, usd_value=5.0)],
            "p2": [RewardAmount(symbol="AERO", amount=4.2, usd_value=2.0)],
        }
    )
    result = augment_with_unclaimed(_wallet("p1", "p2"), positions, source, owner="0xowner")
    assert result.unclaimed_rewards == [RewardAmount(symbol="AERO", amount=14.2, usd_value=7.0)]


def test_rollup_usd_is_none_when_any_part_is_unpriced() -> None:
    positions = [_position("p1"), _position("p2")]
    source = _FakeSource(
        {
            "p1": [RewardAmount(symbol="AERO", amount=10.0, usd_value=5.0)],
            "p2": [RewardAmount(symbol="AERO", amount=4.2, usd_value=None)],
        }
    )
    result = augment_with_unclaimed(_wallet("p1", "p2"), positions, source, owner="0xowner")
    assert result.unclaimed_rewards is not None
    rollup = result.unclaimed_rewards[0]
    assert (rollup.symbol, rollup.amount, rollup.usd_value) == ("AERO", 14.2, None)


def test_replay_figures_are_untouched() -> None:
    positions = [_position("open")]
    source = _FakeSource({"open": [RewardAmount(symbol="AERO", amount=1.0, usd_value=1.0)]})
    before = _wallet("open")
    after = augment_with_unclaimed(before, positions, source, owner="0xowner")
    p = after.positions[0]
    assert (p.realized_usd, p.unrealized_usd, p.cost_basis_usd, p.vs_hodl_usd) == (
        10.0,
        20.0,
        100.0,
        5.0,
    )
    assert after.realized_usd == before.realized_usd
    assert after.unrealized_usd == before.unrealized_usd


def test_a_read_failure_is_best_effort_and_never_raises() -> None:
    positions = [_position("open"), _position("boom")]
    source = _FakeSource(
        {"open": [RewardAmount(symbol="AERO", amount=1.0, usd_value=1.0)]},
        errors=frozenset({"boom"}),
    )
    result = augment_with_unclaimed(_wallet("open", "boom"), positions, source, owner="0xowner")
    by_id = {p.position_id: p for p in result.positions}
    assert by_id["open"].unclaimed_rewards is not None
    assert by_id["boom"].unclaimed_rewards is None  # the failed read left it absent


def test_no_owed_rewards_leaves_the_result_untouched() -> None:
    positions = [_position("p1")]
    source = _FakeSource({})  # nothing owed
    before = _wallet("p1")
    after = augment_with_unclaimed(before, positions, source, owner="0xowner")
    assert after.unclaimed_rewards is None
    assert after.positions[0].unclaimed_rewards is None
