"""Zerion wallet-positions adapter (ADR-0034, ADR-0031, ADR-0019, ADR-0038).

Discovers a wallet's interpreted DeFi positions across Ethereum / Base /
Arbitrum / Optimism from Zerion's REST API — one `GET /v1/wallets/{address}/
positions/` call covers all chains. We call the REST endpoint directly through
the shared `ResilientHttpClient` (no vendor SDK, so no entry on the cooldown/pin
surface — only a key to manage). The API key is read **lazily** from the
`SecretsStore` at call time and injected as HTTP Basic auth (the key is the
username, password empty) inside the sidecar (ADR-0038 server-side injection);
the adapter therefore constructs even before a key is set, and a keyless scan
fails typed (`ZerionAuthError`), not at construction.

Zerion's positions endpoint returns JSON:API: `data` is a list of `positions`,
each with `attributes` (`position_type`, `protocol`, `protocol_module`,
`fungible_info`, `quantity`, `value`, `group_id`) and `relationships`
(`chain.data.id`). We map that onto `DefiPosition`:

- `protocol_module == "lending"` → `lending_borrow` if `position_type == "loan"`
  else `lending_supply` (Aave supply vs borrow).
- `protocol_module == "liquidity_pool"` → `lp`; Zerion splits an LP into one
  position entry per underlying token sharing a `group_id`, so those entries are
  merged into a single `DefiPosition` with both tokens and summed value.
- `position_type == "staked"` → `staking`.
- `wallet` (plain balances) and anything unclassifiable / off-target-chain are
  dropped — discovery is about *DeFi positions*, not raw balances.

Tick boundaries for Uniswap-v3 LPs are deliberately **not** decoded: Zerion does
not expose them; they belong to the deep-adapter plan (ADR-0034, and Plan 0032
"What this plan does NOT do"). `tick_*` / `in_range` stay `None`.

Errors are typed (done-when): a missing key or HTTP 401 raises `ZerionAuthError`;
a 429 / 5xx / transport exhaustion raises the shared `RateLimitedError` /
`UpstreamUnavailableError`; a 2xx whose shape is broken raises `ZerionError`.
Out-of-range measurements (NaN / negative value, non-positive amount) surface as
`pydantic.ValidationError` from the `DefiPosition` boundary — also typed.

Package-internal per ADR-0031: reached through the `WalletPositionsSource`
Protocol and the composition-root registry, never imported directly downstream.
"""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Iterable
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain, DefiPosition, PositionKind, PositionToken
from market_analyser.persistence.secrets import SecretsStore

_POSITIONS_URL = "https://api.zerion.io/v1/wallets/{address}/positions/"
_SOURCE = "zerion"

# Positions are live/volatile and a scan is request-triggered (ADR-0034: a chatty
# cadence could blow the free-tier cap), so the adapter does not cache — each
# scan is a fresh read.
_CACHE_TTL_SECONDS = 0.0

# Zerion's chain ids → our target-chain literal. A position on any chain absent
# from this map is dropped (off-target).
_CHAIN_IDS: dict[str, Chain] = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
}


class ZerionError(ValueError):
    """The upstream 2xx payload was missing a field the position read requires,
    or was structurally not the expected JSON:API shape — raised at the adapter
    boundary before model construction."""


class ZerionAuthError(UpstreamDataError):
    """No Zerion API key is configured, or Zerion rejected the key (HTTP 401).
    A configuration failure surfaced through the typed upstream taxonomy so
    callers branch on a reason rather than a bare exception."""


class ZerionAdapter:
    """Fetches a wallet's interpreted DeFi positions from Zerion's REST API."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        http_client: ResilientHttpClient | None = None,
    ) -> None:
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )

    def fetch_positions(self, address: str) -> list[DefiPosition]:
        """Return the wallet's decoded positions across the target chains.

        Raises `ZerionAuthError` on a missing key or HTTP 401, the shared
        `RateLimitedError` / `UpstreamUnavailableError` on throttle / outage, and
        `ZerionError` (or `pydantic.ValidationError`) on a shape-broken payload.
        """
        key = self._secrets.get("zerion_api_key")
        if not key:
            raise ZerionAuthError(
                "zerion: no API key configured — set `zerion_api_key` before scanning",
            )
        url = _POSITIONS_URL.format(address=urllib.parse.quote(address, safe=""))
        try:
            response = self._http.get(
                url,
                # `filter[positions]` defaults to `only_simple` on Zerion's side,
                # which returns ONLY plain wallet balances and excludes every
                # complex DeFi position (Aave / Uniswap-v3 / Aerodrome) — i.e. the
                # whole point of discovery. Request `no_filter` to get complex
                # positions too; the parser drops the simple balances by kind.
                params={"currency": "usd", "filter[positions]": "no_filter"},
                headers={"Authorization": _basic_auth_header(key)},
                expect_json=True,
            )
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        return _parse_positions(response.json())


def _basic_auth_header(key: str) -> str:
    """Zerion uses HTTP Basic auth with the API key as the username and an empty
    password. The key never reaches a log or the cache key (the client excludes
    headers from the cache key; nothing here logs the header)."""
    token = base64.b64encode(f"{key}:".encode()).decode("ascii")
    return f"Basic {token}"


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: 401 → auth, 429 → rate-limited, anything else → unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 401:
        return ZerionAuthError("zerion: API key rejected (HTTP 401)")
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("zerion: rate limited (HTTP 429) fetching positions")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(f"zerion: upstream unavailable ({detail}) fetching positions")


def _parse_positions(payload: Any) -> list[DefiPosition]:
    """Decode the JSON:API positions payload into normalized `DefiPosition`s.

    LP entries sharing a `group_id` are merged into one position (both tokens,
    summed value). Output order follows Zerion's `data` order — deterministic, no
    set iteration (the determinism contract)."""
    if not isinstance(payload, dict):
        raise ZerionError("zerion: response was not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ZerionError("zerion: response 'data' is missing or not a list")

    # Insertion-ordered accumulation keyed by the position's stable group key, so
    # an LP's two token-entries fold into one position while order stays stable.
    grouped: dict[str, _PositionGroup] = {}
    for entry in data:
        parsed = _parse_entry(entry)
        if parsed is None:
            continue  # off-target chain, plain balance, or unclassifiable — dropped
        group = grouped.get(parsed.key)
        if group is None:
            grouped[parsed.key] = _PositionGroup.from_first(parsed)
        else:
            group.merge(parsed)
    return [group.to_position() for group in grouped.values()]


class _ParsedEntry:
    """One decoded Zerion position entry, pre-merge."""

    __slots__ = ("chain", "key", "kind", "pool", "protocol", "token", "usd_value")

    def __init__(
        self,
        *,
        key: str,
        chain: Chain,
        protocol: str,
        kind: PositionKind,
        token: PositionToken,
        usd_value: float,
        pool: str | None,
    ) -> None:
        self.key = key
        self.chain = chain
        self.protocol = protocol
        self.kind = kind
        self.token = token
        self.usd_value = usd_value
        self.pool = pool


class _PositionGroup:
    """Accumulator that folds same-`group_id` entries into one position."""

    def __init__(self, first: _ParsedEntry) -> None:
        self._first = first
        self._tokens: list[PositionToken] = [first.token]
        self._usd_value = first.usd_value

    @classmethod
    def from_first(cls, entry: _ParsedEntry) -> _PositionGroup:
        return cls(entry)

    def merge(self, entry: _ParsedEntry) -> None:
        self._tokens.append(entry.token)
        self._usd_value += entry.usd_value

    def to_position(self) -> DefiPosition:
        return DefiPosition(
            position_id=self._first.key,
            chain=self._first.chain,
            protocol=self._first.protocol,
            kind=self._first.kind,
            tokens=self._tokens,
            usd_value=self._usd_value,
            pool=self._first.pool,
        )


def _parse_entry(entry: Any) -> _ParsedEntry | None:
    """Decode one Zerion position. Returns `None` for entries we deliberately
    drop (off-target chain, plain wallet balance, unclassifiable). Raises
    `ZerionError` on a structurally broken entry that we *can't* classify as
    droppable (e.g. missing `attributes`)."""
    if not isinstance(entry, dict):
        raise ZerionError("zerion: position entry was not an object")
    attributes = entry.get("attributes")
    if not isinstance(attributes, dict):
        raise ZerionError("zerion: position entry missing 'attributes' object")

    chain = _chain_of(entry)
    if chain is None:
        return None  # off-target or unknown chain

    position_type = attributes.get("position_type")
    protocol_module = attributes.get("protocol_module")
    kind = _classify_kind(position_type, protocol_module)
    if kind is None:
        return None  # plain wallet balance, reward, or otherwise not a tracked kind

    usd_value = _require_float(attributes.get("value"), "value")
    token = _token_of(attributes, chain)
    protocol = _protocol_of(attributes)
    pool = attributes.get("name") if kind == "lp" else None
    pool = pool if isinstance(pool, str) and pool else None

    group_id = attributes.get("group_id")
    stable = group_id if isinstance(group_id, str) and group_id else _entry_id(entry)
    key = f"{chain}:{protocol}:{stable}"

    return _ParsedEntry(
        key=key,
        chain=chain,
        protocol=protocol,
        kind=kind,
        token=token,
        usd_value=usd_value,
        pool=pool,
    )


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
    return _CHAIN_IDS.get(chain_id)


def _classify_kind(position_type: Any, protocol_module: Any) -> PositionKind | None:
    if protocol_module == "liquidity_pool":
        return "lp"
    if protocol_module == "lending":
        return "lending_borrow" if position_type == "loan" else "lending_supply"
    if position_type == "staked":
        return "staking"
    if position_type == "loan":
        return "lending_borrow"
    return None


def _protocol_of(attributes: dict[str, Any]) -> str:
    protocol = attributes.get("protocol")
    if isinstance(protocol, str) and protocol:
        return protocol
    # Zerion occasionally carries the dapp id only; fall back to the name, then a
    # constant — never empty (the model requires a non-empty protocol).
    name = attributes.get("name")
    if isinstance(name, str) and name:
        return name
    return "unknown"


def _token_of(attributes: dict[str, Any], chain: Chain) -> PositionToken:
    fungible = attributes.get("fungible_info")
    if not isinstance(fungible, dict):
        raise ZerionError("zerion: position missing 'fungible_info'")
    symbol = fungible.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ZerionError("zerion: position 'fungible_info' missing a symbol")
    amount = _require_float(_quantity_float(attributes.get("quantity")), "quantity.float")
    return PositionToken(
        symbol=symbol,
        address=_implementation_address(fungible, chain) or symbol,
        amount=amount,
    )


def _quantity_float(quantity: Any) -> Any:
    if not isinstance(quantity, dict):
        raise ZerionError("zerion: position missing 'quantity' object")
    return quantity.get("float")


def _implementation_address(fungible: dict[str, Any], chain: Chain) -> str | None:
    """The token's contract address on this position's chain, if Zerion lists an
    implementation for it; otherwise the first listed, else `None`."""
    implementations = fungible.get("implementations")
    if not isinstance(implementations, Iterable):
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


def _entry_id(entry: dict[str, Any]) -> str:
    entry_id = entry.get("id")
    if isinstance(entry_id, str) and entry_id:
        return entry_id
    raise ZerionError("zerion: position entry missing a stable 'id'")


def _require_float(value: Any, field: str) -> float:
    """Coerce a numeric Zerion field to `float`. A non-numeric / `None` value is
    a malformed position (`ZerionError`); a NaN/Inf/out-of-range value is caught
    later by the `DefiPosition` boundary. `bool` is rejected (it is an `int`
    subclass but never a valid measurement)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZerionError(f"zerion: position field '{field}' missing or non-numeric")
    return float(value)


__all__ = ["ZerionAdapter", "ZerionAuthError", "ZerionError"]
