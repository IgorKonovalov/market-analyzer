"""Plan 0087 / ADR-0081 phase-1 done-when: the keyed Alchemy historical-price
fallback (replacing Plan 0084's inert keyless CoinGecko fallback).

Pinned claims:
- a `(token, block-timestamp)` lookup returns the price **nearest** the timestamp
  from Alchemy's `tokens/historical` series (ISO timestamps, string values),
  snapshot-cached (second call, no network) — keyed identically to the DefiLlama
  primary;
- the key travels in the `Authorization: Bearer` header, **never** the URL path,
  and never appears in the client's failure log (secret hygiene);
- absent a key the adapter is **inert** — it issues no request and returns `None`,
  so the chain degrades to no-coverage (never a crash), the pre-0087 behavior;
- the native coin is identified by symbol, a contract token by network + address;
- an empty / garbage series is `None`, never `0.0`, never snapshotted; 429 raises
  the typed `RateLimitedError`;
- the merged chain result is deterministic: after a fallback resolution both
  sources share one snapshot, so a re-run reads the snapshot with zero network.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.alchemy_historical_price import (
    AlchemyHistoricalPriceAdapter,
)
from market_analyser.data.adapters.coingecko_historical_price import (
    ChainedHistoricalPriceSource,
)
from market_analyser.data.adapters.defillama import DefiLlamaAdapter, token_key
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.sources import HistoricalPriceSource
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository
from market_analyser.persistence.secrets import SecretsStore

_GHST = "0xcd2f22236dd9dfe2356d7c543161d4d260fd9bcb"  # the long-tail token
_TS = 1730000000
_KEY = "sk-alchemy-test-abc123"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _keyed_store(tmp_path: Path, key: str | None = _KEY) -> SecretsStore:
    """A secrets store carrying (or lacking) the Alchemy key via env override, so
    no file is written and no real secret touches disk."""
    environ = {"MARKET_ANALYSER_ALCHEMY_PRICES_KEY": key} if key is not None else {}
    return SecretsStore(tmp_path / "secrets.json", environ=environ)


class _Call:
    def __init__(self, url: str, headers: Mapping[str, str], body: dict[str, Any] | None) -> None:
        self.url = url
        self.headers = dict(headers)
        self.body = body


def _recording_client(body: dict[str, Any], calls: list[_Call]) -> ResilientHttpClient:
    client = ResilientHttpClient(
        source_name="alchemy-price-test", cache_ttl_seconds=0.0, max_retries=0
    )

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        parsed = json.loads(body_arg.decode()) if body_arg is not None else None
        calls.append(_Call(url, headers or {}, parsed))
        return HttpResponse(
            status_code=200, headers={}, body=json.dumps(body).encode(), elapsed_seconds=0.0
        )

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _canned_status_client(status_code: int) -> ResilientHttpClient:
    client = ResilientHttpClient(
        source_name="alchemy-price-test", cache_ttl_seconds=0.0, max_retries=0
    )

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        return HttpResponse(status_code=status_code, headers={}, body=b"{}", elapsed_seconds=0.0)

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _no_network_client() -> ResilientHttpClient:
    client = ResilientHttpClient(
        source_name="alchemy-price-test", cache_ttl_seconds=0.0, max_retries=0
    )

    def _fake_perform(*args: Any, **kwargs: Any) -> HttpResponse:
        raise AssertionError("no network call expected")

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _iso(ts: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _series(*points: tuple[int, float | str]) -> dict[str, Any]:
    """An Alchemy historical body: {"data": [{"timestamp": iso, "value": str}, ...]}."""
    return {"data": [{"timestamp": _iso(ts), "value": str(value)} for ts, value in points]}


def test_adapter_satisfies_the_protocol() -> None:
    assert isinstance(AlchemyHistoricalPriceAdapter(), HistoricalPriceSource)


def test_picks_the_price_nearest_the_block_timestamp(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    body = _series((_TS - 3600, "1.10"), (_TS, "1.21"), (_TS + 7200, "1.35"))
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client(body, []),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    assert adapter.fetch_price(chain="base", address=_GHST, ts=_TS) == 1.21


def test_unkeyed_adapter_is_inert_and_makes_no_request(tmp_path: Path) -> None:
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path, key=None),
        http_client=_no_network_client(),
    )
    assert adapter.fetch_price(chain="base", address=_GHST, ts=_TS) is None


def test_key_travels_in_the_authorization_header_never_the_url_path(tmp_path: Path) -> None:
    calls: list[_Call] = []
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client(_series((_TS, "1.0")), calls),
    )
    adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert calls[0].headers.get("Authorization") == f"Bearer {_KEY}"
    assert _KEY not in calls[0].url
    assert calls[0].url == "https://api.g.alchemy.com/prices/v1/tokens/historical"


def test_key_never_appears_in_a_failure_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401 (bad/rate-limited key) must be logged with the URL *path* only — the
    key lives in the Authorization header, which the client never logs."""
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_canned_status_client(401),
    )
    with caplog.at_level(logging.WARNING), pytest.raises(UpstreamUnavailableError):
        adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert caplog.records, "the failed request must have logged a warning"
    assert _KEY not in caplog.text


def test_native_and_contract_bodies_identify_the_token(tmp_path: Path) -> None:
    calls: list[_Call] = []
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client(_series((_TS, "1.0")), calls),
    )
    adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    adapter.fetch_price(chain="base", address=None, ts=_TS)
    assert calls[0].body == {
        "network": "base-mainnet",
        "address": _GHST,
        "startTime": _iso(_TS - 2 * 86_400),
        "endTime": _iso(_TS + 2 * 86_400),
        "interval": "1h",
    }
    assert calls[1].body is not None
    assert calls[1].body["symbol"] == "ETH"
    assert "network" not in calls[1].body and "address" not in calls[1].body


def test_second_lookup_reads_the_snapshot_without_a_network_call(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    calls: list[_Call] = []
    store = PriceSnapshotRepository(session_factory)
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client(_series((_TS, "1.21")), calls),
        snapshot_store=store,
    )
    first = adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    second = adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert first == second == 1.21
    assert len(calls) == 1
    assert store.get(token_key("base", _GHST), _TS) == 1.21


def test_empty_series_returns_none_not_zero(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client({"data": []}, []),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    price = adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert price is None
    assert price != 0.0


@pytest.mark.parametrize("garbage", ["0", "-1", "nan", "not-a-number"])
def test_garbage_point_is_no_coverage_and_never_snapshotted(
    tmp_path: Path, session_factory: sessionmaker[Session], garbage: str
) -> None:
    store = PriceSnapshotRepository(session_factory)
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client(_series((_TS, garbage)), []),
        snapshot_store=store,
    )
    assert adapter.fetch_price(chain="base", address=_GHST, ts=_TS) is None
    assert store.get(token_key("base", _GHST), _TS) is None


def test_http_429_raises_typed_rate_limit_error(tmp_path: Path) -> None:
    adapter = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_canned_status_client(429),
    )
    with pytest.raises(RateLimitedError):
        adapter.fetch_price(chain="base", address=_GHST, ts=_TS)


# -- the primary → fallback chain -----------------------------------------------


def test_long_tail_token_resolves_via_the_alchemy_fallback_and_is_deterministic(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    """The done-when: DefiLlama has no coverage; keyed Alchemy does; the chain
    resolves it and, via the shared snapshot, a re-run reads it with zero network
    from either source."""
    store = PriceSnapshotRepository(session_factory)
    llama_calls: list[_Call] = []
    alchemy_calls: list[_Call] = []
    primary = DefiLlamaAdapter(
        http_client=_recording_client({"coins": {}}, llama_calls),  # no coverage
        snapshot_store=store,
    )
    fallback = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path),
        http_client=_recording_client(_series((_TS, "3.14")), alchemy_calls),
        snapshot_store=store,
    )
    chain = ChainedHistoricalPriceSource(primary=primary, fallback=fallback)

    first = chain.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert first == 3.14
    assert len(alchemy_calls) == 1, "the fallback resolved the miss"

    second = chain.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert second == 3.14
    assert len(alchemy_calls) == 1, "the shared snapshot serves the re-run — no second fetch"
    assert store.get(token_key("base", _GHST), _TS) == 3.14


def test_unkeyed_fallback_makes_the_chain_degrade_to_no_coverage(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    """Absent a key, the Alchemy fallback is inert, so a primary miss degrades to
    `None` (honest incomplete) rather than crashing — the pre-0084 posture."""
    store = PriceSnapshotRepository(session_factory)
    primary = DefiLlamaAdapter(
        http_client=_recording_client({"coins": {}}, []),  # no coverage
        snapshot_store=store,
    )
    fallback = AlchemyHistoricalPriceAdapter(
        secrets_store=_keyed_store(tmp_path, key=None),
        http_client=_no_network_client(),
        snapshot_store=store,
    )
    chain = ChainedHistoricalPriceSource(primary=primary, fallback=fallback)
    assert chain.fetch_price(chain="base", address=_GHST, ts=_TS) is None
