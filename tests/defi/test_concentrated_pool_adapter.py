"""Plan 0086 phase 2 — concentrated-liquidity Quoter adapter.

Phase-2 done-when claims pinned here:
(a) against recorded Quoter / slot0 fixtures the adapter returns `ExecutableQuote`s
    whose buy_cost / sell_proceeds equal the fixture Quoter outputs (decimals-
    adjusted), each validated positive/finite, carrying the marginal reference from
    slot0 and the fee tier in bps;
(b) fee-tier enumeration yields one quote per configured tier that has a pool — a
    tier whose `getPool` returns the zero address is skipped, not errored;
(c) revert taxonomy by call site (ADR-0086): a **quote-leg** revert omits the pool
    (a thin tier no longer aborts the scan), while a **structural-read** revert
    (getPool / slot0 / decimals / fee) and a malformed / too-short *successful* result
    still raise the typed `ConcentratedPoolError`, and a zero Quoter output raises at
    the `ExecutableQuote` boundary — never a zeroed / NaN quote, never silently omitted;
(d) the adapter satisfies `ExecutableQuoteSource` and is registry-reachable;
(e) config / caller-bug taxonomy (missing RPC URL, unsupported chain, unconfigured
    pair, non-positive trade_size);
(f) the Slipstream fork path reads the pool `fee()` for its reported tier;
(g) the adapter's source carries no key / signing / state-changing RPC — the only
    JSON-RPC method is `eth_call` (source + AST scan);
(h) every function selector matches keccak256(signature)[:4] (selector self-check).

All offline — a fake transport replaces `ResilientHttpClient._perform_request` and
routes each `eth_call` by `(to, selector, tier)`; no live RPC is contacted.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import concentrated_pools as cl_module
from market_analyser.data.adapters.concentrated_pools import (
    _DEFAULT_ADAPTER_USER_AGENT,
    ConcentratedPoolConfigError,
    ConcentratedPoolError,
    ConcentratedPoolPriceAdapter,
    ConcentratedVenueConfig,
    _marginal_from_sqrt,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.sources import ExecutableQuoteSource
from market_analyser.defi.models import ExecutableQuote
from market_analyser.persistence.secrets import SecretsStore

_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

# -- dependency-free Keccak-256 (Ethereum), for the selector self-check ----------
# hashlib.sha3_256 is NIST SHA-3 (different padding) and does NOT give Ethereum
# selectors; a pure-Python Keccak-f[1600] verifies the constants dependency-free.

_KECCAK_RC = [
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
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


# -- selectors (mirror the module constants) -------------------------------------

_SEL_SLOT0 = "0x3850c7bd"
_SEL_DECIMALS = "0x313ce567"
_SEL_FEE = "0xddca3f43"
_SEL_GETPOOL_UNIV3 = "0x1698ee82"
_SEL_GETPOOL_SLIP = "0x28af8d0b"
_SEL_QIN_UNIV3 = "0xc6a5026a"
_SEL_QOUT_UNIV3 = "0xbd21704a"
_SEL_QIN_SLIP = "0x9e7defe6"
_SEL_QOUT_SLIP = "0xfa6af908"

_GETPOOL_SELECTORS = {_SEL_GETPOOL_UNIV3, _SEL_GETPOOL_SLIP}
_QUOTER_SELECTORS = {_SEL_QIN_UNIV3, _SEL_QOUT_UNIV3, _SEL_QIN_SLIP, _SEL_QOUT_SLIP}

# -- addresses (real tokens are constants; venue/pool addresses fabricated) -------

_WETH = "0x4200000000000000000000000000000000000006"  # token0 (lower address)
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"  # token1
_FACTORY = "0xffff000000000000000000000000000000000001"
_QUOTER = "0xffff000000000000000000000000000000000002"
_POOL_500 = "0xeeee000000000000000000000000000000000500"
_POOL_3000 = "0xaaaa000000000000000000000000000000003000"
_POOL_10000 = "0xbbbb000000000000000000000000000000010000"
_ZERO_ADDRESS = "0x" + "00" * 20

# A slot0 sqrtPriceX96 that decodes (WETH token0, dec diff 12) to marginal ~3000.
_SQRT_3000 = 4339505179874779475002393

# -- ABI encoders (stand in for recorded RPC results) ----------------------------


def _uint_word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _addr_word(address: str) -> bytes:
    return bytes.fromhex(address[2:]).rjust(32, b"\x00")


def _hex(data: bytes) -> str:
    return "0x" + data.hex()


def _slot0_result(sqrt_price: int) -> str:
    # slot0() -> (sqrtPriceX96, tick, ...); word0 is all the adapter reads.
    return _hex(_uint_word(sqrt_price) + _uint_word(0))


def _quoter_result(amount: int) -> str:
    # QuoterV2 returns (amount, sqrtPriceX96After, initializedTicksCrossed, gasEstimate).
    return _hex(_uint_word(amount) + _uint_word(0) + _uint_word(0) + _uint_word(21000))


# -- transport fake --------------------------------------------------------------


def _fake_key(to: str, data: str) -> tuple[str, str, int | None]:
    """Route key: getPool / Quoter calls disambiguate by their tier argument
    (getPool 3rd arg, Quoter struct 4th field); everything else by (to, selector)."""
    selector = data[:10]
    if selector in _GETPOOL_SELECTORS:
        return (to, selector, int(data[10 + 128 : 10 + 192], 16))
    if selector in _QUOTER_SELECTORS:
        return (to, selector, int(data[10 + 192 : 10 + 256], 16))
    return (to, selector, None)


class _FakeRpc:
    """Replaces `ResilientHttpClient._perform_request`, routing each `eth_call` by
    `(to, selector, tier)`. A missing entry returns a JSON-RPC error object (a
    revert), so unmapped getters surface as `ConcentratedPoolError`."""

    def __init__(self, responses: dict[tuple[str, str, int | None], str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, int | None]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        payload = json.loads(body)
        call = payload["params"][0]
        key = _fake_key(call["to"].lower(), call["data"])
        self.calls.append(key)
        result = self._responses.get(key)
        if result is None:
            return _json_response(
                200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "revert"}}
            )
        return _json_response(200, {"jsonrpc": "2.0", "id": 1, "result": result})


def _json_response(status_code: int, payload: Any) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={},
        body=json.dumps(payload).encode("utf-8"),
        elapsed_seconds=0.0,
    )


def _static_response(status_code: int) -> Any:
    class _Static:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(self, method: str, url: str, b: Any, h: Any, *, proxy: Any) -> HttpResponse:
            self.attempts += 1
            return HttpResponse(
                status_code=status_code, headers={}, body=b"{}", elapsed_seconds=0.0
            )

    return _Static()


def _decimals_responses() -> dict[tuple[str, str, int | None], str]:
    return {
        (_WETH, _SEL_DECIMALS, None): _hex(_uint_word(18)),
        (_USDC, _SEL_DECIMALS, None): _hex(_uint_word(6)),
    }


def _univ3_venue(tiers: tuple[int, ...] = (3000,)) -> ConcentratedVenueConfig:
    return ConcentratedVenueConfig(
        dex="uniswap-v3",
        chain="base",
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        factory=_FACTORY,
        quoter=_QUOTER,
        quoter_kind="uniswap-v3",
        tiers=tiers,
    )


def _secrets(*, base_rpc_url: str | None = "https://rpc.test/base") -> SecretsStore:
    environ = {"MARKET_ANALYSER_BASE_RPC_URL": base_rpc_url} if base_rpc_url else {}
    return SecretsStore(Path("does-not-exist.json"), environ=environ)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any,
    venues: Any,
    base_rpc_url: str | None = "https://rpc.test/base",
    max_retries: int = 0,
) -> ConcentratedPoolPriceAdapter:
    client = ResilientHttpClient(
        source_name="concentrated-test", cache_ttl_seconds=0.0, max_retries=max_retries
    )
    monkeypatch.setattr(client, "_perform_request", transport)
    return ConcentratedPoolPriceAdapter(
        secrets_store=_secrets(base_rpc_url=base_rpc_url),
        venues=venues,
        http_client=client,
        sleep=lambda _s: None,
        now=lambda: _NOW,
    )


# -- (h) selector self-check -----------------------------------------------------


_SELECTOR_SIGNATURES = {
    _SEL_SLOT0: "slot0()",
    _SEL_DECIMALS: "decimals()",
    _SEL_FEE: "fee()",
    _SEL_GETPOOL_UNIV3: "getPool(address,address,uint24)",
    _SEL_GETPOOL_SLIP: "getPool(address,address,int24)",
    _SEL_QIN_UNIV3: "quoteExactInputSingle((address,address,uint256,uint24,uint160))",
    _SEL_QOUT_UNIV3: "quoteExactOutputSingle((address,address,uint256,uint24,uint160))",
    _SEL_QIN_SLIP: "quoteExactInputSingle((address,address,uint256,int24,uint160))",
    _SEL_QOUT_SLIP: "quoteExactOutputSingle((address,address,uint256,int24,uint160))",
}


def test_every_selector_matches_its_keccak_signature() -> None:
    for selector, signature in _SELECTOR_SIGNATURES.items():
        assert selector == _selector(signature), signature
    # And the module's constants agree with these (the adapter issues the same bytes).
    assert cl_module._SEL_GET_POOL["uniswap-v3"] == _SEL_GETPOOL_UNIV3
    assert cl_module._SEL_QUOTE_EXACT_IN["uniswap-v3"] == _SEL_QIN_UNIV3
    assert cl_module._SEL_QUOTE_EXACT_OUT["slipstream"] == _SEL_QOUT_SLIP


# -- (d) contract ----------------------------------------------------------------


def test_adapter_satisfies_executable_quote_source_protocol() -> None:
    assert isinstance(ConcentratedPoolPriceAdapter(secrets_store=_secrets()), ExecutableQuoteSource)


def test_reachable_through_one_registry_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_QUOTER, _SEL_QIN_UNIV3, 3000): _quoter_result(2994_000000),
        (_QUOTER, _SEL_QOUT_UNIV3, 3000): _quoter_result(3006_000000),
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    registry: Mapping[str, ExecutableQuoteSource] = {"concentrated": adapter}
    selected = registry["concentrated"]
    assert isinstance(selected, ExecutableQuoteSource)
    quotes = selected.fetch_executable_quotes("WETH/USDC", trade_size=1.0)
    assert [q.pool_id for q in quotes] == [_POOL_3000]


# -- (a) happy path: executable quote from the Quoter ----------------------------


def test_quote_equals_fixture_quoter_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_QUOTER, _SEL_QIN_UNIV3, 3000): _quoter_result(2994_000000),  # sell -> 2994 USDC
        (_QUOTER, _SEL_QOUT_UNIV3, 3000): _quoter_result(3006_000000),  # buy  -> 3006 USDC
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))

    (q,) = adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)

    assert isinstance(q, ExecutableQuote)
    assert q.pool_id == _POOL_3000
    assert q.dex == "uniswap-v3"
    assert q.chain == "base"
    assert q.pair == "WETH/USDC"
    assert q.trade_size == 1.0
    assert q.as_of == _NOW
    # Quoter outputs, decimals-adjusted (USDC 6 dp).
    assert q.sell_proceeds == pytest.approx(2994.0)
    assert q.buy_cost == pytest.approx(3006.0)
    assert q.sell_proceeds > 0 and q.buy_cost > 0
    # Marginal from slot0 sqrtPriceX96, and it is a real ETH price.
    assert q.marginal_price == pytest.approx(
        _marginal_from_sqrt(_SQRT_3000, base_decimals=18, quote_decimals=6, is_base_token0=True)
    )
    assert q.marginal_price == pytest.approx(3000.0, abs=1e-6)
    # Uni-v3 fee tier 3000 PPM -> 30 bps.
    assert q.fee_tier == 30


# -- (b) fee-tier enumeration ----------------------------------------------------


def test_fee_tier_enumeration_one_quote_per_pool_that_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        **_decimals_responses(),
        # 500 has no pool (zero address -> skipped); 3000 and 10000 do.
        (_FACTORY, _SEL_GETPOOL_UNIV3, 500): _hex(_addr_word(_ZERO_ADDRESS)),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 10000): _hex(_addr_word(_POOL_10000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_POOL_10000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_QUOTER, _SEL_QIN_UNIV3, 3000): _quoter_result(2994_000000),
        (_QUOTER, _SEL_QOUT_UNIV3, 3000): _quoter_result(3006_000000),
        (_QUOTER, _SEL_QIN_UNIV3, 10000): _quoter_result(2960_000000),
        (_QUOTER, _SEL_QOUT_UNIV3, 10000): _quoter_result(3040_000000),
    }
    venue = _univ3_venue(tiers=(500, 3000, 10000))
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(venue,))

    quotes = adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)

    # One quote per tier that HAS a pool; the 500 tier is skipped, not errored.
    assert [q.pool_id for q in quotes] == [_POOL_3000, _POOL_10000]
    assert [q.fee_tier for q in quotes] == [30, 100]
    assert quotes[1].buy_cost == pytest.approx(3040.0)


# -- (c) revert taxonomy by call site: omit quote-leg, raise structural (ADR-0086) --


def test_quote_leg_revert_omits_pool_not_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # slot0 + getPool succeed, but the Quoter legs revert (no Quoter entries) — the
    # pool has no executable price at the size, so it is omitted, not raised. This is
    # the behaviour flip from the old raise-on-any-revert (Plan 0086 phase-4 finding).
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        # No Quoter entries -> the exact-input (sell) call reverts.
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    assert adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0) == []


def test_buy_leg_revert_also_omits_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    # The sell (exact-input) leg succeeds but the buy (exact-output) leg reverts — a
    # round trip needs both legs, so the pool is still omitted (ADR-0086).
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_QUOTER, _SEL_QIN_UNIV3, 3000): _quoter_result(2994_000000),  # sell ok
        # No exact-output entry -> the buy leg reverts.
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    assert adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0) == []


def test_dust_tier_omitted_deep_tiers_still_priced(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mixed-venue case: the 500 tier's quote legs revert (dust — it has a pool and
    # slot0 but no routable size), while 3000 and 10000 price fine. The scan returns
    # exactly the two deep-tier quotes — one dust tier no longer aborts the whole scan.
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 500): _hex(_addr_word(_POOL_500)),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 10000): _hex(_addr_word(_POOL_10000)),
        (_POOL_500, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_POOL_10000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        # 500 has a pool + slot0 but NO Quoter entries -> both legs revert (dust).
        (_QUOTER, _SEL_QIN_UNIV3, 3000): _quoter_result(2994_000000),
        (_QUOTER, _SEL_QOUT_UNIV3, 3000): _quoter_result(3006_000000),
        (_QUOTER, _SEL_QIN_UNIV3, 10000): _quoter_result(2960_000000),
        (_QUOTER, _SEL_QOUT_UNIV3, 10000): _quoter_result(3040_000000),
    }
    venue = _univ3_venue(tiers=(500, 3000, 10000))
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(venue,))

    quotes = adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)

    assert [q.pool_id for q in quotes] == [_POOL_3000, _POOL_10000]
    assert [q.fee_tier for q in quotes] == [30, 100]
    assert quotes[0].buy_cost == pytest.approx(3006.0)


def test_getpool_revert_raises_not_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # A structural factory read (`getPool`) that reverts is an operator-visible
    # misconfig, not a thin pool — it raises, it does not omit the venue silently.
    responses = {**_decimals_responses()}  # no getPool entry -> the factory read reverts
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    with pytest.raises(ConcentratedPoolError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_slot0_revert_raises_not_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # slot0 is a structural read — a revert raises, never omits.
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        # No slot0 entry -> the structural pool read reverts.
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    with pytest.raises(ConcentratedPoolError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_decimals_revert_raises_not_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # decimals() is a structural read (shared across a venue's tiers) — a revert raises.
    responses: dict[tuple[str, str, int | None], str] = {}  # no decimals -> first read reverts
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    with pytest.raises(ConcentratedPoolError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_slipstream_fee_revert_raises_not_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even when both quote legs succeed, a revert on the Slipstream structural `fee()`
    # read raises — the omit path is narrowly the quote legs, never a structural read.
    slip_factory = "0xcccc000000000000000000000000000000000001"
    slip_quoter = "0xcccc000000000000000000000000000000000002"
    slip_pool = "0xdddd000000000000000000000000000000000100"
    venue = ConcentratedVenueConfig(
        dex="aerodrome-slipstream",
        chain="base",
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        factory=slip_factory,
        quoter=slip_quoter,
        quoter_kind="slipstream",
        tiers=(100,),
    )
    responses = {
        **_decimals_responses(),
        (slip_factory, _SEL_GETPOOL_SLIP, 100): _hex(_addr_word(slip_pool)),
        (slip_pool, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (slip_quoter, _SEL_QIN_SLIP, 100): _quoter_result(2998_000000),
        (slip_quoter, _SEL_QOUT_SLIP, 100): _quoter_result(3002_000000),
        # No fee() entry -> the structural fee read reverts AFTER the quote legs succeed.
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(venue,))
    with pytest.raises(ConcentratedPoolError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_truncated_quote_result_raises_not_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # A *successful* (2xx) but too-short quote result is a decode failure / shape drift,
    # NOT an execution-revert — it must raise, never be swallowed by the omit path.
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_QUOTER, _SEL_QIN_UNIV3, 3000): "0x1234",  # 2xx but too short for word0
        (_QUOTER, _SEL_QOUT_UNIV3, 3000): _quoter_result(3006_000000),
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    with pytest.raises(ConcentratedPoolError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_zero_quoter_output_raises_at_boundary_not_zeroed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (_QUOTER, _SEL_QIN_UNIV3, 3000): _quoter_result(0),  # zero sell proceeds
        (_QUOTER, _SEL_QOUT_UNIV3, 3000): _quoter_result(3006_000000),
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    with pytest.raises(ValidationError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_malformed_slot0_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        **_decimals_responses(),
        (_FACTORY, _SEL_GETPOOL_UNIV3, 3000): _hex(_addr_word(_POOL_3000)),
        (_POOL_3000, _SEL_SLOT0, None): "0x1234",  # too short for word0
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(_univ3_venue(),))
    with pytest.raises(ConcentratedPoolError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


# -- (f) Slipstream fork path reads pool fee() -----------------------------------


def test_slipstream_reads_pool_fee_for_reported_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    slip_factory = "0xcccc000000000000000000000000000000000001"
    slip_quoter = "0xcccc000000000000000000000000000000000002"
    slip_pool = "0xdddd000000000000000000000000000000000100"
    venue = ConcentratedVenueConfig(
        dex="aerodrome-slipstream",
        chain="base",
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        factory=slip_factory,
        quoter=slip_quoter,
        quoter_kind="slipstream",
        tiers=(100,),  # tick spacing, not a fee
    )
    responses = {
        **_decimals_responses(),
        (slip_factory, _SEL_GETPOOL_SLIP, 100): _hex(_addr_word(slip_pool)),
        (slip_pool, _SEL_SLOT0, None): _slot0_result(_SQRT_3000),
        (slip_pool, _SEL_FEE, None): _hex(_uint_word(400)),  # 400 PPM -> 4 bps
        (slip_quoter, _SEL_QIN_SLIP, 100): _quoter_result(2998_000000),
        (slip_quoter, _SEL_QOUT_SLIP, 100): _quoter_result(3002_000000),
    }
    adapter = _adapter(monkeypatch, transport=_FakeRpc(responses), venues=(venue,))

    (q,) = adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)
    assert q.dex == "aerodrome-slipstream"
    assert q.fee_tier == 4  # read from pool.fee(), not the tick spacing
    assert q.buy_cost == pytest.approx(3002.0)


# -- (e) config / caller-bug taxonomy --------------------------------------------


def test_missing_rpc_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(
        monkeypatch, transport=_FakeRpc({}), venues=(_univ3_venue(),), base_rpc_url=None
    )
    with pytest.raises(ConcentratedPoolConfigError, match="RPC URL"):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_unsupported_chain_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = ConcentratedVenueConfig(
        dex="uniswap-v3",
        chain="arbitrum",  # valid Chain, no reserved RPC-URL secret
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        factory=_FACTORY,
        quoter=_QUOTER,
        quoter_kind="uniswap-v3",
        tiers=(3000,),
    )
    adapter = _adapter(monkeypatch, transport=_FakeRpc({}), venues=(venue,))
    with pytest.raises(ConcentratedPoolConfigError, match="chain"):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_unconfigured_pair_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, transport=_FakeRpc({}), venues=(_univ3_venue(),))
    assert adapter.fetch_executable_quotes("WBTC/USDC", trade_size=1.0) == []


def test_non_positive_trade_size_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, transport=_FakeRpc({}), venues=(_univ3_venue(),))
    with pytest.raises(ValueError, match="trade_size"):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=0.0)


def test_429_raises_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, transport=_static_response(429), venues=(_univ3_venue(),))
    with pytest.raises(RateLimitedError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


def test_500_raises_upstream_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, transport=_static_response(500), venues=(_univ3_venue(),))
    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_executable_quotes("WETH/USDC", trade_size=1.0)


# -- configurable User-Agent -----------------------------------------------------


def test_default_user_agent_is_not_the_blocked_default() -> None:
    adapter = ConcentratedPoolPriceAdapter(secrets_store=_secrets())
    assert adapter._http._user_agent == _DEFAULT_ADAPTER_USER_AGENT
    assert "Mozilla" in adapter._http._user_agent


def test_user_agent_is_configurable() -> None:
    adapter = ConcentratedPoolPriceAdapter(secrets_store=_secrets(), user_agent="Custom/9")
    assert adapter._http._user_agent == "Custom/9"


# -- (g) read-only proof: no key / signing / state-changing RPC ------------------


def test_adapter_source_is_read_only() -> None:
    """ADR-0080/0072/0041 read-only proof: the CL adapter carries no key material,
    no signing path, and no state-changing RPC — a source scan rejects the giveaway
    tokens, and an AST walk asserts the only JSON-RPC `method` string is
    `eth_call` (the Quoter is a staticcall simulation, not a transaction)."""
    source = Path(cl_module.__file__).read_text(encoding="utf-8")
    forbidden = [
        r"private[_-]?key",
        r"eip[_-]?712",
        r"\bsign\b",
        r"sign_transaction",
        r"eth_sendtransaction",
        r"eth_sendrawtransaction",
        r"sendrawtransaction",
        r"personal_sign",
        r"mnemonic",
        r"\bwallet\b",
    ]
    offenders = [p for p in forbidden if re.search(p, source, re.IGNORECASE)]
    assert offenders == [], f"CL adapter source contains write/signing tokens: {offenders}"

    tree = ast.parse(source)
    method_values = _rpc_method_values(tree)
    assert method_values == {"eth_call"}, f"unexpected JSON-RPC methods: {method_values}"


def _rpc_method_values(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "method"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                found.add(value.value)
    return found
