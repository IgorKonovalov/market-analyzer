"""Zerion decoded-transaction-history adapter (ADR-0034/0035/0036, Plan 0035).

Implements `TxHistorySource` against `GET /v1/wallets/{address}/transactions/`
(trailing slash — the survey's quirk table: collections want one, `/pnl` must
not have one). The endpoint returns fully decoded history: per-transfer
`direction`/`value`/`price`, `fee`, semantic `acts`, `operation_type`,
`mined_at` + `mined_at_block`, cursor-paged via an opaque `links.next` URL.

Behavior pinned by the plan:

- **Pagination to completion, spaced.** Every page is followed until
  `links.next` is absent, with a deliberate inter-page pause (survey §1: the
  free tier 429s under burst, clearing at ~1.1s spacing). The sleep is
  injectable (`sleep=`), mirroring `RpcLpDetailAdapter`, so tests don't wait.
- **Server-side narrowing.** `filter[chain_ids]` pins the four target chains,
  `filter[trash]=only_non_trash` drops spam, `filter[operation_types]` narrows
  by kind when requested, and `filter[min_mined_at]` (unix **milliseconds**) is
  the gap-fetch seam (Plan 0035 phase 3 passes the newest cached timestamp).
- **Deterministic ordering.** Zerion returns newest-first; the parsed list is
  reversed to oldest-first and each transaction gets an `in_block_index` — its
  ordinal among the wallet's same-block transactions in that chronological
  order — then the result is stably sorted by `(mined_at_block,
  in_block_index)`. Same payload → same list, never set-iteration.
- **Normalization, not interpretation.** Unrecognized `operation_type` strings
  become `"unknown"` (the model's vocabulary is closed; a raw passthrough is a
  `ValidationError`). Off-target-chain transactions are dropped. Transfers with
  `direction: "self"` (wallet-internal shuffles) and zero-amount transfers move
  nothing economically and are dropped at parse; `acts` are lenient hints
  (a malformed act is skipped, the transaction survives). Economic fields
  (`hash`, block, transfers' amounts) stay strict: a broken shape raises
  `ZerionError` rather than guessing.

Errors reuse the Plan 0032 taxonomy: missing key / HTTP 401 → `ZerionAuthError`,
429 → `RateLimitedError`, 5xx / transport exhaustion →
`UpstreamUnavailableError`, shape-broken 2xx → `ZerionError`.

Package-internal per ADR-0031: reached through the `TxHistorySource` Protocol
and the composition-root registry, never imported directly downstream.
"""

from __future__ import annotations

import time
import urllib.parse
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.adapters._zerion_common import CHAIN_IDS, basic_auth_header
from market_analyser.data.adapters.zerion import ZerionAuthError, ZerionError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain
from market_analyser.defi.tx_models import DecodedTx, TxAct, TxFee, TxTransfer
from market_analyser.persistence.secrets import SecretsStore

_TRANSACTIONS_URL = "https://api.zerion.io/v1/wallets/{address}/transactions/"
_SOURCE = "zerion-tx"

# Fresh read per scan, like the positions adapter — the phase-3 SQLite cache is
# the durable layer; an HTTP-level TTL would just hide gap-fetch bugs.
_CACHE_TTL_SECONDS = 0.0

# Inter-page pause. The survey observed free-tier burst 429s clearing at ~1.1s;
# paid once per wallet (the phase-3 cache makes re-scans read SQLite instead).
_INTER_PAGE_SECONDS = 1.1

_PAGE_SIZE = 100

# Backstop against a buggy upstream that keeps returning a `links.next`. A
# 1000-page pull (100k transactions) is far beyond any realistic wallet; hitting
# it means the cursor is looping, which is a payload defect, not data.
_MAX_PAGES = 1000

# The model's closed vocabulary, minus the "unknown" fallback itself. An
# upstream value outside this set normalizes to "unknown" at parse time.
_KNOWN_OPERATION_TYPES = frozenset(
    {
        "receive",
        "send",
        "trade",
        "deposit",
        "withdraw",
        "mint",
        "execute",
        "approve",
        "borrow",
        "repay",
    }
)

_KNOWN_STATUSES = frozenset({"confirmed", "failed", "pending"})


class ZerionTxAdapter:
    """Fetches a wallet's decoded transaction history from Zerion's REST API."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        http_client: ResilientHttpClient | None = None,
        inter_page_seconds: float = _INTER_PAGE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )
        self._inter_page_seconds = inter_page_seconds
        self._sleep = sleep

    def fetch_transactions(
        self,
        address: str,
        *,
        min_mined_at: datetime | None = None,
        operation_types: Sequence[str] | None = None,
    ) -> list[DecodedTx]:
        """Return the wallet's decoded history across the target chains, ordered
        by `(mined_at_block, in_block_index)` ascending.

        `min_mined_at` narrows server-side to transactions mined at/after it
        (the gap-fetch seam); `operation_types` narrows by kind. Raises
        `ZerionAuthError` on a missing key or HTTP 401, `RateLimitedError` /
        `UpstreamUnavailableError` on throttle / outage, and `ZerionError` (or
        `pydantic.ValidationError`) on a shape-broken payload.
        """
        key = self._secrets.get("zerion_api_key")
        if not key:
            raise ZerionAuthError(
                "zerion: no API key configured — set `zerion_api_key` before scanning",
            )
        headers = {"Authorization": basic_auth_header(key)}
        params: dict[str, str | int | float] = {
            "currency": "usd",
            "page[size]": _PAGE_SIZE,
            "filter[chain_ids]": ",".join(CHAIN_IDS),
            "filter[trash]": "only_non_trash",
        }
        if operation_types is not None:
            params["filter[operation_types]"] = ",".join(operation_types)
        if min_mined_at is not None:
            # Zerion's mined-at filters take unix milliseconds.
            params["filter[min_mined_at]"] = int(min_mined_at.timestamp() * 1000)

        url = _TRANSACTIONS_URL.format(address=urllib.parse.quote(address, safe=""))
        newest_first: list[_ParsedTx] = []
        next_url: str | None = url
        first_page = True
        for _page in range(_MAX_PAGES):
            if next_url is None:
                break
            if not first_page:
                self._sleep(self._inter_page_seconds)
            try:
                response = self._http.get(
                    next_url,
                    # `links.next` is an opaque, fully-parameterized URL — params
                    # ride only on the first request.
                    params=params if first_page else None,
                    headers=headers,
                    expect_json=True,
                )
            except ResilientHttpError as err:
                raise _classify_error(err) from err
            payload = response.json()
            newest_first.extend(_parse_page(payload))
            next_url = _next_url(payload)
            first_page = False
        else:
            raise ZerionError(
                f"zerion: transaction history exceeded {_MAX_PAGES} pages — "
                "the pagination cursor appears to be looping",
            )
        return _finalize(newest_first)


class _ParsedTx:
    """One parsed transaction before the ordering pass assigns `in_block_index`."""

    __slots__ = ("kwargs", "mined_at_block")

    def __init__(self, *, mined_at_block: int, kwargs: dict[str, Any]) -> None:
        self.mined_at_block = mined_at_block
        self.kwargs = kwargs


def _finalize(newest_first: list[_ParsedTx]) -> list[DecodedTx]:
    """Assign in-block ordinals and produce the deterministic ascending order.

    Zerion pages newest-first; reversing gives chronological order, in which
    each transaction's ordinal among its block-mates is assigned. The final
    stable sort by block guarantees the `(block, in_block_index)` invariant even
    if upstream ordering was imperfect across a page boundary."""
    chronological = list(reversed(newest_first))
    counters: dict[int, int] = {}
    decoded: list[DecodedTx] = []
    for parsed in chronological:
        index = counters.get(parsed.mined_at_block, 0)
        counters[parsed.mined_at_block] = index + 1
        decoded.append(DecodedTx(in_block_index=index, **parsed.kwargs))
    decoded.sort(key=lambda tx: (tx.mined_at_block, tx.in_block_index))
    return decoded


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: 401 → auth, 429 → rate-limited, anything else → unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 401:
        return ZerionAuthError("zerion: API key rejected (HTTP 401)")
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("zerion: rate limited (HTTP 429) fetching transactions")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"zerion: upstream unavailable ({detail}) fetching transactions",
    )


def _next_url(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        raise ZerionError("zerion: transactions response was not a JSON object")
    links = payload.get("links")
    if not isinstance(links, dict):
        return None
    next_url = links.get("next")
    return next_url if isinstance(next_url, str) and next_url else None


def _parse_page(payload: Any) -> list[_ParsedTx]:
    if not isinstance(payload, dict):
        raise ZerionError("zerion: transactions response was not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ZerionError("zerion: transactions response 'data' is missing or not a list")
    parsed: list[_ParsedTx] = []
    for entry in data:
        tx = _parse_entry(entry)
        if tx is not None:
            parsed.append(tx)
    return parsed


def _parse_entry(entry: Any) -> _ParsedTx | None:
    """Decode one transaction entry. Returns `None` for off-target-chain
    transactions (deliberately dropped); raises `ZerionError` on a structurally
    broken entry."""
    if not isinstance(entry, dict):
        raise ZerionError("zerion: transaction entry was not an object")
    attributes = entry.get("attributes")
    if not isinstance(attributes, dict):
        raise ZerionError("zerion: transaction entry missing 'attributes' object")

    chain = _chain_of(entry)
    if chain is None:
        return None  # off-target or unknown chain

    tx_hash = attributes.get("hash")
    if not isinstance(tx_hash, str) or not tx_hash:
        raise ZerionError("zerion: transaction missing 'hash'")
    mined_at_block = attributes.get("mined_at_block")
    if isinstance(mined_at_block, bool) or not isinstance(mined_at_block, int):
        raise ZerionError(f"zerion: transaction {tx_hash} missing integer 'mined_at_block'")
    mined_at = attributes.get("mined_at")
    if not isinstance(mined_at, str) or not mined_at:
        raise ZerionError(f"zerion: transaction {tx_hash} missing 'mined_at'")
    status = attributes.get("status")
    if status not in _KNOWN_STATUSES:
        raise ZerionError(f"zerion: transaction {tx_hash} has unrecognized status {status!r}")

    raw_operation = attributes.get("operation_type")
    operation_type = (
        raw_operation
        if isinstance(raw_operation, str) and raw_operation in _KNOWN_OPERATION_TYPES
        else "unknown"
    )

    kwargs: dict[str, Any] = {
        "chain": chain,
        "hash": tx_hash,
        "operation_type": operation_type,
        "mined_at": mined_at,  # ISO string; the pydantic boundary parses it
        "mined_at_block": mined_at_block,
        "status": status,
        "transfers": _transfers_of(attributes, chain, tx_hash),
        "fee": _fee_of(attributes),
        "acts": _acts_of(attributes),
    }
    return _ParsedTx(mined_at_block=mined_at_block, kwargs=kwargs)


def _chain_of(entry: dict[str, Any]) -> Chain | None:
    relationships = entry.get("relationships")
    if not isinstance(relationships, dict):
        return None
    chain_rel = relationships.get("chain")
    if not isinstance(chain_rel, dict):
        return None
    chain_data = chain_rel.get("data")
    if not isinstance(chain_data, dict):
        return None
    chain_id = chain_data.get("id")
    if not isinstance(chain_id, str):
        return None
    return CHAIN_IDS.get(chain_id)


def _transfers_of(attributes: dict[str, Any], chain: Chain, tx_hash: str) -> list[TxTransfer]:
    raw = attributes.get("transfers")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ZerionError(f"zerion: transaction {tx_hash} 'transfers' is not a list")
    transfers: list[TxTransfer] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ZerionError(f"zerion: transaction {tx_hash} transfer was not an object")
        direction = item.get("direction")
        if direction == "self":
            continue  # wallet-internal shuffle — no economic movement
        if direction not in ("in", "out"):
            raise ZerionError(
                f"zerion: transaction {tx_hash} transfer has unrecognized direction {direction!r}",
            )
        fungible = item.get("fungible_info")
        if not isinstance(fungible, dict):
            raise ZerionError(f"zerion: transaction {tx_hash} transfer missing 'fungible_info'")
        symbol = fungible.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ZerionError(f"zerion: transaction {tx_hash} transfer missing a symbol")
        amount = _quantity_float(item, tx_hash)
        if amount == 0:
            continue  # a zero-quantity transfer moves nothing — not a leg
        transfers.append(
            TxTransfer(
                direction=direction,
                symbol=symbol,
                address=_implementation_address(fungible, chain),
                amount=amount,
                usd_value=_optional_number(item.get("value")),
                price=_optional_number(item.get("price")),
            )
        )
    return transfers


def _fee_of(attributes: dict[str, Any]) -> TxFee | None:
    fee = attributes.get("fee")
    if not isinstance(fee, dict):
        return None
    fungible = fee.get("fungible_info")
    symbol = fungible.get("symbol") if isinstance(fungible, dict) else None
    if not isinstance(symbol, str) or not symbol:
        return None
    quantity = fee.get("quantity")
    amount = quantity.get("float") if isinstance(quantity, dict) else None
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    return TxFee(symbol=symbol, amount=float(amount), usd_value=_optional_number(fee.get("value")))


def _acts_of(attributes: dict[str, Any]) -> list[TxAct]:
    """Acts are classification *hints* (phase 5), parsed leniently: an act
    missing its id or type is skipped rather than failing the transaction."""
    raw = attributes.get("acts")
    if not isinstance(raw, list):
        return []
    acts: list[TxAct] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        act_id = item.get("id")
        act_type = item.get("type")
        if not isinstance(act_id, str) or not act_id:
            continue
        if not isinstance(act_type, str) or not act_type:
            continue
        metadata = item.get("application_metadata")
        contract_address: str | None = None
        method_name: str | None = None
        if isinstance(metadata, dict):
            raw_contract = metadata.get("contract_address")
            if isinstance(raw_contract, str) and raw_contract:
                contract_address = raw_contract
            method = metadata.get("method")
            if isinstance(method, dict):
                raw_name = method.get("name")
                if isinstance(raw_name, str) and raw_name:
                    method_name = raw_name
        acts.append(
            TxAct(
                act_id=act_id,
                type=act_type,
                contract_address=contract_address,
                method_name=method_name,
            )
        )
    return acts


def _quantity_float(item: dict[str, Any], tx_hash: str) -> float:
    quantity = item.get("quantity")
    if not isinstance(quantity, dict):
        raise ZerionError(f"zerion: transaction {tx_hash} transfer missing 'quantity' object")
    value = quantity.get("float")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZerionError(f"zerion: transaction {tx_hash} transfer 'quantity.float' non-numeric")
    return float(value)


def _optional_number(value: Any) -> float | None:
    """Zerion's point-in-time `value` / `price` may be null for unpriced tokens;
    a non-numeric non-null value is treated as absent rather than trusted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _implementation_address(fungible: dict[str, Any], chain: Chain) -> str | None:
    """The token's contract address on this transaction's chain, if Zerion lists
    an implementation for it; otherwise the first listed, else `None` (native
    coin)."""
    implementations = fungible.get("implementations")
    if not isinstance(implementations, list):
        return None
    first: str | None = None
    for impl in implementations:
        if not isinstance(impl, dict):
            continue
        address = impl.get("address")
        if not isinstance(address, str) or not address:
            continue
        if first is None:
            first = address
        if impl.get("chain_id") == chain:
            return address
    return first


__all__ = ["ZerionTxAdapter"]
