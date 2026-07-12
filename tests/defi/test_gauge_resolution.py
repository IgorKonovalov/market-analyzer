"""Plan 0084 phase 1 done-when: the gauge→pool resolution seam.

Aerodrome pays emissions through a per-pool **gauge** distinct from the pool, so a
gauge `getReward` transaction cannot join the pool position it belongs to without a
gauge→pool map (ADR-0079). `GaugeResolutionAdapter` resolves it with one read-only
`gauge.pool()` `eth_call`, memoized per (chain, gauge).

The "fixture" is the canonical ABI encoding of `gauge.pool()` results routed by
`(to, selector)` — a non-gauge address is returned as a JSON-RPC *revert*, exactly
what the adapter must tolerate as an honest `None`. Pinned here (done-when):

- the AERO/WETH gauge `0x9564…88f1` resolves to pool `0x4e50…ce51`, plus a second
  AERO/WETH gauge and the AAVE/WETH gauge, all against recorded fixtures;
- a cold call hits RPC once; a warm call for the same gauge reads the memo with
  **zero** further RPC;
- an unresolvable gauge (revert) and a zero-address `pool()` both return `None`,
  never a raise;
- the adapter satisfies the `GaugeResolutionSource` Protocol and issues only the
  `eth_call` read method (read-only by construction).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.gauge_resolution import GaugeResolutionAdapter
from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.sources import GaugeResolutionSource
from market_analyser.persistence.secrets import SecretsStore

_SEL_POOL = "0x16f0115b"  # pool() -> address

# Known gauge→pool mappings the recorded responses encode. The first pair is the
# 2026-06-05 live-smoke gauge/CLPool (lp_detail docstring); the other two are the
# wallet's second AERO/WETH gauge and its AAVE/WETH gauge.
_GAUGE_AERO_WETH_1 = "0x9564" + "0" * 32 + "88f1"
_POOL_AERO_WETH_1 = "0x4e50" + "0" * 32 + "ce51"
_GAUGE_AERO_WETH_2 = "0x11a2" + "0" * 32 + "b3c4"
_POOL_AERO_WETH_2 = "0x22d5" + "0" * 32 + "e6f7"
_GAUGE_AAVE_WETH = "0x33ab" + "0" * 32 + "cd12"
_POOL_AAVE_WETH = "0x44ef" + "0" * 32 + "3456"

# An address that is not a gauge (its pool() reverts) and a gauge whose pool()
# returns the zero address — both must resolve to None.
_NOT_A_GAUGE = "0x99ff" + "0" * 32 + "0001"
_GAUGE_ZERO_POOL = "0x99ff" + "0" * 32 + "0002"
_ZERO_ADDRESS = "0x" + "0" * 40

_MAPPINGS = {
    _GAUGE_AERO_WETH_1: _POOL_AERO_WETH_1,
    _GAUGE_AERO_WETH_2: _POOL_AERO_WETH_2,
    _GAUGE_AAVE_WETH: _POOL_AAVE_WETH,
    _GAUGE_ZERO_POOL: _ZERO_ADDRESS,
}


def _addr_word(address: str) -> bytes:
    return bytes.fromhex(address[2:]).rjust(32, b"\x00")


def _hex(data: bytes) -> str:
    return "0x" + data.hex()


_REVERT = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "execution reverted"}}
).encode()


def _routed_client(
    calls: list[tuple[str, str]] | None = None,
    methods: list[str] | None = None,
) -> ResilientHttpClient:
    """A ResilientHttpClient whose physical attempt routes each `eth_call` to a
    canned `gauge.pool()` result by `(to, selector)`. A gauge not in `_MAPPINGS`
    reverts. Optionally records the call sequence and the JSON-RPC methods seen."""
    client = ResilientHttpClient(source_name="gauge-test", cache_ttl_seconds=0.0, max_retries=0)

    def _fake_perform(
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
        to = call["to"].lower()
        selector = call["data"][:10]
        if calls is not None:
            calls.append((to, selector))
        pool = _MAPPINGS.get(to)
        if selector != _SEL_POOL or pool is None:
            return HttpResponse(status_code=200, headers={}, body=_REVERT, elapsed_seconds=0.0)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": _hex(_addr_word(pool))}).encode()
        return HttpResponse(status_code=200, headers={}, body=body, elapsed_seconds=0.0)

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _store_with_base_rpc(tmp_path: Path) -> SecretsStore:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("base_rpc_url", "https://base-rpc.example/v1")
    return store


def _adapter(
    tmp_path: Path,
    *,
    calls: list[tuple[str, str]] | None = None,
    methods: list[str] | None = None,
    client: ResilientHttpClient | None = None,
) -> GaugeResolutionAdapter:
    return GaugeResolutionAdapter(
        secrets_store=_store_with_base_rpc(tmp_path),
        http_client=client if client is not None else _routed_client(calls, methods),
        sleep=lambda _seconds: None,  # don't pace in offline tests
    )


def test_resolves_the_three_recorded_gauge_pool_mappings(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1) == _POOL_AERO_WETH_1
    assert adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_2) == _POOL_AERO_WETH_2
    assert adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AAVE_WETH) == _POOL_AAVE_WETH


def test_result_is_lowercased(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    pool = adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1.upper())
    assert pool == _POOL_AERO_WETH_1  # already lowercase; input casing tolerated


def test_cold_call_hits_rpc_then_warm_call_is_zero_rpc(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    adapter = _adapter(tmp_path, calls=calls)
    first = adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1)
    assert len(calls) == 1, "the cold call issues exactly one eth_call"
    second = adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1)
    assert second == first
    assert len(calls) == 1, "the warm call reads the memo — zero further RPC"


def test_unresolvable_gauge_returns_none_not_a_raise(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter.resolve_pool(chain="base", gauge_address=_NOT_A_GAUGE) is None


def test_zero_address_pool_returns_none(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter.resolve_pool(chain="base", gauge_address=_GAUGE_ZERO_POOL) is None


def test_a_memoized_miss_is_not_reprobed(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    adapter = _adapter(tmp_path, calls=calls)
    assert adapter.resolve_pool(chain="base", gauge_address=_NOT_A_GAUGE) is None
    assert adapter.resolve_pool(chain="base", gauge_address=_NOT_A_GAUGE) is None
    assert len(calls) == 1, "a known non-gauge is memoized, not re-probed"


def test_only_the_eth_call_read_method_is_issued(tmp_path: Path) -> None:
    """Read-only by construction: the adapter never issues a state-changing RPC."""
    methods: list[str] = []
    adapter = _adapter(tmp_path, methods=methods)
    adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1)
    adapter.resolve_pool(chain="base", gauge_address=_NOT_A_GAUGE)
    assert methods == ["eth_call", "eth_call"]


def test_satisfies_the_gauge_resolution_source_protocol(tmp_path: Path) -> None:
    assert isinstance(_adapter(tmp_path), GaugeResolutionSource)


def test_missing_rpc_url_raises_typed_config_error(tmp_path: Path) -> None:
    store = SecretsStore(tmp_path / "secrets.json", environ={})  # no base_rpc_url set
    adapter = GaugeResolutionAdapter(
        secrets_store=store,
        http_client=_routed_client(),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(LpDetailConfigError):
        adapter.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1)


def test_unsupported_chain_raises_typed_config_error(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    with pytest.raises(LpDetailConfigError):
        # arbitrum has no reserved RPC-URL secret in the schema (base/ethereum only).
        adapter.resolve_pool(chain="arbitrum", gauge_address=_GAUGE_AERO_WETH_1)


def test_transport_failure_surfaces_typed_errors(tmp_path: Path) -> None:
    """A 429 → RateLimitedError, a 5xx → UpstreamUnavailableError — the shared
    taxonomy, not a bare exception (and not a fabricated pool)."""

    def _client(status: int) -> ResilientHttpClient:
        client = ResilientHttpClient(source_name="gauge-test", cache_ttl_seconds=0.0, max_retries=0)

        def _perform(
            method: str,
            url: str,
            body_arg: bytes | None,
            headers: Mapping[str, str] | None,
            *,
            proxy: ProxyConfig | None,
        ) -> HttpResponse:
            return HttpResponse(status_code=status, headers={}, body=b"", elapsed_seconds=0.0)

        client._perform_request = _perform  # type: ignore[method-assign, assignment]
        return client

    rate_limited = _adapter(tmp_path, client=_client(429))
    with pytest.raises(RateLimitedError):
        rate_limited.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1)
    unavailable = _adapter(tmp_path, client=_client(503))
    with pytest.raises(UpstreamUnavailableError):
        unavailable.resolve_pool(chain="base", gauge_address=_GAUGE_AERO_WETH_1)
