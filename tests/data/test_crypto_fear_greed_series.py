"""Plan 0055 phase 2 — Fear & Greed history: full backfill + incremental update.

Done-when claims pinned here:
(a) backfill from the fixture lands one point per day with values 0-100 and the
    earliest at the fixture's first day (2018-02-01) — asserted against the
    fixture, never the live API;
(b) re-running the backfill is idempotent (row count unchanged);
(c) a live `crypto_fear_greed` call (the real MCP tool, in-process) appends
    today's point exactly once.

Fixture provenance: a verbatim capture of the full `?limit=0` response could
not be taken in this environment (no raw network access), so the history
payload below is built in-code in EXACTLY the captured per-entry shape
(`alternative_me_fng_response.json`, a real `?limit=1` capture: string-encoded
value/timestamp, newest-first ordering, `time_until_update` on the leading
entry) spanning the index's real first day, 2018-02-01. The `network`-marked
test at the bottom verifies the same claims against the real `?limit=0`
endpoint when run locally with `uv run pytest -m network`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, cast

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_tools.crypto_fear_greed import register_crypto_fear_greed
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.crypto_fear_greed import (
    CryptoFearGreedAdapter,
    CryptoFearGreedError,
)
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.metric_series import SERIES_FNG_VALUE
from market_analyser.data.sources import MetricSeriesSource
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

# 2018-02-01T00:00:00Z — the index's real first day (pinned by the network test).
_FIRST_DAY_TS = int(datetime(2018, 2, 1, tzinfo=UTC).timestamp())
_DAY = 86_400
_HISTORY_DAYS = 120

# The current-reading capture (mirrors tests/data/fixtures/alternative_me_fng_response.json).
_CURRENT_TS = 1_715_212_800
_CURRENT_VALUE = 55


def _history_value(day_index: int) -> int:
    """Deterministic 0-100 value for the synthetic history (no randomness)."""
    return (day_index * 7) % 101


def _history_body(days: int = _HISTORY_DAYS) -> bytes:
    """A full-history (`?limit=0`-shaped) payload: newest-first, string-encoded
    fields, `time_until_update` only on the leading entry — the captured
    real-response shape extended over `days` days from 2018-02-01."""
    entries: list[dict[str, str]] = []
    for day_index in range(days - 1, -1, -1):  # newest first, like the real API
        entry = {
            "value": str(_history_value(day_index)),
            "value_classification": "Neutral",
            "timestamp": str(_FIRST_DAY_TS + day_index * _DAY),
        }
        if not entries:
            entry["time_until_update"] = "60000"
        entries.append(entry)
    return json.dumps(
        {"name": "Fear and Greed Index", "data": entries, "metadata": {"error": None}},
    ).encode("utf-8")


def _current_body() -> bytes:
    return json.dumps(
        {
            "name": "Fear and Greed Index",
            "data": [
                {
                    "value": str(_CURRENT_VALUE),
                    "value_classification": "Greed",
                    "timestamp": str(_CURRENT_TS),
                    "time_until_update": "60000",
                },
            ],
            "metadata": {"error": None},
        },
    ).encode("utf-8")


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> MetricPointsRepository:
    return MetricPointsRepository(session_factory)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    store: MetricPointsRepository | None,
    *,
    history_body: bytes | None = None,
) -> CryptoFearGreedAdapter:
    """Adapter whose transport seam serves the current-reading capture for
    `?limit=1` and the full-history payload for `?limit=0`."""
    client = ResilientHttpClient(source_name="fng-test", cache_ttl_seconds=0.0, max_retries=0)
    history = history_body if history_body is not None else _history_body()

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = history if "limit=0" in url else _current_body()
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return CryptoFearGreedAdapter(http_client=client, metric_store=store)


# --- fetch_series: the MetricSeriesSource contract ---------------------------


def test_adapter_satisfies_metric_series_source_protocol() -> None:
    assert isinstance(CryptoFearGreedAdapter(), MetricSeriesSource)


def test_fetch_series_parses_full_history_ascending(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter = _adapter(monkeypatch, store)

    points = adapter.fetch_series(SERIES_FNG_VALUE)

    assert len(points) == _HISTORY_DAYS
    assert points[0].ts == _FIRST_DAY_TS  # earliest first despite newest-first upstream
    assert all(later.ts - earlier.ts == _DAY for earlier, later in pairwise(points))
    assert points[3].value == float(_history_value(3))


def test_fetch_series_clips_to_inclusive_window(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter = _adapter(monkeypatch, store)

    points = adapter.fetch_series(
        SERIES_FNG_VALUE,
        start=_FIRST_DAY_TS + _DAY,
        end=_FIRST_DAY_TS + 3 * _DAY,
    )

    assert [p.ts for p in points] == [
        _FIRST_DAY_TS + _DAY,
        _FIRST_DAY_TS + 2 * _DAY,
        _FIRST_DAY_TS + 3 * _DAY,
    ]


def test_fetch_series_rejects_foreign_series_id(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter = _adapter(monkeypatch, store)

    with pytest.raises(ValueError, match=r"fng\.value"):
        adapter.fetch_series("coingecko.btc_dominance")


def test_fetch_series_shape_drift_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """A renamed per-entry field is upstream drift: a typed adapter error,
    never a silently-skipped point (plan 0055 risk note / ADR-0019)."""
    drifted = json.dumps(
        {
            "name": "Fear and Greed Index",
            "data": [{"score": "55", "timestamp": str(_FIRST_DAY_TS)}],
            "metadata": {"error": None},
        },
    ).encode("utf-8")
    adapter = _adapter(monkeypatch, store, history_body=drifted)

    with pytest.raises(CryptoFearGreedError):
        adapter.fetch_series(SERIES_FNG_VALUE)


def test_fetch_series_out_of_range_value_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    bad = json.dumps(
        {
            "name": "Fear and Greed Index",
            "data": [{"value": "105", "value_classification": "x", "timestamp": "1517443200"}],
            "metadata": {"error": None},
        },
    ).encode("utf-8")
    adapter = _adapter(monkeypatch, store, history_body=bad)

    with pytest.raises(CryptoFearGreedError, match=r"\[0, 100\]"):
        adapter.fetch_series(SERIES_FNG_VALUE)


# --- (a) + (b) backfill: full history, one point per day, idempotent ---------


def test_backfill_lands_one_point_per_day_in_range_from_first_fixture_day(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter = _adapter(monkeypatch, store)

    inserted = adapter.backfill_series()

    assert inserted == _HISTORY_DAYS
    stored = store.range(SERIES_FNG_VALUE, 0, _FIRST_DAY_TS + _HISTORY_DAYS * _DAY)
    assert len(stored) == _HISTORY_DAYS
    assert stored[0].ts == _FIRST_DAY_TS
    assert datetime.fromtimestamp(stored[0].ts, tz=UTC) == datetime(2018, 2, 1, tzinfo=UTC)
    assert all(later.ts - earlier.ts == _DAY for earlier, later in pairwise(stored))
    assert all(0.0 <= p.value <= 100.0 for p in stored)


def test_backfill_rerun_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter = _adapter(monkeypatch, store)

    first = adapter.backfill_series()
    second = adapter.backfill_series()

    assert first == _HISTORY_DAYS
    assert second == 0  # nothing new on the re-run
    stored = store.range(SERIES_FNG_VALUE, 0, _FIRST_DAY_TS + _HISTORY_DAYS * _DAY)
    assert len(stored) == _HISTORY_DAYS  # row count unchanged


def test_backfill_without_store_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, store=None)

    with pytest.raises(ValueError, match="metric store"):
        adapter.backfill_series()


# --- (c) the live tool call appends today's point exactly once ---------------


def test_crypto_fear_greed_tool_call_appends_todays_point_exactly_once(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """The real MCP tool, in-process: two calls write one stored point, keyed by
    the upstream publish timestamp, with the published value."""
    adapter = _adapter(monkeypatch, store)
    provider = DefaultMarketDataProvider(crypto_fng=adapter)
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_crypto_fear_greed(server, provider=provider)

    for _ in range(2):
        result = anyio.run(server.call_tool, "crypto_fear_greed", {"params": {}})
        _content, structured = cast("tuple[Any, dict[str, Any]]", result)
        assert structured["value"] == _CURRENT_VALUE

    stored = store.range(SERIES_FNG_VALUE, 0, 2_000_000_000)
    assert len(stored) == 1
    assert stored[0].ts == _CURRENT_TS
    assert stored[0].value == float(_CURRENT_VALUE)


def test_fetch_current_without_store_writes_nothing_and_still_returns(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    adapter = _adapter(monkeypatch, store=None)

    sample = adapter.fetch_current()

    assert sample.value == _CURRENT_VALUE
    assert store.range(SERIES_FNG_VALUE, 0, 2_000_000_000) == []


def test_write_through_storage_failure_is_swallowed_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    store: MetricPointsRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Persistence must never break the live read: a failing store logs a
    warning and the sample is still returned."""
    adapter = _adapter(monkeypatch, store)

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "upsert_points", boom)

    with caplog.at_level("WARNING"):
        sample = adapter.fetch_current()

    assert sample.value == _CURRENT_VALUE
    assert any("write-through" in record.message for record in caplog.records)


# --- live verification (skipped in CI; `uv run pytest -m network`) -----------


@pytest.mark.network
def test_live_full_history_starts_2018_02_01_with_daily_0_100_values() -> None:
    """The real `?limit=0` endpoint: full history parses through the same
    boundary, earliest day is 2018-02-01, every value in [0, 100]."""
    points = CryptoFearGreedAdapter().fetch_series(SERIES_FNG_VALUE)

    assert len(points) >= 2_900  # ~8 years of daily points by 2026
    assert datetime.fromtimestamp(points[0].ts, tz=UTC) == datetime(2018, 2, 1, tzinfo=UTC)
    assert all(0.0 <= p.value <= 100.0 for p in points)
