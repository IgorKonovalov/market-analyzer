"""Plan 0079 phase 1 — on-chain DEX pool-price adapter.

Phase-1 done-when claims pinned here:
(a) against a recorded JSON-RPC fixture, `fetch_pool_quotes` returns a `PoolQuote`
    per configured pool for a pair + size, each price validated positive and
    finite, correctly oriented (quote-per-base) regardless of the pool's on-chain
    token0/token1 ordering, carrying dex/chain/pair/depth/fee/trade_size/as_of;
(b) a malformed / shape-broken RPC response raises the typed `PoolPriceError`
    (JSON-RPC error object, too-short result, non-hex result, token0 matching
    neither configured token, a zero reserve) — never a silently zeroed price;
(c) a 429 raises `RateLimitedError`, a 5xx `UpstreamUnavailableError`; a missing
    RPC URL or an unsupported chain raises `PoolPriceConfigError`;
(d) the adapter satisfies `isinstance(adapter, PoolPriceSource)` and is reachable
    through one registry entry (`Mapping[str, PoolPriceSource]`);
(e) an unconfigured pair returns `[]`; a non-positive trade_size is a caller bug
    (`ValueError`), not an upstream error;
(f) the adapter carries no private key, no signing path, and no state-changing RPC
    method anywhere in its source — only `eth_call` (source scan).

All offline — the fake transport replaces `ResilientHttpClient._perform_request`;
no live RPC endpoint is contacted. Reserves/decimals/token0 are ABI-encoded into
the exact 32-byte words a real `eth_call` returns.
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

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import onchain_pools as onchain_pools_module
from market_analyser.data.adapters.onchain_pools import (
    OnchainPoolPriceAdapter,
    PoolConfig,
    PoolPriceConfigError,
    PoolPriceError,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.sources import PoolPriceSource
from market_analyser.defi.models import PoolQuote
from market_analyser.persistence.secrets import SecretsStore

# A fixed provenance clock so as_of is deterministic.
_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

# Selectors the adapter issues (mirrors the module constants).
_SEL_GET_RESERVES = "0x0902f1ac"
_SEL_TOKEN0 = "0x0dfe1681"
_SEL_DECIMALS = "0x313ce567"

# Real, public Base token addresses (constants, not credentials) for the pair.
_WETH = "0x4200000000000000000000000000000000000006"
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
# Fabricated pool addresses (fixtures only).
_POOL_A = "0xaaaa000000000000000000000000000000000001"  # token0 = WETH (base)
_POOL_B = "0xbbbb000000000000000000000000000000000002"  # token0 = USDC (quote)

_POOLS = (
    PoolConfig(
        pool_id=_POOL_A,
        dex="aerodrome",
        chain="base",
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        fee_bps=5.0,
    ),
    PoolConfig(
        pool_id=_POOL_B,
        dex="uniswap-v2",
        chain="base",
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        fee_bps=30.0,
    ),
)


def _uint_word(value: int) -> str:
    return format(value, "064x")


def _addr_word(address: str) -> str:
    return "000000000000000000000000" + address[2:].lower()


def _reserves_result(reserve0: int, reserve1: int) -> str:
    # getReserves() -> (reserve0, reserve1, blockTimestampLast)
    return "0x" + _uint_word(reserve0) + _uint_word(reserve1) + _uint_word(0)


class _FakeRpc:
    """Replaces `ResilientHttpClient._perform_request`, routing each `eth_call` by
    its `(to, selector)`. A missing entry returns a JSON-RPC error object (a
    revert), so unmapped getters surface as `PoolPriceError`. Records every call."""

    def __init__(self, responses: dict[tuple[str, str], str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        payload = json.loads(body)
        call = payload["params"][0]
        to = call["to"].lower()
        selector = call["data"][:10]
        self.calls.append((to, selector))
        result = self._responses.get((to, selector))
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


def _static_response(status_code: int, body: bytes) -> Any:
    """A transport fake that always returns one response, counting attempts."""

    class _Static:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(
            self, method: str, url: str, req_body: Any, req_headers: Any, *, proxy: Any
        ) -> HttpResponse:
            self.attempts += 1
            return HttpResponse(status_code=status_code, headers={}, body=body, elapsed_seconds=0.0)

    return _Static()


def _secrets(*, base_rpc_url: str | None = "https://rpc.test/base") -> SecretsStore:
    environ = {"MARKET_ANALYSER_BASE_RPC_URL": base_rpc_url} if base_rpc_url else {}
    # A path that does not exist -> the file read yields an empty SecretsFile, so
    # only the injected env override is visible.
    return SecretsStore(Path("does-not-exist.json"), environ=environ)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any,
    pools: Any = _POOLS,
    base_rpc_url: str | None = "https://rpc.test/base",
    max_retries: int = 0,
) -> tuple[OnchainPoolPriceAdapter, Any]:
    client = ResilientHttpClient(
        source_name="onchain-pool-test", cache_ttl_seconds=0.0, max_retries=max_retries
    )
    monkeypatch.setattr(client, "_perform_request", transport)
    adapter = OnchainPoolPriceAdapter(
        secrets_store=_secrets(base_rpc_url=base_rpc_url),
        pools=pools,
        http_client=client,
        sleep=lambda _s: None,
        now=lambda: _NOW,
    )
    return adapter, transport


def _both_pools_transport() -> _FakeRpc:
    """Pool A (token0=WETH): 100 WETH / 300_000 USDC -> price 3000.
    Pool B (token0=USDC): 50 WETH / 150_500 USDC -> price 3010."""
    weth = 10**18
    usdc = 10**6
    return _FakeRpc(
        {
            (_POOL_A, _SEL_GET_RESERVES): _reserves_result(100 * weth, 300_000 * usdc),
            (_POOL_A, _SEL_TOKEN0): "0x" + _addr_word(_WETH),
            (_POOL_B, _SEL_GET_RESERVES): _reserves_result(150_500 * usdc, 50 * weth),
            (_POOL_B, _SEL_TOKEN0): "0x" + _addr_word(_USDC),
            (_WETH, _SEL_DECIMALS): "0x" + _uint_word(18),
            (_USDC, _SEL_DECIMALS): "0x" + _uint_word(6),
        }
    )


# --- (d) contract ---------------------------------------------------------------


def test_adapter_satisfies_source_protocol() -> None:
    assert isinstance(OnchainPoolPriceAdapter(secrets_store=_secrets()), PoolPriceSource)


def test_non_conforming_object_does_not_satisfy_protocol() -> None:
    class _Missing:
        pass

    assert not isinstance(_Missing(), PoolPriceSource)


def test_reachable_through_one_registry_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The selector-registry shape (ADR-0031): the adapter is the sole value of a
    `Mapping[str, PoolPriceSource]`, selected by source name."""
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport())
    registry: Mapping[str, PoolPriceSource] = {"onchain": adapter}
    selected = registry["onchain"]
    assert isinstance(selected, PoolPriceSource)
    quotes = selected.fetch_pool_quotes("WETH/USDC", trade_size=1.0)
    assert [q.pool_id for q in quotes] == [_POOL_A, _POOL_B]


# --- (a) happy path -------------------------------------------------------------


def test_fetch_quotes_returns_a_quote_per_pool_with_oriented_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport())

    quotes = adapter.fetch_pool_quotes("WETH/USDC", trade_size=2.5)

    assert len(quotes) == 2
    assert all(isinstance(q, PoolQuote) for q in quotes)
    a, b = quotes
    # Pool A: token0 = base (WETH) — reserves used as-is.
    assert a.pool_id == _POOL_A
    assert a.dex == "aerodrome"
    assert a.chain == "base"
    assert a.pair == "WETH/USDC"
    assert a.base_token == _WETH
    assert a.quote_token == _USDC
    assert a.price == pytest.approx(3000.0)
    assert a.fee_bps == 5.0
    assert a.liquidity_base == pytest.approx(100.0)
    assert a.liquidity_quote == pytest.approx(300_000.0)
    assert a.trade_size == 2.5
    assert a.as_of == _NOW
    # Pool B: token0 = quote (USDC) — reserves swapped to orient. Price still
    # quote-per-base despite the reversed on-chain ordering.
    assert b.pool_id == _POOL_B
    assert b.price == pytest.approx(3010.0)
    assert b.liquidity_base == pytest.approx(50.0)
    assert b.liquidity_quote == pytest.approx(150_500.0)
    assert b.fee_bps == 30.0


def test_price_is_positive_and_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport())
    quotes = adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)
    assert all(q.price > 0 for q in quotes)


def test_pair_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport())
    assert len(adapter.fetch_pool_quotes("weth/usdc", trade_size=1.0)) == 2


# --- (e) empty / caller-bug paths -----------------------------------------------


def test_unconfigured_pair_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch, transport=_both_pools_transport())
    assert adapter.fetch_pool_quotes("WBTC/USDC", trade_size=1.0) == []
    # No pool matched -> no RPC call was issued.
    assert fake.calls == []


def test_non_positive_trade_size_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport())
    with pytest.raises(ValueError, match="trade_size"):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=0.0)


# --- (b) malformed reads raise the typed error ----------------------------------


def test_json_rpc_error_object_raises_pool_price_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmapped getter returns a JSON-RPC error object (a revert)."""
    transport = _FakeRpc({})  # every call reverts
    adapter, _ = _adapter(monkeypatch, transport=transport)
    with pytest.raises(PoolPriceError):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


def test_too_short_result_raises_pool_price_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeRpc({(_POOL_A, _SEL_GET_RESERVES): "0x1234"})  # < 2 words
    adapter, _ = _adapter(monkeypatch, transport=transport, pools=(_POOLS[0],))
    with pytest.raises(PoolPriceError):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


def test_non_hex_result_raises_pool_price_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeRpc({(_POOL_A, _SEL_GET_RESERVES): "0xzzzz"})
    adapter, _ = _adapter(monkeypatch, transport=transport, pools=(_POOLS[0],))
    with pytest.raises(PoolPriceError):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


def test_token0_matching_neither_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    other = "0xcccc000000000000000000000000000000000003"
    transport = _FakeRpc(
        {
            (_POOL_A, _SEL_GET_RESERVES): _reserves_result(100 * 10**18, 300_000 * 10**6),
            (_POOL_A, _SEL_TOKEN0): "0x" + _addr_word(other),
        }
    )
    adapter, _ = _adapter(monkeypatch, transport=transport, pools=(_POOLS[0],))
    with pytest.raises(PoolPriceError, match="token0"):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


def test_zero_reserve_raises_rather_than_zeroing_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero base reserve must raise, not silently produce a 0 / inf price."""
    transport = _FakeRpc(
        {
            (_POOL_A, _SEL_GET_RESERVES): _reserves_result(0, 300_000 * 10**6),
            (_POOL_A, _SEL_TOKEN0): "0x" + _addr_word(_WETH),
            (_WETH, _SEL_DECIMALS): "0x" + _uint_word(18),
            (_USDC, _SEL_DECIMALS): "0x" + _uint_word(6),
        }
    )
    adapter, _ = _adapter(monkeypatch, transport=transport, pools=(_POOLS[0],))
    with pytest.raises(PoolPriceError, match="reserve"):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


# --- (c) transport + config error taxonomy --------------------------------------


def test_429_raises_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(
        monkeypatch, transport=_static_response(429, b"{}"), pools=(_POOLS[0],), max_retries=0
    )
    with pytest.raises(RateLimitedError):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)
    assert fake.attempts == 1


def test_500_raises_upstream_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(
        monkeypatch, transport=_static_response(500, b"{}"), pools=(_POOLS[0],), max_retries=0
    )
    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


def test_missing_rpc_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport(), base_rpc_url=None)
    with pytest.raises(PoolPriceConfigError, match="RPC URL"):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


def test_unsupported_chain_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = PoolConfig(
        pool_id=_POOL_A,
        dex="camelot",
        chain="arbitrum",  # valid Chain, but no reserved RPC-URL secret
        pair="WETH/USDC",
        base_token=_WETH,
        quote_token=_USDC,
        fee_bps=5.0,
    )
    adapter, _ = _adapter(monkeypatch, transport=_both_pools_transport(), pools=(pool,))
    with pytest.raises(PoolPriceConfigError, match="chain"):
        adapter.fetch_pool_quotes("WETH/USDC", trade_size=1.0)


# --- (f) read-only proof: no key / signing / state-changing RPC -----------------


def test_adapter_source_is_read_only() -> None:
    """ADR-0072/0041 read-only proof: the adapter carries no key material, no
    signing path, and no state-changing RPC — a source scan rejects the giveaway
    tokens, and an AST walk asserts the only JSON-RPC `method` string is
    `eth_call` (the read-only credential `SecretsStore` for the RPC *read URL* is
    deliberately allowed — it is not a trade key)."""
    source = Path(onchain_pools_module.__file__).read_text(encoding="utf-8")
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
    assert offenders == [], f"adapter source contains write/signing tokens: {offenders}"

    # Every JSON-RPC "method" value in the module is `eth_call` — no write method.
    tree = ast.parse(source)
    method_values = _rpc_method_values(tree)
    assert method_values == {"eth_call"}, f"unexpected JSON-RPC methods: {method_values}"


def _rpc_method_values(tree: ast.AST) -> set[str]:
    """Collect the string value of every `"method": <str>` entry in a dict literal
    — the JSON-RPC methods the adapter issues."""
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
