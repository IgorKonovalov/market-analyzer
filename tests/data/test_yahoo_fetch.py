"""Plan 0003 phase 1: regression tests for the in-house Yahoo Chart parser.

A recorded Yahoo response is loaded from ``tests/fixtures/yahoo/aapl_1d.json``
and fed through :func:`_fetch_yahoo_ohlcv` with ``urllib.request.urlopen``
monkeypatched out. The tests defend the parser's row shape, its
None-row-skipping behaviour, and the outgoing request's URL / User-Agent /
timeout, per ADR-0007's "validate at boundaries" rule.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from market_analyser.data.adapters._yahoo_fetch import (
    _USER_AGENT,
    _YF_BASE,
    _fetch_yahoo_ohlcv,
)

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "yahoo"


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    captured: dict[str, Any],
) -> None:
    @contextmanager
    def fake_urlopen(req: Any, timeout: float = 15.0) -> Iterator[Any]:
        captured["url"] = req.full_url
        captured["user_agent"] = req.get_header("User-agent")
        captured["timeout"] = timeout
        response = MagicMock()
        response.read.return_value = payload
        yield response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_parses_fixture_skipping_none_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = (_FIXTURE_DIR / "aapl_1d.json").read_bytes()
    captured: dict[str, Any] = {}
    _install_fake_urlopen(monkeypatch, payload, captured)

    rows = _fetch_yahoo_ohlcv("AAPL", "1mo", "1d")

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


def test_sends_expected_url_and_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = (_FIXTURE_DIR / "aapl_1d.json").read_bytes()
    captured: dict[str, Any] = {}
    _install_fake_urlopen(monkeypatch, payload, captured)

    _fetch_yahoo_ohlcv("AAPL", "1mo", "1d")

    assert captured["url"] == f"{_YF_BASE}/AAPL?interval=1d&range=1mo"
    assert captured["user_agent"] == _USER_AGENT
    assert captured["timeout"] == 15


def test_intraday_uses_hourly_date_format(monkeypatch: pytest.MonkeyPatch) -> None:
    inline_response = {
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
                            }
                        ]
                    },
                }
            ],
        }
    }
    captured: dict[str, Any] = {}
    _install_fake_urlopen(
        monkeypatch,
        json.dumps(inline_response).encode("utf-8"),
        captured,
    )

    rows = _fetch_yahoo_ohlcv("AAPL", "1mo", "1h")

    assert [r["date"] for r in rows] == [
        "2026-01-01 00:00",
        "2026-01-01 01:00",
    ]


def test_null_volume_normalized_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    inline_response = {
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
                            }
                        ]
                    },
                }
            ],
        }
    }
    captured: dict[str, Any] = {}
    _install_fake_urlopen(
        monkeypatch,
        json.dumps(inline_response).encode("utf-8"),
        captured,
    )

    rows = _fetch_yahoo_ohlcv("AAPL", "1mo", "1d")

    assert rows[0]["volume"] == 0
