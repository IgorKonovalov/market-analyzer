"""Plan 0034 phase 3 done-when: the Aerodrome (one-hop) LP deep adapter.

Against recorded `eth_call` responses (offline, deterministic), the adapter keyed
on `pool_address` yields an `LpPositionDetail` with the tick range, current tick,
in-range status, and uncollected fees decoded from canonical ABI encodings. And
it raises *typed* errors (not bare exceptions) on a missing RPC URL, an
unsupported chain, a JSON-RPC error object, and an HTTP throttle / outage.

The "fixture" is the canonical ABI encoding of known on-chain values (built by the
`_*_result` helpers below) routed per `eth_call` by `(to, selector)` — standing in
for recorded RPC responses, so the decode path is what's verified offline. The
transport is driven through the documented `_perform_request` seam of
`ResilientHttpClient` (ADR-0019), the same seam the Zerion adapter test uses.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.lp_detail import (
    LpDetailConfigError,
    LpDetailError,
    RpcLpDetailAdapter,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.persistence.secrets import SecretsStore

# Known on-chain values the recorded responses encode.
_POOL = "0xe3800a58b5535935850a10e082952ec3577d8dcc"
_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_WETH = "0x4200000000000000000000000000000000000006"
_CURRENT_TICK = 100
_TICK_LOWER = -200
_TICK_UPPER = 200
_OWED0_RAW = 1_500_000  # USDC, 6 decimals -> 1.5
_OWED1_RAW = 20_000_000_000_000_000  # WETH, 18 decimals -> 0.02

_SEL_SLOT0 = "0x3850c7bd"
_SEL_TOKEN0 = "0x0dfe1681"
_SEL_TOKEN1 = "0xd21220a7"
_SEL_SYMBOL = "0x95d89b41"
_SEL_DECIMALS = "0x313ce567"
_SEL_POSITIONS = "0x99fbab88"


# -- ABI encoders (canonical; stand in for recorded RPC results) ----------------


def _uint_word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _int_word(value: int) -> bytes:
    return (value & ((1 << 256) - 1)).to_bytes(32, "big")  # two's complement


def _addr_word(address: str) -> bytes:
    raw = bytes.fromhex(address[2:])
    return raw.rjust(32, b"\x00")


def _slot0_result(tick: int) -> bytes:
    return _uint_word(0) + _int_word(tick)  # word0 sqrtPrice (unused), word1 tick


def _positions_result(tl: int, tu: int, owed0: int, owed1: int) -> bytes:
    words = [_uint_word(0)] * 12
    words[5] = _int_word(tl)
    words[6] = _int_word(tu)
    words[10] = _uint_word(owed0)
    words[11] = _uint_word(owed1)
    return b"".join(words)


def _string_result(text: str) -> bytes:
    raw = text.encode("utf-8")
    return _uint_word(32) + _uint_word(len(raw)) + raw.ljust(32, b"\x00")


def _hex(data: bytes) -> str:
    return "0x" + data.hex()


# Recorded responses keyed by (to-address, selector).
def _aerodrome_responses() -> dict[tuple[str, str], str]:
    return {
        (_POOL, _SEL_SLOT0): _hex(_slot0_result(_CURRENT_TICK)),
        (_POOL, _SEL_POSITIONS): _hex(
            _positions_result(_TICK_LOWER, _TICK_UPPER, _OWED0_RAW, _OWED1_RAW)
        ),
        (_POOL, _SEL_TOKEN0): _hex(_addr_word(_USDC)),
        (_POOL, _SEL_TOKEN1): _hex(_addr_word(_WETH)),
        (_USDC, _SEL_SYMBOL): _hex(_string_result("USDC")),
        (_WETH, _SEL_SYMBOL): _hex(_string_result("WETH")),
        (_USDC, _SEL_DECIMALS): _hex(_uint_word(6)),
        (_WETH, _SEL_DECIMALS): _hex(_uint_word(18)),
    }


# -- transport fake -------------------------------------------------------------


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


def _ok_responder(table: dict[tuple[str, str], str]) -> Callable[[str, str], tuple[int, bytes]]:
    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        result = table[(to.lower(), selector)]
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


# -- tests ----------------------------------------------------------------------


def test_aerodrome_detail_decodes_range_tick_and_fees(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_aerodrome_responses()))
    detail = adapter.fetch_lp_detail(chain="base", pool_address=_POOL)

    assert detail.tick_lower == _TICK_LOWER
    assert detail.tick_upper == _TICK_UPPER
    assert detail.current_tick == _CURRENT_TICK
    assert detail.in_range is True  # -200 <= 100 < 200
    fees = {t.symbol: t for t in detail.uncollected_fees}
    assert set(fees) == {"USDC", "WETH"}
    assert fees["USDC"].amount == pytest.approx(1.5)
    assert fees["USDC"].address == _USDC
    assert fees["WETH"].amount == pytest.approx(0.02)


def test_out_of_range_when_current_tick_above_upper(tmp_path: Path) -> None:
    table = _aerodrome_responses()
    table[(_POOL, _SEL_SLOT0)] = _hex(_slot0_result(500))  # above tick_upper=200
    detail = _adapter(tmp_path, _ok_responder(table)).fetch_lp_detail(
        chain="base", pool_address=_POOL
    )
    assert detail.current_tick == 500
    assert detail.in_range is False


def test_zero_owed_fees_yield_empty_list(tmp_path: Path) -> None:
    table = _aerodrome_responses()
    table[(_POOL, _SEL_POSITIONS)] = _hex(_positions_result(_TICK_LOWER, _TICK_UPPER, 0, 0))
    detail = _adapter(tmp_path, _ok_responder(table)).fetch_lp_detail(
        chain="base", pool_address=_POOL
    )
    assert detail.uncollected_fees == []


def test_output_is_deterministic(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_aerodrome_responses()))
    first = adapter.fetch_lp_detail(chain="base", pool_address=_POOL)
    second = adapter.fetch_lp_detail(chain="base", pool_address=_POOL)
    assert first == second


def test_missing_rpc_url_raises_typed_config_error(tmp_path: Path) -> None:
    store = SecretsStore(tmp_path / "secrets.json", environ={})  # no base_rpc_url
    adapter = RpcLpDetailAdapter(
        secrets_store=store, http_client=_routed_client(_ok_responder(_aerodrome_responses()))
    )
    with pytest.raises(LpDetailConfigError):
        adapter.fetch_lp_detail(chain="base", pool_address=_POOL)


def test_unsupported_chain_raises_typed_config_error(tmp_path: Path) -> None:
    # arbitrum / optimism have no reserved RPC-URL secret in the schema.
    adapter = _adapter(tmp_path, _ok_responder(_aerodrome_responses()))
    with pytest.raises(LpDetailConfigError):
        adapter.fetch_lp_detail(chain="arbitrum", pool_address=_POOL)


def test_jsonrpc_error_object_raises_typed_lp_detail_error(tmp_path: Path) -> None:
    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "execution reverted"}}
        ).encode()
        return 200, body

    with pytest.raises(LpDetailError):
        _adapter(tmp_path, _respond).fetch_lp_detail(chain="base", pool_address=_POOL)


def test_http_500_raises_typed_unavailable_error(tmp_path: Path) -> None:
    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        return 500, b"upstream boom"

    with pytest.raises(UpstreamUnavailableError):
        _adapter(tmp_path, _respond).fetch_lp_detail(chain="base", pool_address=_POOL)


def test_http_429_raises_typed_rate_limited_error(tmp_path: Path) -> None:
    def _respond(to: str, selector: str) -> tuple[int, bytes]:
        return 429, b'{"error":"slow down"}'

    with pytest.raises(RateLimitedError):
        _adapter(tmp_path, _respond).fetch_lp_detail(chain="base", pool_address=_POOL)


def test_implements_lp_position_detail_source_protocol(tmp_path: Path) -> None:
    from market_analyser.data.sources import LpPositionDetailSource

    adapter = RpcLpDetailAdapter(secrets_store=_store_with_base_rpc(tmp_path))
    assert isinstance(adapter, LpPositionDetailSource)


# -- Plan 0034 phase 4: Uniswap-v3 two-hop (tokenId-keyed) ----------------------
#
# Live verification is deferred (F3 — no in-scope wallet holds a Uni-v3 position);
# the read + tokenId resolution are exercised here against recorded responses.

_NPM = "0x03a520b32c04bf3beef7beb72e919cf822ed34f1"  # Base NonfungiblePositionManager
_OWNER = "0xae5b…9790"
_TOKEN_ID = 12345
_SEL_BALANCE_OF = "0x70a08231"
_SEL_TOKEN_OF_OWNER_BY_INDEX = "0x2f745c59"


def _positions_with_tokens_result(
    token0: str, token1: str, tl: int, tu: int, owed0: int, owed1: int
) -> bytes:
    # positions(tokenId): token0=word2, token1=word3, tickLower=5, tickUpper=6,
    # tokensOwed0=10, tokensOwed1=11.
    words = [_uint_word(0)] * 12
    words[2] = _addr_word(token0)
    words[3] = _addr_word(token1)
    words[5] = _int_word(tl)
    words[6] = _int_word(tu)
    words[10] = _uint_word(owed0)
    words[11] = _uint_word(owed1)
    return b"".join(words)


def _univ3_responses() -> dict[tuple[str, str], str]:
    return {
        (_NPM, _SEL_POSITIONS): _hex(
            _positions_with_tokens_result(
                _USDC, _WETH, _TICK_LOWER, _TICK_UPPER, _OWED0_RAW, _OWED1_RAW
            )
        ),
        (_POOL, _SEL_SLOT0): _hex(_slot0_result(_CURRENT_TICK)),
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


def test_univ3_detail_decodes_via_token_id(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_univ3_responses()))
    detail = adapter.fetch_lp_detail(chain="base", pool_address=_POOL, token_id=_TOKEN_ID)

    assert detail.tick_lower == _TICK_LOWER
    assert detail.tick_upper == _TICK_UPPER
    assert detail.current_tick == _CURRENT_TICK
    assert detail.in_range is True
    fees = {t.symbol: t for t in detail.uncollected_fees}
    assert set(fees) == {"USDC", "WETH"}
    assert fees["USDC"].amount == pytest.approx(1.5)
    assert fees["WETH"].amount == pytest.approx(0.02)


def test_resolve_univ3_token_id_matches_pool_pair(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _ok_responder(_resolution_responses()))
    token_id = adapter.resolve_univ3_token_id(chain="base", pool_address=_POOL, owner=_OWNER)
    assert token_id == _TOKEN_ID


def test_resolve_univ3_token_id_returns_none_when_wallet_holds_no_position(
    tmp_path: Path,
) -> None:
    table = _resolution_responses()
    table[(_NPM, _SEL_BALANCE_OF)] = _hex(_uint_word(0))  # owns no Uni-v3 positions
    adapter = _adapter(tmp_path, _ok_responder(table))
    assert adapter.resolve_univ3_token_id(chain="base", pool_address=_POOL, owner=_OWNER) is None


def test_resolve_then_fetch_two_hop_round_trip(tmp_path: Path) -> None:
    # The two-hop the enrichment runs: resolve the tokenId, then read its detail.
    adapter = _adapter(tmp_path, _ok_responder(_resolution_responses()))
    token_id = adapter.resolve_univ3_token_id(chain="base", pool_address=_POOL, owner=_OWNER)
    assert token_id is not None
    detail = adapter.fetch_lp_detail(chain="base", pool_address=_POOL, token_id=token_id)
    assert detail.current_tick == _CURRENT_TICK
    assert detail.in_range is True
