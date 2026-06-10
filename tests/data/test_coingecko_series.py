"""Plan 0055 phase 3 — BTC dominance / total-mcap accrual via macro write-through.

Done-when claims pinned here:
(a) two macro fetches in the same hour produce one point per series; fetches in
    different hours produce two — both asserted;
(b) a failed upstream fetch writes nothing (no fabricated points);
(c) the write-through never makes the macro read fail (storage error → logged,
    snapshot still returned) — asserted at the adapter seam and through the real
    `bitcoin_market_pulse` MCP tool in-process.

The accrual key is the snapshot's own `updated_at` (an upstream-payload value,
not a wall-clock read) truncated to the hour, so the tests steer time by editing
the committed `/global` capture's `updated_at` field.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_tools.bitcoin_market_pulse import register_bitcoin_market_pulse
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.coingecko import CoinGeckoAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.data.metric_series import (
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_COINGECKO_TOTAL_MCAP_USD,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_FIXTURES = Path(__file__).parent / "fixtures"
_GLOBAL_PAYLOAD: dict[str, Any] = json.loads((_FIXTURES / "coingecko_global.json").read_bytes())

# Snapshot timestamp in the committed fixture, its hour bucket, and the two
# measurements the accrual stores.
_FIXTURE_UPDATED_AT = 1_716_544_000
_HOUR = 3_600
_FIXTURE_BUCKET = _FIXTURE_UPDATED_AT // _HOUR * _HOUR
_FIXTURE_DOMINANCE = 52.3
_FIXTURE_TOTAL_MCAP = 2_500_000_000_000.0

# Wide-open read window for "everything stored".
_ALL = (0, 4_000_000_000)


def _global_body(
    *,
    updated_at: int = _FIXTURE_UPDATED_AT,
    dominance: float | None = None,
) -> bytes:
    payload = json.loads(json.dumps(_GLOBAL_PAYLOAD))  # deep copy of the capture
    payload["data"]["updated_at"] = updated_at
    if dominance is not None:
        payload["data"]["market_cap_percentage"]["btc"] = dominance
    return json.dumps(payload).encode("utf-8")


def _price_body() -> bytes:
    return json.dumps({"bitcoin": {"usd": 65789.47, "usd_24h_change": 3.2}}).encode("utf-8")


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
    body_holder: dict[str, bytes],
) -> CoinGeckoAdapter:
    """Adapter whose transport seam serves `body_holder['global']` for `/global`
    (mutable between calls, so a test can steer `updated_at`) and a fixed
    `/simple/price` body. TTL 0 so every fetch reaches the seam."""
    client = ResilientHttpClient(source_name="coingecko-test", cache_ttl_seconds=0.0, max_retries=0)

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = _price_body() if "simple/price" in url else body_holder["global"]
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return CoinGeckoAdapter(http_client=client, metric_store=store)


# --- (a) hour-bucket idempotency -----------------------------------------------


def test_two_fetches_in_same_hour_produce_one_point_per_series(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    holder = {"global": _global_body()}
    adapter = _adapter(monkeypatch, store, holder)

    adapter.fetch_macro_context()
    # 400s later, still inside the same hour bucket, with a drifted dominance —
    # the second fetch must be a no-op, not a second point and not a conflict.
    holder["global"] = _global_body(updated_at=_FIXTURE_UPDATED_AT + 400, dominance=53.0)
    adapter.fetch_macro_context()

    dominance = store.range(SERIES_COINGECKO_BTC_DOMINANCE, *_ALL)
    total_mcap = store.range(SERIES_COINGECKO_TOTAL_MCAP_USD, *_ALL)
    assert [p.ts for p in dominance] == [_FIXTURE_BUCKET]
    assert dominance[0].value == _FIXTURE_DOMINANCE  # first write in the hour wins
    assert [p.ts for p in total_mcap] == [_FIXTURE_BUCKET]
    assert total_mcap[0].value == _FIXTURE_TOTAL_MCAP


def test_fetches_in_different_hours_produce_two_points(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    holder = {"global": _global_body()}
    adapter = _adapter(monkeypatch, store, holder)

    adapter.fetch_macro_context()
    holder["global"] = _global_body(updated_at=_FIXTURE_UPDATED_AT + _HOUR, dominance=53.0)
    adapter.fetch_macro_context()

    dominance = store.range(SERIES_COINGECKO_BTC_DOMINANCE, *_ALL)
    total_mcap = store.range(SERIES_COINGECKO_TOTAL_MCAP_USD, *_ALL)
    assert [p.ts for p in dominance] == [_FIXTURE_BUCKET, _FIXTURE_BUCKET + _HOUR]
    assert dominance[1].value == 53.0
    assert [p.ts for p in total_mcap] == [_FIXTURE_BUCKET, _FIXTURE_BUCKET + _HOUR]


# --- (b) a failed upstream fetch writes nothing ---------------------------------


def test_failed_upstream_fetch_writes_no_points(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    client = ResilientHttpClient(source_name="coingecko-down", cache_ttl_seconds=0.0, max_retries=0)

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=503, headers={}, body=b"down", elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = CoinGeckoAdapter(http_client=client, metric_store=store)

    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_macro_context()

    assert store.range(SERIES_COINGECKO_BTC_DOMINANCE, *_ALL) == []
    assert store.range(SERIES_COINGECKO_TOTAL_MCAP_USD, *_ALL) == []


def test_adapter_without_store_writes_nothing_and_still_returns(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    holder = {"global": _global_body()}
    adapter = _adapter(monkeypatch, store=None, body_holder=holder)

    macro = adapter.fetch_macro_context()

    assert macro.btc_dominance_pct == _FIXTURE_DOMINANCE
    assert store.range(SERIES_COINGECKO_BTC_DOMINANCE, *_ALL) == []


# --- (c) write-through never breaks the macro read ------------------------------


def test_storage_failure_is_swallowed_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    store: MetricPointsRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    holder = {"global": _global_body()}
    adapter = _adapter(monkeypatch, store, holder)

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "upsert_points", boom)

    with caplog.at_level("WARNING"):
        macro = adapter.fetch_macro_context()

    assert macro.btc_dominance_pct == _FIXTURE_DOMINANCE
    assert any("write-through" in record.message for record in caplog.records)


def test_bitcoin_market_pulse_tool_call_accrues_one_point_per_hour(
    monkeypatch: pytest.MonkeyPatch, store: MetricPointsRepository
) -> None:
    """The real MCP tool, in-process: two same-hour calls store one point per
    series, and the tool's reply still carries the live snapshot."""
    holder = {"global": _global_body()}
    adapter = _adapter(monkeypatch, store, holder)
    provider = DefaultMarketDataProvider(coingecko=adapter)
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_bitcoin_market_pulse(server, provider=provider)

    for _ in range(2):
        result = anyio.run(server.call_tool, "bitcoin_market_pulse", {"params": {}})
        _content, structured = cast("tuple[Any, dict[str, Any]]", result)
        assert structured["macro"]["btc_dominance_pct"] == _FIXTURE_DOMINANCE

    dominance = store.range(SERIES_COINGECKO_BTC_DOMINANCE, *_ALL)
    total_mcap = store.range(SERIES_COINGECKO_TOTAL_MCAP_USD, *_ALL)
    assert [p.ts for p in dominance] == [_FIXTURE_BUCKET]
    assert [p.ts for p in total_mcap] == [_FIXTURE_BUCKET]
    assert total_mcap[0].value == _FIXTURE_TOTAL_MCAP
