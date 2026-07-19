"""Plan 0042 phase 1 — offline tests for the Aave v3 account-health adapter.

A fake JSON-RPC transport (the monkeypatched `ResilientHttpClient._perform_request`)
returns a canned `getUserAccountData` result, so the suite never touches a real RPC;
a real `SecretsStore` (env-injected) supplies the per-chain RPC URL. `_now` is frozen
so `as_of` is deterministic.

Pins the phase-1 done-when:
(a) the 6 return words decode with correct base/bps/WAD scaling;
(b) a no-debt account (`totalDebtBase == 0`) → `health_factor is None`, not a fabricated number;
(c) a missing RPC URL / unsupported chain fails typed (`LpDetailConfigError`), a revert /
    too-short result raises `LpDetailError`, a 429 maps to `RateLimitedError`;
(d) the selector matches `keccak256("getUserAccountData(address)")[:4]` (the only ground truth);
plus determinism, the outgoing call shape (Pool `to` + owner arg), and `AaveAccountSource`
conformance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import aave_account
from market_analyser.data.adapters.aave_account import AaveAccountAdapter
from market_analyser.data.adapters.lp_detail import LpDetailConfigError, LpDetailError
from market_analyser.data.errors import RateLimitedError
from market_analyser.data.sources import AaveAccountSource
from market_analyser.persistence.secrets import SecretsStore
from tests.data.test_lp_detail_adapter import _selector

_BASE_POOL = "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
_OWNER = "0x1111111111111111111111111111111111111111"
_BASE_UNIT = 10**8
_WAD = 10**18
_UINT_MAX = 2**256 - 1

_FROZEN_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

# A known account: 10000 USD collateral, 4000 USD debt, 2000 USD borrowable,
# LT 82.5%, LTV 80%, HF = 10000*0.825/4000 = 2.0625.
_HAPPY_WORDS = [
    10_000 * _BASE_UNIT,
    4_000 * _BASE_UNIT,
    2_000 * _BASE_UNIT,
    8_250,
    8_000,
    int(2.0625 * _WAD),
]


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aave_account, "_now", lambda: _FROZEN_NOW)


def _word(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def _result(words: list[int]) -> str:
    return "0x" + "".join(_word(w) for w in words)


def _store(*, base_url: str | None = "https://base.example/rpc") -> SecretsStore:
    env = {"MARKET_ANALYSER_BASE_RPC_URL": base_url} if base_url else {}
    return SecretsStore(Path("unused-secrets.json"), environ=env)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result_hex: str | None = None,
    status: int = 200,
    jsonrpc_error: bool = False,
    store: SecretsStore | None = None,
) -> tuple[AaveAccountAdapter, list[dict[str, Any]]]:
    """Wire an adapter to a canned JSON-RPC response; return it and the recorded calls."""
    _freeze(monkeypatch)
    client = ResilientHttpClient(source_name="aave-account-test", max_retries=0)
    calls: list[dict[str, Any]] = []
    body_hex = result_hex if result_hex is not None else _result(_HAPPY_WORDS)

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = json.loads(body)
        params = payload["params"][0]
        calls.append({"to": params["to"], "data": params["data"], "url": url})
        if jsonrpc_error:
            rpc = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "reverted"}}
        else:
            rpc = {"jsonrpc": "2.0", "id": 1, "result": body_hex}
        return HttpResponse(
            status_code=status, headers={}, body=json.dumps(rpc).encode(), elapsed_seconds=0.0
        )

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = AaveAccountAdapter(secrets_store=store or _store(), http_client=client)
    return adapter, calls


# -- (a) decode with correct units ------------------------------------------


def test_happy_path_decodes_with_correct_scaling(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, calls = _adapter(monkeypatch)

    detail = adapter.fetch_account_detail(chain="base", owner=_OWNER)

    assert detail.chain == "base"
    assert detail.total_collateral_base == pytest.approx(10_000.0)
    assert detail.total_debt_base == pytest.approx(4_000.0)
    assert detail.available_borrows_base == pytest.approx(2_000.0)
    assert detail.liquidation_threshold == pytest.approx(0.825)
    assert detail.ltv == pytest.approx(0.80)
    assert detail.health_factor == pytest.approx(2.0625)
    assert detail.as_of == _FROZEN_NOW
    # The call went to the Base Pool with the getUserAccountData selector + owner arg.
    assert len(calls) == 1
    assert calls[0]["to"] == _BASE_POOL
    assert calls[0]["data"].startswith(aave_account._SEL_GET_USER_ACCOUNT_DATA)
    assert _OWNER[2:].lower() in calls[0]["data"].lower()


# -- (b) no-debt account → health_factor None -------------------------------


def test_no_debt_account_has_none_health_factor(monkeypatch: pytest.MonkeyPatch) -> None:
    # Supply-only account: collateral present, zero debt, HF word = uint256 max.
    words = [10_000 * _BASE_UNIT, 0, 8_000 * _BASE_UNIT, 8_250, 8_000, _UINT_MAX]
    adapter, _ = _adapter(monkeypatch, result_hex=_result(words))

    detail = adapter.fetch_account_detail(chain="base", owner=_OWNER)

    assert detail.total_debt_base == 0.0
    assert detail.health_factor is None  # not a fabricated ~1e59 number
    assert detail.total_collateral_base == pytest.approx(10_000.0)


# -- (c) typed errors -------------------------------------------------------


def test_missing_rpc_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, store=_store(base_url=None))

    with pytest.raises(LpDetailConfigError):
        adapter.fetch_account_detail(chain="base", owner=_OWNER)


def test_unsupported_chain_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # arbitrum has no reserved RPC-URL secret → typed config error, no request issued.
    adapter, calls = _adapter(monkeypatch)

    with pytest.raises(LpDetailConfigError):
        adapter.fetch_account_detail(chain="arbitrum", owner=_OWNER)
    assert calls == []


def test_jsonrpc_revert_raises_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, jsonrpc_error=True)

    with pytest.raises(LpDetailError):
        adapter.fetch_account_detail(chain="base", owner=_OWNER)


def test_short_result_raises_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only 3 words returned where 6 are required.
    adapter, _ = _adapter(monkeypatch, result_hex=_result([1, 2, 3]))

    with pytest.raises(LpDetailError):
        adapter.fetch_account_detail(chain="base", owner=_OWNER)


def test_rate_limit_maps_to_rate_limited_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, status=429)

    with pytest.raises(RateLimitedError):
        adapter.fetch_account_detail(chain="base", owner=_OWNER)


# -- (d) selector self-check (keccak is the only ground truth) ---------------


def test_selector_matches_keccak_signature() -> None:
    assert _selector("getUserAccountData(address)") == aave_account._SEL_GET_USER_ACCOUNT_DATA


# -- determinism + conformance ----------------------------------------------


def test_decode_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)

    first = adapter.fetch_account_detail(chain="base", owner=_OWNER)
    second = adapter.fetch_account_detail(chain="base", owner=_OWNER)

    assert first == second


def test_adapter_conforms_to_aave_account_source(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    assert isinstance(adapter, AaveAccountSource)
