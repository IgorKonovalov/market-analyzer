"""Plan 0040 phase 1 — Polymarket prediction-market odds adapter.

Phase-1 done-when claims pinned here:
(a) against a recorded Gamma fixture, `fetch_market` returns a `PredictionMarket`
    with its outcomes, each carrying an `implied_probability` in [0, 1] + a label;
(b) a malformed / missing-field payload raises the typed error taxonomy — a
    length mismatch, a non-JSON `outcomes`, a non-numeric or out-of-[0,1] price
    each raise `PolymarketError`, never a silently zeroed probability;
(c) a Gamma 404 raises the typed `UnknownMarketError` (unknown id, not a retryable
    outage); 429 → `RateLimitedError`; other exhaustion → `UpstreamUnavailableError`;
(d) `search_markets` flattens the odds-bearing markets out of the events payload,
    skips not-yet-trading markets, and honours the empty/zero-match/limit rules;
(e) the adapter satisfies `isinstance(adapter, PredictionMarketSource)` and is
    reachable through one registry entry (`Mapping[str, PredictionMarketSource]`);
(f) no auth header, no key, no signing anywhere in the adapter (source grep).

Plan 0089 additions: (g) each market flattened out of an event carries a
`market_url` built + host-validated from the **event** `slug`
(`https://polymarket.com/event/<slug>`), `None` when the slug is absent / unusable
(never fabricated, never the numeric id, never a raise); the by-id `fetch_market`
path has no event wrapper so its `market_url is None`. Live-confirmed 2026-07-12
against the real Gamma `public-search`: `events[].slug` is present and
`polymarket.com/event/<slug>` resolves (200; a bogus slug 404s), so the event slug
is the URL basis. Still offline here — the fake transport serves the shape.

Fixture provenance: the market objects mirror the real, verified Gamma wire shape
— `outcomes` and `outcomePrices` are parallel JSON-**encoded string arrays**
(`'["Yes", "No"]'` / `'["0.0585", "0.9415"]'`), `endDate` an ISO-8601 string,
`volumeNum` / `liquidityNum` JSON numbers, `id` / `question` strings. The 404 body
mirrors a real `{"type":"not found error"}`. All offline — the fake transport
replaces `ResilientHttpClient._perform_request`; no live gamma-api.polymarket.com.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import polymarket as polymarket_module
from market_analyser.data.adapters.polymarket import (
    PolymarketError,
    PolymarketOddsAdapter,
    UnknownMarketError,
)
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.sources import PredictionMarketSource
from market_analyser.data.types import PredictionMarket

# A fixed provenance clock so queried_at is deterministic.
_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _market(
    *,
    market_id: str = "558951",
    question: str = "Will Norway win the 2026 FIFA World Cup?",
    outcomes: str = '["Yes", "No"]',
    outcome_prices: str = '["0.0585", "0.9415"]',
    end_date: str | None = "2026-07-20T00:00:00Z",
    volume_num: Any = 128843528.19,
    liquidity_num: Any = 5244399.88,
    closed: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One Gamma market object in the verified wire shape. `outcomes` /
    `outcome_prices` are JSON-encoded string arrays (as the upstream sends them)."""
    raw: dict[str, Any] = {
        "id": market_id,
        "question": question,
        "outcomes": outcomes,
        "outcomePrices": outcome_prices,
        "closed": closed,
        "clobTokenIds": '["60447443643099453130956385288904175887233107411078568881602330835010340506057", "111538579557239934343870815626480092245052857494675784434731223739153238373070"]',  # noqa: E501
    }
    if end_date is not None:
        raw["endDate"] = end_date
    if volume_num is not None:
        raw["volumeNum"] = volume_num
    if liquidity_num is not None:
        raw["liquidityNum"] = liquidity_num
    if extra:
        raw.update(extra)
    return raw


class _FakeTransport:
    """Replaces `ResilientHttpClient._perform_request` (the transport seam),
    routing by URL: `/public-search` serves the configured search payload;
    `/markets/{id}` serves the configured single market (keyed by the trailing id),
    or a 404 body when the id is unknown. Records every requested URL."""

    def __init__(
        self,
        *,
        search: Any = None,
        markets: dict[str, Any] | None = None,
    ) -> None:
        self._search = search if search is not None else {"events": []}
        self._markets = markets if markets is not None else {}
        self.requested_urls: list[str] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        self.requested_urls.append(url)
        split = urllib.parse.urlsplit(url)
        if "/public-search" in split.path:
            return _json_response(200, self._search)
        # /markets/{id}
        market_id = split.path.rsplit("/", 1)[-1]
        market = self._markets.get(market_id)
        if market is None:
            return _json_response(404, {"type": "not found error", "error": "id not found"})
        return _json_response(200, market)


def _json_response(status_code: int, payload: Any) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={},
        body=json.dumps(payload).encode("utf-8"),
        elapsed_seconds=0.0,
    )


def _static_response(status_code: int, body: bytes, headers: dict[str, str] | None = None) -> Any:
    """A transport fake that always returns one response, counting attempts."""

    class _Static:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(
            self, method: str, url: str, req_body: Any, req_headers: Any, *, proxy: Any
        ) -> HttpResponse:
            self.attempts += 1
            return HttpResponse(
                status_code=status_code,
                headers=headers or {},
                body=body,
                elapsed_seconds=0.0,
            )

    return _Static()


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any | None = None,
    max_retries: int = 0,
) -> tuple[PolymarketOddsAdapter, Any]:
    client = ResilientHttpClient(
        source_name="polymarket-test", cache_ttl_seconds=0.0, max_retries=max_retries
    )
    fake = transport if transport is not None else _FakeTransport()
    monkeypatch.setattr(client, "_perform_request", fake)
    return PolymarketOddsAdapter(http_client=client, now=lambda: _NOW), fake


# --- (e) contract ---------------------------------------------------------------


def test_adapter_satisfies_source_protocol() -> None:
    assert isinstance(PolymarketOddsAdapter(), PredictionMarketSource)


def test_reachable_through_one_registry_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The selector-registry shape (ADR-0031): the adapter is the sole value of a
    `Mapping[str, PredictionMarketSource]`, selected by source name."""
    from collections.abc import Mapping

    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(markets={"558951": _market()}))
    registry: Mapping[str, PredictionMarketSource] = {"polymarket": adapter}
    selected = registry["polymarket"]
    assert isinstance(selected, PredictionMarketSource)
    assert selected.fetch_market("558951").market_id == "558951"


# --- (a) fetch_market happy path ------------------------------------------------


def test_fetch_market_returns_outcomes_with_probabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(markets={"558951": _market()}))

    market = adapter.fetch_market("558951")

    assert isinstance(market, PredictionMarket)
    assert market.market_id == "558951"
    assert market.question == "Will Norway win the 2026 FIFA World Cup?"
    assert market.source == "polymarket"
    assert market.queried_at == _NOW
    assert market.closed is False
    assert market.closes_at == datetime(2026, 7, 20, tzinfo=UTC)
    assert market.volume_usd == pytest.approx(128843528.19)
    assert market.liquidity_usd == pytest.approx(5244399.88)
    # Outcomes parsed from the parallel JSON-string arrays, prices in [0, 1].
    assert [o.label for o in market.outcomes] == ["Yes", "No"]
    assert [o.implied_probability for o in market.outcomes] == pytest.approx([0.0585, 0.9415])
    assert all(0.0 <= o.implied_probability <= 1.0 for o in market.outcomes)


def test_fetch_market_multi_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-binary market: N outcomes handled generically by the parallel arrays."""
    adapter, _ = _adapter(
        monkeypatch,
        transport=_FakeTransport(
            markets={
                "m": _market(
                    market_id="m",
                    question="Who wins?",
                    outcomes='["A", "B", "C"]',
                    outcome_prices='["0.2", "0.3", "0.5"]',
                )
            }
        ),
    )

    market = adapter.fetch_market("m")

    assert [o.label for o in market.outcomes] == ["A", "B", "C"]
    assert [o.implied_probability for o in market.outcomes] == [
        pytest.approx(0.2),
        pytest.approx(0.3),
        pytest.approx(0.5),
    ]


def test_fetch_market_missing_optional_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent endDate/volume/liquidity degrade to None — the odds still parse."""
    adapter, _ = _adapter(
        monkeypatch,
        transport=_FakeTransport(
            markets={
                "m": _market(market_id="m", end_date=None, volume_num=None, liquidity_num=None)
            }
        ),
    )

    market = adapter.fetch_market("m")

    assert market.closes_at is None
    assert market.volume_usd is None
    assert market.liquidity_usd is None
    assert len(market.outcomes) == 2


# --- (b) malformed payloads raise the typed error -------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            _market(outcomes='["Yes", "No"]', outcome_prices='["0.6"]'), id="length_mismatch"
        ),
        pytest.param(_market(outcomes="not-json"), id="non_json_outcomes"),
        pytest.param(_market(outcome_prices='["1.5", "-0.5"]'), id="prices_out_of_range"),
        pytest.param(_market(outcome_prices='["abc", "def"]'), id="non_numeric_prices"),
        pytest.param(_market(outcomes="[1, 2]"), id="non_string_outcomes"),
        pytest.param({"id": "m", "question": "Q?"}, id="missing_outcomes"),
        pytest.param(_market(extra={"id": 123}), id="non_string_id"),
    ],
)
def test_fetch_market_malformed_raises_polymarket_error(
    monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
) -> None:
    market_id = str(raw.get("id", "m"))
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(markets={market_id: raw}))
    with pytest.raises(PolymarketError):
        adapter.fetch_market(market_id)


def test_fetch_market_never_zeros_a_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing price must raise, not silently coerce the outcome to 0.0."""
    adapter, _ = _adapter(
        monkeypatch,
        transport=_FakeTransport(markets={"m": _market(market_id="m", outcome_prices='["", ""]')}),
    )
    with pytest.raises(PolymarketError):
        adapter.fetch_market("m")


def test_fetch_market_non_object_payload_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport(markets={"m": ["not", "an", "object"]})
    adapter, _ = _adapter(monkeypatch, transport=transport)
    with pytest.raises(PolymarketError):
        adapter.fetch_market("m")


# --- (c) upstream error taxonomy ------------------------------------------------


def test_fetch_market_unknown_id_raises_unknown_market_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(markets={}))
    with pytest.raises(UnknownMarketError) as excinfo:
        adapter.fetch_market("999999999")
    assert excinfo.value.market_id == "999999999"


def test_fetch_market_404_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 is permanent — exactly one transport attempt even with retries on."""
    transport = _static_response(404, json.dumps({"type": "not found error"}).encode("utf-8"))
    adapter, fake = _adapter(monkeypatch, transport=transport, max_retries=3)
    with pytest.raises(UnknownMarketError):
        adapter.fetch_market("m")
    assert fake.attempts == 1


def test_fetch_market_429_raises_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _static_response(429, b"{}", headers={"Retry-After": "12"})
    adapter, _ = _adapter(monkeypatch, transport=transport, max_retries=0)
    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_market("m")
    assert excinfo.value.retry_after_seconds == 12


def test_fetch_market_500_raises_upstream_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _static_response(500, b"{}")
    adapter, _ = _adapter(monkeypatch, transport=transport, max_retries=0)
    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_market("m")


def test_search_429_raises_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _static_response(429, b"{}")
    adapter, _ = _adapter(monkeypatch, transport=transport, max_retries=0)
    with pytest.raises(RateLimitedError):
        adapter.search_markets("bitcoin")


# --- (d) search ------------------------------------------------------------------


def test_search_flattens_markets_out_of_events(monkeypatch: pytest.MonkeyPatch) -> None:
    search = {
        "events": [
            {"id": "e1", "markets": [_market(market_id="a"), _market(market_id="b")]},
            {"id": "e2", "markets": [_market(market_id="c")]},
        ]
    }
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search=search))

    results = adapter.search_markets("world cup")

    assert [m.market_id for m in results] == ["a", "b", "c"]
    assert all(m.source == "polymarket" for m in results)
    assert all(m.queried_at == _NOW for m in results)


def test_search_skips_not_yet_trading_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A market with empty outcomes/prices (not yet trading) is skipped, not
    fabricated and not an error; the odds-bearing sibling still parses."""
    search = {
        "events": [
            {
                "markets": [
                    {"id": "new", "question": "Pending?", "outcomes": "", "outcomePrices": ""},
                    _market(market_id="live"),
                ]
            }
        ]
    }
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search=search))

    results = adapter.search_markets("x")

    assert [m.market_id for m in results] == ["live"]


def test_search_empty_query_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, fake = _adapter(monkeypatch)
    assert adapter.search_markets("   ") == []
    # No network call for an empty query.
    assert fake.requested_urls == []


def test_search_zero_matches_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search={"events": []}))
    assert adapter.search_markets("nothing matches") == []


def test_search_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    search = {
        "events": [
            {"markets": [_market(market_id=str(i)) for i in range(10)]},
        ]
    }
    adapter, fake = _adapter(monkeypatch, transport=_FakeTransport(search=search))

    results = adapter.search_markets("x", limit=3)

    assert len(results) == 3
    # The limit is forwarded to the upstream as limit_per_type.
    (url,) = fake.requested_urls
    assert "limit_per_type=3" in url
    assert "events_status=active" in url


def test_search_rejects_non_positive_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    with pytest.raises(ValueError, match="limit"):
        adapter.search_markets("x", limit=0)


def test_search_malformed_event_shape_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search={"events": "not-a-list"}))
    with pytest.raises(PolymarketError):
        adapter.search_markets("x")


# --- (g) market_url from the event slug (Plan 0089) -----------------------------


def test_search_builds_market_url_from_event_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every market flattened out of an event with a `slug` carries the canonical
    public URL `https://polymarket.com/event/<event-slug>` — the live-confirmed
    basis (2026-07-12: `events[].slug` resolves 200, a bogus slug 404s)."""
    search = {
        "events": [
            {
                "id": "e1",
                "slug": "bitcoin-above-on-july-13-2026",
                "markets": [_market(market_id="2818067"), _market(market_id="2818099")],
            },
        ]
    }
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search=search))

    results = adapter.search_markets("bitcoin")

    assert [m.market_id for m in results] == ["2818067", "2818099"]
    assert all(
        m.market_url == "https://polymarket.com/event/bitcoin-above-on-july-13-2026"
        for m in results
    )
    # The link is https-scheme + exact polymarket.com host, and never the numeric id.
    for m in results:
        assert m.market_url is not None
        split = urllib.parse.urlsplit(m.market_url)
        assert split.scheme == "https"
        assert split.netloc == "polymarket.com"
        assert m.market_id not in m.market_url


def test_search_market_url_none_when_event_slug_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """An event with no `slug` yields `market_url is None` — no fabricated link,
    never the numeric-id URL, never a raise (ADR-0041)."""
    search = {"events": [{"id": "e1", "markets": [_market(market_id="a")]}]}
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search=search))

    (result,) = adapter.search_markets("x")

    assert result.market_url is None


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",  # empty
        "   ",  # whitespace-only
        123,  # non-string
        None,  # explicit null
        "has/slash",  # would alter the path
        "has space",  # not URL-safe
        "has%2Fencoded",  # percent-encoding
        "q?x=1",  # query char
        "frag#ment",  # fragment char
    ],
)
def test_search_market_url_none_for_unusable_slug(
    monkeypatch: pytest.MonkeyPatch, bad_slug: Any
) -> None:
    """A malformed / non-string / path-manipulating slug degrades to `market_url
    is None` — the URL is host + single-segment validated, so an off-host or
    path-altering link is never emitted, and a bad slug never raises."""
    search = {"events": [{"slug": bad_slug, "markets": [_market(market_id="a")]}]}
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(search=search))

    (result,) = adapter.search_markets("x")

    assert result.market_url is None


def test_fetch_market_by_id_has_no_market_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The by-id `fetch_market` path has no parent-event wrapper, so there is no
    event slug and `market_url is None` (the event slug is the only sanctioned
    basis; the market's own slug does not build the public page)."""
    adapter, _ = _adapter(monkeypatch, transport=_FakeTransport(markets={"558951": _market()}))

    market = adapter.fetch_market("558951")

    assert market.market_url is None


# --- (f) no auth / key / signing anywhere in the adapter ------------------------


def test_adapter_source_holds_no_auth_key_or_signing() -> None:
    """ADR-0041: public reads only — the adapter must carry no credential or
    signing path. A grep of the module source rejects the giveaway tokens."""
    source = Path(polymarket_module.__file__).read_text(encoding="utf-8")
    # Strip the docstring/comment mentions of the words in prose; assert no
    # code-level auth constructs. These patterns catch header injection, key
    # material, and EIP-712 signing — none of which a read adapter needs.
    forbidden = [
        r"Authorization",
        r"Bearer",
        r"api[_-]?key",
        r"private[_-]?key",
        r"\bsign\b",
        r"eip712",
        r"secret",
    ]
    offenders = [p for p in forbidden if re.search(p, source, re.IGNORECASE)]
    assert offenders == [], f"adapter source contains auth/signing tokens: {offenders}"
