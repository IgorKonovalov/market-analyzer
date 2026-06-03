"""Plan 0007 phase 4.1 done-when: the shared data directory is contractual.

Defends ADR-0020:

- `default_app_data_dir()` follows one canonical algorithm per platform branch,
  and the literal `"market-analyser"` is the dirname under every platform.
- `MARKET_ANALYSER_DATA_DIR` is taken verbatim (no suffix appended).
- `GET /healthz` discloses `data_dir` only when the request carries the
  renderer bearer — the unauthenticated probe stays `{ok, version}` and the
  MCP bearer (cross-tenant) cannot read the field either.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.config import APP_DIRNAME, default_app_data_dir

RENDERER_SECRET = "renderer-test-secret"
MCP_SECRET = "mcp-test-secret-distinct-from-renderer"


@pytest.fixture
def client_without_mcp() -> TestClient:
    """Bare app — no MCP secret, no annotations dependency."""
    return TestClient(create_app(secret=RENDERER_SECRET))


def test_windows_branch_uses_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    monkeypatch.delenv("MARKET_ANALYSER_DATA_DIR", raising=False)
    result = default_app_data_dir()
    assert result.name == APP_DIRNAME
    assert result == Path(r"C:\Users\test\AppData\Roaming") / APP_DIRNAME


def test_windows_branch_falls_back_to_home_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("MARKET_ANALYSER_DATA_DIR", raising=False)
    result = default_app_data_dir()
    assert result.name == APP_DIRNAME
    assert "AppData" in str(result) and "Roaming" in str(result)


def test_darwin_branch_uses_application_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("MARKET_ANALYSER_DATA_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: Path("/Users/test"))
    result = default_app_data_dir()
    assert result.name == APP_DIRNAME
    assert result == Path("/Users/test/Library/Application Support") / APP_DIRNAME


def test_linux_branch_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/test/.local/share")
    monkeypatch.delenv("MARKET_ANALYSER_DATA_DIR", raising=False)
    result = default_app_data_dir()
    assert result.name == APP_DIRNAME
    assert result == Path("/home/test/.local/share") / APP_DIRNAME


def test_linux_branch_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("MARKET_ANALYSER_DATA_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: Path("/home/test"))
    result = default_app_data_dir()
    assert result.name == APP_DIRNAME
    assert result == Path("/home/test/.local/share") / APP_DIRNAME


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_literal_market_analyser_appears_under_every_platform(
    platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory name is the contract — never derived. ADR-0020."""
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.delenv("MARKET_ANALYSER_DATA_DIR", raising=False)
    if platform == "win32":
        monkeypatch.setenv("APPDATA", r"C:\Users\anyone\AppData\Roaming")
    elif platform == "linux":
        monkeypatch.setenv("XDG_DATA_HOME", "/anywhere")
    result = default_app_data_dir()
    assert str(result).endswith(APP_DIRNAME)
    assert APP_DIRNAME == "market-analyser"  # locks the contract value too


def test_env_var_override_is_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """MARKET_ANALYSER_DATA_DIR is taken as-is. No suffix appended."""
    monkeypatch.setenv("MARKET_ANALYSER_DATA_DIR", "/tmp/foo")
    result = default_app_data_dir()
    assert result == Path("/tmp/foo")


def test_env_var_override_wins_over_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    monkeypatch.setenv("MARKET_ANALYSER_DATA_DIR", "/tmp/override-wins")
    result = default_app_data_dir()
    assert result == Path("/tmp/override-wins")


def test_healthz_without_bearer_omits_data_dir(client_without_mcp: TestClient) -> None:
    response = client_without_mcp.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "version" in body
    assert "data_dir" not in body


def test_healthz_with_renderer_bearer_includes_data_dir(
    client_without_mcp: TestClient,
) -> None:
    response = client_without_mcp.get(
        "/healthz", headers={"Authorization": f"Bearer {RENDERER_SECRET}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data_dir"] == str(default_app_data_dir())


def test_healthz_with_wrong_bearer_omits_data_dir(client_without_mcp: TestClient) -> None:
    response = client_without_mcp.get(
        "/healthz", headers={"Authorization": "Bearer not-the-renderer-bearer"}
    )
    assert response.status_code == 200
    assert "data_dir" not in response.json()


def test_healthz_with_mcp_bearer_omits_data_dir(tmp_path: Path) -> None:
    """Cross-tenant: an agent on the MCP bearer must not learn the data dir.

    The `data_dir` disclosure is renderer-only — `/healthz` with the MCP bearer
    looks (to this route) like an arbitrary non-renderer-bearer request, since
    the route compares only against the renderer secret via `compare_digest`.
    """
    from collections.abc import Sequence
    from datetime import datetime

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

    class _Stub:
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

    mcp_secret = load_or_generate_mcp_secret(tmp_path / "mcp-secret.json")
    engine = make_engine(":memory:")
    apply_migrations(engine)
    annotations_repo = AnnotationsRepository(make_session_factory(engine))
    try:
        app = create_app(
            secret=RENDERER_SECRET,
            mcp_secret=mcp_secret,
            mcp_secret_path=tmp_path / "mcp-secret.json",
            provider=_Stub(),
            annotations_repository=annotations_repo,
        )
        with TestClient(app) as client:
            response = client.get("/healthz", headers={"Authorization": f"Bearer {mcp_secret}"})
            assert response.status_code == 200
            assert "data_dir" not in response.json()
    finally:
        engine.dispose()
