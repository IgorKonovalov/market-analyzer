"""TradingView screener adapter — Plan 0009 phase 2 (ADR-0019, ADR-0007).

Builds the scanner request with the `tradingview-screener` library's query
builder, then executes the POST through `ResilientHttpClient` so the request
inherits the shared TTL cache / retry / backoff / concurrency cap. The library's
own `requests`-based execution path (`get_scanner_data`) is deliberately *not*
used — every external HTTP request in this codebase goes through
`ResilientHttpClient` per ADR-0019. The library here is purely a payload/URL
builder, which also means it tracks upstream's scanner-endpoint changes for us.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol, never by importing this class.

Filter DSL (validated at this boundary, strict by design per Plan 0009's risk
section — unknown fields are rejected, not passed through):

    {"RSI": {"lt": 30}, "market_cap_basic": {"gte": 1e10}, "close": 50}

Each field maps to one of the supported comparison operators; a bare scalar is
shorthand for equality.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from tradingview_screener.column import col
from tradingview_screener.query import Query

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.types import ScreenerRow


class ScreenerFilterError(ValueError):
    """A malformed screener query: unknown field/operator/market or an
    out-of-range limit. Raised at the adapter boundary before any HTTP."""


# Markets we expose. Mirrors the MCP tool's enum so the two layers agree.
_SUPPORTED_MARKETS: frozenset[str] = frozenset({"america", "crypto", "forex", "egypt"})

# Curated allowlist of filterable TradingView columns. Strict-by-design: an
# unknown field fails here rather than producing an upstream error. Extend
# manually when a new column is needed (Plan 0009 risk: "filter DSL drifts").
_SUPPORTED_FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "close",
        "open",
        "high",
        "low",
        "change",
        "volume",
        "market_cap_basic",
        "RSI",
        "RSI7",
        "MACD.macd",
        "MACD.signal",
        "BB.upper",
        "BB.lower",
        "SMA50",
        "SMA200",
        "EMA50",
        "EMA200",
        "relative_volume_10d_calc",
        "price_earnings_ttm",
        "exchange",
    },
)

# Columns every row carries back regardless of filters, so the agent always sees
# a useful indicator set. This tuple's order is the contract the offline test
# fixture is captured against; filter fields not already here are appended so a
# filtered metric is always present in the response.
_BASE_COLUMNS: tuple[str, ...] = (
    "name",
    "close",
    "change",
    "volume",
    "market_cap_basic",
    "RSI",
    "exchange",
)

_MAX_LIMIT = 500

_Operator = Callable[[Any, Any], Any]
_OPS: dict[str, _Operator] = {
    "lt": lambda c, v: c < v,
    "lte": lambda c, v: c <= v,
    "gt": lambda c, v: c > v,
    "gte": lambda c, v: c >= v,
    "eq": lambda c, v: c == v,
    "ne": lambda c, v: c != v,
}


class TradingViewScreenerAdapter:
    """Screens TradingView's scanner endpoint via ResilientHttpClient."""

    def __init__(self, http_client: ResilientHttpClient | None = None) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="tradingview-screener",
                cache_ttl_seconds=60.0,  # ADR-0019 screener default
                max_concurrency=4,
            )
        )

    def query(
        self,
        filters: Mapping[str, Any],
        *,
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
    ) -> list[ScreenerRow]:
        """Run a screen and return the matching rows. Raises `ScreenerFilterError`
        on a malformed query and `ResilientHttpError` on upstream exhaustion."""
        self._validate(filters, market, limit)
        columns = self._columns_for(filters)
        builder = Query().select(*columns).set_markets(market)
        conditions = self._build_conditions(filters, exchange)
        if conditions:
            builder = builder.where(*conditions)
        builder = builder.limit(limit)

        payload = builder.query
        # The default client cache key (method, url, params) ignores the POST
        # body, so we pass an explicit key derived from the resolved payload —
        # identical screens within the TTL window then collapse to one call.
        cache_key = json.dumps(payload, sort_keys=True, default=str)
        response = self._http.post(
            builder.url,
            json=payload,
            cache_key=cache_key,
            expect_json=True,
        )
        return self._parse(response.json(), columns)

    def _validate(self, filters: Mapping[str, Any], market: str, limit: int) -> None:
        if market not in _SUPPORTED_MARKETS:
            raise ScreenerFilterError(
                f"unsupported market {market!r}; supported: {sorted(_SUPPORTED_MARKETS)}",
            )
        if not 1 <= limit <= _MAX_LIMIT:
            raise ScreenerFilterError(
                f"limit {limit} out of range; must be between 1 and {_MAX_LIMIT}",
            )
        for field in filters:
            if field not in _SUPPORTED_FILTER_FIELDS:
                raise ScreenerFilterError(
                    f"unknown filter field {field!r}; "
                    f"supported: {sorted(_SUPPORTED_FILTER_FIELDS)}",
                )

    def _columns_for(self, filters: Mapping[str, Any]) -> list[str]:
        columns = list(_BASE_COLUMNS)
        for field in filters:
            if field not in columns:
                columns.append(field)
        return columns

    def _build_conditions(self, filters: Mapping[str, Any], exchange: str | None) -> list[Any]:
        conditions: list[Any] = []
        for field, spec in filters.items():
            if isinstance(spec, Mapping):
                for op_name, value in spec.items():
                    operator = _OPS.get(op_name)
                    if operator is None:
                        raise ScreenerFilterError(
                            f"unknown operator {op_name!r} for field {field!r}; "
                            f"supported: {sorted(_OPS)}",
                        )
                    conditions.append(operator(col(field), value))
            else:
                conditions.append(col(field) == spec)  # scalar shorthand = equality
        if exchange is not None:
            conditions.append(col("exchange") == exchange)
        return conditions

    def _parse(self, payload: Any, columns: list[str]) -> list[ScreenerRow]:
        rows: list[ScreenerRow] = []
        data = payload.get("data", []) if isinstance(payload, Mapping) else []
        for entry in data:
            values = entry.get("d", [])
            fields: dict[str, float | str | None] = {
                name: values[i] for i, name in enumerate(columns) if i < len(values)
            }
            raw_symbol = str(entry.get("s", ""))
            symbol = raw_symbol.split(":", 1)[1] if ":" in raw_symbol else raw_symbol
            if not symbol:
                name_value = fields.get("name")
                symbol = str(name_value) if name_value else raw_symbol
            if not symbol:
                continue  # ScreenerRow requires a non-empty symbol; skip the unidentifiable
            rows.append(ScreenerRow(symbol=symbol, fields=fields))
        return rows


__all__ = ["ScreenerFilterError", "TradingViewScreenerAdapter"]
