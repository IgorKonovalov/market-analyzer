"""Plan 0113 phase 2 — offline tests for the Finnhub earnings-calendar adapter.

An inline `calendar/earnings` payload drives `FinnhubEarningsSource` through a
`ResilientHttpClient` whose transport seam (`_perform_request`) is monkeypatched, so
the suite never touches the network. The key comes from a `SecretsStore` over an
injected environ (pinning `MARKET_ANALYSER_FINNHUB_API_KEY`).

Pins the phase-2 done-when: (a) the field mapping including a **partial / estimate-
gated row** (null `epsEstimate` → `magnitude=None` with a disclosing note), (b) **no
key → inert honest-empty** (no request issued, an unconfigured note), (c) the key
rides the `X-Finnhub-Token` header (never the URL) and the window bounds from/to, and
(d) an upstream failure degrades to honest-empty with a note.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.finnhub_earnings import FinnhubEarningsSource
from market_analyser.persistence.secrets import SecretsStore

_KEY_ENV = "MARKET_ANALYSER_FINNHUB_API_KEY"
_TEST_KEY = "test-finnhub-key"
_FROZEN_NOW = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)

# Two upcoming rows (one fully-estimated, one estimate-gated) and one past row.
_CALENDAR = {
    "earningsCalendar": [
        {
            "date": "2026-06-01",  # past — filtered
            "symbol": "TSLA",
            "epsEstimate": 1.0,
            "revenueEstimate": 1,
            "hour": "amc",
            "quarter": 1,
            "year": 2026,
        },
        {
            "date": "2026-07-23",
            "symbol": "TSLA",
            "epsEstimate": 2.5,
            "revenueEstimate": 25_000_000_000,
            "hour": "amc",
            "quarter": 2,
            "year": 2026,
        },
        {
            "date": "2026-10-22",
            "symbol": "TSLA",
            "epsEstimate": None,  # estimate-gated on the free tier
            "revenueEstimate": None,
            "hour": "bmo",
            "quarter": 3,
            "year": 2026,
        },
    ]
}


def _store(tmp_path: Path, *, key: str | None = _TEST_KEY) -> SecretsStore:
    environ = {_KEY_ENV: key} if key is not None else {}
    return SecretsStore(tmp_path / "secrets.json", environ=environ)


def _source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    key: str | None = _TEST_KEY,
    status: int = 200,
    body: bytes | None = None,
) -> tuple[FinnhubEarningsSource, list[str], list[dict[str, str]]]:
    client = ResilientHttpClient(source_name="finnhub-test", max_retries=0)
    urls: list[str] = []
    headers_seen: list[dict[str, str]] = []
    payload = body if body is not None else json.dumps(_CALENDAR).encode()

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        urls.append(url)
        headers_seen.append(dict(headers or {}))
        return HttpResponse(status_code=status, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    source = FinnhubEarningsSource(
        secrets_store=_store(tmp_path, key=key),
        http_client=client,
        clock=lambda: _FROZEN_NOW,
    )
    return source, urls, headers_seen


# -- (a) field mapping incl. the estimate-gated row -------------------------


def test_maps_earnings_rows_with_estimate_gating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, _, _ = _source(monkeypatch, tmp_path)

    events = source.fetch_events(symbol="TSLA", window="180d").events

    by_date = {event.scheduled_at: event for event in events}
    # The past row (2026-06-01) is filtered; two upcoming rows map cleanly.
    assert set(by_date) == {
        datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
    }
    full = by_date[datetime(2026, 7, 23, 12, 0, tzinfo=UTC)]
    assert full.category == "earnings"
    assert full.title == "TSLA earnings"
    assert full.symbol == "TSLA"
    assert full.source == "finnhub"
    assert full.magnitude == 2.5  # EPS estimate rides magnitude
    assert "after market close" in (full.note or "")
    assert "revenue est 25,000,000,000" in (full.note or "")

    gated = by_date[datetime(2026, 10, 22, 12, 0, tzinfo=UTC)]
    assert gated.magnitude is None  # gated estimate → null, not a fabricated 0
    assert "EPS/revenue estimate unavailable on the free tier" in (gated.note or "")


def test_window_bounds_the_query_and_key_rides_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, urls, headers_seen = _source(monkeypatch, tmp_path)

    source.fetch_events(symbol="tsla", window="30d")

    assert len(urls) == 1
    # from=today, to=today+30d; symbol upper-cased into the query.
    assert "from=2026-07-21" in urls[0]
    assert "to=2026-08-20" in urls[0]
    assert "symbol=TSLA" in urls[0]
    # Secret hygiene (ADR-0038): key in the X-Finnhub-Token header, never the URL.
    assert headers_seen == [{"X-Finnhub-Token": _TEST_KEY}]
    assert _TEST_KEY not in urls[0]


# -- (b) no key → inert honest-empty ----------------------------------------


def test_no_key_is_inert_with_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, urls, _ = _source(monkeypatch, tmp_path, key=None)

    fetch = source.fetch_events(symbol="TSLA")

    assert fetch.events == []
    assert urls == []  # inert: zero requests
    assert len(fetch.notes) == 1
    assert "finnhub_api_key" in fetch.notes[0]


def test_no_store_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ResilientHttpClient(source_name="finnhub-test", max_retries=0)
    calls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return HttpResponse(status_code=200, headers={}, body=b"{}", elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    source = FinnhubEarningsSource(http_client=client, clock=lambda: _FROZEN_NOW)

    assert source.fetch_events(symbol="TSLA").events == []
    assert calls == []


# -- (c) whole-window form (no symbol) --------------------------------------


def test_window_form_without_symbol_omits_symbol_param(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, urls, _ = _source(monkeypatch, tmp_path)

    source.fetch_events(window="7d")

    assert "symbol=" not in urls[0]  # whole-window query, all companies


# -- (d) degrade ------------------------------------------------------------


def test_upstream_failure_degrades_to_empty_with_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, _, _ = _source(monkeypatch, tmp_path, status=500)

    fetch = source.fetch_events(symbol="TSLA")

    assert fetch.events == []
    assert len(fetch.notes) == 1
    assert "unavailable" in fetch.notes[0]


@pytest.mark.parametrize(
    "body",
    [b"{}", b'{"earningsCalendar": null}', b'{"earningsCalendar": "x"}', b'{"other": 1}'],
)
def test_shape_broken_payload_yields_no_events_no_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: bytes
) -> None:
    source, _, _ = _source(monkeypatch, tmp_path, body=body)

    assert source.fetch_events(symbol="TSLA").events == []
