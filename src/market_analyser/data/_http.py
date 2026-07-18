"""Shared resilience layer for every external HTTP adapter (ADR-0019).

`ResilientHttpClient` wraps stdlib `urllib.request` with four cross-cutting
behaviours so each Tier-2 adapter does not re-invent them:

1. **Short-lived in-memory TTL cache** (LRU-bounded) for results that go stale
   fast — distinct from the SQLite `bars` cache, which is cross-session.
2. **Transient-error classification + exponential backoff retry**, with a longer
   floor for rate-limit (HTTP 429) responses.
3. **Bounded concurrency** via a per-instance semaphore, so a fanned-out MCP
   call cannot stampede an upstream's politeness budget.
4. **Proxy configuration from environment only** (`ProxyConfig.from_env`), with a
   per-request proxy→direct fallback.

One instance is constructed per source (per adapter); each source owns its own
cache lifetime, concurrency budget, and quirk-classifier. The module is
package-internal (underscore prefix): downstream code reaches the data layer
through the `MarketDataProvider` Protocol, never this client directly.

The single physical-attempt seam is `_perform_request`; tests monkeypatch it to
simulate transport behaviour offline. Jitter is drawn from a constructor-injected
`random.Random`, so seeded tests get reproducible backoff timings.
"""

from __future__ import annotations

import json as json_lib
import logging
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from market_analyser import __version__

_logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = f"market-analyser/{__version__}"

# Env-var bundle read by ProxyConfig.from_env(). Documented in ADR-0019.
_ENV_PROXY_HTTP = "MARKET_ANALYSER_PROXY_HTTP_URL"
_ENV_PROXY_HTTPS = "MARKET_ANALYSER_PROXY_HTTPS_URL"
_ENV_PROXY_ROTATION = "MARKET_ANALYSER_PROXY_ROTATION_SESSION_ID"


class ErrorKind(StrEnum):
    """How a failed attempt should be handled."""

    TRANSIENT = "transient"  # retry with backoff
    PERMANENT = "permanent"  # raise to caller immediately
    RATELIMIT = "ratelimit"  # retry with backoff, longer initial floor


@dataclass(frozen=True)
class HttpResponse:
    """A buffered HTTP response. Returned for any status the transport produced
    (including 4xx/5xx) — the retry layer decides what to do via `classify`.

    A frozen dataclass rather than a pydantic model: this is a package-internal
    carrier (no boundary validation needed) and `pydantic.BaseModel` already
    reserves a `.json()` method with an incompatible signature.
    """

    status_code: int
    headers: dict[str, str]
    body: bytes
    elapsed_seconds: float

    def json(self) -> Any:
        """Parse the body as JSON. Raises `json.JSONDecodeError` on bad bodies."""
        return json_lib.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class ProxyConfig(BaseModel):
    """Proxy endpoints, constructed via `from_env()` — never hand-built in code,
    so no proxy credentials are ever committed."""

    model_config = ConfigDict(frozen=True)

    http_url: str
    https_url: str
    rotation_session_id: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProxyConfig | None:
        """Read the proxy bundle from the environment. Returns `None` (direct-only)
        unless both the HTTP and HTTPS proxy URLs are set."""
        source = env if env is not None else os.environ
        http_url = source.get(_ENV_PROXY_HTTP)
        https_url = source.get(_ENV_PROXY_HTTPS)
        if not http_url or not https_url:
            return None
        return cls(
            http_url=http_url,
            https_url=https_url,
            rotation_session_id=source.get(_ENV_PROXY_ROTATION),
        )


class HttpClientStats(BaseModel):
    """Per-instance telemetry snapshot. Carries no header or body values."""

    model_config = ConfigDict(frozen=True)

    requests: int
    cache_hits: int
    retries: int
    exhaustions: int
    cache_evictions: int
    proxy_fallbacks: int


class ResilientHttpError(Exception):
    """Raised when a logical request fails permanently or exhausts its retries."""

    def __init__(
        self,
        *,
        source_name: str,
        last_response: HttpResponse | None,
        last_exception: BaseException | None,
        attempts: int,
    ) -> None:
        self.source_name = source_name
        self.last_response = last_response
        self.last_exception = last_exception
        self.attempts = attempts
        status = last_response.status_code if last_response is not None else None
        exc_name = type(last_exception).__name__ if last_exception is not None else None
        super().__init__(
            f"{source_name}: request failed after {attempts} attempt(s) "
            f"(last_status={status}, last_exception={exc_name})",
        )


class ResilientHttpClient:
    """A blocking HTTP client with TTL cache, retry, backoff, and concurrency cap.

    Thread-safe and re-entrant: a single instance is shared across the threads
    that an MCP fan-out may spawn. Cache, retry policy, and concurrency cap are
    fixed at construction.
    """

    def __init__(
        self,
        *,
        source_name: str,
        cache_ttl_seconds: float = 0.0,
        cache_max_entries: int = 256,
        max_retries: int = 3,
        backoff_initial_seconds: float = 0.5,
        backoff_factor: float = 2.0,
        backoff_max_seconds: float = 30.0,
        max_concurrency: int = 4,
        request_timeout_seconds: float = 10.0,
        user_agent: str = _DEFAULT_USER_AGENT,
        proxy: ProxyConfig | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if cache_max_entries < 1:
            raise ValueError("cache_max_entries must be >= 1")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._source_name = source_name
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._max_retries = max_retries
        self._backoff_initial = backoff_initial_seconds
        self._backoff_factor = backoff_factor
        self._backoff_max = backoff_max_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._user_agent = user_agent
        self._proxy = proxy
        self._rng = rng if rng is not None else random.Random()

        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrency)
        self._cache: OrderedDict[str, tuple[float, HttpResponse]] = OrderedDict()

        # Counters guarded by `self._lock`; never mutated while holding it for
        # an IO/sleep, so contention stays trivial.
        self._requests = 0
        self._cache_hits = 0
        self._retries = 0
        self._exhaustions = 0
        self._cache_evictions = 0
        self._proxy_fallbacks = 0

    # -- public API ---------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_key: str | None = None,
        expect_json: bool = False,
    ) -> HttpResponse:
        return self._request(
            "GET",
            url,
            params=params,
            headers=headers,
            body=None,
            cache_key=cache_key,
            expect_json=expect_json,
        )

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        data: bytes | None = None,
        params: Mapping[str, str | int | float] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_key: str | None = None,
        expect_json: bool = False,
    ) -> HttpResponse:
        # `json=` JSON-encodes a mapping; `data=` sends raw bytes verbatim (the form
        # body an OAuth2 token endpoint needs). They are mutually exclusive — passing
        # both is a caller bug, not a body to guess between.
        if json is not None and data is not None:
            raise ValueError("post() accepts json= or data=, not both")
        body: bytes | None = None
        merged_headers: dict[str, str] = dict(headers or {})
        if json is not None:
            body = json_lib.dumps(json, sort_keys=True, separators=(",", ":")).encode("utf-8")
            merged_headers.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = data
            merged_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        # The default cache key (method, url, params) ignores the request body;
        # body-sensitive POST caching must pass an explicit cache_key.
        return self._request(
            "POST",
            url,
            params=params,
            headers=merged_headers,
            body=body,
            cache_key=cache_key,
            expect_json=expect_json,
        )

    def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
        """Default transient/permanent/ratelimit split. Subclasses override to
        teach the client an upstream's quirks (e.g. StockTwits' 403-as-rate-limit)."""
        if response is not None:
            sc = response.status_code
            if sc == 429:
                return ErrorKind.RATELIMIT
            if sc == 408 or 500 <= sc <= 599:
                return ErrorKind.TRANSIENT
            if 400 <= sc < 500:
                return ErrorKind.PERMANENT
            return ErrorKind.PERMANENT
        if exc is not None:
            if isinstance(exc, urllib.error.HTTPError):  # defensive — normally a response
                return self.classify(None, _http_error_to_response(exc, 0.0))
            if isinstance(exc, urllib.error.URLError):
                if isinstance(exc.reason, socket.gaierror):
                    return ErrorKind.PERMANENT  # DNS failure
                return ErrorKind.TRANSIENT
            if isinstance(exc, (ConnectionError, TimeoutError, json_lib.JSONDecodeError)):
                return ErrorKind.TRANSIENT
            return ErrorKind.PERMANENT
        return ErrorKind.PERMANENT

    def stats(self) -> HttpClientStats:
        with self._lock:
            return HttpClientStats(
                requests=self._requests,
                cache_hits=self._cache_hits,
                retries=self._retries,
                exhaustions=self._exhaustions,
                cache_evictions=self._cache_evictions,
                proxy_fallbacks=self._proxy_fallbacks,
            )

    # -- request pipeline ---------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int | float] | None,
        headers: Mapping[str, str] | None,
        body: bytes | None,
        cache_key: str | None,
        expect_json: bool,
    ) -> HttpResponse:
        key = self._make_key(method, url, params, cache_key)
        if self._cache_ttl_seconds > 0:
            cached = self._cache_lookup(key)
            if cached is not None:
                with self._lock:
                    self._cache_hits += 1
                return cached

        final_url = self._build_url(url, params)
        with self._semaphore:
            with self._lock:
                self._requests += 1
            attempt = 0
            last_exc: BaseException | None = None
            last_resp: HttpResponse | None = None
            while True:
                attempt += 1
                kind: ErrorKind | None = None
                try:
                    resp = self._attempt(method, final_url, body, headers)
                except Exception as exc:  # classified below, then re-raised wrapped
                    last_exc = exc
                    kind = self.classify(exc, None)
                else:
                    if 200 <= resp.status_code < 400:
                        if expect_json and not _is_valid_json(resp):
                            # A 2xx with a missing/garbled JSON body is a transient
                            # upstream hiccup, not a status the classifier sees.
                            last_resp = resp
                            kind = ErrorKind.TRANSIENT
                        else:
                            if self._cache_ttl_seconds > 0:
                                self._cache_store(key, resp)
                            return resp
                    else:
                        last_resp = resp
                        kind = self.classify(None, resp)

                if kind is ErrorKind.PERMANENT or attempt >= self._max_retries + 1:
                    if kind is not ErrorKind.PERMANENT:
                        with self._lock:
                            self._exhaustions += 1
                    self._log_failure(method, url, last_resp, last_exc, attempt)
                    raise ResilientHttpError(
                        source_name=self._source_name,
                        last_response=last_resp,
                        last_exception=last_exc,
                        attempts=attempt,
                    )

                with self._lock:
                    self._retries += 1
                time.sleep(self._backoff(attempt, kind))

    def _attempt(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
    ) -> HttpResponse:
        """One logical attempt, including the proxy→direct fallback. Raises on a
        transport error (no HTTP response); returns the `HttpResponse` otherwise."""
        if self._proxy is None:
            return self._perform_request(method, url, body, headers, proxy=None)
        try:
            return self._perform_request(method, url, body, headers, proxy=self._proxy)
        except (ConnectionError, urllib.error.URLError, TimeoutError):
            # Proxy died before producing an HTTP response — fall back to direct.
            with self._lock:
                self._proxy_fallbacks += 1
            return self._perform_request(method, url, body, headers, proxy=None)

    def _perform_request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        *,
        proxy: ProxyConfig | None,
    ) -> HttpResponse:
        """Single physical HTTP attempt via stdlib urllib.

        This is the transport seam: tests monkeypatch it to drive cache/retry/
        backoff/proxy logic offline. HTTP error statuses (4xx/5xx) are returned
        as an `HttpResponse`; only transport-level failures raise.
        """
        request_headers = {"User-Agent": self._user_agent, **(headers or {})}
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        if proxy is not None:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy.http_url, "https": proxy.https_url}),
            )
        else:
            opener = urllib.request.build_opener()

        started = time.monotonic()
        try:
            with opener.open(request, timeout=self._request_timeout_seconds) as response:
                payload = response.read()
                return HttpResponse(
                    status_code=response.status,
                    headers={k: str(v) for k, v in response.headers.items()},
                    body=payload,
                    elapsed_seconds=time.monotonic() - started,
                )
        except urllib.error.HTTPError as exc:
            return _http_error_to_response(exc, time.monotonic() - started)

    # -- cache --------------------------------------------------------------

    def _make_key(
        self,
        method: str,
        url: str,
        params: Mapping[str, str | int | float] | None,
        cache_key: str | None,
    ) -> str:
        canonical = "&".join(f"{k}={params[k]}" for k in sorted(params)) if params else ""
        # Headers are deliberately excluded so auth tokens never reach the key.
        return "\x1f".join((method, url, canonical, cache_key or ""))

    def _cache_lookup(self, key: str) -> HttpResponse | None:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            inserted_at, response = entry
            if now - inserted_at >= self._cache_ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return response

    def _cache_store(self, key: str, response: HttpResponse) -> None:
        now = time.monotonic()
        with self._lock:
            self._cache[key] = (now, response)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)
                self._cache_evictions += 1

    # -- backoff & logging --------------------------------------------------

    def _backoff(self, attempt: int, kind: ErrorKind) -> float:
        base = self._backoff_initial * (self._backoff_factor ** (attempt - 1))
        if kind is ErrorKind.RATELIMIT:
            base = max(base, 2 * self._backoff_initial)
        base = min(base, self._backoff_max)
        return base + self._rng.uniform(0, 0.25 * base)

    def _log_failure(
        self,
        method: str,
        url: str,
        last_resp: HttpResponse | None,
        last_exc: BaseException | None,
        attempts: int,
    ) -> None:
        # Path only — query string and headers are never logged (no secret leak).
        path = urllib.parse.urlsplit(url).path
        status = last_resp.status_code if last_resp is not None else None
        _logger.warning(
            "%s %s %s failed after %d attempt(s) (status=%s, exc=%s)",
            self._source_name,
            method,
            path,
            attempts,
            status,
            type(last_exc).__name__ if last_exc is not None else None,
        )

    def _build_url(self, url: str, params: Mapping[str, str | int | float] | None) -> str:
        if not params:
            return url
        query = urllib.parse.urlencode({k: str(v) for k, v in params.items()})
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{query}"


def _is_valid_json(response: HttpResponse) -> bool:
    if not response.body:
        return False
    try:
        json_lib.loads(response.body)
    except (json_lib.JSONDecodeError, ValueError):
        return False
    return True


def _http_error_to_response(exc: urllib.error.HTTPError, elapsed: float) -> HttpResponse:
    try:
        payload = exc.read()
    except Exception:  # a body-less HTTPError still carries a usable status code
        payload = b""
    return HttpResponse(
        status_code=exc.code,
        headers={k: str(v) for k, v in (exc.headers or {}).items()},
        body=payload,
        elapsed_seconds=elapsed,
    )


__all__ = [
    "ErrorKind",
    "HttpClientStats",
    "HttpResponse",
    "ProxyConfig",
    "ResilientHttpClient",
    "ResilientHttpError",
]
