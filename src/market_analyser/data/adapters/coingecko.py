"""CoinGecko macro-context adapter — Plan 0022 (ADR-0027, ADR-0019, ADR-0007).

Two keyless calls to CoinGecko's free public API, both through one
`ResilientHttpClient` (shared 60s TTL cache / retry / backoff / concurrency cap):

- ``GET /api/v3/global`` → BTC dominance (``market_cap_percentage.btc``), total
  market cap in USD (``total_market_cap.usd``), total-cap 24h change
  (``market_cap_change_percentage_24h_usd``), and the snapshot timestamp
  (``updated_at``).
- ``GET /api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true``
  → BTC price (``bitcoin.usd``) and BTC's own 24h change (``bitcoin.usd_24h_change``).

The ``/global`` endpoint carries dominance and the aggregate cap trend but neither
BTC's spot price nor BTC's own 24h move; the second (equally keyless) call supplies
both. BTC's 24h change is not merely displayed — it feeds the regime classifier:
BTC out/under-performing the whole market is the dominance-*trend* proxy that
``/global``'s single dominance snapshot cannot give on its own.

`regime` is computed in-house by `classify_crypto_regime` per ADR-0027: a fixed,
closed, neutral vocabulary describing market *structure*, never advice. The exact
thresholds are pinned here (the constants below) and confirmed against ADR-0027 at
the Plan 0022 close ceremony.

A `ResilientHttpError` (exhausted retries / permanent failure) is translated into
the typed `UpstreamDataError` taxonomy (429 → rate-limited, else unavailable) so
callers branch on a reason rather than a raw transport exception. A shape-broken
2xx payload raises `CoinGeckoError`.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol, never by importing this class.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.types import CryptoRegime, MacroContext

_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
_SOURCE = "coingecko"

# 60s TTL (ADR-0019): macro structure shifts slowly, so a minute of caching
# collapses a burst of "give me the crypto picture" calls into one upstream hit
# per endpoint without ever serving a meaningfully stale read.
_CACHE_TTL_SECONDS = 60.0

# --- Regime classification thresholds (ADR-0027; pinned here, confirmed at close) ---
# Total market cap contracting by at least this many percent over 24h is a broad
# risk-off outflow across the asset class, regardless of where dominance sits.
_RISK_OFF_TOTAL_CHANGE_PCT = -5.0
# BTC out/under-performing the whole market's 24h move by at least this many
# percentage points is the dominance-trend signal: outperformance ⇒ dominance
# rising (btc_led); underperformance while the market is not contracting ⇒ capital
# rotating toward alts (alt_structure). Inside the band the structure is mixed
# (neutral). A percentage-point comparison of two 24h-change percentages.
_DOMINANCE_TREND_DEADBAND_PP = 1.0


def classify_crypto_regime(
    *,
    btc_change_24h: float,
    total_market_cap_change_24h: float,
) -> CryptoRegime:
    """Map the two 24h trend measurements onto the neutral structural vocabulary
    (ADR-0027). Pure and deterministic — no wall-clock read, no ordering
    dependence, so the same inputs always yield the same label.

    Evaluation order encodes ADR-0027's table priority: a material broad
    contraction is `risk_off_structure` first; otherwise BTC's performance
    relative to the whole market sets the dominance trend (`btc_led` outperforming,
    `alt_structure` underperforming); a within-band, non-contracting market is
    `neutral`.
    """
    if total_market_cap_change_24h <= _RISK_OFF_TOTAL_CHANGE_PCT:
        return "risk_off_structure"
    relative_to_market = btc_change_24h - total_market_cap_change_24h
    if relative_to_market >= _DOMINANCE_TREND_DEADBAND_PP:
        return "btc_led"
    if relative_to_market <= -_DOMINANCE_TREND_DEADBAND_PP:
        return "alt_structure"
    return "neutral"


class CoinGeckoError(ValueError):
    """The upstream 2xx payload was missing a field the macro read requires —
    raised at the adapter boundary before model construction."""


class CoinGeckoAdapter:
    """Fetches the current crypto macro context from CoinGecko's free public API."""

    def __init__(self, http_client: ResilientHttpClient | None = None) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="coingecko",
                cache_ttl_seconds=_CACHE_TTL_SECONDS,
            )
        )

    def fetch_macro_context(self) -> MacroContext:
        """Return the current crypto macro context.

        Raises a typed `UpstreamDataError` on upstream exhaustion, `CoinGeckoError`
        on a shape-broken payload, and `pydantic.ValidationError` on an
        out-of-range measurement (e.g. dominance outside ``[0, 100]``).
        """
        try:
            global_payload = self._http.get(_GLOBAL_URL, expect_json=True).json()
            price_payload = self._http.get(
                _SIMPLE_PRICE_URL,
                params={
                    "ids": "bitcoin",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
                expect_json=True,
            ).json()
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        return self._parse(global_payload, price_payload)

    def _parse(self, global_payload: Any, price_payload: Any) -> MacroContext:
        data = global_payload.get("data") if isinstance(global_payload, dict) else None
        if not isinstance(data, dict):
            raise CoinGeckoError("coingecko: /global payload missing 'data' object")
        bitcoin = price_payload.get("bitcoin") if isinstance(price_payload, dict) else None
        if not isinstance(bitcoin, dict):
            raise CoinGeckoError("coingecko: /simple/price payload missing 'bitcoin' object")

        dominance = _nested_float(data, "market_cap_percentage", "btc")
        total_cap = _nested_float(data, "total_market_cap", "usd")
        total_change = _get_float(data.get("market_cap_change_percentage_24h_usd"))
        btc_price = _get_float(bitcoin.get("usd"))
        btc_change = _get_float(bitcoin.get("usd_24h_change"))
        updated_at = data.get("updated_at")

        if (
            dominance is None
            or total_cap is None
            or total_change is None
            or btc_price is None
            or btc_change is None
        ):
            raise CoinGeckoError("coingecko: payload missing a required macro measurement")
        if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
            raise CoinGeckoError("coingecko: /global payload missing numeric 'updated_at'")

        return MacroContext(
            market="crypto",
            btc_price=btc_price,
            btc_change_24h=btc_change,
            btc_dominance_pct=dominance,
            total_market_cap_usd=total_cap,
            total_market_cap_change_24h=total_change,
            regime=classify_crypto_regime(
                btc_change_24h=btc_change,
                total_market_cap_change_24h=total_change,
            ),
            as_of=datetime.fromtimestamp(int(updated_at), tz=UTC),
            source=_SOURCE,
        )


def _get_float(value: Any) -> float | None:
    """Coerce a CoinGecko numeric field to `float`, or `None` if absent/non-numeric.
    `bool` is rejected (it is an `int` subclass but never a valid measurement)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nested_float(data: dict[str, Any], key: str, subkey: str) -> float | None:
    inner = data.get(key)
    if not isinstance(inner, dict):
        return None
    return _get_float(inner.get(subkey))


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy, mirroring the quote/OHLCV seams. HTTP 429 → rate-limited (carrying
    `Retry-After`); any other status or transport failure → upstream-unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            "coingecko: rate limited (HTTP 429) fetching macro context",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"coingecko: upstream unavailable ({detail}) fetching macro context"
    )


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (urllib preserves the upstream's casing)."""
    lowered = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lowered), None)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a `Retry-After` header as whole seconds; the HTTP-date form is
    unsupported (returns None) — the agent gets the rate-limit signal regardless."""
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


__all__ = ["CoinGeckoAdapter", "CoinGeckoError", "classify_crypto_regime"]
