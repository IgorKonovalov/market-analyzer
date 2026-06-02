"""Plan 0013 phase 1 done-when: YahooAdapter classifies upstream failures into
the typed `UpstreamDataError` taxonomy.

The adapter's `fetcher` seam is injected with doubles that raise
`ResilientHttpError` (mirroring what the shared client raises once it exhausts
retries / hits a permanent status) or return an empty list. Asserts:
- HTTP 429 → `RateLimitedError` carrying the status + parsed `Retry-After`.
- 5xx / connection-refused / timeout → `UpstreamUnavailableError`.
- empty response on a >= 1mo period for a valid symbol → `UnknownSymbolError`.
- input-validation bugs still raise plain `ValueError`, NOT the typed taxonomy.
- the taxonomy is importable from the public `market_analyser.data` surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from market_analyser.data import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data._http import HttpResponse, ResilientHttpError
from market_analyser.data.adapters.yahoo import YahooAdapter

_START = datetime(2026, 4, 1, tzinfo=UTC)
_END = datetime(2026, 5, 1, tzinfo=UTC)


def _raising_fetcher(
    *,
    status: int | None = None,
    headers: dict[str, str] | None = None,
    exc: BaseException | None = None,
) -> Any:
    """A fetcher that raises `ResilientHttpError` the way the shared client does
    once retries are exhausted — carrying a `last_response` (for HTTP statuses)
    and/or a `last_exception` (for transport failures)."""
    last_response = (
        None
        if status is None
        else HttpResponse(status_code=status, headers=headers or {}, body=b"", elapsed_seconds=0.0)
    )

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        raise ResilientHttpError(
            source_name="yahoo",
            last_response=last_response,
            last_exception=exc,
            attempts=4,
        )

    return fetcher


def _empty_fetcher(
    symbol: str, start: datetime, end: datetime, interval: str = "1d"
) -> list[dict[str, Any]]:
    return []


def test_http_429_becomes_rate_limited_with_retry_after() -> None:
    adapter = YahooAdapter(fetcher=_raising_fetcher(status=429, headers={"Retry-After": "60"}))
    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_ohlcv("AAPL", "1d", _START, _END)
    assert excinfo.value.retry_after_seconds == 60
    assert "429" in str(excinfo.value)  # carries the HTTP status in its message


def test_http_429_without_retry_after_header_has_none() -> None:
    adapter = YahooAdapter(fetcher=_raising_fetcher(status=429))
    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_ohlcv("AAPL", "1d", _START, _END)
    assert excinfo.value.retry_after_seconds is None


def test_http_5xx_becomes_upstream_unavailable() -> None:
    adapter = YahooAdapter(fetcher=_raising_fetcher(status=503))
    with pytest.raises(UpstreamUnavailableError) as excinfo:
        adapter.fetch_ohlcv("AAPL", "1d", _START, _END)
    assert "503" in str(excinfo.value)


def test_connection_refused_becomes_upstream_unavailable() -> None:
    adapter = YahooAdapter(fetcher=_raising_fetcher(exc=ConnectionError("connection refused")))
    with pytest.raises(UpstreamUnavailableError) as excinfo:
        adapter.fetch_ohlcv("AAPL", "1d", _START, _END)
    assert "ConnectionError" in str(excinfo.value)


def test_timeout_becomes_upstream_unavailable() -> None:
    adapter = YahooAdapter(fetcher=_raising_fetcher(exc=TimeoutError("timed out")))
    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_ohlcv("AAPL", "1d", _START, _END)


def test_empty_response_on_multiday_period_becomes_unknown_symbol() -> None:
    adapter = YahooAdapter(fetcher=_empty_fetcher)
    with pytest.raises(UnknownSymbolError) as excinfo:
        adapter.fetch_ohlcv("MADEUP", "1d", _START, _END)
    assert excinfo.value.symbol == "MADEUP"


def test_input_validation_still_raises_plain_value_error() -> None:
    """Caller bugs (bad timeframe, empty symbol, start>=end) stay `ValueError` —
    they are NOT upstream failures and must not be swallowed into the typed
    taxonomy (which subclasses `Exception`, not `ValueError`)."""
    adapter = YahooAdapter(fetcher=_empty_fetcher)

    with pytest.raises(ValueError) as bad_timeframe:
        adapter.fetch_ohlcv("AAPL", "5m", _START, _END)
    assert not isinstance(bad_timeframe.value, UpstreamDataError)

    with pytest.raises(ValueError) as empty_symbol:
        adapter.fetch_ohlcv("  ", "1d", _START, _END)
    assert not isinstance(empty_symbol.value, UpstreamDataError)

    with pytest.raises(ValueError) as start_after_end:
        adapter.fetch_ohlcv("AAPL", "1d", _END, _START)
    assert not isinstance(start_after_end.value, UpstreamDataError)


def test_typed_errors_exported_from_data_package() -> None:
    assert issubclass(RateLimitedError, UpstreamDataError)
    assert issubclass(UpstreamUnavailableError, UpstreamDataError)
    assert issubclass(UnknownSymbolError, UpstreamDataError)
