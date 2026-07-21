"""Plan 0113 phase 1 — offline tests for the FRED release-dates adapter.

An inline `release/dates` payload drives `FredReleasesSource` through a
`ResilientHttpClient` whose transport seam (`_perform_request`) is monkeypatched, so
the suite never touches the network. The key comes from a `SecretsStore` over an
injected environ (pinning `MARKET_ANALYSER_FRED_API_KEY`).

Pins the phase-1 done-when: (a) the FRED field mapping (date → `scheduled_at`, title,
source, day-level note; past dates filtered), (b) **no key → inert honest-empty** (no
request issued, an unconfigured note), (c) `file_type=json` is pinned and the key
rides the query (never a header) while the resilient client logs only the path, and
(d) a per-release upstream failure degrades that release with a note without failing
the other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.fred_releases import FredReleasesSource
from market_analyser.persistence.secrets import SecretsStore

_KEY_ENV = "MARKET_ANALYSER_FRED_API_KEY"
_TEST_KEY = "test-fred-key"
_FROZEN_NOW = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)

# FRED serves each release's scheduled dates; 10=CPI, 54=Personal Income & Outlays.
# One past date (filtered) and two upcoming per release.
_CPI_DATES = {
    "release_dates": [
        {"release_id": 10, "date": "2026-06-11"},  # past — filtered
        {"release_id": 10, "date": "2026-08-12"},
        {"release_id": 10, "date": "2026-09-11"},
    ]
}
_PCE_DATES = {
    "release_dates": [
        {"release_id": 54, "date": "2026-07-31"},
        {"release_id": 54, "date": "2026-08-28"},
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
    cpi_status: int = 200,
    pce_status: int = 200,
) -> tuple[FredReleasesSource, list[str]]:
    """Wire a source to a per-release transport response keyed off `release_id` in the
    request URL; return it plus the requested URLs (for the inert / hygiene asserts)."""
    import json

    client = ResilientHttpClient(source_name="fred-test", max_retries=0)
    urls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        urls.append(url)
        if "release_id=54" in url:
            return HttpResponse(
                status_code=pce_status,
                headers={},
                body=json.dumps(_PCE_DATES).encode(),
                elapsed_seconds=0.0,
            )
        return HttpResponse(
            status_code=cpi_status,
            headers={},
            body=json.dumps(_CPI_DATES).encode(),
            elapsed_seconds=0.0,
        )

    monkeypatch.setattr(client, "_perform_request", fake)
    source = FredReleasesSource(
        secrets_store=_store(tmp_path, key=key),
        http_client=client,
        clock=lambda: _FROZEN_NOW,
    )
    return source, urls


# -- (a) field mapping ------------------------------------------------------


def test_maps_release_dates_to_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source, urls = _source(monkeypatch, tmp_path)

    fetch = source.fetch_events()

    # Two release queries (CPI id=10, PCE id=54); no degrade notes on the happy path.
    assert len(urls) == 2
    assert fetch.notes == []
    by_scheduled = {event.scheduled_at: event for event in fetch.events}
    # The past CPI date (2026-06-11) is filtered; the three upcoming dates map cleanly.
    assert set(by_scheduled) == {
        datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    }
    cpi = by_scheduled[datetime(2026, 8, 12, 12, 0, tzinfo=UTC)]
    assert cpi.title == "CPI release"
    assert cpi.category == "macro"
    assert cpi.source == "fred"
    assert cpi.magnitude is None
    assert cpi.symbol is None
    assert "day-level" in (cpi.note or "")
    pce = by_scheduled[datetime(2026, 7, 31, 12, 0, tzinfo=UTC)]
    assert pce.title.startswith("PCE release")


def test_query_pins_json_and_carries_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, urls = _source(monkeypatch, tmp_path)
    source.fetch_events()

    for url in urls:
        # FRED defaults to XML (ADR-0107 risk) — JSON is pinned in the query.
        assert "file_type=json" in url
        # FRED has no header auth; the key rides the query. The resilient client logs
        # only the path (query never logged), so this stays secret-hygienic.
        assert f"api_key={_TEST_KEY}" in url


# -- (b) no key → inert honest-empty ----------------------------------------


def test_no_key_is_inert_with_unconfigured_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, urls = _source(monkeypatch, tmp_path, key=None)

    fetch = source.fetch_events()

    assert fetch.events == []
    assert urls == []  # inert: zero requests
    assert len(fetch.notes) == 1
    assert "fred_api_key" in fetch.notes[0]


def test_no_store_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ResilientHttpClient(source_name="fred-test", max_retries=0)
    calls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return HttpResponse(status_code=200, headers={}, body=b"{}", elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    source = FredReleasesSource(http_client=client, clock=lambda: _FROZEN_NOW)

    fetch = source.fetch_events()

    assert fetch.events == []
    assert calls == []


# -- (d) per-release degrade -------------------------------------------------


def test_one_release_failing_degrades_only_that_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # PCE (id=54) returns HTTP 500 → skipped with a note; CPI (id=10) still returns.
    source, _ = _source(monkeypatch, tmp_path, pce_status=500)

    fetch = source.fetch_events()

    titles = {event.title for event in fetch.events}
    assert titles == {"CPI release"}
    assert len(fetch.notes) == 1
    assert "PCE" in fetch.notes[0]


def test_shape_broken_payload_yields_no_events_no_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    client = ResilientHttpClient(source_name="fred-test", max_retries=0)

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps({"unexpected": True}).encode(),
            elapsed_seconds=0.0,
        )

    monkeypatch.setattr(client, "_perform_request", fake)
    source = FredReleasesSource(
        secrets_store=_store(tmp_path), http_client=client, clock=lambda: _FROZEN_NOW
    )

    fetch = source.fetch_events()
    assert fetch.events == []  # no fabrication, no raise
