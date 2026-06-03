"""Plan 0032 phase 1 done-when (ADR-0038): the write-only API-key endpoints.

Asserted behaviors:
- `POST /settings/secret` sets a key, writes it to `secrets.json` at `0600`
  (POSIX), and returns the status map — never echoing the submitted value.
- `GET /settings/secrets` reports `"set"`/`"unset"` per key, never a value.
- The submitted value appears in no response body of either endpoint.
- Both endpoints reject the MCP bearer with 401 (cross-tenant: an agent cannot
  manage the renderer's third-party keys) and reject a missing bearer with 401.
- An unknown key is rejected at the boundary with 422.
"""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
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
from market_analyser.persistence.secrets import SECRETS_FILENAME, SecretsStore

RENDERER_SECRET = "renderer-test-secret"
ZERION_KEY = "zk_live_supersecret_value_0123456789"


class _FakeProvider:
    """Secrets tests don't exercise the data path; bodies are never invoked."""

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
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
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
def secrets_path(tmp_path: Path) -> Path:
    return tmp_path / SECRETS_FILENAME


@pytest.fixture
def secrets_store(secrets_path: Path) -> SecretsStore:
    # Empty environ so the file is the only source — tests control persistence.
    return SecretsStore(secrets_path, environ={})


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


@pytest.fixture
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    secrets_store: SecretsStore,
    annotations_repo: AnnotationsRepository,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        secrets_store=secrets_store,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def test_post_set_secret_persists_and_returns_status_without_value(client: TestClient) -> None:
    response = client.post(
        "/settings/secret",
        json={"key": "zerion_api_key", "value": ZERION_KEY},
        headers=_renderer_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["zerion_api_key"] == "set"
    # Write-only contract: the submitted value must not be echoed back anywhere.
    assert ZERION_KEY not in response.text


def test_post_set_secret_writes_file_at_0600(client: TestClient, secrets_path: Path) -> None:
    response = client.post(
        "/settings/secret",
        json={"key": "zerion_api_key", "value": ZERION_KEY},
        headers=_renderer_headers(),
    )
    assert response.status_code == 200
    on_disk = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert on_disk == {"zerion_api_key": ZERION_KEY}
    if sys.platform != "win32":
        mode_bits = stat.S_IMODE(secrets_path.stat().st_mode)
        assert mode_bits == 0o600, f"expected 0600, got {oct(mode_bits)}"


def test_get_status_reports_set_after_post_without_value(client: TestClient) -> None:
    client.post(
        "/settings/secret",
        json={"key": "zerion_api_key", "value": ZERION_KEY},
        headers=_renderer_headers(),
    )
    response = client.get("/settings/secrets", headers=_renderer_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["zerion_api_key"] == "set"
    assert body["graph_api_key"] == "unset"
    assert ZERION_KEY not in response.text


def test_get_status_all_unset_before_any_set(client: TestClient) -> None:
    response = client.get("/settings/secrets", headers=_renderer_headers())
    assert response.status_code == 200
    assert all(value == "unset" for value in response.json().values())


def test_post_set_secret_rejects_unknown_key(client: TestClient) -> None:
    response = client.post(
        "/settings/secret",
        json={"key": "not_a_real_key", "value": "x"},
        headers=_renderer_headers(),
    )
    assert response.status_code == 422


def test_post_set_secret_rejects_empty_value(client: TestClient) -> None:
    response = client.post(
        "/settings/secret",
        json={"key": "zerion_api_key", "value": ""},
        headers=_renderer_headers(),
    )
    assert response.status_code == 422


def test_get_status_rejects_missing_bearer(client: TestClient) -> None:
    assert client.get("/settings/secrets").status_code == 401


def test_post_set_secret_rejects_missing_bearer(client: TestClient) -> None:
    response = client.post(
        "/settings/secret",
        json={"key": "zerion_api_key", "value": ZERION_KEY},
    )
    assert response.status_code == 401


def test_get_status_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: an agent on /mcp cannot read the renderer's key status."""
    response = client.get("/settings/secrets", headers=_mcp_headers(mcp_secret))
    assert response.status_code == 401


def test_post_set_secret_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: an agent on /mcp cannot set the renderer's third-party keys."""
    response = client.post(
        "/settings/secret",
        json={"key": "zerion_api_key", "value": ZERION_KEY},
        headers=_mcp_headers(mcp_secret),
    )
    assert response.status_code == 401
