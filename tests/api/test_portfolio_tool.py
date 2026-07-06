"""`portfolio_summary` MCP tool tests (Plan 0041 phase 3).

The done-when, read at the assertion level:

(a) the tool returns unified holdings with average-cost basis — including the
    DeFi basis joined through the **real** ADR-0036 replay (cached decoded-tx
    history in a real in-memory `DefiTxRepository`, block-time prices from a
    fake `HistoricalPriceSource`) — plus unrealized P&L and exposure by asset
    and by venue;
(b) each leg reports its pricing reference and its own as-of time;
(c) a failing leg is contained into `leg_errors` while the others aggregate;
(d) the output and the tool description carry **no** advice — no
    rebalance/exit/buy/sell language, and the summary's field set is pinned
    exactly (no advice-shaped field can appear silently).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_tools.portfolio import (
    PORTFOLIO_SUMMARY_DESCRIPTION,
    register_portfolio_summary,
)
from market_analyser.data.adapters.binance_account import BinanceAccountAuthError
from market_analyser.data.errors import UnknownSymbolError
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import (
    AccountHoldings,
    FuturesPosition,
    Quote,
    SpotBalance,
)
from market_analyser.defi.models import Chain, DefiPosition, PositionToken
from market_analyser.defi.tx_models import DecodedTx, TxAct, TxTransfer
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_WALLET = "0x" + "ab" * 20
_BINANCE_AS_OF = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
_QUOTE_AS_OF = datetime(2026, 7, 6, 11, 59, tzinfo=UTC)
_MANUAL_AS_OF = "2026-07-01T00:00:00Z"

# Block-time prices the fake HistoricalPriceSource serves: the deposit's legs
# value to 2500 * 1.0 + 0.8 * 3000 = 4900 — the replay's remaining basis.
_TOKEN_PRICES = {"0xusdc": 1.0, "0xweth": 3000.0}
_EXPECTED_LP_BASIS = 2_500.0 * 1.0 + 0.8 * 3_000.0


class _FakeAccountSource:
    def __init__(self, holdings: AccountHoldings | None = None, error: Exception | None = None):
        self._holdings = holdings
        self._error = error

    def fetch_account_holdings(self) -> AccountHoldings:
        if self._error is not None:
            raise self._error
        assert self._holdings is not None
        return self._holdings


class _FakeWalletSource:
    def __init__(self, positions: list[DefiPosition]) -> None:
        self._positions = positions

    def fetch_positions(self, address: str) -> list[DefiPosition]:
        return self._positions


class _SpyTxSource:
    """Serves the decoded history on a cold-cache pull, recording every call."""

    def __init__(self, transactions: list[DecodedTx]) -> None:
        self._transactions = transactions
        self.calls: list[str] = []

    def fetch_transactions(
        self, address: str, *, min_mined_at: datetime | None = None
    ) -> list[DecodedTx]:
        self.calls.append(address)
        return self._transactions


class _FakePriceSource:
    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return _TOKEN_PRICES.get(address or "")


class _QuoteProvider:
    """Only `get_quote` is exercised by the tool; a symbol outside the table
    is honestly unquotable."""

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
        futures=[
            FuturesPosition(
                symbol="BTCUSDT",
                quantity=0.01,
                entry_price=60_000.0,
                position_side="BOTH",
                mark_price=61_000.0,
            ),
        ],
        as_of=_BINANCE_AS_OF,
    )


def _lp_position() -> DefiPosition:
    return DefiPosition(
        position_id="base:aerodrome:lp-1",
        chain="base",
        protocol="aerodrome",
        kind="lp",
        tokens=[
            PositionToken(symbol="USDC", address="0xusdc", amount=2_500.0),
            PositionToken(symbol="WETH", address="0xweth", amount=0.8),
        ],
        usd_value=5_000.0,
        pool="vAMM-WETH/USDC",
        pool_address="0xpool",
    )


def _deposit_tx() -> DecodedTx:
    """One confirmed deposit joining the LP by pool address (join rule 1),
    classifying as `add_liquidity` off the position's kind."""
    return DecodedTx(
        chain="base",
        hash="0x" + "cd" * 32,
        operation_type="deposit",
        mined_at=datetime(2026, 6, 1, tzinfo=UTC),
        mined_at_block=100,
        in_block_index=0,
        status="confirmed",
        transfers=[
            TxTransfer(direction="out", symbol="USDC", address="0xusdc", amount=2_500.0),
            TxTransfer(direction="out", symbol="WETH", address="0xweth", amount=0.8),
        ],
        acts=[
            TxAct(
                act_id="act-1",
                type="execute",
                contract_address="0xpool",
                method_name="deposit",
            ),
        ],
    )


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _write_manual(tmp_path: Path) -> Path:
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "as_of": _MANUAL_AS_OF,
                "positions": [
                    {"symbol": "AAPL", "quantity": 100, "avg_cost": 185.5},
                    {"symbol": "GLD", "quantity": 20},
                ],
            },
        ),
        encoding="utf-8",
    )
    return path


def _call(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    *,
    account_error: Exception | None = None,
    seed_history: bool = True,
    params: dict[str, Any] | None = None,
    tx_source: _SpyTxSource | None = None,
    manual_path: Path | None = None,
) -> dict[str, Any]:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    account = (
        _FakeAccountSource(error=account_error)
        if account_error is not None
        else _FakeAccountSource(holdings=_account())
    )
    history = [_deposit_tx()] if seed_history else []
    tx = tx_source if tx_source is not None else _SpyTxSource(history)
    register_portfolio_summary(
        server,
        provider=cast(
            "MarketDataProvider",
            _QuoteProvider({"BTC-USD": 61_000.0, "AAPL": 200.0}),
        ),
        account_holdings_sources={"binance": account},
        manual_positions_path=manual_path if manual_path is not None else _write_manual(tmp_path),
        wallet_positions_sources={"zerion": _FakeWalletSource([_lp_position()])},
        tx_history_sources={"zerion": tx},
        defi_tx_repository=DefiTxRepository(session_factory),
        historical_price_source=_FakePriceSource(),
    )
    payload = {"params": params if params is not None else {"wallet": _WALLET}}
    result = anyio.run(server.call_tool, "portfolio_summary", payload)
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)
    return structured


# --- (a) unified holdings + the real ADR-0036 basis join ---------------------------


def test_summary_unifies_all_three_venues_with_average_cost_basis(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    response = _call(tmp_path, session_factory)

    assert response["error"] is None
    assert response["leg_errors"] == {}
    summary = response["summary"]
    by_key = {(h["venue"], h["symbol"]): h for h in summary["holdings"]}
    assert set(by_key) == {
        ("binance", "BTC"),
        ("binance", "BTCUSDT"),
        ("defi", "vAMM-WETH/USDC"),
        ("manual", "AAPL"),
        ("manual", "GLD"),
    }

    # The DeFi basis came through the REAL replay: cached history (ingested
    # once through the cold-cache path into a real DefiTxRepository), mapped
    # to add_liquidity, block-time priced — ADR-0036 reused, not reimplemented.
    lp = by_key[("defi", "vAMM-WETH/USDC")]
    assert lp["avg_cost"] == pytest.approx(_EXPECTED_LP_BASIS)
    assert lp["usd_value"] == 5_000.0
    assert lp["pricing_source"] == "zerion"

    # Futures basis = entry price, priced at the venue mark.
    fut = by_key[("binance", "BTCUSDT")]
    assert fut["avg_cost"] == 60_000.0
    assert fut["usd_value"] == pytest.approx(610.0)
    assert fut["pricing_source"] == "binance-mark"

    # Manual basis from the file; spot has none (honestly unknown).
    assert by_key[("manual", "AAPL")]["avg_cost"] == 185.5
    assert by_key[("binance", "BTC")]["avg_cost"] is None

    # Unrealized P&L: futures +10, LP 5000-4900=+100, AAPL +1450.
    assert summary["unrealized_pnl_usd"] == pytest.approx(10.0 + 100.0 + 1_450.0)
    # Exposure spans assets and venues, hand-worked.
    assert summary["exposure_by_venue"] == pytest.approx(
        {
            "binance": 0.5 * 61_000.0 + 610.0,
            "defi": 5_000.0,
            "manual": 100 * 200.0,  # GLD is unquotable -> excluded, not zeroed
        }
    )
    # Coverage + unpriced-GLD notes are explicit, never silent.
    assert any("unrealized_pnl_usd covers 3 of 5" in note for note in response["notes"])
    assert any("unpriced manual:GLD" in note for note in response["notes"])


def test_each_leg_reports_its_own_as_of_and_pricing_reference(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    response = _call(tmp_path, session_factory)
    summary = response["summary"]
    assert set(summary["legs_as_of"]) == {"binance", "defi", "manual"}
    assert summary["legs_as_of"]["binance"] == _BINANCE_AS_OF.isoformat().replace("+00:00", "Z")
    assert summary["legs_as_of"]["manual"] == "2026-07-01T00:00:00Z"
    for holding in summary["holdings"]:
        assert (holding["usd_value"] is None) == (holding["pricing_source"] is None)


# --- (c) per-leg containment --------------------------------------------------------


def test_binance_auth_failure_is_contained_and_other_legs_still_aggregate(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    response = _call(
        tmp_path,
        session_factory,
        account_error=BinanceAccountAuthError("binance-account: no read credential configured"),
    )
    assert response["error"] is None
    assert response["leg_errors"]["binance"].startswith("auth:")
    summary = response["summary"]
    venues = {h["venue"] for h in summary["holdings"]}
    assert venues == {"defi", "manual"}  # the failed leg is absent, not zeroed
    assert "binance" not in summary["legs_as_of"]


def test_malformed_manual_file_is_contained(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    bad = tmp_path / "portfolio.json"
    bad.write_text(json.dumps({"as_of": _MANUAL_AS_OF, "positions": [{"symbol": ""}]}))
    response = _call(tmp_path, session_factory, manual_path=bad)
    assert response["leg_errors"]["manual"].startswith("malformed_file:")
    assert {h["venue"] for h in response["summary"]["holdings"]} == {"binance", "defi"}


def test_without_wallet_the_defi_leg_is_simply_absent(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    response = _call(tmp_path, session_factory, params={})
    assert response["leg_errors"] == {}
    assert "defi" not in response["summary"]["legs_as_of"]
    assert all(h["venue"] != "defi" for h in response["summary"]["holdings"])


def test_include_defi_basis_false_never_touches_the_history_source(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    spy = _SpyTxSource([_deposit_tx()])
    response = _call(
        tmp_path,
        session_factory,
        tx_source=spy,
        params={"wallet": _WALLET, "include_defi_basis": False},
    )
    assert spy.calls == []  # no ingestion, no replay
    assert any("basis skipped" in note for note in response["notes"])
    lp = next(h for h in response["summary"]["holdings"] if h["venue"] == "defi")
    assert lp["avg_cost"] is None


# --- (d) no advice, structurally ------------------------------------------------------


def test_output_and_description_carry_no_advice_language(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    """The ADR-0029 boundary: this surface reports facts. No rebalance / exit /
    buy / sell language anywhere in the response or the tool description, and
    the summary's field set is pinned exactly so an advice-shaped field cannot
    appear silently."""
    response = _call(tmp_path, session_factory)
    blob = json.dumps(response).lower() + " " + PORTFOLIO_SUMMARY_DESCRIPTION.lower()
    for token in ("rebalance", "exit", "buy", "sell", "conviction", "stop_loss", "target_price"):
        assert not re.search(rf"\b{token}\b", blob), f"advice token {token!r} leaked"

    assert set(response["summary"].keys()) == {
        "holdings",
        "unrealized_pnl_usd",
        "exposure_by_asset",
        "exposure_by_venue",
        "legs_as_of",
        "queried_at",
    }
    for holding in response["summary"]["holdings"]:
        assert set(holding.keys()) == {
            "symbol",
            "venue",
            "quantity",
            "avg_cost",
            "as_of",
            "usd_value",
            "pricing_source",
            "kind",
        }
