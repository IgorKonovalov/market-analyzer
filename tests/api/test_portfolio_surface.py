"""Plan 0043 phase 1: the `GET /portfolio` + `POST /portfolio/risk` renderer surface.

The done-when, at the assertion level:

(a) the renderer can obtain the cross-venue `PortfolioSummary` — holdings, P&L,
    exposure — with **each leg carrying its own as-of** (freshness never blended,
    the ADR-0042 negative-consequence mitigation the UI must preserve);
(b) the renderer can request a DeFi risk recompute — `kind="scenario"` returns the
    shock response (impermanent loss / liquidation distance), `kind="conditional"`
    returns a probability **with its volatility assumption inline** (ADR-0037);
(c) inputs are validated at the boundary (a non-address wallet, a leg-less risk body
    → typed 422, never a 500);
(d) both routes are renderer-bearer-gated (missing bearer → 401; the MCP bearer is
    rejected cross-tenant), and the surface is absent without an account source.

Fakes are injected via `create_app(...)`; no network is touched.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.errors import UnknownSymbolError
from market_analyser.data.types import AccountHoldings, Quote, SpotBalance
from market_analyser.defi.models import DefiPosition, PositionToken
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
_WALLET = "0x" + "ab" * 20
_BINANCE_AS_OF = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
_QUOTE_AS_OF = datetime(2026, 7, 6, 11, 59, tzinfo=UTC)
_MANUAL_AS_OF = "2026-07-01T00:00:00Z"


class _FakeAccountSource:
    def __init__(self, holdings: AccountHoldings) -> None:
        self._holdings = holdings

    def fetch_account_holdings(self) -> AccountHoldings:
        return self._holdings


class _FakeWalletSource:
    def __init__(self, positions: list[DefiPosition]) -> None:
        self._positions = positions

    def fetch_positions(self, address: str) -> list[DefiPosition]:
        return self._positions


class _QuoteProvider:
    """Only `get_quote` is exercised by the portfolio path; a symbol outside the
    table is honestly unquotable."""

    def __init__(self, quotes: dict[str, float]) -> None:
        self._quotes = quotes

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        if symbol not in self._quotes:
            raise UnknownSymbolError(f"no quote for {symbol}", symbol=symbol)
        return Quote(symbol=symbol, price=self._quotes[symbol], as_of=_QUOTE_AS_OF, source="yahoo")


def _account() -> AccountHoldings:
    return AccountHoldings(
        venue="binance",
        spot=[SpotBalance(asset="BTC", free=0.5, locked=0.0)],
        futures=[],
        as_of=_BINANCE_AS_OF,
    )


def _lp_position() -> DefiPosition:
    return DefiPosition(
        position_id="base:aerodrome:lp-1",
        chain="base",
        protocol="aerodrome",
        kind="lp",
        tokens=[PositionToken(symbol="USDC", address="0xusdc", amount=2_500.0)],
        usd_value=5_000.0,
        pool="vAMM-WETH/USDC",
        pool_address="0xpool",
    )


def _write_manual(tmp_path: Path) -> Path:
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "as_of": _MANUAL_AS_OF,
                "positions": [{"symbol": "AAPL", "quantity": 100, "avg_cost": 185.5}],
            },
        ),
        encoding="utf-8",
    )
    return path


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


def _build_app(
    *,
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    manual_path: Path,
    account_sources: dict[str, _FakeAccountSource] | None = None,
    wallet_sources: dict[str, _FakeWalletSource] | None = None,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        account_holdings_sources=(
            account_sources
            if account_sources is not None
            else {"binance": _FakeAccountSource(_account())}
        ),
        wallet_positions_sources=wallet_sources or {},
        manual_positions_path=manual_path,
        provider=_QuoteProvider({"BTC-USD": 61_000.0, "AAPL": 200.0}),  # type: ignore[arg-type]
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    tmp_path: Path,
) -> FastAPI:
    return _build_app(
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
        manual_path=_write_manual(tmp_path),
        wallet_sources={"zerion": _FakeWalletSource([_lp_position()])},
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


# --- (a) obtain the summary; per-leg as-of is not blended ----------------------------


def test_get_portfolio_returns_summary_with_unblended_per_leg_as_of(client: TestClient) -> None:
    response = client.get("/portfolio", headers=_renderer_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] is None
    assert body["leg_errors"] == {}
    summary = body["summary"]
    by_key = {(h["venue"], h["symbol"]): h for h in summary["holdings"]}
    assert ("binance", "BTC") in by_key
    assert ("manual", "AAPL") in by_key
    # Each leg keeps its own as-of stamp — never collapsed into one blended "now".
    legs = summary["legs_as_of"]
    assert legs["binance"] == _BINANCE_AS_OF.isoformat().replace("+00:00", "Z")
    assert legs["manual"] == _MANUAL_AS_OF
    assert legs["binance"] != legs["manual"]
    # Every valuation names its pricing reference (paired provenance).
    for holding in summary["holdings"]:
        assert (holding["usd_value"] is None) == (holding["pricing_source"] is None)


def test_get_portfolio_with_wallet_switches_the_defi_leg_on(client: TestClient) -> None:
    response = client.get("/portfolio", params={"wallet": _WALLET}, headers=_renderer_headers())
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    assert "defi" in summary["legs_as_of"]
    assert any(h["venue"] == "defi" for h in summary["holdings"])


# --- (b) request a risk recompute ----------------------------------------------------


def test_risk_scenario_returns_the_supplied_lp_shock_response(client: TestClient) -> None:
    response = client.post(
        "/portfolio/risk",
        json={
            "kind": "scenario",
            "lp": {
                "amount0": 1.0,
                "price0": 3_000.0,
                "shock0": -0.3,
                "amount1": 3_000.0,
                "price1": 1.0,
                "shock1": 0.0,
            },
        },
        headers=_renderer_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "scenario"
    assert body["lp"]["impermanent_loss"] is not None
    assert body["lp"]["value_before"] is not None


def test_risk_conditional_carries_its_volatility_assumption_inline(client: TestClient) -> None:
    response = client.post(
        "/portfolio/risk",
        json={
            "kind": "conditional",
            "lp": {"ratio_log_returns": [0.01, -0.02, 0.015, -0.01, 0.005, -0.008]},
        },
        headers=_renderer_headers(),
    )
    assert response.status_code == 200, response.text
    lp = response.json()["lp"]
    # The probability panel must never show a bare number — the vol assumption travels
    # with it (ADR-0037 invariant 3), and it is reproducible from a seed.
    assert lp["assumption"]
    assert lp["daily_vol"] is not None
    assert "seed" in lp


# --- (c) boundary validation ---------------------------------------------------------


def test_invalid_wallet_is_typed_422_not_500(client: TestClient) -> None:
    response = client.get(
        "/portfolio", params={"wallet": "not-an-address"}, headers=_renderer_headers()
    )
    assert response.status_code == 422


def test_risk_with_no_leg_is_typed_422_not_500(client: TestClient) -> None:
    response = client.post(
        "/portfolio/risk", json={"kind": "scenario"}, headers=_renderer_headers()
    )
    assert response.status_code == 422


# --- (d) auth + mount gating ---------------------------------------------------------


def test_get_portfolio_rejects_missing_bearer(client: TestClient) -> None:
    assert client.get("/portfolio").status_code == 401


def test_risk_rejects_missing_bearer(client: TestClient) -> None:
    assert client.post("/portfolio/risk", json={"kind": "scenario"}).status_code == 401


def test_get_portfolio_rejects_mcp_bearer_cross_tenant(client: TestClient, mcp_secret: str) -> None:
    response = client.get("/portfolio", headers={"Authorization": f"Bearer {mcp_secret}"})
    assert response.status_code == 401


def test_surface_absent_without_an_account_source(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    tmp_path: Path,
) -> None:
    """No account source wired → the router is not mounted (404, not 503/500)."""
    app = _build_app(
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        annotations_repo=annotations_repo,
        manual_path=_write_manual(tmp_path),
        account_sources={},  # explicitly empty
    )
    with TestClient(app) as client:
        assert client.get("/portfolio", headers=_renderer_headers()).status_code == 404
