"""Plan 0084 phase 4 done-when: the CoinGecko historical-price fallback + chain.

Pinned claims:
- a `(token, block-timestamp)` lookup returns the price **nearest** the timestamp
  from CoinGecko's `market_chart/range` series, snapshot-cached (second call, no
  network) — keyed identically to the DefiLlama primary;
- the native coin routes to `/coins/ethereum/market_chart/range`, a contract token
  to `/coins/{platform}/contract/{address}/market_chart/range`;
- an empty / garbage series is `None`, never `0.0`, never snapshotted; 429 raises
  the typed `RateLimitedError`;
- the chain tries the primary first and consults the fallback ONLY on a primary
  miss — the long-tail token DefiLlama cannot price resolves via CoinGecko — while
  a primary *error* propagates (is not swallowed into a fallback attempt);
- the merged result is deterministic: after a fallback resolution both sources
  share one snapshot, so a re-run reads the snapshot with zero network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.coingecko_historical_price import (
    ChainedHistoricalPriceSource,
    CoinGeckoHistoricalPriceAdapter,
)
from market_analyser.data.adapters.defillama import DefiLlamaAdapter, token_key
from market_analyser.data.errors import RateLimitedError
from market_analyser.data.sources import HistoricalPriceSource
from market_analyser.defi.models import Chain
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository

_GHST = "0xcd2f22236dd9dfe2356d7c543161d4d260fd9bcb"  # the long-tail token
_TS = 1730000000


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _counting_client(body: dict[str, Any], calls: list[str]) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="cg-price-test", cache_ttl_seconds=0.0, max_retries=0)

    def _fake_perform(
        method: str,
        url: str,
        body_arg: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        calls.append(url)
        return HttpResponse(
            status_code=200, headers={}, body=json.dumps(body).encode(), elapsed_seconds=0.0
        )

    client._perform_request = _fake_perform  # type: ignore[method-assign, assignment]
    return client


def _canned_status_client(status_code: int) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="cg-price-test", cache_ttl_seconds=0.0, max_retries=0)

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


def _series(*points: tuple[int, float]) -> dict[str, Any]:
    """A CoinGecko market_chart body: [[ts_ms, price], ...]."""
    return {"prices": [[ts * 1000, price] for ts, price in points]}


def test_adapter_satisfies_the_protocol() -> None:
    assert isinstance(CoinGeckoHistoricalPriceAdapter(), HistoricalPriceSource)


def test_picks_the_price_nearest_the_block_timestamp(
    session_factory: sessionmaker[Session],
) -> None:
    body = _series((_TS - 3600, 1.10), (_TS, 1.21), (_TS + 7200, 1.35))
    adapter = CoinGeckoHistoricalPriceAdapter(
        http_client=_counting_client(body, []),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    assert adapter.fetch_price(chain="base", address=_GHST, ts=_TS) == 1.21


def test_contract_and_native_routes_hit_the_right_endpoints(
    session_factory: sessionmaker[Session],
) -> None:
    calls: list[str] = []
    adapter = CoinGeckoHistoricalPriceAdapter(
        http_client=_counting_client(_series((_TS, 1.0)), calls),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    adapter.fetch_price(chain="base", address=None, ts=_TS)
    assert f"/coins/base/contract/{_GHST}/market_chart/range" in calls[0]
    assert "/coins/ethereum/market_chart/range" in calls[1]


def test_second_lookup_reads_the_snapshot_without_a_network_call(
    session_factory: sessionmaker[Session],
) -> None:
    calls: list[str] = []
    store = PriceSnapshotRepository(session_factory)
    adapter = CoinGeckoHistoricalPriceAdapter(
        http_client=_counting_client(_series((_TS, 1.21)), calls),
        snapshot_store=store,
    )
    first = adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    second = adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert first == second == 1.21
    assert len(calls) == 1
    assert store.get(token_key("base", _GHST), _TS) == 1.21


def test_empty_series_returns_none_not_zero(session_factory: sessionmaker[Session]) -> None:
    adapter = CoinGeckoHistoricalPriceAdapter(
        http_client=_counting_client({"prices": []}, []),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    price = adapter.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert price is None
    assert price != 0.0


@pytest.mark.parametrize("garbage", [0.0, -1.0, float("nan")])
def test_garbage_point_is_no_coverage_and_never_snapshotted(
    session_factory: sessionmaker[Session], garbage: float
) -> None:
    store = PriceSnapshotRepository(session_factory)
    adapter = CoinGeckoHistoricalPriceAdapter(
        http_client=_counting_client(_series((_TS, garbage)), []),
        snapshot_store=store,
    )
    assert adapter.fetch_price(chain="base", address=_GHST, ts=_TS) is None
    assert store.get(token_key("base", _GHST), _TS) is None


def test_http_429_raises_typed_rate_limit_error() -> None:
    adapter = CoinGeckoHistoricalPriceAdapter(http_client=_canned_status_client(429))
    with pytest.raises(RateLimitedError):
        adapter.fetch_price(chain="base", address=_GHST, ts=_TS)


# -- the primary → fallback chain -----------------------------------------------


class _SpySource:
    """A HistoricalPriceSource returning a fixed price (or None), counting calls."""

    def __init__(self, price: float | None) -> None:
        self._price = price
        self.calls = 0

    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        self.calls += 1
        return self._price


class _ErrorSource:
    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        raise RateLimitedError("primary rate limited")


def test_primary_hit_never_consults_the_fallback() -> None:
    primary = _SpySource(1.21)
    fallback = _SpySource(9.99)
    chain = ChainedHistoricalPriceSource(primary=primary, fallback=fallback)
    assert chain.fetch_price(chain="base", address=_GHST, ts=_TS) == 1.21
    assert (primary.calls, fallback.calls) == (1, 0)


def test_primary_miss_falls_through_to_the_fallback() -> None:
    primary = _SpySource(None)
    fallback = _SpySource(0.42)
    chain = ChainedHistoricalPriceSource(primary=primary, fallback=fallback)
    assert chain.fetch_price(chain="base", address=_GHST, ts=_TS) == 0.42
    assert (primary.calls, fallback.calls) == (1, 1)


def test_primary_error_is_not_swallowed_into_a_fallback_attempt() -> None:
    fallback = _SpySource(0.42)
    chain = ChainedHistoricalPriceSource(primary=_ErrorSource(), fallback=fallback)
    with pytest.raises(RateLimitedError):
        chain.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert fallback.calls == 0


def test_fallback_error_degrades_to_no_coverage_never_crashes() -> None:
    """Regression (Plan 0084 ph6 smoke): a fallback transport failure — e.g.
    CoinGecko's keyless historical endpoint 401 — must return None (no coverage),
    not propagate and crash a reconstruction the primary alone would complete."""
    chain = ChainedHistoricalPriceSource(primary=_SpySource(None), fallback=_ErrorSource())
    assert chain.fetch_price(chain="base", address=_GHST, ts=_TS) is None


def test_long_tail_token_resolves_via_the_fallback_and_is_deterministic(
    session_factory: sessionmaker[Session],
) -> None:
    """The done-when: DefiLlama has no GHST coverage; CoinGecko does; the chain
    resolves it and, via the shared snapshot, a re-run reads it with zero network
    from either source."""
    store = PriceSnapshotRepository(session_factory)
    llama_calls: list[str] = []
    cg_calls: list[str] = []
    primary = DefiLlamaAdapter(
        http_client=_counting_client({"coins": {}}, llama_calls),  # no coverage
        snapshot_store=store,
    )
    fallback = CoinGeckoHistoricalPriceAdapter(
        http_client=_counting_client(_series((_TS, 3.14)), cg_calls),
        snapshot_store=store,
    )
    chain = ChainedHistoricalPriceSource(primary=primary, fallback=fallback)

    first = chain.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert first == 3.14
    assert len(cg_calls) == 1, "fallback resolved the miss"

    second = chain.fetch_price(chain="base", address=_GHST, ts=_TS)
    assert second == 3.14
    assert len(cg_calls) == 1, "the shared snapshot serves the re-run — no second fallback fetch"
    assert store.get(token_key("base", _GHST), _TS) == 3.14
