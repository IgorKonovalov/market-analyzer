"""Plan 0099 phase 3 — the `recommend_rebalance` tool body + the ADR-0029
no-execution-path boundary.

Pinned here:
- resolving by watch_id scores the newest alert, but the watch's CURRENT
  dwell state wins: a position that re-entered its range since the alert
  yields "hold — no action", never a stale rebalance call;
- resolving by alert_id scores that specific alert; unknown ids and a call
  with neither id are refused;
- an out-of-range excursion that has not met the dwell threshold yet yields
  an honest hold;
- wallets are masked before they enter the recommendation;
- **the ADR-0029/0025 boundary, source-level**: no order token, no
  trade-permissioned secret, and no network import exists in the advisor
  rebalance module or the tool module (the `recommend` twin of
  `test_advisor_holds_no_key_and_no_order_path`).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

import market_analyser.advisor.rebalance as rebalance_module
import market_analyser.api.mcp_tools.recommend_rebalance as rebalance_tool
from market_analyser.api.mcp_tools.recommend_rebalance import (
    RECOMMEND_REBALANCE_DESCRIPTION,
    _recommend_rebalance_response,
)
from market_analyser.defi.position_watch import DwellState
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)

# Synthetic placeholder addresses — never a real wallet (public repo).
WALLET = "0x" + "ab" * 20
POOL = "0x" + "cd" * 20
MASKED_WALLET = f"{WALLET[:6]}…{WALLET[-4:]}"

CREATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
OUT_SINCE = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
FIRED_AT = OUT_SINCE + timedelta(hours=6)
NOW = FIRED_AT + timedelta(hours=1)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def watches(session_factory: sessionmaker[Session]) -> DefiPositionWatchesRepository:
    return DefiPositionWatchesRepository(session_factory)


@pytest.fixture
def alerts(session_factory: sessionmaker[Session]) -> DefiPositionAlertsRepository:
    return DefiPositionAlertsRepository(session_factory)


def _create_watch(watches: DefiPositionWatchesRepository) -> int:
    return watches.create(
        wallet=WALLET,
        chain="base",
        pool_address=POOL,
        nft_token_id=42,
        dwell_hours=6.0,
        interval_seconds=900,
        source="agent",
        created_at=CREATED_AT,
    ).id


def _insert_alert(alerts: DefiPositionAlertsRepository, watch_id: int, **overrides: Any) -> int:
    kwargs: dict[str, Any] = {
        "watch_id": watch_id,
        "wallet": WALLET,
        "chain": "base",
        "pool_address": POOL,
        "nft_token_id": 42,
        "fired_at": FIRED_AT,
        "out_since": OUT_SINCE,
        "hours_out": 6.0,
        "tick_lower": -100,
        "tick_upper": 100,
        "current_tick": 150,  # 0.25 range-widths beyond -> widen
        "uncollected_fees": None,
    }
    kwargs.update(overrides)
    return alerts.insert(**kwargs).id


def _respond(
    watches: DefiPositionWatchesRepository,
    alerts: DefiPositionAlertsRepository,
    **kwargs: Any,
) -> Any:
    return _recommend_rebalance_response(
        watches_repository=watches,
        alerts_repository=alerts,
        watch_id=kwargs.get("watch_id"),
        alert_id=kwargs.get("alert_id"),
        now=NOW,
    )


class TestResolution:
    def test_watch_id_scores_newest_alert_when_still_out(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        _insert_alert(alerts, watch_id)
        # The monitor's persisted state says the excursion is live + fired.
        watches.set_dwell_state(watch_id, DwellState(out_since=OUT_SINCE, fired=True))
        rec = _respond(watches, alerts, watch_id=watch_id)
        assert rec.action == "widen"
        assert rec.label == "advisory"
        assert rec.wallet == MASKED_WALLET
        assert WALLET not in rec.model_dump_json()
        assert rec.basis["dwell_hours"] == 6.0

    def test_re_entered_watch_yields_hold_despite_stored_alert(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        _insert_alert(alerts, watch_id)
        # Re-entry reset the dwell state after the alert fired.
        watches.set_dwell_state(watch_id, DwellState())
        rec = _respond(watches, alerts, watch_id=watch_id)
        assert rec.action == "hold"
        assert any("no action" in line for line in rec.rationale)

    def test_alert_id_scores_that_alert(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        alert_id = _insert_alert(alerts, watch_id, current_tick=500)  # deep -> exit
        rec = _respond(watches, alerts, alert_id=alert_id)
        assert rec.action == "exit"

    def test_pre_dwell_excursion_yields_honest_hold(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        # Out of range but no alert yet (dwell not met): honest hold.
        watches.set_dwell_state(watch_id, DwellState(out_since=NOW - timedelta(hours=1)))
        rec = _respond(watches, alerts, watch_id=watch_id)
        assert rec.action == "hold"
        assert any("insufficient basis" in line for line in rec.rationale)
        assert rec.basis["hours_out"] == pytest.approx(1.0)

    def test_unknown_ids_and_missing_ids_are_refused(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        with pytest.raises(ValueError, match="watch_id or alert_id"):
            _respond(watches, alerts)
        with pytest.raises(ValueError, match="unknown watch_id"):
            _respond(watches, alerts, watch_id=999)
        with pytest.raises(ValueError, match="unknown alert_id"):
            _respond(watches, alerts, alert_id=999)


class TestAdvisoryBoundary:
    def test_description_labels_the_tool_advisory(self) -> None:
        assert "ADVISORY" in RECOMMEND_REBALANCE_DESCRIPTION
        assert "places no order" in RECOMMEND_REBALANCE_DESCRIPTION

    def test_rebalance_surface_holds_no_key_and_no_order_path(self) -> None:
        """Phase-3 done-when (ADR-0029 / ADR-0025 / ADR-0072 BA-1 boundary):
        no order token, no trade-permissioned secret, and no network import
        exists in the advisor rebalance module or its tool module —
        source-level, so a future 'just rebalance it' accretion fails here
        before it ships."""
        sources = [Path(str(rebalance_module.__file__)), Path(str(rebalance_tool.__file__))]

        forbidden_tokens = (
            "place_order",
            "create_order",
            "new_order",
            "submit_order",
            "send_transaction",
            "sign_transaction",
            "signtypeddata",
            "eth_sendrawtransaction",
            "x-mbx-apikey",
            "hmac",
            "api_key",
            "apikey",
            "trade_key",
            "private_key",
            "keyring",
        )
        forbidden_imports = (
            "httpx",
            "requests",
            "urllib",
            "web3",
            "market_analyser.data._http",
            "market_analyser.data.adapters",
            "market_analyser.persistence.secrets",
        )

        for source in sources:
            text = source.read_text(encoding="utf-8")
            lowered = text.lower()
            for token in forbidden_tokens:
                assert token not in lowered, f"{source.name} contains forbidden token {token!r}"
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    continue
                for name in imported:
                    for banned in forbidden_imports:
                        assert not name.startswith(banned), (
                            f"{source.name} imports {name!r} — the rebalance advisory "
                            "surface must not reach the chain, the network, or any "
                            "secret store"
                        )
