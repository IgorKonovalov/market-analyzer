"""Plan 0048 done-when: the gauge-indirection LP deep adapter.

Plan 0034's one-hop `pool_address` read was wrong for *staked* Slipstream
concentrated-liquidity positions: Zerion hands us the CL **gauge** as
`pool_address`, and the gauge exposes neither `slot0()` nor a wallet-owned NFT.
This suite pins the corrected read against recorded `eth_call` responses mirroring
the 2026-06-05 live smoke (gauge `0x9564…88f1`, NPM `0xe1f8…8b53`, CLPool
`0x4e50…ce51`, tokenId `232923`): the gauge chain decodes to ticks `84000..86200`,
current `85198`, **in range**. It also covers the shape-aware token-id resolver
(staked / unstaked / v2), the unstaked-CL (Uniswap-v3) path that survives the
rework, and the typed-error contract on a revert / missing URL / outage.

The "fixture" is the canonical ABI encoding of known on-chain values (built by the
`_*_result` helpers) routed per `eth_call` by `(to, selector)` — a missing getter
is returned as a JSON-RPC *revert*, exactly as the shape probe must tolerate. Every
function selector the adapter uses is recomputed here from its signature with a
dependency-free Keccak-256 and asserted equal (the plan's selector self-check).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters import lp_detail as mod
from market_analyser.data.adapters.lp_detail import (
    LpDetailConfigError,
    LpDetailError,
    RpcLpDetailAdapter,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.persistence.secrets import SecretsStore

# -- dependency-free Keccak-256 (Ethereum), for the selector self-check ----------
#
# hashlib.sha3_256 is NIST SHA-3 (different padding) — it does NOT give Ethereum
# selectors. A full pure-Python Keccak-f[1600] avoids adding a dependency under the
# cooldown/pin policy just to verify five 4-byte constants.

_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_KECCAK_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_MASK64 = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK64


def _keccak256(data: bytes) -> bytes:
    rate = 136  # 1088-bit rate for Keccak-256
    buf = bytearray(data)
    buf.append(0x01)
    while len(buf) % rate != 0:
        buf.append(0x00)
    buf[-1] ^= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(buf), rate):
        block = buf[off : off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[i * 8 : i * 8 + 8], "little")
        for rnd in range(24):
            c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
            d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    a[x][y] ^= d[x]
            b = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _KECCAK_ROT[x][y])
            for x in range(5):
                for y in range(5):
                    a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
            a[0][0] ^= _KECCAK_RC[rnd]
    out = bytearray()
    for i in range(rate // 8):
        out += a[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out[:32])


def _selector(signature: str) -> str:
    return "0x" + _keccak256(signature.encode())[:4].hex()


# Known on-chain values the recorded responses encode (smoke: WETH/AERO staked CL).
_GAUGE = "0x9564" + "0" * 32 + "88f1"
_GAUGE_NPM = "0xe1f8" + "0" * 32 + "8b53"
_CLPOOL = "0x4e50" + "0" * 32 + "ce51"
_WETH = "0x4200000000000000000000000000000000000006"
_AERO = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
_STAKED_TOKEN_ID = 232923
_TICK_LOWER = 84000
_TICK_UPPER = 86200
_CURRENT_TICK = 85198  # 84000 <= 85198 < 86200 -> in range

_OWNER = "0xae5b…9790"

_SEL_SLOT0 = "0x3850c7bd"
_SEL_TOKEN0 = "0x0dfe1681"
_SEL_TOKEN1 = "0xd21220a7"
_SEL_SYMBOL = "0x95d89b41"
_SEL_DECIMALS = "0x313ce567"
_SEL_POSITIONS = "0x99fbab88"
_SEL_BALANCE_OF = "0x70a08231"
_SEL_TOKEN_OF_OWNER_BY_INDEX = "0x2f745c59"
_SEL_POOL = "0x16f0115b"
_SEL_NFT = "0x47ccca02"
_SEL_STAKED_VALUES = "0x4b937763"
_SEL_STAKED_BY_INDEX = "0x38463937"
_SEL_STAKED_LENGTH = "0xae775c32"


# -- ABI encoders (canonical; stand in for recorded RPC results) ----------------


def _uint_word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _int_word(value: int) -> bytes:
    return (value & ((1 << 256) - 1)).to_bytes(32, "big")  # two's complement


def _addr_word(address: str) -> bytes:
    return bytes.fromhex(address[2:]).rjust(32, b"\x00")


def _slot0_result(tick: int) -> bytes:
    return _uint_word(0) + _int_word(tick)  # word0 sqrtPrice (unused), word1 tick


def _positions_result(token0: str, token1: str, tl: int, tu: int, owed0: int, owed1: int) -> bytes:
    # positions(tokenId): token0=word2, token1=word3, tickLower=5, tickUpper=6,
    # tokensOwed0=10, tokensOwed1=11 (Slipstream shares the Uni-v3 layout; word4 is
    # tickSpacing rather than fee, which the decode skips).
    words = [_uint_word(0)] * 12
    words[2] = _addr_word(token0)
    words[3] = _addr_word(token1)
    words[5] = _int_word(tl)
    words[6] = _int_word(tu)
    words[10] = _uint_word(owed0)
    words[11] = _uint_word(owed1)
    return b"".join(words)


def _uint_array_result(values: list[int]) -> bytes:
    # uint256[]: word0 offset (0x20), word1 length, then the elements.
    return _uint_word(32) + _uint_word(len(values)) + b"".join(_uint_word(v) for v in values)


def _string_result(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _uint_word(32) + _uint_word(len(raw)) + raw.ljust(32, b"\x00")


def _hex(data: bytes) -> str:
    return "0x" + data.hex()


# Recorded responses keyed by (to-address, selector) for the staked-CL gauge chain.
def _staked_cl_responses(owed0: int = 0, owed1: int = 0) -> dict[tuple[str, str], str]:
    return {
        (_GAUGE, _SEL_POOL): _hex(_addr_word(_CLPOOL)),
        (_GAUGE, _SEL_NFT): _hex(_addr_word(_GAUGE_NPM)),
        (_GAUGE, _SEL_STAKED_VALUES): _hex(_uint_array_result([_STAKED_TOKEN_ID, 999999])),
        (_GAUGE_NPM, _SEL_POSITIONS): _hex(
            _positions_result(_WETH, _AERO, _TICK_LOWER, _TICK_UPPER, owed0, owed1)
        ),
        (_CLPOOL, _SEL_SLOT0): _hex(_slot0_result(_CURRENT_TICK)),
        (_WETH, _SEL_SYMBOL): _hex(_string_result("WETH")),
        (_AERO, _SEL_SYMBOL): _hex(_string_result("AERO")),
        (_WETH, _SEL_DECIMALS): _hex(_uint_word(18)),
        (_AERO, _SEL_DECIMALS): _hex(_uint_word(18)),
    }


# -- transport fake -------------------------------------------------------------

_REVERT = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "execution reverted"}}
).encode()


def _routed_client(
    responder: Callable[[str, str], tuple[int, bytes]],
) -> ResilientHttpClient:
    """A ResilientHttpClient whose physical attempt routes each `eth_call` to a
    canned response by `(to, selector)`. `max_retries=0` so a non-2xx path raises
    immediately with no backoff sleep."""
    client = ResilientHttpClient(source_name="lp-detail-test", cache_ttl_seconds=0.0, max_retries=0)

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        assert body_arg is not None
        call = json.loads(body_arg)["params"][0]
        status, body = responder(call["to"], call["data"][:10])
        return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _ok_responder(
    table: dict[tuple[str, str], str],
    calls: list[tuple[str, str]] | None = None,
) -> Callable[[str, str], tuple[int, bytes]]:
    """Route by `(to, selector)`. A missing entry is returned as a *revert* (a
    JSON-RPC error object), mirroring an `eth_call` to a getter the contract lacks —
    the signal the shape probe routes on. Optionally records the call sequence."""

    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        if calls is not None:
            calls.append((to.lower(), selector))
        result = table.get((to.lower(), selector))
        if result is None:
            return 200, _REVERT
        return 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()

    return _respond


def _store_with_base_rpc(tmp_path: Path) -> SecretsStore:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("base_rpc_url", "https://base-rpc.example/v1")
    return store


def _adapter(
    tmp_path: Path, responder: Callable[[str, str], tuple[int, bytes]]
) -> RpcLpDetailAdapter:
    return RpcLpDetailAdapter(
        secrets_store=_store_with_base_rpc(tmp_path),
        http_client=_routed_client(responder),
    )


# -- selector self-check --------------------------------------------------------


def test_every_selector_matches_its_keccak_signature() -> None:
    # Pin each selector against keccak256(signature)[:4] (the plan's self-check),
    # and the well-known ones against their public values. `known=None` for the
    # gauge selectors Plan 0048 adds — keccak is their only ground truth.
    checks: list[tuple[str, str, str | None]] = [
        (mod._SEL_SLOT0, "slot0()", "0x3850c7bd"),
        (mod._SEL_TOKEN0, "token0()", "0x0dfe1681"),
        (mod._SEL_TOKEN1, "token1()", "0xd21220a7"),
        (mod._SEL_SYMBOL, "symbol()", "0x95d89b41"),
        (mod._SEL_DECIMALS, "decimals()", "0x313ce567"),
        (mod._SEL_POSITIONS, "positions(uint256)", "0x99fbab88"),
        (mod._SEL_BALANCE_OF, "balanceOf(address)", "0x70a08231"),
        (mod._SEL_TOKEN_OF_OWNER_BY_INDEX, "tokenOfOwnerByIndex(address,uint256)", "0x2f745c59"),
        (mod._SEL_POOL, "pool()", "0x16f0115b"),  # used live in the smoke
        (mod._SEL_NFT, "nft()", "0x47ccca02"),  # used live in the smoke
        (mod._SEL_STAKED_VALUES, "stakedValues(address)", None),
        (mod._SEL_STAKED_BY_INDEX, "stakedByIndex(address,uint256)", None),
        (mod._SEL_STAKED_LENGTH, "stakedLength(address)", None),
    ]
    for selector, signature, known in checks:
        assert selector == _selector(signature), signature
        if known is not None:
            assert selector == known, signature


def test_keccak_helper_matches_known_selectors() -> None:
    # Guard the test's own Keccak against drift (if this breaks, the self-check
    # above is meaningless): well-known selectors must reproduce exactly.
    assert _selector("transfer(address,uint256)") == "0xa9059cbb"
    assert _selector("approve(address,uint256)") == "0x095ea7b3"


# -- staked-CL gauge chain (the core fix) ---------------------------------------


def test_staked_cl_gauge_chain_decodes_range_and_in_range(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses()))
    detail = adapter.fetch_lp_detail(
        chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
    )

    assert detail.tick_lower == _TICK_LOWER
    assert detail.tick_upper == _TICK_UPPER
    assert detail.current_tick == _CURRENT_TICK
    assert detail.in_range is True  # 84000 <= 85198 < 86200
    assert detail.uncollected_fees == []  # tokensOwed read 0 in the smoke


def test_staked_cl_walks_gauge_pool_nft_positions_slot0(tmp_path: Path) -> None:
    # Call-pattern assertion: the read routes through the gauge chain, not the
    # one-hop pool read. gauge.pool() (probe + slot0 source), gauge.nft(),
    # NPM.positions(tokenId), CLPool.slot0().
    calls: list[tuple[str, str]] = []
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses(), calls))
    adapter.fetch_lp_detail(chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID)

    assert (_GAUGE, _SEL_POOL) in calls  # gauge probe / CLPool resolution
    assert (_GAUGE, _SEL_NFT) in calls  # gauge -> NonfungiblePositionManager
    assert (_GAUGE_NPM, _SEL_POSITIONS) in calls  # bounds + owed + token pair
    assert (_CLPOOL, _SEL_SLOT0) in calls  # current tick from the CLPool
    # The gauge itself is never asked for slot0/positions (the original bug).
    assert (_GAUGE, _SEL_SLOT0) not in calls
    assert (_GAUGE, _SEL_POSITIONS) not in calls


def test_staked_cl_scales_owed_fees_when_present(tmp_path: Path) -> None:
    # When the struct's tokensOwed words are non-zero, they scale by decimals into
    # labelled PositionTokens (owed0 = 1.5 WETH-wei? -> use 18-dec values).
    owed_weth = 30_000_000_000_000_000  # 0.03 WETH (18 dec)
    owed_aero = 5_000_000_000_000_000_000  # 5 AERO (18 dec)
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses(owed_weth, owed_aero)))
    detail = adapter.fetch_lp_detail(
        chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
    )
    fees = {t.symbol: t for t in detail.uncollected_fees}
    assert set(fees) == {"WETH", "AERO"}
    assert fees["WETH"].amount == pytest.approx(0.03)
    assert fees["AERO"].amount == pytest.approx(5.0)
    assert fees["WETH"].address == _WETH


def test_uncollected_fees_are_struct_owed_words_as_is(tmp_path: Path) -> None:
    # Plan 0048 fee definition (option a): `uncollected_fees` is the position
    # struct's tokensOwed0/1 read directly and scaled by decimals — NOT recomputed
    # from feeGrowthInside. So a position whose owed words are 0 reports no fees
    # (the under-report case the smoke hit), and a non-zero owed word maps 1:1.
    zero = _adapter(tmp_path, _ok_responder(_staked_cl_responses(0, 0))).fetch_lp_detail(
        chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
    )
    assert zero.uncollected_fees == []  # owed words 0 -> empty (under-reports)

    owed_weth = 12_000_000_000_000_000  # 0.012 WETH, straight from tokensOwed0
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses(owed_weth, 0)))
    one_sided = adapter.fetch_lp_detail(
        chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
    )
    # Only the owing leg, value as-is from the struct word (AERO owed 0 -> dropped).
    assert len(one_sided.uncollected_fees) == 1
    assert one_sided.uncollected_fees[0].symbol == "WETH"
    assert one_sided.uncollected_fees[0].amount == pytest.approx(0.012)


def test_staked_cl_out_of_range_when_current_tick_above_upper(tmp_path: Path) -> None:
    table = _staked_cl_responses()
    table[(_CLPOOL, _SEL_SLOT0)] = _hex(_slot0_result(90000))  # above tick_upper
    detail = _adapter(tmp_path, _ok_responder(table)).fetch_lp_detail(
        chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
    )
    assert detail.current_tick == 90000
    assert detail.in_range is False


def test_staked_cl_output_is_deterministic(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses()))
    first = adapter.fetch_lp_detail(chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID)
    second = adapter.fetch_lp_detail(chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID)
    assert first == second


def test_gauge_getter_revert_raises_typed_lp_detail_error(tmp_path: Path) -> None:
    # gauge.pool() resolves (so it routes to the staked path), but gauge.nft()
    # reverts -> a typed LpDetailError, not a bare exception.
    table = _staked_cl_responses()
    del table[(_GAUGE, _SEL_NFT)]
    with pytest.raises(LpDetailError):
        _adapter(tmp_path, _ok_responder(table)).fetch_lp_detail(
            chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
        )


# -- shape-aware token-id resolution --------------------------------------------


def test_resolve_staked_cl_token_id_from_staked_values(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses()))
    token_id = adapter.resolve_univ3_token_id(chain="base", pool_address=_GAUGE, owner=_OWNER)
    assert token_id == _STAKED_TOKEN_ID  # the first staked NFT id


def test_resolve_staked_cl_returns_none_when_no_staked_position(tmp_path: Path) -> None:
    table = _staked_cl_responses()
    table[(_GAUGE, _SEL_STAKED_VALUES)] = _hex(_uint_array_result([]))  # owner staked nothing
    adapter = _adapter(tmp_path, _ok_responder(table))
    assert adapter.resolve_univ3_token_id(chain="base", pool_address=_GAUGE, owner=_OWNER) is None


def test_resolve_staked_cl_falls_back_to_length_and_index(tmp_path: Path) -> None:
    # A gauge variant without stakedValues: stakedValues reverts, so the resolver
    # falls back to stakedLength + stakedByIndex(0).
    table = _staked_cl_responses()
    del table[(_GAUGE, _SEL_STAKED_VALUES)]
    table[(_GAUGE, _SEL_STAKED_LENGTH)] = _hex(_uint_word(1))
    table[(_GAUGE, _SEL_STAKED_BY_INDEX)] = _hex(_uint_word(_STAKED_TOKEN_ID))
    adapter = _adapter(tmp_path, _ok_responder(table))
    assert adapter.resolve_univ3_token_id(chain="base", pool_address=_GAUGE, owner=_OWNER) == (
        _STAKED_TOKEN_ID
    )


def test_resolve_v2_amm_returns_none_no_nft(tmp_path: Path) -> None:
    # A v2 constant-product pool: neither gauge.pool() nor slot0() resolves, so the
    # resolver returns None and the position is left at discovery depth.
    v2_pool = "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59"
    adapter = _adapter(tmp_path, _ok_responder({}))  # every getter reverts
    assert adapter.resolve_univ3_token_id(chain="base", pool_address=v2_pool, owner=_OWNER) is None


# -- unstaked-CL (Uniswap-v3) path, preserved through the rework -----------------
#
# Live verification is still deferred (F3 — no in-scope wallet holds one); the read
# + tokenId resolution are exercised here against recorded responses.

_POOL = "0xc6962004f452be9203591991d15f6b388e09e8d0"  # an unstaked CL pool
_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_NPM = "0x03a520b32c04bf3beef7beb72e919cf822ed34f1"  # Base canonical NPM
_TOKEN_ID = 12345
_U_TICK_LOWER = -200
_U_TICK_UPPER = 200
_U_CURRENT_TICK = 100
_OWED0_RAW = 1_500_000  # USDC, 6 decimals -> 1.5
_OWED1_RAW = 20_000_000_000_000_000  # WETH, 18 decimals -> 0.02


def _univ3_responses() -> dict[tuple[str, str], str]:
    return {
        (_NPM, _SEL_POSITIONS): _hex(
            _positions_result(_USDC, _WETH, _U_TICK_LOWER, _U_TICK_UPPER, _OWED0_RAW, _OWED1_RAW)
        ),
        (_POOL, _SEL_SLOT0): _hex(_slot0_result(_U_CURRENT_TICK)),
        (_USDC, _SEL_SYMBOL): _hex(_string_result("USDC")),
        (_WETH, _SEL_SYMBOL): _hex(_string_result("WETH")),
        (_USDC, _SEL_DECIMALS): _hex(_uint_word(6)),
        (_WETH, _SEL_DECIMALS): _hex(_uint_word(18)),
    }


def _resolution_responses() -> dict[tuple[str, str], str]:
    table = _univ3_responses()
    table[(_POOL, _SEL_TOKEN0)] = _hex(_addr_word(_USDC))
    table[(_POOL, _SEL_TOKEN1)] = _hex(_addr_word(_WETH))
    table[(_NPM, _SEL_BALANCE_OF)] = _hex(_uint_word(1))
    table[(_NPM, _SEL_TOKEN_OF_OWNER_BY_INDEX)] = _hex(_uint_word(_TOKEN_ID))
    return table


def test_unstaked_cl_detail_decodes_via_token_id(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_univ3_responses()))
    detail = adapter.fetch_lp_detail(chain="base", pool_address=_POOL, token_id=_TOKEN_ID)

    assert detail.tick_lower == _U_TICK_LOWER
    assert detail.tick_upper == _U_TICK_UPPER
    assert detail.current_tick == _U_CURRENT_TICK
    assert detail.in_range is True
    fees = {t.symbol: t for t in detail.uncollected_fees}
    assert set(fees) == {"USDC", "WETH"}
    assert fees["USDC"].amount == pytest.approx(1.5)
    assert fees["WETH"].amount == pytest.approx(0.02)


def test_unstaked_cl_uses_canonical_npm_not_gauge_chain(tmp_path: Path) -> None:
    # Call-pattern assertion: a bare CL pool routes through the canonical NPM +
    # pool.slot0(), and does NOT walk the gauge chain (gauge.nft/stakedValues).
    calls: list[tuple[str, str]] = []
    adapter = _adapter(tmp_path, _ok_responder(_univ3_responses(), calls))
    adapter.fetch_lp_detail(chain="base", pool_address=_POOL, token_id=_TOKEN_ID)

    assert (_POOL, _SEL_POOL) in calls  # the probe (reverts -> bare path)
    assert (_NPM, _SEL_POSITIONS) in calls
    assert (_POOL, _SEL_SLOT0) in calls
    assert all(sel != _SEL_NFT for _to, sel in calls)
    assert all(sel != _SEL_STAKED_VALUES for _to, sel in calls)


def test_resolve_unstaked_cl_token_id_matches_pool_pair(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_resolution_responses()))
    token_id = adapter.resolve_univ3_token_id(chain="base", pool_address=_POOL, owner=_OWNER)
    assert token_id == _TOKEN_ID


def test_resolve_unstaked_cl_returns_none_when_wallet_holds_no_position(tmp_path: Path) -> None:
    table = _resolution_responses()
    table[(_NPM, _SEL_BALANCE_OF)] = _hex(_uint_word(0))  # owns no positions
    adapter = _adapter(tmp_path, _ok_responder(table))
    assert adapter.resolve_univ3_token_id(chain="base", pool_address=_POOL, owner=_OWNER) is None


def test_resolve_then_fetch_two_hop_round_trip(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_resolution_responses()))
    token_id = adapter.resolve_univ3_token_id(chain="base", pool_address=_POOL, owner=_OWNER)
    assert token_id is not None
    detail = adapter.fetch_lp_detail(chain="base", pool_address=_POOL, token_id=token_id)
    assert detail.current_tick == _U_CURRENT_TICK
    assert detail.in_range is True


# -- typed-error contract -------------------------------------------------------


def test_fetch_without_token_id_raises_typed_error(tmp_path: Path) -> None:
    # The one-hop pool_address read was removed; a None token_id has no read path.
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses()))
    with pytest.raises(LpDetailError):
        adapter.fetch_lp_detail(chain="base", pool_address=_GAUGE)


def test_missing_rpc_url_raises_typed_config_error(tmp_path: Path) -> None:
    store = SecretsStore(tmp_path / "secrets.json", environ={})  # no base_rpc_url
    adapter = RpcLpDetailAdapter(
        secrets_store=store, http_client=_routed_client(_ok_responder(_staked_cl_responses()))
    )
    with pytest.raises(LpDetailConfigError):
        adapter.fetch_lp_detail(chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID)


def test_unsupported_chain_raises_typed_config_error(tmp_path: Path) -> None:
    # arbitrum / optimism have no reserved RPC-URL secret in the schema.
    adapter = _adapter(tmp_path, _ok_responder(_staked_cl_responses()))
    with pytest.raises(LpDetailConfigError):
        adapter.fetch_lp_detail(chain="arbitrum", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID)


def test_http_500_raises_typed_unavailable_error(tmp_path: Path) -> None:
    # A transport outage during the shape probe surfaces (it is NOT swallowed as a
    # revert) — only a JSON-RPC revert is routing signal.
    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        return 500, b"upstream boom"

    with pytest.raises(UpstreamUnavailableError):
        _adapter(tmp_path, _respond).fetch_lp_detail(
            chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
        )


def test_http_429_raises_typed_rate_limited_error(tmp_path: Path) -> None:
    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        return 429, b'{"error":"slow down"}'

    with pytest.raises(RateLimitedError):
        _adapter(tmp_path, _respond).fetch_lp_detail(
            chain="base", pool_address=_GAUGE, token_id=_STAKED_TOKEN_ID
        )


def test_implements_lp_position_detail_source_protocol(tmp_path: Path) -> None:
    from market_analyser.data.sources import LpPositionDetailSource

    adapter = RpcLpDetailAdapter(secrets_store=_store_with_base_rpc(tmp_path))
    assert isinstance(adapter, LpPositionDetailSource)
