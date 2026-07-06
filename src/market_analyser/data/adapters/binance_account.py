"""Binance read-only account adapter (Plan 0041 phase 1; ADR-0042, ADR-0031,
ADR-0038, ADR-0019, ADR-0052).

Fetches the user's **spot balances** (`GET /api/v3/account`, `api.binance.com`)
and **open USDⓈ-M futures positions** (`GET /fapi/v2/positionRisk`,
`fapi.binance.com`) into one `AccountHoldings` snapshot — the Binance leg of
the cross-venue portfolio (ADR-0042). Both endpoints are signed reads
(`USER_DATA`): the request carries an `X-MBX-APIKEY` header plus an
HMAC-SHA256 signature of the query string, computed with the **read-only**
API key/secret pair sourced lazily from the `SecretsStore` at call time
(`binance_read_api_key` / `binance_read_api_secret`, ADR-0038 server-side
injection — the adapter constructs before a key is set; a keyless fetch fails
typed, never at construction).

**Read-only by charter.** This adapter reaches only the two account-read
endpoints above; no order, transfer, or any other write path exists here (the
Plan 0041 done-when pins this with a source-level scan). The credential it
uses belongs in the ADR-0038 third-party store precisely because it is a
read-only key — the trade keychain is Pillar 5's (ADR-0044) and is not
touched.

Secret handling (ADR-0038 / ADR-0011): the key travels only in the request
header, the secret is used only as HMAC material — neither is ever logged,
echoed into an error message, or placed in a URL. `__repr__` renders no
credential state beyond what `SecretsStore.status()` would.

Parsing: Binance serves quantities as decimal strings (`"0.50000000"`); they
are parsed with `float(...)`. Zero-total spot balances and flat (zero
`positionAmt`) futures rows are dropped — an emptied wallet line is "nothing
held", not a holding. Upstream order is preserved (determinism — no set
iteration). A spot-only account whose key lacks futures permission fails
**loud** with the typed auth error naming the futures leg — enable futures
*read* permission on the key, or the venue leg stays unreadable; the adapter
never silently returns a half-empty snapshot.

`as_of` is the query instant (injected `clock`, defaulting to `time.time`) —
a live account read has no replayable history, so the leg's freshness stamp is
the fetch time, carried as provenance and never blended with other venues
(ADR-0042). The same clock feeds the signed request's `timestamp` parameter.

Errors are typed: missing key/secret or a rejected key (HTTP 401, or the
-1022 / -2014 / -2015 Binance auth codes) → `BinanceAccountAuthError`;
HTTP 451 → `GeoRestrictedError` (permanent, never retried — ADR-0052's rule,
pinned by `BinanceAccountHttpClient`); HTTP 429 → `RateLimitedError`; other
exhaustion → `UpstreamUnavailableError`; a shape-broken 2xx →
`BinanceAccountError`.

Package-internal per ADR-0031: reached through the `AccountHoldingsSource`
Protocol and the composition-root registry, never imported directly
downstream.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import (
    ErrorKind,
    HttpResponse,
    ResilientHttpClient,
    ResilientHttpError,
)
from market_analyser.data.errors import (
    GeoRestrictedError,
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.types import AccountHoldings, FuturesPosition, SpotBalance
from market_analyser.persistence.secrets import SecretsStore

_SPOT_ACCOUNT_URL = "https://api.binance.com/api/v3/account"
_FUTURES_POSITIONS_URL = "https://fapi.binance.com/fapi/v2/positionRisk"
_SOURCE = "binance-account"
_VENUE = "binance"

# Account reads are live/volatile and fetch-on-request; caching buys nothing.
_CACHE_TTL_SECONDS = 0.0

# Binance auth-failure body codes that mean "credential problem", not outage:
# -1022 signature mismatch (wrong secret), -2014 API-key format invalid,
# -2015 invalid key / IP / permissions (also what a key without futures-read
# permission returns on the fapi leg).
_AUTH_ERROR_CODES = frozenset({-1022, -2014, -2015})


class BinanceAccountError(ValueError):
    """The upstream 2xx payload broke shape (missing/non-numeric field, non-list
    positions body) — raised at the adapter boundary before model construction."""


class BinanceAccountAuthError(UpstreamDataError):
    """No Binance read key/secret is configured, or Binance rejected the
    credential (HTTP 401, or a -1022/-2014/-2015 auth code — the latter is also
    what a key lacking futures *read* permission returns on the positions leg).
    A configuration failure surfaced through the typed upstream taxonomy; the
    message never carries a credential value."""


class BinanceAccountHttpClient(ResilientHttpClient):
    """`ResilientHttpClient` pinning Binance's geo-restriction response: HTTP
    451 is structural (ADR-0052), `PERMANENT`, never retried — the same pin as
    `BinanceFuturesHttpClient`, restated here so the guarantee is independent
    of the base classifier's default 4xx policy."""

    def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
        if response is not None and response.status_code == 451:
            return ErrorKind.PERMANENT
        return super().classify(exc, response)


class BinanceAccountAdapter:
    """Fetches the account's spot balances + open USDⓈ-M positions, read-only.

    `clock` is the adapter's only time source (signed-request `timestamp` and
    the snapshot's `as_of` stamp) — injectable so tests are deterministic.
    """

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        http_client: ResilientHttpClient | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else BinanceAccountHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )
        self._clock: Callable[[], float] = clock if clock is not None else time.time

    def fetch_account_holdings(self) -> AccountHoldings:
        """Return the account's holdings snapshot (`AccountHoldingsSource`).

        Raises `BinanceAccountAuthError` on a missing or rejected credential,
        `GeoRestrictedError` on HTTP 451 (never retried), the shared
        `RateLimitedError` / `UpstreamUnavailableError` on throttle / outage,
        and `BinanceAccountError` (or `pydantic.ValidationError`) on a
        shape-broken payload.
        """
        key = self._secrets.get("binance_read_api_key")
        secret = self._secrets.get("binance_read_api_secret")
        if not key or not secret:
            raise BinanceAccountAuthError(
                "binance-account: no read credential configured — set "
                "`binance_read_api_key` and `binance_read_api_secret` before reading holdings",
            )
        as_of = datetime.fromtimestamp(self._clock(), tz=UTC)
        spot = _parse_spot_balances(self._signed_get(_SPOT_ACCOUNT_URL, key, secret, what="spot"))
        futures = _parse_futures_positions(
            self._signed_get(_FUTURES_POSITIONS_URL, key, secret, what="futures"),
        )
        return AccountHoldings(venue=_VENUE, spot=spot, futures=futures, as_of=as_of)

    def _signed_get(self, url: str, key: str, secret: str, *, what: str) -> Any:
        """One signed Binance read: HMAC-SHA256 of the exact query string,
        key in the `X-MBX-APIKEY` header. The full URL is assembled here (no
        client-side param re-encoding) so the signature is provably computed
        over the bytes that go out on the wire."""
        query = urllib.parse.urlencode({"timestamp": int(self._clock() * 1000)})
        signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256)
        try:
            response = self._http.get(
                f"{url}?{query}&signature={signature.hexdigest()}",
                headers={"X-MBX-APIKEY": key},
                expect_json=True,
            )
        except ResilientHttpError as err:
            raise _classify_error(err, what=what) from err
        return response.json()

    def __repr__(self) -> str:
        # Never a credential value (ADR-0038 rule 1); presence lives in
        # SecretsStore.status(), not here.
        return f"BinanceAccountAdapter(source={_SOURCE!r})"


def _classify_error(err: ResilientHttpError, *, what: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: 401 / Binance auth body-codes → auth, 451 → geo-restricted,
    429 → rate-limited, anything else → unavailable. Never echoes a header or
    body value — only the status and leg name reach the message."""
    resp = err.last_response
    if resp is not None:
        if resp.status_code == 401 or _auth_error_code(resp) in _AUTH_ERROR_CODES:
            return BinanceAccountAuthError(
                f"binance-account: read credential rejected fetching {what} holdings "
                f"(HTTP {resp.status_code}) — check the key, its IP allowlist, and that "
                "futures *read* permission is enabled for the positions leg",
            )
        if resp.status_code == 451:
            return GeoRestrictedError(
                f"binance-account: geo-restricted (HTTP 451) fetching {what} holdings",
            )
        if resp.status_code == 429:
            return RateLimitedError(
                f"binance-account: rate limited (HTTP 429) fetching {what} holdings",
            )
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"binance-account: upstream unavailable ({detail}) fetching {what} holdings",
    )


def _auth_error_code(resp: HttpResponse) -> int | None:
    """Binance's JSON error body code (`{"code": -2015, "msg": ...}`), or `None`
    when the body is not that shape — auth failures can arrive as HTTP 400/403
    with the reason only in the body code."""
    try:
        payload = json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("code"), int):
        return int(payload["code"])
    return None


def _parse_spot_balances(payload: Any) -> list[SpotBalance]:
    """Decode `/api/v3/account` → non-empty `SpotBalance`s, upstream order kept."""
    if not isinstance(payload, dict):
        raise BinanceAccountError("binance-account: spot account response was not a JSON object")
    balances = payload.get("balances")
    if not isinstance(balances, list):
        raise BinanceAccountError(
            "binance-account: spot account response 'balances' is missing or not a list",
        )
    parsed: list[SpotBalance] = []
    for entry in balances:
        if not isinstance(entry, dict):
            raise BinanceAccountError("binance-account: spot balance entry was not an object")
        asset = entry.get("asset")
        if not isinstance(asset, str) or not asset:
            raise BinanceAccountError("binance-account: spot balance entry missing 'asset'")
        free = _require_decimal(entry.get("free"), "free")
        locked = _require_decimal(entry.get("locked"), "locked")
        if free + locked == 0:
            continue  # emptied wallet line — nothing held, not a holding
        parsed.append(SpotBalance(asset=asset, free=free, locked=locked))
    return parsed


def _parse_futures_positions(payload: Any) -> list[FuturesPosition]:
    """Decode `/fapi/v2/positionRisk` → open `FuturesPosition`s, upstream order
    kept. `positionAmt` is signed (negative = short); flat rows are dropped."""
    if not isinstance(payload, list):
        raise BinanceAccountError("binance-account: futures positions response was not a list")
    parsed: list[FuturesPosition] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise BinanceAccountError("binance-account: futures position entry was not an object")
        quantity = _require_decimal(entry.get("positionAmt"), "positionAmt")
        if quantity == 0:
            continue  # flat contract row — positionRisk lists every symbol ever touched
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise BinanceAccountError("binance-account: futures position entry missing 'symbol'")
        position_side = entry.get("positionSide")
        if not isinstance(position_side, str) or not position_side:
            raise BinanceAccountError(
                "binance-account: futures position entry missing 'positionSide'",
            )
        parsed.append(
            FuturesPosition(
                symbol=symbol,
                quantity=quantity,
                entry_price=_require_decimal(entry.get("entryPrice"), "entryPrice"),
                position_side=position_side,
                mark_price=_optional_decimal(entry.get("markPrice")),
                unrealized_pnl_usd=_optional_decimal(entry.get("unRealizedProfit")),
            ),
        )
    return parsed


def _require_decimal(value: Any, field: str) -> float:
    """Coerce Binance's decimal-string (or numeric) field to `float`. A
    missing / unparseable value is a malformed entry (`BinanceAccountError`);
    NaN/Inf/sign violations are caught by the model boundary. `bool` is
    rejected (an `int` subclass, never a valid measurement)."""
    if isinstance(value, bool):
        raise BinanceAccountError(f"binance-account: field '{field}' missing or non-numeric")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            raise BinanceAccountError(
                f"binance-account: field '{field}' missing or non-numeric",
            ) from None
    raise BinanceAccountError(f"binance-account: field '{field}' missing or non-numeric")


def _optional_decimal(value: Any) -> float | None:
    """Like `_require_decimal` for fields the venue may omit; `None` (or an
    absent/empty value) stays `None` rather than failing the row."""
    if value is None or (isinstance(value, str) and not value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


__all__ = [
    "BinanceAccountAdapter",
    "BinanceAccountAuthError",
    "BinanceAccountError",
    "BinanceAccountHttpClient",
]
