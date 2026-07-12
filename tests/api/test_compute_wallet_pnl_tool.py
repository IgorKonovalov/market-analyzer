"""Plan 0035 phase 7: the `compute_wallet_pnl` MCP tool.

Drives the tool in-process via `FastMCP.call_tool` over fake sources and a real
in-memory cache. Asserts the reconstructed per-position + total figures come
back with the `defi.pnl_*` events streamed, an invalid address / stray key is
rejected at the input boundary, a missing key surfaces as a structured `auth`
error, and the tool is registered. (Presence in the full `create_mcp_components`
toolset is pinned by the exhaustive registration test in `test_mcp_tools.py`.)
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_tools.compute_wallet_pnl import register_compute_wallet_pnl
from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.adapters.zerion import ZerionAuthError
from market_analyser.defi.models import Chain, DefiPosition, PositionToken, RewardAmount
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.events import Envelope, EventBus
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_WALLET = "0x2222222222222222222222222222222222222222"
_POOL = "0xpool0000000000000000000000000000000000001"
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_TS1 = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


_DEPOSIT_TX = DecodedTx.model_validate(
    {
        "chain": "base",
        "hash": "0xadd",
        "operation_type": "deposit",
        "mined_at": _TS1,
        "mined_at_block": 100,
        "status": "confirmed",
        "transfers": [{"direction": "out", "symbol": "USDC", "address": _USDC, "amount": 1000.0}],
        "acts": [{"act_id": "a1", "type": "deposit", "contract_address": _POOL}],
    }
)

_POSITION = DefiPosition(
    position_id="base:aerodrome:lp-1",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[PositionToken(symbol="USDC", address=_USDC, amount=1000.0)],
    usd_value=1100.0,
    pool="USDC pool",
    pool_address=_POOL,
)


class _FakeTxSource:
    def __init__(self, txs: list[DecodedTx] | None = None, error: Exception | None = None) -> None:
        self._txs = txs or []
        self._error = error

    def fetch_transactions(
        self, address: str, *, min_mined_at: datetime | None = None
    ) -> list[DecodedTx]:
        if self._error is not None:
            raise self._error
        return self._txs


class _FakePositionsSource:
    def fetch_positions(self, address: str) -> list[DefiPosition]:
        return [_POSITION]


class _TablePriceSource:
    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return {(f"base:{_USDC}", int(_TS1.timestamp())): 1.0}.get((token_key(chain, address), ts))


class _FakeUnclaimedSource:
    def fetch_unclaimed(self, *, position: DefiPosition, owner: str) -> list[RewardAmount]:
        return [RewardAmount(symbol="AERO", amount=34.2, usd_value=18.0)]


def _server(
    session_factory: sessionmaker[Session],
    bus: EventBus,
    *,
    tx_source: _FakeTxSource | None = None,
    unclaimed_source: _FakeUnclaimedSource | None = None,
) -> FastMCP:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_compute_wallet_pnl(
        server,
        tx_history_sources={
            "zerion": tx_source if tx_source is not None else _FakeTxSource([_DEPOSIT_TX])
        },
        wallet_positions_sources={"zerion": _FakePositionsSource()},
        historical_price_source=_TablePriceSource(),
        defi_tx_repository=DefiTxRepository(session_factory),
        event_bus=bus,
        unclaimed_rewards_source=unclaimed_source,
    )
    return server


def _call(server: FastMCP, arguments: dict[str, Any]) -> Any:
    return anyio.run(server.call_tool, "compute_wallet_pnl", arguments)


def _drain(queue: asyncio.Queue[Envelope]) -> list[str]:
    types: list[str] = []
    while not queue.empty():
        types.append(queue.get_nowait().type)
    return types


def test_happy_path_returns_reconstruction_and_streams_events(
    session_factory: sessionmaker[Session],
) -> None:
    bus = EventBus()
    sub = bus.subscribe()
    server = _server(session_factory, bus)

    _content, structured = _call(server, {"params": {"address": _WALLET}})

    assert structured["error"] is None
    assert structured["position_count"] == 1
    assert structured["incomplete"] is False
    # Basis 1000 (1000 USDC @ 1), current value 1100 → unrealized 100.
    assert structured["realized_usd"] == 0.0
    assert structured["unrealized_usd"] == 100.0
    position = structured["positions"][0]
    assert position["cost_basis_usd"] == 1000.0
    assert position["incomplete"] is False
    assert structured["wallet"] == "0x2222…2222"  # masked
    assert _WALLET not in str(structured)
    assert _drain(sub.queue) == ["defi.pnl_started", "defi.pnl_completed"]


def test_unclaimed_rewards_surface_when_a_source_is_wired(
    session_factory: sessionmaker[Session],
) -> None:
    bus = EventBus()
    server = _server(session_factory, bus, unclaimed_source=_FakeUnclaimedSource())
    _content, structured = _call(server, {"params": {"address": _WALLET}})
    expected = [{"symbol": "AERO", "amount": 34.2, "usd_value": 18.0}]
    assert structured["error"] is None
    assert structured["unclaimed_rewards"] == expected
    assert structured["positions"][0]["unclaimed_rewards"] == expected


def test_unclaimed_rewards_null_without_a_source(session_factory: sessionmaker[Session]) -> None:
    server = _server(session_factory, EventBus())
    _content, structured = _call(server, {"params": {"address": _WALLET}})
    assert structured["unclaimed_rewards"] is None


def test_invalid_address_rejected_at_input_boundary(
    session_factory: sessionmaker[Session],
) -> None:
    server = _server(session_factory, EventBus())
    with pytest.raises(ToolError):
        _call(server, {"params": {"address": "not-an-address"}})


def test_extra_key_rejected_at_input_boundary(session_factory: sessionmaker[Session]) -> None:
    server = _server(session_factory, EventBus())
    with pytest.raises(ToolError):
        _call(server, {"params": {"address": _WALLET, "fifo": True}})


def test_missing_key_returns_structured_auth_error(
    session_factory: sessionmaker[Session],
) -> None:
    server = _server(
        session_factory,
        EventBus(),
        tx_source=_FakeTxSource(error=ZerionAuthError("zerion: no API key configured")),
    )
    _content, structured = _call(server, {"params": {"address": _WALLET}})
    assert structured["positions"] is None
    assert structured["error"] == "auth"
    assert "key" in structured["message"].lower()


def test_tool_is_registered_directly(session_factory: sessionmaker[Session]) -> None:
    server = _server(session_factory, EventBus())
    tools = anyio.run(server.list_tools)
    assert "compute_wallet_pnl" in {tool.name for tool in tools}


def test_description_advertises_reconstruction_and_advisory_crosscheck(
    session_factory: sessionmaker[Session],
) -> None:
    server = _server(session_factory, EventBus())
    tool = next(t for t in anyio.run(server.list_tools) if t.name == "compute_wallet_pnl")
    description = (tool.description or "").lower()
    assert "reconstruct" in description
    assert "advisory" in description  # the cross-check is labeled, not trusted
    assert "auth" in description  # the set-your-key recovery path
    assert "unclaimed_rewards" in description  # the Plan 0084 current-state field
