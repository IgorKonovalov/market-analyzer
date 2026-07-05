"""Plan 0035 phase 2 done-when: the Zerion decoded-tx-history adapter.

Against a recorded two-page fixture (offline, deterministic), the adapter:
- follows `links.next` to completion and parses both pages,
- orders the result by `(mined_at_block, in_block_index)` ascending,
  deterministically across runs (never set-iteration),
- populates transfers' `direction`/`usd_value`/`price` and the fee,
- normalizes an unrecognized `operation_type` to `"unknown"`,
- drops off-target-chain transactions and `self`/zero-amount transfers,
- narrows server-side when `operation_types` is passed,
- paces one deliberate pause per follow-up page,
- and raises the *typed* taxonomy on a missing key / 401 / 429.

No live network: the transport is the documented `_perform_request` seam of
`ResilientHttpClient` (its own docstring: tests monkeypatch it).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.zerion import ZerionAuthError
from market_analyser.data.adapters.zerion_tx import ZerionTxAdapter
from market_analyser.data.errors import RateLimitedError
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.persistence.secrets import SecretsStore

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "zerion_transactions.json"
_WALLET = "0x2222222222222222222222222222222222222222"


def _store_with_key(tmp_path: Path) -> SecretsStore:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("zerion_api_key", "zk_test_key")
    return store


def _paged_client(requested_urls: list[str]) -> ResilientHttpClient:
    """A client whose transport serves the fixture's page 1, then page 2 when
    the request URL carries the `links.next` cursor. Requested URLs (final,
    params-encoded) are recorded for assertion."""
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    client = ResilientHttpClient(source_name="zerion-tx-test", cache_ttl_seconds=0.0, max_retries=0)

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        requested_urls.append(url)
        page = fixture["page2"] if "CURSOR2" in url else fixture["page1"]
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(page).encode(),
            elapsed_seconds=0.0,
        )

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _canned_client(status_code: int, body: bytes) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="zerion-tx-test", cache_ttl_seconds=0.0, max_retries=0)

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        return HttpResponse(status_code=status_code, headers={}, body=body, elapsed_seconds=0.0)

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _fetch(
    tmp_path: Path,
    *,
    requested_urls: list[str] | None = None,
    sleeps: list[float] | None = None,
    **kwargs: Any,
) -> list[DecodedTx]:
    recorded_sleeps = sleeps if sleeps is not None else []
    adapter = ZerionTxAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_paged_client(requested_urls if requested_urls is not None else []),
        sleep=recorded_sleeps.append,
    )
    return adapter.fetch_transactions(_WALLET, **kwargs)


def test_two_page_history_parses_in_block_then_index_order(tmp_path: Path) -> None:
    txs = _fetch(tmp_path)
    # The polygon tx is dropped; the remaining six order by (block, index).
    assert [t.hash for t in txs] == ["0xc3", "0xa1", "0xb2", "0xd1", "0xd2", "0xe5"]
    assert [(t.mined_at_block, t.in_block_index) for t in txs] == [
        (19000000, 0),
        (21999990, 0),
        (22000000, 0),
        (22412350, 0),
        (22412350, 1),
        (22412360, 0),
    ]


def test_same_block_transactions_keep_chronological_in_block_order(tmp_path: Path) -> None:
    """0xd1 (deposit, nonce 39) precedes 0xd2 (withdraw, nonce 40) in block
    22412350; Zerion lists them newest-first, the adapter restores order."""
    txs = {t.hash: t for t in _fetch(tmp_path)}
    assert txs["0xd1"].in_block_index == 0
    assert txs["0xd2"].in_block_index == 1


def test_transfers_carry_direction_value_and_price(tmp_path: Path) -> None:
    trade = next(t for t in _fetch(tmp_path) if t.hash == "0xe5")
    # The 'self' transfer is dropped: exactly the out-AERO and in-USDC legs.
    assert [(x.direction, x.symbol) for x in trade.transfers] == [
        ("out", "AERO"),
        ("in", "USDC"),
    ]
    aero = trade.transfers[0]
    assert aero.amount == 50.0
    assert aero.usd_value == 60.5
    assert aero.price == 1.21
    assert aero.address == "0x940181a94a35a4569e4529a3cdfb74e38fd98631"


def test_fee_is_populated(tmp_path: Path) -> None:
    trade = next(t for t in _fetch(tmp_path) if t.hash == "0xe5")
    assert trade.fee is not None
    assert trade.fee.symbol == "ETH"
    assert trade.fee.amount == pytest.approx(3.15e-5)
    assert trade.fee.usd_value == pytest.approx(0.11025)


def test_unrecognized_operation_type_normalizes_to_unknown(tmp_path: Path) -> None:
    burn = next(t for t in _fetch(tmp_path) if t.hash == "0xc3")
    assert burn.operation_type == "unknown"


def test_zero_amount_transfer_is_dropped(tmp_path: Path) -> None:
    burn = next(t for t in _fetch(tmp_path) if t.hash == "0xc3")
    assert [x.symbol for x in burn.transfers] == ["GHST"]


def test_off_target_chain_transaction_is_dropped(tmp_path: Path) -> None:
    assert all(t.hash != "0xpoly" for t in _fetch(tmp_path))


def test_native_coin_transfer_has_no_contract_address(tmp_path: Path) -> None:
    receive = next(t for t in _fetch(tmp_path) if t.hash == "0xa1")
    assert receive.transfers[0].symbol == "ETH"
    assert receive.transfers[0].address is None
    assert receive.fee is None


def test_approve_carries_no_transfers_and_acts_parse(tmp_path: Path) -> None:
    approve = next(t for t in _fetch(tmp_path) if t.hash == "0xb2")
    assert approve.transfers == []
    assert approve.acts[0].type == "approve"
    assert approve.acts[0].method_name == "approve"
    assert approve.mined_at == datetime(2025, 9, 20, 11, 30, tzinfo=UTC)


def test_parse_is_deterministic_across_runs(tmp_path: Path) -> None:
    assert _fetch(tmp_path) == _fetch(tmp_path)


def test_pagination_paces_one_pause_per_follow_up_page(tmp_path: Path) -> None:
    sleeps: list[float] = []
    urls: list[str] = []
    _fetch(tmp_path, requested_urls=urls, sleeps=sleeps)
    assert len(urls) == 2, "two pages must mean exactly two requests"
    assert "CURSOR2" in urls[1]
    assert sleeps == [1.1], "exactly one pause, before the follow-up page only"


def test_first_request_carries_target_chain_and_trash_filters(tmp_path: Path) -> None:
    urls: list[str] = []
    _fetch(tmp_path, requested_urls=urls)
    query = parse_qs(urlparse(urls[0]).query)
    assert query["currency"] == ["usd"]
    assert query["filter[chain_ids]"] == ["ethereum,base,arbitrum,optimism"]
    assert query["filter[trash]"] == ["only_non_trash"]


def test_operation_types_filter_narrows_server_side(tmp_path: Path) -> None:
    """The narrowing is upstream's: the adapter must *send* the filter (asserted
    on the URL); the fixture's trade-bearing pages stand in for the narrowed
    response, and every parsed kind matches or normalizes per the vocabulary."""
    urls: list[str] = []
    _fetch(tmp_path, requested_urls=urls, operation_types=["trade"])
    query = parse_qs(urlparse(urls[0]).query)
    assert query["filter[operation_types]"] == ["trade"]


def test_min_mined_at_is_sent_as_unix_milliseconds(tmp_path: Path) -> None:
    urls: list[str] = []
    since = datetime(2025, 9, 20, 11, 30, tzinfo=UTC)
    _fetch(tmp_path, requested_urls=urls, min_mined_at=since)
    query = parse_qs(urlparse(urls[0]).query)
    assert query["filter[min_mined_at]"] == [str(int(since.timestamp() * 1000))]


def test_missing_key_raises_auth_error_without_a_request(tmp_path: Path) -> None:
    urls: list[str] = []
    adapter = ZerionTxAdapter(
        secrets_store=SecretsStore(tmp_path / "secrets.json", environ={}),
        http_client=_paged_client(urls),
        sleep=lambda _s: None,
    )
    with pytest.raises(ZerionAuthError):
        adapter.fetch_transactions(_WALLET)
    assert urls == []


def test_http_401_raises_typed_auth_error(tmp_path: Path) -> None:
    adapter = ZerionTxAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_canned_client(401, b'{"errors":[{"title":"unauthorized"}]}'),
        sleep=lambda _s: None,
    )
    with pytest.raises(ZerionAuthError):
        adapter.fetch_transactions(_WALLET)


def test_http_429_raises_typed_rate_limit_error(tmp_path: Path) -> None:
    adapter = ZerionTxAdapter(
        secrets_store=_store_with_key(tmp_path),
        http_client=_canned_client(429, b'{"errors":[{"title":"too many requests"}]}'),
        sleep=lambda _s: None,
    )
    with pytest.raises(RateLimitedError):
        adapter.fetch_transactions(_WALLET)
