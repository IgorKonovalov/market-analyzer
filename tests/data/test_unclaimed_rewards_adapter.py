"""Plan 0084 phase 5 done-when: the on-chain unclaimed-reward reader.

`RpcUnclaimedRewardsAdapter` reads a gauge-staked position's owed-but-unclaimed
emissions via the gauge's `earned()`. The "fixture" is the canonical ABI encoding
of known on-chain values routed by `(to, selector)` — a getter the contract lacks
is a JSON-RPC *revert*, the signal the adapter routes on. The three new selectors
are recomputed here from their signatures with a dependency-free Keccak-256 and
asserted equal (the plan's selector self-check).

Pinned: a staked CL position resolves reward token + earned + symbol/decimals into
a priced `RewardAmount` (34.2 AERO ≈ $18); a non-gauge address (rewardToken reverts)
and a zero `earned()` both yield `[]`; pricing is best-effort (`usd_value=None`
when unpriced); the read is read-only (only `eth_call`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.unclaimed_rewards import (
    _SEL_EARNED_CL,
    _SEL_EARNED_V2,
    _SEL_REWARD_TOKEN,
    RpcUnclaimedRewardsAdapter,
)
from market_analyser.data.sources import UnclaimedRewardsSource
from market_analyser.defi.models import Chain, DefiPosition, PositionToken
from market_analyser.persistence.secrets import SecretsStore

# -- dependency-free Keccak-256 (Ethereum), for the selector self-check ----------

_RC = [
    0x1, 0x8082, 0x800000000000808A, 0x8000000080008000, 0x808B, 0x80000001,
    0x8000000080008081, 0x8000000000008009, 0x8A, 0x88, 0x80008009, 0x8000000A,
    0x8000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x80000001, 0x8000000080008008,
]  # fmt: skip
_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]  # fmt: skip
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak256(data: bytes) -> bytes:
    rate = 136
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
                    b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
            for x in range(5):
                for y in range(5):
                    a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
            a[0][0] ^= _RC[rnd]
    out = bytearray()
    for i in range(rate // 8):
        out += a[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out[:32])


def _selector(signature: str) -> str:
    return "0x" + _keccak256(signature.encode())[:4].hex()


# -- known on-chain values -------------------------------------------------------

_GAUGE = "0x33ab" + "0" * 32 + "cd12"
_AERO = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
_WETH = "0x4200000000000000000000000000000000000006"
_OWNER = "0xae5b…9790"
_TOKEN_ID = 232923
_EARNED_RAW = 34_200_000_000_000_000_000  # 34.2 AERO (18 decimals)

_SEL_SYMBOL = "0x95d89b41"
_SEL_DECIMALS = "0x313ce567"


def _uint_word(v: int) -> bytes:
    return v.to_bytes(32, "big")


def _addr_word(a: str) -> bytes:
    return bytes.fromhex(a[2:]).rjust(32, b"\x00")


def _string_result(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _uint_word(32) + _uint_word(len(raw)) + raw.ljust(32, b"\x00")


def _hex(data: bytes) -> str:
    return "0x" + data.hex()


_REVERT = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "reverted"}}
).encode()


def _responses() -> dict[tuple[str, str], str]:
    return {
        (_GAUGE, _SEL_REWARD_TOKEN): _hex(_addr_word(_AERO)),
        (_GAUGE, _SEL_EARNED_CL): _hex(_uint_word(_EARNED_RAW)),
        (_AERO, _SEL_SYMBOL): _hex(_string_result("AERO")),
        (_AERO, _SEL_DECIMALS): _hex(_uint_word(18)),
    }


def _routed_client(
    table: dict[tuple[str, str], str], methods: list[str] | None = None
) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="unclaimed-test", cache_ttl_seconds=0.0, max_retries=0)

    def _perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        assert body_arg is not None
        payload = json.loads(body_arg)
        if methods is not None:
            methods.append(payload["method"])
        call = payload["params"][0]
        result = table.get((call["to"].lower(), call["data"][:10]))
        if result is None:
            return HttpResponse(status_code=200, headers={}, body=_REVERT, elapsed_seconds=0.0)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
        return HttpResponse(status_code=200, headers={}, body=body, elapsed_seconds=0.0)

    client._perform_request = _perform  # type: ignore[method-assign, assignment]
    return client


class _FakeLpDetail:
    """Resolves the staked NFT token id (the shape-aware resolver's job)."""

    def __init__(self, token_id: int | None) -> None:
        self._token_id = token_id

    def resolve_univ3_token_id(self, *, chain: Chain, pool_address: str, owner: str) -> int | None:
        return self._token_id

    def fetch_lp_detail(self, *, chain: Chain, pool_address: str, token_id: int | None = None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class _FakePrice:
    def __init__(self, price: float | None) -> None:
        self._price = price

    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return self._price


def _store(tmp_path: Path) -> SecretsStore:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("base_rpc_url", "https://base-rpc.example/v1")
    return store


def _position(pool: str | None = _GAUGE) -> DefiPosition:
    return DefiPosition(
        position_id="base:aerodrome:aave-weth",
        chain="base",
        protocol="aerodrome",
        kind="lp",
        tokens=[PositionToken(symbol="WETH", address=_WETH, amount=0.5)],
        usd_value=1000.0,
        pool_address=pool,
    )


def _adapter(
    tmp_path: Path,
    *,
    table: dict[tuple[str, str], str] | None = None,
    token_id: int | None = _TOKEN_ID,
    price: float | None = 0.52,
    methods: list[str] | None = None,
) -> RpcUnclaimedRewardsAdapter:
    return RpcUnclaimedRewardsAdapter(
        secrets_store=_store(tmp_path),
        lp_detail=_FakeLpDetail(token_id),
        price_source=_FakePrice(price),
        http_client=_routed_client(table if table is not None else _responses(), methods),
        now=lambda: 1_730_000_000,
        sleep=lambda _s: None,
    )


def test_selectors_match_their_signatures() -> None:
    assert _selector("rewardToken()") == _SEL_REWARD_TOKEN
    assert _selector("earned(address,uint256)") == _SEL_EARNED_CL
    assert _selector("earned(address)") == _SEL_EARNED_V2


def test_reads_and_prices_the_staked_positions_unclaimed_reward(tmp_path: Path) -> None:
    rewards = _adapter(tmp_path).fetch_unclaimed(position=_position(), owner=_OWNER)
    assert len(rewards) == 1
    reward = rewards[0]
    assert reward.symbol == "AERO"
    assert reward.amount == 34.2
    assert reward.usd_value is not None
    assert abs(reward.usd_value - 34.2 * 0.52) < 1e-9


def test_unpriced_reward_reports_amount_with_none_usd(tmp_path: Path) -> None:
    reward = _adapter(tmp_path, price=None).fetch_unclaimed(position=_position(), owner=_OWNER)[0]
    assert reward.amount == 34.2
    assert reward.usd_value is None


def test_non_gauge_address_yields_no_rewards(tmp_path: Path) -> None:
    # rewardToken() reverts (empty table) → not a gauge → [].
    assert _adapter(tmp_path, table={}).fetch_unclaimed(position=_position(), owner=_OWNER) == []


def test_zero_earned_yields_no_rewards(tmp_path: Path) -> None:
    table = {**_responses(), (_GAUGE, _SEL_EARNED_CL): _hex(_uint_word(0))}
    assert _adapter(tmp_path, table=table).fetch_unclaimed(position=_position(), owner=_OWNER) == []


def test_no_pool_address_yields_no_rewards(tmp_path: Path) -> None:
    assert _adapter(tmp_path).fetch_unclaimed(position=_position(pool=None), owner=_OWNER) == []


def test_only_the_eth_call_read_method_is_issued(tmp_path: Path) -> None:
    methods: list[str] = []
    _adapter(tmp_path, methods=methods).fetch_unclaimed(position=_position(), owner=_OWNER)
    assert set(methods) == {"eth_call"}


def test_satisfies_the_protocol(tmp_path: Path) -> None:
    assert isinstance(_adapter(tmp_path), UnclaimedRewardsSource)
