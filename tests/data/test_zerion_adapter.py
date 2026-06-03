"""Plan 0032 phase 2 done-when: the Zerion wallet-positions adapter.

Against a recorded fixture Zerion payload (offline, deterministic), the adapter
yields a typed `list[DefiPosition]` that:
- spans the four target chains (ethereum / base / arbitrum / optimism),
- includes an Aave v3 supply and an Aave v3 borrow, correctly split,
- folds the two grouped Uniswap-v3 LP token-entries into one `lp` position with
  both tokens (tick fields None — Zerion does not expose them),
- folds the Aerodrome LP likewise,
- drops the plain wallet balance.

And it raises a *typed* error (not a bare exception) on a missing key, an HTTP
401, and a shape-broken 2xx payload.

The transport is driven offline via the documented `_perform_request` seam of
`ResilientHttpClient` (the client's own docstring: tests monkeypatch it).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.zerion import ZerionAdapter, ZerionAuthError, ZerionError
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.defi.models import DefiPosition
from market_analyser.persistence.secrets import SecretsStore

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "zerion_positions.json"
_WALLET = "0x1111111111111111111111111111111111111111"


def _store_with_key(tmp_path: Path) -> SecretsStore:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("zerion_api_key", "zk_test_key")
    return store


def _canned_client(status_code: int, body: bytes) -> ResilientHttpClient:
    """A ResilientHttpClient whose single physical attempt returns a canned
    response — the transport seam tests monkeypatch (ADR-0019). `max_retries=0`
    so a non-2xx path raises immediately with no backoff sleep."""
    client = ResilientHttpClient(source_name="zerion-test", cache_ttl_seconds=0.0, max_retries=0)

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        return HttpResponse(status_code=status_code, headers={}, body=body, elapsed_seconds=0.0)

    # Stub the documented transport seam (the client builds fresh per call, so no
    # leak across tests); the fake has no `self`, hence the method-assign ignore.
    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _adapter_returning_fixture(tmp_path: Path) -> ZerionAdapter:
    body = _FIXTURE.read_bytes()
    return ZerionAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_canned_client(200, body),
    )


def _positions(tmp_path: Path) -> list[DefiPosition]:
    return _adapter_returning_fixture(tmp_path).fetch_positions(_WALLET)


def test_positions_span_the_four_target_chains(tmp_path: Path) -> None:
    chains = {p.chain for p in _positions(tmp_path)}
    assert chains == {"ethereum", "base", "arbitrum", "optimism"}


def test_aave_supply_and_borrow_are_split(tmp_path: Path) -> None:
    positions = _positions(tmp_path)
    supply = [p for p in positions if p.protocol == "aave-v3" and p.kind == "lending_supply"]
    borrow = [p for p in positions if p.protocol == "aave-v3" and p.kind == "lending_borrow"]
    assert len(supply) == 1
    assert len(borrow) == 1
    assert supply[0].tokens[0].symbol == "USDC"
    assert supply[0].usd_value == 1000.0
    assert borrow[0].tokens[0].symbol == "WETH"
    assert borrow[0].usd_value == 500.0


def test_uniswap_v3_lp_folds_two_token_entries_into_one_position(tmp_path: Path) -> None:
    lp = [p for p in _positions(tmp_path) if p.protocol == "uniswap-v3" and p.kind == "lp"]
    assert len(lp) == 1, "the two grouped token-entries must fold into one LP position"
    position = lp[0]
    assert {t.symbol for t in position.tokens} == {"USDC", "WETH"}
    assert position.usd_value == 1000.0  # 600 + 400 summed
    assert position.pool == "USDC / WETH"
    # Zerion does not expose tick boundaries (deep-adapter plan) — stay None.
    assert position.tick_lower is None
    assert position.tick_upper is None
    assert position.in_range is None


def test_aerodrome_lp_is_classified_and_folded(tmp_path: Path) -> None:
    lp = [p for p in _positions(tmp_path) if p.protocol == "aerodrome" and p.kind == "lp"]
    assert len(lp) == 1
    assert {t.symbol for t in lp[0].tokens} == {"cbBTC", "WETH"}
    assert lp[0].usd_value == 1000.0  # 700 + 300
    assert lp[0].chain == "base"


def test_staking_position_is_classified(tmp_path: Path) -> None:
    staking = [p for p in _positions(tmp_path) if p.kind == "staking"]
    assert len(staking) == 1
    assert staking[0].chain == "optimism"
    assert staking[0].tokens[0].symbol == "OP"


def test_plain_wallet_balance_is_dropped(tmp_path: Path) -> None:
    # The "wallet" position_type entry (USDC balance) must not appear — discovery
    # is about DeFi positions, not raw balances.
    positions = _positions(tmp_path)
    assert all(p.kind != "staking" or p.tokens[0].symbol != "USDC" for p in positions)
    # Exactly the five DeFi positions (2 Aave + 1 Uni LP + 1 Aero LP + 1 staking).
    assert len(positions) == 5


def test_token_address_resolves_to_position_chain_implementation(tmp_path: Path) -> None:
    supply = next(p for p in _positions(tmp_path) if p.kind == "lending_supply")
    assert supply.tokens[0].address == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def test_output_is_deterministic(tmp_path: Path) -> None:
    first = [p.position_id for p in _positions(tmp_path)]
    second = [p.position_id for p in _positions(tmp_path)]
    assert first == second


def test_missing_key_raises_typed_auth_error(tmp_path: Path) -> None:
    store = SecretsStore(tmp_path / "secrets.json", environ={})  # no key set
    adapter = ZerionAdapter(secrets_store=store, http_client=_canned_client(200, b"{}"))
    with pytest.raises(ZerionAuthError):
        adapter.fetch_positions(_WALLET)


def test_http_401_raises_typed_auth_error(tmp_path: Path) -> None:
    adapter = ZerionAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_canned_client(401, b'{"errors":[{"status":"401"}]}'),
    )
    with pytest.raises(ZerionAuthError):
        adapter.fetch_positions(_WALLET)


def test_http_500_raises_typed_unavailable_error(tmp_path: Path) -> None:
    adapter = ZerionAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_canned_client(500, b"upstream boom"),
    )
    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_positions(_WALLET)


def test_malformed_payload_raises_typed_zerion_error(tmp_path: Path) -> None:
    adapter = ZerionAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_canned_client(200, json.dumps({"data": "not-a-list"}).encode()),
    )
    with pytest.raises(ZerionError):
        adapter.fetch_positions(_WALLET)


def test_implements_wallet_positions_source_protocol(tmp_path: Path) -> None:
    from market_analyser.data.sources import WalletPositionsSource

    adapter = ZerionAdapter(secrets_store=_store_with_key(tmp_path))
    assert isinstance(adapter, WalletPositionsSource)
