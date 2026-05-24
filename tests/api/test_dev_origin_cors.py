"""Dev-mode CORS gating.

In `pnpm --filter desktop dev`, the renderer is cross-origin from the sidecar
(Vite at http://localhost:5173 → sidecar at 127.0.0.1:<port>), so every
authenticated fetch fires an OPTIONS preflight. Without CORS middleware, the
bearer middleware 401s the preflight and the browser aborts the fetch.

`create_app(dev_origin=...)` installs `CORSMiddleware` for that one origin so
the preflight is short-circuited before bearer auth sees it. Packaged builds
pass `dev_origin=None` and behave exactly as before — no CORS headers leaked
and OPTIONS still 401s.

These tests defend the contract from both sides (with-dev-origin and
without), the `/mcp` raw-Route registration's interaction with CORSMiddleware,
and the `--dev-origin` argparse validator that keeps prod from accidentally
opening the sidecar to a non-loopback origin.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_analyser.api.__main__ import _dev_origin, _parse_args
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.types import (
    Bar,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
DEV_ORIGIN = "http://localhost:5173"


class _FakeProvider:
    """Stub provider — CORS tests don't touch the data path beyond what the
    standard fixtures need. Returning [] keeps GET /ohlcv on the happy path."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return []

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


@pytest.fixture
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


def _make_app(
    *,
    dev_origin: str | None,
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        dev_origin=dev_origin,
    )


# ---------------------------------------------------------------------------
# Behaviour WITH dev_origin set: CORS middleware installs and preflight passes
# ---------------------------------------------------------------------------


def test_options_ohlcv_preflight_succeeds_with_dev_origin(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> None:
    """The browser preflight for GET /ohlcv must return 200 + ACAO when dev_origin is set."""
    app = _make_app(
        dev_origin=DEV_ORIGIN,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
    )
    with TestClient(app) as client:
        response = client.options(
            "/ohlcv",
            headers={
                "Origin": DEV_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == DEV_ORIGIN
    allowed_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allowed_headers


def test_options_mcp_preflight_succeeds_with_dev_origin(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> None:
    """The /mcp route is appended as a raw Starlette Route, not a FastAPI router —
    verify CORSMiddleware still wraps it. Without this, the renderer's MCP-side
    health probe (if any) and any cross-origin tool calls would silently 401."""
    app = _make_app(
        dev_origin=DEV_ORIGIN,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
    )
    with TestClient(app) as client:
        response = client.options(
            "/mcp",
            headers={
                "Origin": DEV_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == DEV_ORIGIN


def test_get_ohlcv_carries_acao_header_with_dev_origin(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> None:
    """After the preflight, the actual GET response must also carry ACAO so the
    browser hands the body to the fetch caller. The stub provider returns [],
    so the body itself is an empty list — the assertion is the header + 200."""
    app = _make_app(
        dev_origin=DEV_ORIGIN,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
    )
    with TestClient(app) as client:
        response = client.get(
            "/ohlcv",
            params={
                "symbol": "AAPL",
                "timeframe": "1d",
                "start": "2026-04-01T00:00:00+00:00",
                "end": "2026-05-01T00:00:00+00:00",
            },
            headers={
                "Origin": DEV_ORIGIN,
                "Authorization": f"Bearer {RENDERER_SECRET}",
            },
        )
    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == DEV_ORIGIN


# ---------------------------------------------------------------------------
# Behaviour WITHOUT dev_origin: prod parity preserved
# ---------------------------------------------------------------------------


def test_options_ohlcv_preflight_returns_401_without_dev_origin(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> None:
    """Without dev_origin, no CORS middleware — OPTIONS hits bearer first and
    401s. This is the pre-bugfix behaviour, preserved verbatim for prod."""
    app = _make_app(
        dev_origin=None,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
    )
    with TestClient(app) as client:
        response = client.options(
            "/ohlcv",
            headers={
                "Origin": DEV_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


def test_get_ohlcv_carries_no_acao_header_without_dev_origin(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
) -> None:
    """Even on the success path, prod must not leak CORS headers — they would
    advertise the sidecar to any origin that probes from a packaged build."""
    app = _make_app(
        dev_origin=None,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
    )
    with TestClient(app) as client:
        response = client.get(
            "/ohlcv",
            params={
                "symbol": "AAPL",
                "timeframe": "1d",
                "start": "2026-04-01T00:00:00+00:00",
                "end": "2026-05-01T00:00:00+00:00",
            },
            headers={"Authorization": f"Bearer {RENDERER_SECRET}"},
        )
    assert response.status_code == 200, response.text
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Argparse validator: refuses anything not loopback http
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com",
        "https://localhost:5173",  # https is not allowed (the dev script is plain http)
        "http://0.0.0.0:5173",  # binds-all is not loopback
        "http://evil.com:5173",
        "*",
        "",
        "http://localhost",  # missing port
        "http://localhost:",  # empty port
        "http://localhost:5173/",  # trailing slash — Vite gives a bare origin
        "ftp://localhost:5173",
    ],
)
def test_dev_origin_validator_rejects_non_loopback_http(raw: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _dev_origin(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "http://localhost:5173",
        "http://localhost:1",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
    ],
)
def test_dev_origin_validator_accepts_loopback_http(raw: str) -> None:
    assert _dev_origin(raw) == raw


def test_parse_args_threads_dev_origin_through() -> None:
    """End-to-end on the argparse layer: --port + --dev-origin parse together
    and the value lands on the namespace."""
    args = _parse_args(["--port=0", "--dev-origin=http://localhost:5173"])
    assert args.port == 0
    assert args.dev_origin == "http://localhost:5173"


def test_parse_args_dev_origin_default_is_none() -> None:
    """Absent --dev-origin (the packaged-build case) leaves the field None."""
    args = _parse_args(["--port=0"])
    assert args.dev_origin is None
