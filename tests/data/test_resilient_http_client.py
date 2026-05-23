"""Plan 0009 phase 1 — behavioural tests for `ResilientHttpClient`.

Every test drives the cache / retry / backoff / concurrency / proxy logic
through the `_perform_request` transport seam (monkeypatched) so the suite is
fully offline. `time.sleep` and `time.monotonic` are patched on the module's
`time` reference where deterministic timing is asserted; the concurrency test
deliberately uses real time so the cap is observed under genuine threads.
"""

from __future__ import annotations

import concurrent.futures
import logging
import random
import socket
import threading
import time
import urllib.error
from collections.abc import Callable, Mapping
from typing import Any, cast

import pytest

from market_analyser.data._http import (
    ErrorKind,
    HttpResponse,
    ProxyConfig,
    ResilientHttpClient,
    ResilientHttpError,
)

Transport = Callable[..., HttpResponse]


def _ok(status: int = 200, body: bytes = b'{"ok": true}') -> HttpResponse:
    return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)


def _seq_transport(items: list[Any]) -> Transport:
    """A transport that replays `items` in order; an exception item is raised."""
    box = list(items)

    def fake(
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        item = box.pop(0)
        if isinstance(item, BaseException):
            raise item
        return cast(HttpResponse, item)

    return fake


# -- cache --------------------------------------------------------------------


def test_cache_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    client = ResilientHttpClient(source_name="t", cache_ttl_seconds=10, cache_max_entries=4)
    calls: list[str] = []

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return _ok()

    monkeypatch.setattr(client, "_perform_request", fake)

    client.get("https://x/a")
    client.get("https://x/a")  # within TTL -> cache hit, no second request
    assert len(calls) == 1

    clock["t"] += 11  # advance past the TTL
    client.get("https://x/a")
    assert len(calls) == 2

    stats = client.stats()
    assert stats.cache_hits == 1
    assert stats.requests == 2


def test_cache_lru_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    client = ResilientHttpClient(source_name="t", cache_ttl_seconds=100, cache_max_entries=2)
    calls: list[str] = []

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return _ok()

    monkeypatch.setattr(client, "_perform_request", fake)

    client.get("https://x/a")
    client.get("https://x/b")
    client.get("https://x/c")  # third distinct entry evicts the LRU (a)
    assert client.stats().cache_evictions == 1

    client.get("https://x/a")  # a was evicted -> miss
    assert client.stats().cache_hits == 0
    assert len(calls) == 4  # all four were misses


def test_cache_key_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    client = ResilientHttpClient(source_name="t", cache_ttl_seconds=100, cache_max_entries=16)
    calls: list[str] = []

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return _ok()

    monkeypatch.setattr(client, "_perform_request", fake)

    client.get("https://x/q", params={"a": 1})
    client.get("https://x/q", params={"a": 2})  # different params -> distinct key
    assert len(calls) == 2
    client.get("https://x/q", params={"a": 1})  # same params -> cache hit
    assert len(calls) == 2


def test_cache_key_ignores_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    client = ResilientHttpClient(source_name="t", cache_ttl_seconds=100)
    calls: list[str] = []

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return _ok()

    monkeypatch.setattr(client, "_perform_request", fake)

    client.get("https://x/h", headers={"Authorization": "Bearer one"})
    client.get("https://x/h", headers={"Authorization": "Bearer two"})  # header not in key
    assert len(calls) == 1
    assert client.stats().cache_hits == 1


# -- retry / backoff ----------------------------------------------------------


def test_transient_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = ResilientHttpClient(source_name="t")
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([ConnectionError("1"), ConnectionError("2"), _ok()]),
    )

    resp = client.get("https://x/a")
    assert resp.status_code == 200
    stats = client.stats()
    assert stats.retries == 2  # two physical retries
    assert stats.requests == 1  # one logical request


def test_backoff_timing_matches_seeded_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    client = ResilientHttpClient(
        source_name="t",
        backoff_initial_seconds=0.5,
        backoff_factor=2.0,
        backoff_max_seconds=30.0,
        max_retries=3,
        rng=random.Random(1),
    )
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([ConnectionError(), ConnectionError(), ConnectionError(), _ok()]),
    )

    client.get("https://x/a")

    expected_rng = random.Random(1)
    expected = []
    for attempt in range(1, 4):
        base = min(0.5 * 2.0 ** (attempt - 1), 30.0)
        expected.append(base + expected_rng.uniform(0, 0.25 * base))

    assert len(sleeps) == 3
    for got, want in zip(sleeps, expected, strict=True):
        assert abs(got - want) < 1e-9


def test_permanent_error_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_sleep(_: float) -> None:
        raise AssertionError("permanent errors must not back off")

    monkeypatch.setattr(time, "sleep", no_sleep)
    client = ResilientHttpClient(source_name="t")
    monkeypatch.setattr(client, "_perform_request", _seq_transport([_ok(status=404, body=b"no")]))

    with pytest.raises(ResilientHttpError):
        client.get("https://x/a")
    assert client.stats().retries == 0


def test_ratelimit_uses_longer_backoff_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    client = ResilientHttpClient(
        source_name="t",
        backoff_initial_seconds=0.5,
        rng=random.Random(7),
    )
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([_ok(status=429, body=b"slow down"), _ok()]),
    )

    resp = client.get("https://x/a")
    assert resp.status_code == 200
    assert client.stats().retries == 1
    assert sleeps[0] >= 2 * 0.5  # ratelimit floor is >= 2x backoff_initial


def test_retry_exhaustion_raises_with_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = ResilientHttpClient(source_name="src", max_retries=3)
    monkeypatch.setattr(client, "_perform_request", _seq_transport([ConnectionError("x")] * 4))

    with pytest.raises(ResilientHttpError) as excinfo:
        client.get("https://x/a")

    err = excinfo.value
    assert isinstance(err.last_exception, ConnectionError)
    assert err.source_name == "src"
    assert err.attempts == 4  # max_retries=3 -> 4 total attempts
    assert client.stats().exhaustions == 1


# -- concurrency --------------------------------------------------------------


def test_concurrency_cap_serializes_in_batches() -> None:
    client = ResilientHttpClient(source_name="t", max_concurrency=2)
    lock = threading.Lock()
    state = {"current": 0, "max": 0}

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return _ok()

    client._perform_request = fake  # type: ignore[method-assign]

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(client.get, f"https://x/{i}") for i in range(4)]
        for future in futures:
            future.result()
    elapsed = time.perf_counter() - start

    # The cap is the load-bearing assertion: never more than 2 in flight.
    assert state["max"] == 2
    # ...and four 50ms calls in batches of two serialize to ~100ms, not ~50ms.
    assert elapsed >= 0.09
    assert elapsed < 0.5  # generous upper bound to stay non-flaky on slow CI


# -- proxy --------------------------------------------------------------------


def test_proxy_fallback_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = ProxyConfig(http_url="http://nope:8080", https_url="http://nope:8080")
    client = ResilientHttpClient(source_name="t", proxy=proxy)

    def fake(
        method: str, url: str, body: Any, headers: Any, *, proxy: ProxyConfig | None
    ) -> HttpResponse:
        if proxy is not None:
            raise ConnectionError("proxy dead")
        return _ok()

    monkeypatch.setattr(client, "_perform_request", fake)

    resp = client.get("https://x/a")
    assert resp.status_code == 200
    assert client.stats().proxy_fallbacks == 1


def test_proxy_config_from_env() -> None:
    assert ProxyConfig.from_env(env={}) is None

    cfg = ProxyConfig.from_env(
        env={
            "MARKET_ANALYSER_PROXY_HTTP_URL": "http://x:8080",
            "MARKET_ANALYSER_PROXY_HTTPS_URL": "https://y:8080",
        },
    )
    assert cfg is not None
    assert cfg.http_url == "http://x:8080"
    assert cfg.https_url == "https://y:8080"
    assert cfg.rotation_session_id is None

    cfg2 = ProxyConfig.from_env(
        env={
            "MARKET_ANALYSER_PROXY_HTTP_URL": "http://x:8080",
            "MARKET_ANALYSER_PROXY_HTTPS_URL": "https://y:8080",
            "MARKET_ANALYSER_PROXY_ROTATION_SESSION_ID": "sess-1",
        },
    )
    assert cfg2 is not None
    assert cfg2.rotation_session_id == "sess-1"


def test_proxy_config_from_env_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKET_ANALYSER_PROXY_HTTP_URL", raising=False)
    monkeypatch.delenv("MARKET_ANALYSER_PROXY_HTTPS_URL", raising=False)
    monkeypatch.delenv("MARKET_ANALYSER_PROXY_ROTATION_SESSION_ID", raising=False)
    assert ProxyConfig.from_env() is None

    monkeypatch.setenv("MARKET_ANALYSER_PROXY_HTTP_URL", "http://p:1")
    monkeypatch.setenv("MARKET_ANALYSER_PROXY_HTTPS_URL", "https://p:2")
    cfg = ProxyConfig.from_env()
    assert cfg is not None
    assert cfg.http_url == "http://p:1"


# -- secret hygiene & classifier extension ------------------------------------


def test_no_secret_leak_in_logs_or_stats(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = ResilientHttpClient(source_name="t", max_retries=1)
    monkeypatch.setattr(client, "_perform_request", _seq_transport([ConnectionError("net")] * 2))

    with caplog.at_level(logging.WARNING), pytest.raises(ResilientHttpError):
        client.get(
            "https://x/secret-path?token=abc",
            headers={"Authorization": "Bearer abc"},
        )

    assert "abc" not in caplog.text
    assert "abc" not in client.stats().model_dump_json()


def test_classifier_override_reclassifies_403_as_ratelimit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    class StockTwitsLike(ResilientHttpClient):
        def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
            if response is not None and response.status_code == 403:
                return ErrorKind.RATELIMIT
            return super().classify(exc, response)

    client = StockTwitsLike(
        source_name="st",
        backoff_initial_seconds=0.5,
        rng=random.Random(3),
    )
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([_ok(status=403, body=b"forbidden"), _ok()]),
    )

    resp = client.get("https://x/a")
    assert resp.status_code == 200
    assert client.stats().retries == 1  # default would have raised on 403
    assert sleeps[0] >= 2 * 0.5  # retried with the ratelimit floor


# -- transport edge cases -----------------------------------------------------


def test_expect_json_empty_body_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = ResilientHttpClient(source_name="t")
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([_ok(status=200, body=b""), _ok(status=200, body=b'{"ok": 1}')]),
    )

    resp = client.get("https://x/a", expect_json=True)
    assert resp.json() == {"ok": 1}
    assert client.stats().retries == 1


def test_dns_failure_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ResilientHttpClient(source_name="t")
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([urllib.error.URLError(socket.gaierror("name resolution"))]),
    )

    with pytest.raises(ResilientHttpError):
        client.get("https://nope/a")
    assert client.stats().retries == 0  # DNS failure does not retry


def test_non_dns_url_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = ResilientHttpClient(source_name="t", max_retries=1)
    monkeypatch.setattr(
        client,
        "_perform_request",
        _seq_transport([urllib.error.URLError("connection refused"), _ok()]),
    )

    resp = client.get("https://x/a")
    assert resp.status_code == 200
    assert client.stats().retries == 1


def test_http_response_json_and_text() -> None:
    resp = HttpResponse(status_code=200, headers={}, body=b'{"a": 1}', elapsed_seconds=0.0)
    assert resp.json() == {"a": 1}
    assert resp.text == '{"a": 1}'
