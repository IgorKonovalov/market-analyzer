"""Plan 0003 phase 1 + Plan 0009 phase 3: the in-house Yahoo Chart fetcher.

`_parse_chart_payload` is tested directly on payloads (no I/O). `_fetch_yahoo_ohlcv`
is tested through a `ResilientHttpClient` whose transport seam is monkeypatched —
proving the request now goes through the shared client (ADR-0019) and that the
fetcher builds the expected Yahoo chart URL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters._yahoo_fetch import (
    _YF_BASE,
    _fetch_yahoo_ohlcv,
    _parse_chart_payload,
)
from market_analyser.data.errors import UpstreamUnavailableError

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "yahoo"

_START = datetime(2026, 1, 1, tzinfo=UTC)
_END = datetime(2026, 2, 1, tzinfo=UTC)


def _client_returning(payload: bytes, captured: dict[str, Any]) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="yahoo-test")

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        captured["method"] = method
        captured["url"] = url
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    # Replace the transport seam; the request still flows through the client's
    # get() so caching/retry/timeout wrapping is exercised in production.
    client._perform_request = fake  # type: ignore[method-assign]
    return client


def test_fetch_goes_through_client_and_builds_absolute_window_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Plan 0031: the request carries absolute period1/period2 (Unix seconds),
    # not the now-relative range= — so a past-ending window is fetched verbatim.
    payload = (_FIXTURE_DIR / "aapl_1d.json").read_bytes()
    captured: dict[str, Any] = {}
    client = _client_returning(payload, captured)

    rows = _fetch_yahoo_ohlcv("AAPL", _START, _END, "1d", client=client)

    assert captured["method"] == "GET"
    assert captured["url"] == (
        f"{_YF_BASE}/AAPL"
        f"?period1={int(_START.timestamp())}&period2={int(_END.timestamp())}&interval=1d"
    )
    assert "range=" not in captured["url"]
    assert [r["date"] for r in rows] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-04",
        "2026-01-05",
    ]


def test_fetch_url_encodes_symbol_path_segment() -> None:
    # A symbol with a space (e.g. a mistyped crypto pair "BTC USD") must be
    # percent-encoded into the path segment — an un-encoded space raises
    # http.client.InvalidURL before the request leaves the process. Regression
    # for the 2026-05-24 smoke crash.
    payload = (_FIXTURE_DIR / "aapl_1d.json").read_bytes()
    captured: dict[str, Any] = {}
    client = _client_returning(payload, captured)

    _fetch_yahoo_ohlcv("BTC USD", _START, _END, "1d", client=client)

    assert captured["url"] == (
        f"{_YF_BASE}/BTC%20USD"
        f"?period1={int(_START.timestamp())}&period2={int(_END.timestamp())}&interval=1d"
    )
    assert " " not in captured["url"]


def test_parse_raises_on_error_envelope() -> None:
    # Yahoo returns 200 OK with a populated error envelope; that must surface as
    # the typed taxonomy, not a raw KeyError escaping as a 500 (Plan 0031).
    payload = {"chart": {"error": {"code": "Not Found", "description": "No data"}, "result": None}}
    with pytest.raises(UpstreamUnavailableError, match="error envelope"):
        _parse_chart_payload(payload, "1d")


def test_parse_raises_on_null_result() -> None:
    payload = {"chart": {"error": None, "result": None}}
    with pytest.raises(UpstreamUnavailableError, match="null/empty 'result'"):
        _parse_chart_payload(payload, "1d")


def test_parse_missing_timestamp_returns_empty() -> None:
    # A well-formed result with no `timestamp` key is Yahoo's "no bars for this
    # window" — yields [] so the adapter's empty-response heuristic classifies it,
    # rather than raising here.
    payload = {"chart": {"error": None, "result": [{"meta": {"symbol": "AAPL"}}]}}
    assert _parse_chart_payload(payload, "1d") == []


def test_parse_skips_none_rows() -> None:
    payload = json.loads((_FIXTURE_DIR / "aapl_1d.json").read_text(encoding="utf-8"))

    rows = _parse_chart_payload(payload, "1d")

    assert [r["date"] for r in rows] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-04",
        "2026-01-05",
    ]
    assert rows[0]["open"] == 100.0
    assert rows[0]["high"] == 102.0
    assert rows[0]["low"] == 99.0
    assert rows[0]["close"] == 101.5
    assert rows[0]["volume"] == 1000


def test_parse_intraday_uses_hourly_date_format() -> None:
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [1767225600, 1767229200],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 101.0],
                                "high": [102.0, 103.0],
                                "low": [99.0, 100.0],
                                "close": [101.0, 102.0],
                                "volume": [1000, 1100],
                            },
                        ],
                    },
                },
            ],
        },
    }

    rows = _parse_chart_payload(payload, "1h")

    assert [r["date"] for r in rows] == ["2026-01-01 00:00", "2026-01-01 01:00"]


def test_parse_null_volume_normalized_to_zero() -> None:
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [1767225600],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0],
                                "high": [102.0],
                                "low": [99.0],
                                "close": [101.0],
                                "volume": [None],
                            },
                        ],
                    },
                },
            ],
        },
    }

    rows = _parse_chart_payload(payload, "1d")

    assert rows[0]["volume"] == 0
