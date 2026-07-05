"""Plan 0035 phase 4 done-when: the DefiLlama historical-price adapter.

Pinned claims:
(a) a `(token, block-timestamp)` lookup returns the snapshot-cached price on
    the second call WITHOUT a second network call (counting fake transport);
(b) an uncovered token surfaces the typed "no price" — `None`, not `0.0`;
(c) a garbage upstream price (zero / NaN) is treated as no coverage and never
    snapshotted;
(d) HTTP 429 raises the typed `RateLimitedError`;
(e) the native coin (address None) resolves via the `coingecko:ethereum` key.

The live coverage check the survey deferred to this plan is the
`network`-marked test at the bottom: run once with `-m network` and record the
result in the close handoff.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data._http import HttpResponse, ProxyConfig, ResilientHttpClient
from market_analyser.data.adapters.defillama import DefiLlamaAdapter, token_key
from market_analyser.data.errors import RateLimitedError
from market_analyser.data.sources import HistoricalPriceSource
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository

_AERO = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
_TS = 1730000000


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _counting_client(body: dict[str, Any], calls: list[str]) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="llama-test", cache_ttl_seconds=0.0, max_retries=0)

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
    client = ResilientHttpClient(source_name="llama-test", cache_ttl_seconds=0.0, max_retries=0)

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


def _covered_body(token: str, price: float) -> dict[str, Any]:
    return {
        "coins": {
            token: {
                "decimals": 18,
                "symbol": "AERO",
                "price": price,
                "timestamp": _TS,
                "confidence": 0.99,
            }
        }
    }


def test_adapter_satisfies_the_protocol() -> None:
    assert isinstance(DefiLlamaAdapter(), HistoricalPriceSource)


def test_covered_token_parses_price(session_factory: sessionmaker[Session]) -> None:
    token = token_key("base", _AERO)
    calls: list[str] = []
    adapter = DefiLlamaAdapter(
        http_client=_counting_client(_covered_body(token, 1.21), calls),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    assert adapter.fetch_price(chain="base", address=_AERO, ts=_TS) == 1.21
    assert len(calls) == 1
    assert f"/prices/historical/{_TS}/{token}" in calls[0]


def test_second_lookup_reads_the_snapshot_without_a_network_call(
    session_factory: sessionmaker[Session],
) -> None:
    token = token_key("base", _AERO)
    calls: list[str] = []
    store = PriceSnapshotRepository(session_factory)
    adapter = DefiLlamaAdapter(
        http_client=_counting_client(_covered_body(token, 1.21), calls),
        snapshot_store=store,
    )
    first = adapter.fetch_price(chain="base", address=_AERO, ts=_TS)
    second = adapter.fetch_price(chain="base", address=_AERO, ts=_TS)
    assert first == second == 1.21
    assert len(calls) == 1, "the snapshot must serve the second lookup"
    assert store.get(token, _TS) == 1.21


def test_snapshot_survives_an_upstream_revision(session_factory: sessionmaker[Session]) -> None:
    """The determinism claim itself: after the first resolution, a *different*
    upstream number must not change what the adapter returns."""
    token = token_key("base", _AERO)
    store = PriceSnapshotRepository(session_factory)
    first = DefiLlamaAdapter(
        http_client=_counting_client(_covered_body(token, 1.21), []),
        snapshot_store=store,
    )
    assert first.fetch_price(chain="base", address=_AERO, ts=_TS) == 1.21
    revised = DefiLlamaAdapter(
        http_client=_counting_client(_covered_body(token, 9.99), []),
        snapshot_store=store,
    )
    assert revised.fetch_price(chain="base", address=_AERO, ts=_TS) == 1.21


def test_uncovered_token_returns_none_not_zero(session_factory: sessionmaker[Session]) -> None:
    adapter = DefiLlamaAdapter(
        http_client=_counting_client({"coins": {}}, []),
        snapshot_store=PriceSnapshotRepository(session_factory),
    )
    price = adapter.fetch_price(chain="base", address=_AERO, ts=_TS)
    assert price is None
    assert price != 0.0


@pytest.mark.parametrize("garbage", [0.0, -1.0, float("nan")])
def test_garbage_upstream_price_is_no_coverage_and_never_snapshotted(
    session_factory: sessionmaker[Session], garbage: float
) -> None:
    token = token_key("base", _AERO)
    store = PriceSnapshotRepository(session_factory)
    adapter = DefiLlamaAdapter(
        http_client=_counting_client(_covered_body(token, garbage), []),
        snapshot_store=store,
    )
    assert adapter.fetch_price(chain="base", address=_AERO, ts=_TS) is None
    assert store.get(token, _TS) is None


def test_http_429_raises_typed_rate_limit_error() -> None:
    adapter = DefiLlamaAdapter(http_client=_canned_status_client(429))
    with pytest.raises(RateLimitedError):
        adapter.fetch_price(chain="base", address=_AERO, ts=_TS)


def test_native_coin_resolves_via_coingecko_key() -> None:
    calls: list[str] = []
    adapter = DefiLlamaAdapter(
        http_client=_counting_client(_covered_body("coingecko:ethereum", 3500.0), calls),
    )
    assert adapter.fetch_price(chain="base", address=None, ts=_TS) == 3500.0
    assert "coingecko:ethereum" in calls[0]


def test_token_key_lowercases_contract_addresses() -> None:
    assert token_key("base", _AERO.upper().replace("0X", "0x")) == f"base:{_AERO}"


@pytest.mark.network
def test_live_coverage_for_the_smoke_wallet_tokens() -> None:
    """The survey's deferred DefiLlama-coverage verification (Plan 0035 phase 4
    done-when): the phase-8 smoke wallet's held tokens must resolve at
    historical timestamps. Run once with `-m network`; record the outcome in
    the close handoff."""
    adapter = DefiLlamaAdapter()
    held: list[tuple[str, str, str | None]] = [
        ("AERO", "base", _AERO),
        ("WETH", "base", "0x4200000000000000000000000000000000000006"),
        ("USDC", "base", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
        ("GHST", "base", "0xcd2f22236dd9dfe2356d7c543161d4d260fd9bcb"),
        ("ETH-native", "base", None),
    ]
    timestamps = [1717200000, 1748736000]  # 2024-06-01, 2025-06-01 (UTC)
    coverage: dict[str, list[float]] = {}
    for symbol, chain, address in held:
        for ts in timestamps:
            price = adapter.fetch_price(chain="base", address=address, ts=ts)
            assert price is not None, f"DefiLlama has no {symbol} price at {ts}"
            assert price > 0
            coverage.setdefault(symbol, []).append(price)
    # USDC is the sanity anchor: a stablecoin far from $1 means a wrong decode.
    assert all(0.9 < p < 1.1 for p in coverage["USDC"])
