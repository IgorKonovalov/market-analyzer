"""Plan 0099 phase 1 — the `defi_position_watches` + `defi_position_alerts`
repositories (ADR-0093).

Done-when claims pinned here:
(a) a watch round-trips create → get/list with its dwell state defaulting to
    armed (`out_since=None`, not fired), with the boundary rejecting
    malformed addresses / chains / dwells before any write;
(b) the dwell state persists across a simulated restart — written through
    one engine, read back through a **fresh engine over the same SQLite
    file** (the dwell survives process death; done-when (e) at the
    persistence level);
(c) an alert round-trips insert → list, newest-first with deterministic
    offset/limit paging and honest totals, and deleting a watch removes its
    history.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.defi.position_watch import DefiPositionAlert, DefiPositionWatch, DwellState
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

CREATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
OUT_SINCE = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
FIRED_AT = OUT_SINCE + timedelta(hours=6)


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


def _create_watch(repo: DefiPositionWatchesRepository, **overrides: Any) -> DefiPositionWatch:
    kwargs: dict[str, Any] = {
        "wallet": WALLET,
        "chain": "base",
        "pool_address": POOL,
        "nft_token_id": 42,
        "dwell_hours": 6.0,
        "interval_seconds": 900,
        "source": "agent",
        "created_at": CREATED_AT,
    }
    kwargs.update(overrides)
    watch = repo.create(**kwargs)
    assert isinstance(watch, DefiPositionWatch)
    return watch


def _insert_alert(
    repo: DefiPositionAlertsRepository, watch_id: int, **overrides: Any
) -> DefiPositionAlert:
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
        "current_tick": 150,
        "uncollected_fees": None,
    }
    kwargs.update(overrides)
    return repo.insert(**kwargs)


class TestWatchRoundTrip:
    def test_create_get_list_round_trip(self, watches: DefiPositionWatchesRepository) -> None:
        created = _create_watch(watches)
        assert created.dwell_state == DwellState()
        assert created.enabled is True
        assert created.source == "agent"

        fetched = watches.get(created.id)
        assert fetched == created
        assert watches.list() == [created]

    def test_list_enabled_only(self, watches: DefiPositionWatchesRepository) -> None:
        enabled = _create_watch(watches)
        disabled = _create_watch(watches)
        assert watches.set_enabled(disabled.id, enabled=False) is True
        listed = watches.list(enabled_only=True)
        assert [w.id for w in listed] == [enabled.id]

    def test_delete_returns_false_when_absent(self, watches: DefiPositionWatchesRepository) -> None:
        assert watches.delete(999) is False
        assert watches.set_enabled(999, enabled=False) is False
        assert watches.set_dwell_state(999, DwellState()) is False
        assert watches.get(999) is None

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("wallet", "0xnothex", "wallet must be an EVM address"),
            ("pool_address", "0x123", "pool_address must be an EVM address"),
            ("chain", "solana", "unknown chain"),
            ("source", "cron", "unknown source"),
            ("nft_token_id", -1, "nft_token_id must be >= 0"),
            ("dwell_hours", 0.0, "dwell_hours must be > 0"),
            ("dwell_hours", float("nan"), "dwell_hours must be > 0"),
            ("interval_seconds", 0, "interval_seconds must be > 0"),
            ("created_at", datetime(2026, 7, 15, 12, 0), "timezone-aware"),
        ],
    )
    def test_create_rejects_bad_input_before_write(
        self,
        watches: DefiPositionWatchesRepository,
        field: str,
        value: Any,
        match: str,
    ) -> None:
        with pytest.raises(ValueError, match=match):
            _create_watch(watches, **{field: value})
        assert watches.list() == []


class TestDwellStatePersistence:
    def test_set_dwell_state_round_trip(self, watches: DefiPositionWatchesRepository) -> None:
        watch = _create_watch(watches)
        state = DwellState(out_since=OUT_SINCE, fired=True)
        assert watches.set_dwell_state(watch.id, state) is True
        fetched = watches.get(watch.id)
        assert fetched is not None
        assert fetched.dwell_state == state

    def test_naive_out_since_rejected(self, watches: DefiPositionWatchesRepository) -> None:
        watch = _create_watch(watches)
        # Bypass the model boundary deliberately to pin the repo's own check.
        state = DwellState.model_construct(out_since=datetime(2026, 7, 16, 3, 0), fired=False)
        with pytest.raises(ValueError, match="timezone-aware"):
            watches.set_dwell_state(watch.id, state)

    def test_dwell_state_survives_restart(self, tmp_path: Path) -> None:
        """Written through one engine, read through a fresh engine over the
        same file — the dwell reducer's memory survives process death."""
        db_path = tmp_path / "positions.db"

        engine = make_engine(db_path)
        apply_migrations(engine)
        repo = DefiPositionWatchesRepository(make_session_factory(engine))
        watch = _create_watch(repo)
        repo.set_dwell_state(watch.id, DwellState(out_since=OUT_SINCE, fired=False))
        engine.dispose()

        fresh_engine = make_engine(db_path)
        apply_migrations(fresh_engine)
        fresh_repo = DefiPositionWatchesRepository(make_session_factory(fresh_engine))
        revived = fresh_repo.get(watch.id)
        assert revived is not None
        assert revived.dwell_state == DwellState(out_since=OUT_SINCE, fired=False)
        fresh_engine.dispose()


class TestAlertHistory:
    def test_insert_and_read_back(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = _create_watch(watches)
        alert = _insert_alert(alerts, watch.id)
        assert alert.in_range is False
        assert alert.hours_out == 6.0

        page, total = alerts.list(limit=10)
        assert total == 1
        assert page == [alert]

    def test_newest_first_paging_with_honest_totals(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = _create_watch(watches)
        stored = [
            _insert_alert(alerts, watch.id, fired_at=FIRED_AT + timedelta(hours=i))
            for i in range(5)
        ]
        newest_first = list(reversed(stored))

        first_page, total = alerts.list(limit=2)
        assert total == 5
        assert first_page == newest_first[:2]

        second_page, _ = alerts.list(limit=2, offset=2)
        assert second_page == newest_first[2:4]

    def test_scoped_to_watch_id(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        first = _create_watch(watches)
        second = _create_watch(watches)
        _insert_alert(alerts, first.id)
        scoped = _insert_alert(alerts, second.id, fired_at=FIRED_AT + timedelta(hours=1))

        page, total = alerts.list(watch_id=second.id, limit=10)
        assert total == 1
        assert page == [scoped]

    def test_unknown_watch_id_rejected(self, alerts: DefiPositionAlertsRepository) -> None:
        with pytest.raises(ValueError, match="unknown watch_id"):
            _insert_alert(alerts, 999)

    def test_delete_watch_removes_alert_history(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = _create_watch(watches)
        _insert_alert(alerts, watch.id)
        assert watches.delete(watch.id) is True
        _, total = alerts.list(limit=10)
        assert total == 0

    def test_naive_timestamps_rejected(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = _create_watch(watches)
        with pytest.raises(ValueError, match="timezone-aware"):
            _insert_alert(alerts, watch.id, fired_at=datetime(2026, 7, 16, 9, 0))
