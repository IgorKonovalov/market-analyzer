"""Plan 0012 phase 1 — offline tests for the StockTwits sentiment adapter.

Two committed live captures (`stocktwits_AAPL_response.json`,
`stocktwits_BTCX_response.json`, both scrubbed of users/PII/media) plus inline
payloads drive `StockTwitsAdapter` through a client whose transport seam
(`_perform_request`) is monkeypatched, so the suite never touches the network.
`_now` is frozen so the real captured timestamps stay inside the window
deterministically. The single live call is isolated behind `@pytest.mark.network`.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data import UnknownSymbolError
from market_analyser.data._http import ErrorKind, HttpResponse, ResilientHttpError
from market_analyser.data.adapters import stocktwits
from market_analyser.data.adapters.stocktwits import (
    StockTwitsAdapter,
    StockTwitsHttpClient,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_AAPL_BYTES = (_FIXTURES / "stocktwits_AAPL_response.json").read_bytes()
_BTCX_BYTES = (_FIXTURES / "stocktwits_BTCX_response.json").read_bytes()

# A wall-clock "now" just after both captures' newest post, so window="24h"
# includes every message in either fixture forever (the captures are from
# 2026-05-25; without freezing they would age out and silently break the test).
_FROZEN_NOW = datetime(2026, 5, 25, 20, 0, 0, tzinfo=UTC)

# Label counts in the committed fixtures. If a fixture is recaptured these must be
# updated in lockstep — the mismatch is the signal that the recapture changed.
_AAPL_COUNTS = {"positive": 7, "negative": 4, "neutral": 19}  # 30 messages
_BTCX_COUNTS = {"positive": 13, "negative": 7, "neutral": 10}  # 30 messages


def _freeze(monkeypatch: pytest.MonkeyPatch, now: datetime = _FROZEN_NOW) -> None:
    monkeypatch.setattr(stocktwits, "_now", lambda: now)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes = _AAPL_BYTES,
    status: int = 200,
    cache_ttl_seconds: float = 0.0,
    max_retries: int = 0,
) -> tuple[StockTwitsAdapter, StockTwitsHttpClient, list[str]]:
    """Wire an adapter to a fixed transport response; return it, the client (for
    stats), and the list of requested URLs (for path assertions)."""
    client = StockTwitsHttpClient(
        source_name="st-test",
        cache_ttl_seconds=cache_ttl_seconds,
        max_retries=max_retries,
    )
    urls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        urls.append(url)
        return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return StockTwitsAdapter(http_client=client), client, urls


def _payload(messages: list[dict[str, Any]], symbol: str = "X") -> bytes:
    return json.dumps(
        {"symbol": {"symbol": symbol}, "messages": messages, "response": {"status": 200}}
    ).encode("utf-8")


def _msg(created_at: str, basic: str | None) -> dict[str, Any]:
    return {"id": 1, "created_at": created_at, "entities": {"sentiment": {"basic": basic}}}


# -- correctness ------------------------------------------------------------


def test_aapl_fixture_counts_labels_field_by_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, body=_AAPL_BYTES)

    sample = adapter.fetch_sentiment(symbol="AAPL", window="24h")

    k1, k2 = _AAPL_COUNTS["positive"], _AAPL_COUNTS["negative"]
    assert sample.symbol == "AAPL"
    assert sample.source == "stocktwits"
    assert sample.window == "24h"
    assert sample.score == (k1 - k2) / max(1, k1 + k2)
    assert sample.breakdown == _AAPL_COUNTS
    assert sample.as_of == _FROZEN_NOW
    assert sample.as_of.tzinfo is not None


def test_crypto_btcx_is_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _, urls = _adapter(monkeypatch, body=_BTCX_BYTES)

    sample = adapter.fetch_sentiment(symbol="BTC.X", window="24h")

    # Pass-through: the requested path is the verbatim ticker, no rewriting.
    assert urls == ["https://api.stocktwits.com/api/2/streams/symbol/BTC.X.json"]
    assert sample.symbol == "BTC.X"
    assert sample.source == "stocktwits"
    assert sample.breakdown == _BTCX_COUNTS
    assert sample.score == 0.3  # (13 - 7) / 20


def test_lowercase_crypto_ticker_keeps_the_x_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _, urls = _adapter(monkeypatch, body=_BTCX_BYTES)

    sample = adapter.fetch_sentiment(symbol="btc.x", window="24h")

    assert urls == ["https://api.stocktwits.com/api/2/streams/symbol/BTC.X.json"]
    assert sample.symbol == "BTC.X"


def test_window_filters_out_old_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    _freeze(monkeypatch, now)
    body = _payload(
        [
            _msg("2026-05-25T11:50:00Z", "Bullish"),  # 10 min — inside 1h
            _msg("2026-05-25T10:30:00Z", "Bearish"),  # 90 min — outside
            _msg("2026-05-25T07:00:00Z", "Bearish"),  # 5 h — outside
            _msg("2026-05-22T12:00:00Z", "Bearish"),  # 3 d — outside
        ]
    )
    adapter, _, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="AAPL", window="1h")

    assert sample.breakdown == {"positive": 1, "negative": 0, "neutral": 0}
    assert sample.score == 1.0


def test_lowercase_symbol_normalises_to_uppercase_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _, urls = _adapter(monkeypatch, body=_AAPL_BYTES)

    lower = adapter.fetch_sentiment(symbol="aapl", window="24h")
    upper = adapter.fetch_sentiment(symbol="AAPL", window="24h")

    assert urls == ["https://api.stocktwits.com/api/2/streams/symbol/AAPL.json"] * 2
    assert lower.model_dump() == upper.model_dump()


def test_all_unlabeled_is_neutral_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    body = _payload([_msg("2026-05-25T19:55:00Z", None) for _ in range(50)])
    adapter, _, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="AAPL", window="24h")

    assert sample.score == 0.0
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 50}


def test_zero_posts_is_neutral_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, body=_payload([]))

    sample = adapter.fetch_sentiment(symbol="AAPL", window="24h")

    assert sample.score == 0.0
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 0}


# -- error mapping ----------------------------------------------------------


def test_upstream_404_raises_unknown_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    # Plan 0013: the StockTwits 404 now raises the canonical UnknownSymbolError
    # (from the public market_analyser.data surface), carrying the ticker.
    # Plan 0012's SymbolNotCoveredError no longer exists as a name.
    body = b'{"errors":[{"message":"Symbol not found"}],"response":{"status":404}}'
    adapter, _, _ = _adapter(monkeypatch, body=body, status=404)

    with pytest.raises(UnknownSymbolError, match="not tracked") as excinfo:
        adapter.fetch_sentiment(symbol="MADEUP", window="24h")
    assert excinfo.value.symbol == "MADEUP"


def test_upstream_500_propagates_as_resilient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    adapter, _, _ = _adapter(monkeypatch, body=b"boom", status=500)

    # A 500 is a transport failure, not a coverage gap — must NOT become
    # UnknownSymbolError (only the 404→symbol-unknown mapping moved to the typed
    # taxonomy in Plan 0013; StockTwits transport errors stay ResilientHttpError).
    with pytest.raises(ResilientHttpError):
        adapter.fetch_sentiment(symbol="AAPL", window="24h")


# -- rate-limit classifier override -----------------------------------------


def test_classify_maps_only_ratelimit_403() -> None:
    client = StockTwitsHttpClient(source_name="st-test")
    ratelimit = HttpResponse(
        status_code=403,
        headers={},
        body=b'{"errors":[{"message":"Rate limit exceeded. Try later."}]}',
        elapsed_seconds=0.0,
    )
    plain = HttpResponse(
        status_code=403,
        headers={},
        body=b'{"errors":[{"message":"Forbidden"}]}',
        elapsed_seconds=0.0,
    )

    assert client.classify(None, ratelimit) == ErrorKind.RATELIMIT
    assert client.classify(None, plain) == ErrorKind.PERMANENT


def test_ratelimit_403_retries_with_longer_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    body = b'{"errors":[{"message":"Rate limit exceeded"}]}'
    adapter, _, urls = _adapter(monkeypatch, body=body, status=403, max_retries=1)

    with pytest.raises(ResilientHttpError):
        adapter.fetch_sentiment(symbol="AAPL", window="24h")

    assert len(urls) == 2  # retried once (rate-limit, not permanent)
    assert sleeps[0] >= 2 * 0.5  # rate-limit floor is >= 2x the 0.5s backoff_initial


def test_plain_403_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    body = b'{"errors":[{"message":"Forbidden"}]}'
    adapter, _, urls = _adapter(monkeypatch, body=body, status=403, max_retries=3)

    with pytest.raises(ResilientHttpError):
        adapter.fetch_sentiment(symbol="AAPL", window="24h")

    assert len(urls) == 1  # permanent — no retry despite max_retries=3


# -- cache & window validation ----------------------------------------------


def test_second_call_within_ttl_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, client, _ = _adapter(monkeypatch, body=_AAPL_BYTES, cache_ttl_seconds=300.0)

    first = adapter.fetch_sentiment(symbol="AAPL", window="24h")
    second = adapter.fetch_sentiment(symbol="AAPL", window="24h")

    assert first == second
    stats = client.stats()
    assert stats.requests == 1
    assert stats.cache_hits == 1


def test_unsupported_window_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _, _ = _adapter(monkeypatch, body=_AAPL_BYTES)

    with pytest.raises(ValueError, match="unsupported window"):
        adapter.fetch_sentiment(symbol="AAPL", window="2h")


# -- live smoke -------------------------------------------------------------


@pytest.mark.network
def test_live_fetch_returns_valid_reading() -> None:
    sample = StockTwitsAdapter().fetch_sentiment(symbol="AAPL", window="24h")

    assert -1.0 <= sample.score <= 1.0
    assert sample.source == "stocktwits"
    assert sum(sample.breakdown.values()) >= 0
