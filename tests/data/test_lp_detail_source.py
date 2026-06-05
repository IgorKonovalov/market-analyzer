"""Plan 0034 phase 2 done-when: the `LpPositionDetailSource` Protocol seam.

The Protocol is the source-agnostic contract the deep adapter (phases 3-4)
implements and the enrichment step (phase 5) consumes. It must be
`@runtime_checkable` so the composition root can register a concrete adapter
behind it, and a structurally-conforming fake must `isinstance`-satisfy it (the
same shape-test `WalletPositionsSource` carries).
"""

from __future__ import annotations

from market_analyser.data.sources import LpPositionDetailSource
from market_analyser.defi.models import Chain, LpPositionDetail, PositionToken


class _FakeLpDetailSource:
    """Structurally conforms to `LpPositionDetailSource` without inheriting it."""

    def fetch_lp_detail(
        self,
        *,
        chain: Chain,
        pool_address: str,
        token_id: int | None = None,
    ) -> LpPositionDetail:
        return LpPositionDetail(
            tick_lower=-100,
            tick_upper=100,
            current_tick=0,
            in_range=True,
            uncollected_fees=[PositionToken(symbol="WETH", address="0x42", amount=0.01)],
        )

    def resolve_univ3_token_id(
        self,
        *,
        chain: Chain,
        pool_address: str,
        owner: str,
    ) -> int | None:
        return None


def test_protocol_is_runtime_checkable_and_fake_satisfies_it() -> None:
    source = _FakeLpDetailSource()
    assert isinstance(source, LpPositionDetailSource)


def test_non_conforming_object_does_not_satisfy_protocol() -> None:
    class _Missing:
        pass

    assert not isinstance(_Missing(), LpPositionDetailSource)


def test_fake_source_returns_a_typed_detail() -> None:
    detail = _FakeLpDetailSource().fetch_lp_detail(
        chain="base", pool_address="0xe3800a58b5535935850a10e082952ec3577d8dcc"
    )
    assert detail.in_range is True
    assert detail.uncollected_fees[0].symbol == "WETH"
