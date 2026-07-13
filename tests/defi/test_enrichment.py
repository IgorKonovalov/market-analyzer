"""Plan 0034 phase 5 / Plan 0048 phase 2 done-when: LP deep-state enrichment.

After discovery, each `kind="lp"` position carrying a `pool_address` is deepened
with its on-chain detail (tick range, current tick, in-range status, uncollected
fees) read through an `LpPositionDetailSource`. Plan 0048 makes the routing
shape-aware via the source's resolver, which is the discriminator:

- a **v2 AMM** pool resolves to `None` → skipped with **no detail read** (no ticks),
- a **staked-CL** or **unstaked-CL** pool resolves to a position NFT `tokenId` →
  read by that id.

Enrichment no longer branches on the protocol display string. Non-LP positions and
LPs whose detail can't be read pass through unchanged (best-effort), order is
preserved, `usd_value` is untouched, and reads are spaced (serialized).
"""

from __future__ import annotations

from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.defi.enrichment import enrich_lp_positions
from market_analyser.defi.models import Chain, DefiPosition, LpPositionDetail, PositionToken

_OWNER = "0xdead00000000000000000000000000000000beef"
_STAKED_CL_GAUGE = "0xe3800a58b5535935850a10e082952ec3577d8dcc"  # Zerion gives the gauge
_UNSTAKED_CL_POOL = "0xc6962004f452be9203591991d15f6b388e09e8d0"
_V2_POOL = "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59"  # constant-product, no ticks


def _detail(current_tick: int = 100) -> LpPositionDetail:
    return LpPositionDetail(
        tick_lower=-200,
        tick_upper=200,
        current_tick=current_tick,
        in_range=-200 <= current_tick < 200,
        uncollected_fees=[PositionToken(symbol="WETH", address="0x42", amount=0.02)],
    )


def _lp(
    protocol: str,
    pool_address: str | None,
    *,
    chain: Chain = "base",
    usd_value: float = 1000.0,
) -> DefiPosition:
    return DefiPosition(
        position_id=f"{chain}:{protocol}:{pool_address}",
        chain=chain,
        protocol=protocol,
        kind="lp",
        tokens=[PositionToken(symbol="WETH", address="0x42", amount=0.1)],
        usd_value=usd_value,
        pool="WETH / AERO",
        pool_address=pool_address,
    )


def _lending(chain: Chain = "ethereum") -> DefiPosition:
    return DefiPosition(
        position_id=f"{chain}:aave-v3:supply",
        chain=chain,
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[PositionToken(symbol="USDC", address="0xa0b8", amount=1000.0)],
        usd_value=1000.0,
    )


class _FakeDetailSource:
    """A configurable `LpPositionDetailSource` recording its calls. The resolver is
    the shape discriminator: `token_ids[pool] = None` models a v2 pool (or an
    unresolved position); an int models a resolved CL position NFT."""

    def __init__(
        self,
        *,
        token_ids: dict[str, int | None] | None = None,
        details: dict[tuple[str, int | None], LpPositionDetail] | None = None,
        resolve_errors: dict[str, Exception] | None = None,
        fetch_errors: dict[str, Exception] | None = None,
    ) -> None:
        self._token_ids = token_ids or {}
        self._details = details or {}
        self._resolve_errors = resolve_errors or {}
        self._fetch_errors = fetch_errors or {}
        self.resolve_calls: list[str] = []
        self.fetch_calls: list[tuple[str, int | None]] = []

    def resolve_univ3_token_id(self, *, chain: Chain, pool_address: str, owner: str) -> int | None:
        self.resolve_calls.append(pool_address)
        if pool_address in self._resolve_errors:
            raise self._resolve_errors[pool_address]
        return self._token_ids.get(pool_address)

    def fetch_lp_detail(
        self, *, chain: Chain, pool_address: str, token_id: int | None = None
    ) -> LpPositionDetail:
        self.fetch_calls.append((pool_address, token_id))
        if pool_address in self._fetch_errors:
            raise self._fetch_errors[pool_address]
        return self._details[(pool_address, token_id)]


def _no_sleep(_seconds: float) -> None:
    return None


def test_staked_cl_lp_is_enriched_via_resolved_token_id() -> None:
    source = _FakeDetailSource(
        token_ids={_STAKED_CL_GAUGE: 232923},
        details={(_STAKED_CL_GAUGE, 232923): _detail()},
    )
    [enriched] = enrich_lp_positions(
        [_lp("aerodrome", _STAKED_CL_GAUGE)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert enriched.tick_lower == -200
    assert enriched.tick_upper == 200
    assert enriched.current_tick == 100
    assert enriched.in_range is True
    assert enriched.uncollected_fees is not None
    assert enriched.uncollected_fees[0].symbol == "WETH"
    assert source.resolve_calls == [_STAKED_CL_GAUGE]  # resolve first (the discriminator)
    assert source.fetch_calls == [(_STAKED_CL_GAUGE, 232923)]  # then read by tokenId


def test_unstaked_cl_lp_is_enriched_via_resolved_token_id() -> None:
    source = _FakeDetailSource(
        token_ids={_UNSTAKED_CL_POOL: 12345},
        details={(_UNSTAKED_CL_POOL, 12345): _detail(current_tick=50)},
    )
    [enriched] = enrich_lp_positions(
        [_lp("uniswap-v3", _UNSTAKED_CL_POOL)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert enriched.in_range is True
    assert enriched.current_tick == 50
    assert source.resolve_calls == [_UNSTAKED_CL_POOL]
    assert source.fetch_calls == [(_UNSTAKED_CL_POOL, 12345)]


def test_v2_amm_lp_is_skipped_with_no_detail_read() -> None:
    # The resolver returns None for a v2 constant-product pool (no ticks); the
    # position is left at discovery depth and fetch_lp_detail is never called.
    source = _FakeDetailSource(token_ids={_V2_POOL: None})
    [out] = enrich_lp_positions([_lp("aerodrome", _V2_POOL)], source, owner=_OWNER, sleep=_no_sleep)
    assert out.tick_lower is None
    assert out.in_range is None
    assert source.resolve_calls == [_V2_POOL]
    assert source.fetch_calls == []  # no deep read for the no-ticks shape


def test_non_lp_position_passes_through_untouched() -> None:
    source = _FakeDetailSource()
    [out] = enrich_lp_positions([_lending()], source, owner=_OWNER, sleep=_no_sleep)
    assert out.tick_lower is None
    assert out.current_tick is None
    assert source.resolve_calls == []
    assert source.fetch_calls == []


def test_lp_without_pool_address_is_not_enriched() -> None:
    source = _FakeDetailSource()
    [out] = enrich_lp_positions([_lp("aerodrome", None)], source, owner=_OWNER, sleep=_no_sleep)
    assert out.tick_lower is None
    assert source.resolve_calls == []
    assert source.fetch_calls == []


def test_upstream_error_leaves_position_at_discovery_depth() -> None:
    source = _FakeDetailSource(
        token_ids={_STAKED_CL_GAUGE: 1},
        fetch_errors={_STAKED_CL_GAUGE: UpstreamUnavailableError("rpc down")},
    )
    [out] = enrich_lp_positions(
        [_lp("aerodrome", _STAKED_CL_GAUGE)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert out.tick_lower is None  # best-effort: not enriched, not failed
    assert out.usd_value == 1000.0  # discovery numbers untouched


def test_config_error_skips_remaining_positions_on_that_chain() -> None:
    # The first base LP hits an unconfigured-RPC error during resolution (the real
    # adapter reads the RPC URL there first); the second base LP is skipped without
    # re-attempting (the chain is memoized).
    pool_a = "0x" + "a" * 40
    pool_b = "0x" + "b" * 40
    source = _FakeDetailSource(
        resolve_errors={pool_a: LpDetailConfigError("no base_rpc_url")},
        token_ids={pool_b: 2},
        details={(pool_b, 2): _detail()},
    )
    out = enrich_lp_positions(
        [_lp("aerodrome", pool_a), _lp("aerodrome", pool_b)],
        source,
        owner=_OWNER,
        sleep=_no_sleep,
    )
    assert all(p.tick_lower is None for p in out)
    assert source.resolve_calls == [pool_a]  # second pool never attempted
    assert source.fetch_calls == []


def test_order_is_preserved_and_mixed_kinds_handled() -> None:
    source = _FakeDetailSource(
        token_ids={_STAKED_CL_GAUGE: 7},
        details={(_STAKED_CL_GAUGE, 7): _detail()},
    )
    positions = [_lending("ethereum"), _lp("aerodrome", _STAKED_CL_GAUGE), _lending("base")]
    out = enrich_lp_positions(positions, source, owner=_OWNER, sleep=_no_sleep)
    assert [p.position_id for p in out] == [p.position_id for p in positions]
    assert out[0].tick_lower is None  # lending untouched
    assert out[1].tick_lower == -200  # lp enriched
    assert out[2].tick_lower is None


def test_reads_are_spaced_between_positions() -> None:
    calls: list[float] = []

    def _record(seconds: float) -> None:
        calls.append(seconds)

    source = _FakeDetailSource(
        token_ids={_STAKED_CL_GAUGE: 1, _UNSTAKED_CL_POOL: 2},
        details={(_STAKED_CL_GAUGE, 1): _detail(), (_UNSTAKED_CL_POOL, 2): _detail()},
    )
    enrich_lp_positions(
        [_lp("aerodrome", _STAKED_CL_GAUGE), _lp("uniswap-v3", _UNSTAKED_CL_POOL)],
        source,
        owner=_OWNER,
        sleep=_record,
    )
    # Two enrichable positions -> exactly one spacing pause between them.
    assert len(calls) == 1
    assert calls[0] > 0
