"""Plan 0034 phase 5 done-when: LP deep-state enrichment.

After discovery, each `kind="lp"` position carrying a `pool_address` is deepened
with its on-chain detail (tick range, current tick, in-range status, uncollected
fees) read through an `LpPositionDetailSource` — the Aerodrome class one-hop, the
Uniswap-v3 class two-hop (resolve the NFT tokenId first). Non-LP positions and
LPs whose detail can't be read pass through unchanged (best-effort), order is
preserved, `usd_value` is untouched, and reads are spaced (serialized).
"""

from __future__ import annotations

from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.defi.enrichment import enrich_lp_positions
from market_analyser.defi.models import Chain, DefiPosition, LpPositionDetail, PositionToken

_OWNER = "0xae5b…9790"
_AERO_POOL = "0xe3800a58b5535935850a10e082952ec3577d8dcc"
_UNI_POOL = "0xc6962004f452be9203591991d15f6b388e09e8d0"


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
    """A configurable `LpPositionDetailSource` recording its calls."""

    def __init__(
        self,
        *,
        aerodrome: dict[str, LpPositionDetail] | None = None,
        univ3_token_ids: dict[str, int | None] | None = None,
        univ3_detail: dict[tuple[str, int], LpPositionDetail] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._aerodrome = aerodrome or {}
        self._univ3_token_ids = univ3_token_ids or {}
        self._univ3_detail = univ3_detail or {}
        self._errors = errors or {}
        self.fetch_calls: list[tuple[str, int | None]] = []
        self.resolve_calls: list[str] = []

    def fetch_lp_detail(
        self, *, chain: Chain, pool_address: str, token_id: int | None = None
    ) -> LpPositionDetail:
        self.fetch_calls.append((pool_address, token_id))
        if pool_address in self._errors:
            raise self._errors[pool_address]
        if token_id is None:
            return self._aerodrome[pool_address]
        return self._univ3_detail[(pool_address, token_id)]

    def resolve_univ3_token_id(self, *, chain: Chain, pool_address: str, owner: str) -> int | None:
        self.resolve_calls.append(pool_address)
        return self._univ3_token_ids.get(pool_address)


def _no_sleep(_seconds: float) -> None:
    return None


def test_aerodrome_lp_is_enriched_one_hop() -> None:
    source = _FakeDetailSource(aerodrome={_AERO_POOL: _detail()})
    [enriched] = enrich_lp_positions(
        [_lp("aerodrome", _AERO_POOL)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert enriched.tick_lower == -200
    assert enriched.tick_upper == 200
    assert enriched.current_tick == 100
    assert enriched.in_range is True
    assert enriched.uncollected_fees is not None
    assert enriched.uncollected_fees[0].symbol == "WETH"
    assert source.fetch_calls == [(_AERO_POOL, None)]  # one-hop: token_id None
    assert source.resolve_calls == []  # no NFT resolution for the Aerodrome class


def test_univ3_lp_is_enriched_two_hop() -> None:
    source = _FakeDetailSource(
        univ3_token_ids={_UNI_POOL: 12345},
        univ3_detail={(_UNI_POOL, 12345): _detail(current_tick=50)},
    )
    [enriched] = enrich_lp_positions(
        [_lp("uniswap-v3", _UNI_POOL)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert enriched.in_range is True
    assert enriched.current_tick == 50
    assert source.resolve_calls == [_UNI_POOL]  # tokenId resolved first
    assert source.fetch_calls == [(_UNI_POOL, 12345)]  # then read by tokenId


def test_non_lp_position_passes_through_untouched() -> None:
    source = _FakeDetailSource()
    [out] = enrich_lp_positions([_lending()], source, owner=_OWNER, sleep=_no_sleep)
    assert out.tick_lower is None
    assert out.current_tick is None
    assert source.fetch_calls == []


def test_lp_without_pool_address_is_not_enriched() -> None:
    source = _FakeDetailSource()
    [out] = enrich_lp_positions([_lp("aerodrome", None)], source, owner=_OWNER, sleep=_no_sleep)
    assert out.tick_lower is None
    assert source.fetch_calls == []


def test_unresolved_univ3_position_passes_through() -> None:
    source = _FakeDetailSource(univ3_token_ids={_UNI_POOL: None})
    [out] = enrich_lp_positions(
        [_lp("uniswap-v3", _UNI_POOL)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert out.tick_lower is None
    assert source.resolve_calls == [_UNI_POOL]
    assert source.fetch_calls == []  # nothing to read without a tokenId


def test_upstream_error_leaves_position_at_discovery_depth() -> None:
    source = _FakeDetailSource(errors={_AERO_POOL: UpstreamUnavailableError("rpc down")})
    [out] = enrich_lp_positions(
        [_lp("aerodrome", _AERO_POOL)], source, owner=_OWNER, sleep=_no_sleep
    )
    assert out.tick_lower is None  # best-effort: not enriched, not failed
    assert out.usd_value == 1000.0  # discovery numbers untouched


def test_config_error_skips_remaining_positions_on_that_chain() -> None:
    # First base LP hits an unconfigured-RPC error; the second base LP is skipped
    # without re-attempting (the chain is memoized).
    pool_a = "0x" + "a" * 40
    pool_b = "0x" + "b" * 40
    source = _FakeDetailSource(errors={pool_a: LpDetailConfigError("no base_rpc_url")})
    out = enrich_lp_positions(
        [_lp("aerodrome", pool_a), _lp("aerodrome", pool_b)],
        source,
        owner=_OWNER,
        sleep=_no_sleep,
    )
    assert all(p.tick_lower is None for p in out)
    assert source.fetch_calls == [(pool_a, None)]  # second pool never attempted


def test_order_is_preserved_and_mixed_kinds_handled() -> None:
    source = _FakeDetailSource(aerodrome={_AERO_POOL: _detail()})
    positions = [_lending("ethereum"), _lp("aerodrome", _AERO_POOL), _lending("base")]
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
        aerodrome={_AERO_POOL: _detail(), _UNI_POOL: _detail()},
    )
    enrich_lp_positions(
        [_lp("aerodrome", _AERO_POOL), _lp("aerodrome", _UNI_POOL)],
        source,
        owner=_OWNER,
        sleep=_record,
    )
    # Two enrichable positions -> exactly one spacing pause between them.
    assert len(calls) == 1
    assert calls[0] > 0
