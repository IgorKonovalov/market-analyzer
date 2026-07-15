"""Plan 0060 phase 1 — the `watches` + `alerts` repositories (ADR-0055).

Done-when claims pinned here:
(a) each watch kind's params round-trip through the stored JSON, with the
    boundary rejecting unknown kinds and malformed params before any write;
(b) `last_state` persists across a simulated restart — written through one
    engine, read back through a **fresh engine over the same SQLite file**
    (the edge-detector's memory survives process death);
(c) alert history reads are newest-first with deterministic offset/limit
    paging and honest totals.

Plan 0110 phase 1 adds: the free-text `note` round-trips (create → read,
`set_note` update, `set_note(id, None)` clear), with the length cap enforced
before any write.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.alerts.types import (
    NOTE_MAX_LENGTH,
    IndicatorThresholdParams,
    PatternParams,
    StrategySignalParams,
    UnknownWatchKindError,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

CREATED_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
FIRED_AT = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def watches(session_factory: sessionmaker[Session]) -> WatchesRepository:
    return WatchesRepository(session_factory)


@pytest.fixture
def alerts(session_factory: sessionmaker[Session]) -> AlertsRepository:
    return AlertsRepository(session_factory)


def _create_threshold_watch(repo: WatchesRepository, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "symbol": "BTC-USD",
        "timeframe": "1d",
        "kind": "indicator_threshold",
        "params": {"indicator": "rsi", "operator": "<", "level": 30.0},
        "interval_seconds": 86_400,
        "created_at": CREATED_AT,
    }
    kwargs.update(overrides)
    return repo.create(**kwargs)


class TestParamsRoundTrip:
    def test_indicator_threshold_round_trips(self, watches: WatchesRepository) -> None:
        created = _create_threshold_watch(watches)
        loaded = watches.get(created.id)
        assert loaded is not None
        assert loaded.params == IndicatorThresholdParams(indicator="rsi", operator="<", level=30.0)
        assert loaded.kind == "indicator_threshold"
        assert loaded.created_at == CREATED_AT

    def test_pattern_round_trips(self, watches: WatchesRepository) -> None:
        created = watches.create(
            symbol="ETH-USD",
            timeframe="4h",
            kind="pattern",
            params={"pattern": "hammer"},
            interval_seconds=14_400,
            created_at=CREATED_AT,
        )
        loaded = watches.get(created.id)
        assert loaded is not None
        assert loaded.params == PatternParams(pattern="hammer")

    def test_strategy_signal_round_trips(self, watches: WatchesRepository) -> None:
        created = watches.create(
            symbol="BTC-USD",
            timeframe="1d",
            kind="strategy_signal",
            params={"strategy_id": "rsi_stop", "params": {"period": 14}},
            interval_seconds=86_400,
            created_at=CREATED_AT,
        )
        loaded = watches.get(created.id)
        assert loaded is not None
        assert loaded.params == StrategySignalParams(strategy_id="rsi_stop", params={"period": 14})


class TestCreateBoundary:
    def test_unknown_kind_is_rejected_and_nothing_persists(
        self, watches: WatchesRepository
    ) -> None:
        with pytest.raises(UnknownWatchKindError):
            _create_threshold_watch(watches, kind="forecast_probability")
        assert watches.list() == []

    def test_malformed_params_are_rejected_and_nothing_persists(
        self, watches: WatchesRepository
    ) -> None:
        with pytest.raises(ValidationError):
            _create_threshold_watch(
                watches,
                params={"indicator": "rsi", "operator": "<", "level": 30.0, "action": "buy"},
            )
        assert watches.list() == []

    def test_unregistered_timeframe_is_rejected(self, watches: WatchesRepository) -> None:
        with pytest.raises(ValueError, match="unknown timeframe"):
            _create_threshold_watch(watches, timeframe="13m")

    def test_non_positive_interval_is_rejected(self, watches: WatchesRepository) -> None:
        with pytest.raises(ValueError, match="interval_seconds"):
            _create_threshold_watch(watches, interval_seconds=0)

    def test_naive_created_at_is_rejected(self, watches: WatchesRepository) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _create_threshold_watch(watches, created_at=datetime(2026, 7, 1, 12, 0))

    def test_empty_symbol_is_rejected(self, watches: WatchesRepository) -> None:
        with pytest.raises(ValueError, match="symbol"):
            _create_threshold_watch(watches, symbol="")


class TestWatchLifecycle:
    def test_list_orders_by_id_and_filters_enabled(self, watches: WatchesRepository) -> None:
        first = _create_threshold_watch(watches)
        second = _create_threshold_watch(watches, symbol="ETH-USD", enabled=False)
        assert [w.id for w in watches.list()] == [first.id, second.id]
        assert [w.id for w in watches.list(enabled_only=True)] == [first.id]

    def test_set_enabled_round_trips(self, watches: WatchesRepository) -> None:
        created = _create_threshold_watch(watches)
        assert watches.set_enabled(created.id, enabled=False) is True
        loaded = watches.get(created.id)
        assert loaded is not None and loaded.enabled is False
        assert watches.set_enabled(9999, enabled=True) is False

    def test_delete_removes_watch_and_its_alert_history(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        created = _create_threshold_watch(watches)
        alerts.insert(watch_id=created.id, fired_at=FIRED_AT, payload={"condition": "x"})
        assert watches.delete(created.id) is True
        assert watches.get(created.id) is None
        page, total = alerts.list(watch_id=created.id, limit=10)
        assert page == [] and total == 0
        assert watches.delete(created.id) is False

    def test_note_round_trips_through_create(self, watches: WatchesRepository) -> None:
        created = _create_threshold_watch(watches, note="ETH long scenario A - neckline retest")
        assert created.note == "ETH long scenario A - neckline retest"
        loaded = watches.get(created.id)
        assert loaded is not None
        assert loaded.note == "ETH long scenario A - neckline retest"
        assert [w.note for w in watches.list()] == ["ETH long scenario A - neckline retest"]

    def test_note_defaults_to_none(self, watches: WatchesRepository) -> None:
        created = _create_threshold_watch(watches)
        assert created.note is None

    def test_set_note_updates_and_clears(self, watches: WatchesRepository) -> None:
        created = _create_threshold_watch(watches, note="original")
        assert watches.set_note(created.id, "revised") is True
        loaded = watches.get(created.id)
        assert loaded is not None and loaded.note == "revised"

        assert watches.set_note(created.id, None) is True
        cleared = watches.get(created.id)
        assert cleared is not None and cleared.note is None

        assert watches.set_note(9999, "ghost") is False

    def test_over_length_note_is_rejected_before_any_write(
        self, watches: WatchesRepository
    ) -> None:
        too_long = "x" * (NOTE_MAX_LENGTH + 1)
        with pytest.raises(ValueError, match="note"):
            _create_threshold_watch(watches, note=too_long)
        assert watches.list() == []

        created = _create_threshold_watch(watches, note="keep me")
        with pytest.raises(ValueError, match="note"):
            watches.set_note(created.id, too_long)
        loaded = watches.get(created.id)
        assert loaded is not None and loaded.note == "keep me"

    def test_last_state_starts_none_and_updates(self, watches: WatchesRepository) -> None:
        created = _create_threshold_watch(watches)
        assert created.last_state is None
        assert watches.set_last_state(created.id, last_state=True) is True
        loaded = watches.get(created.id)
        assert loaded is not None and loaded.last_state is True
        assert watches.set_last_state(9999, last_state=False) is False


class TestLastStateSurvivesRestart:
    def test_last_state_persists_across_engine_reopen(self, tmp_path: Path) -> None:
        """The edge-detector's memory survives process death: write through one
        engine, dispose it, reopen the same SQLite file with a fresh engine +
        repository, and read the state back."""
        db_path = tmp_path / "alerts.sqlite3"

        engine = make_engine(db_path)
        apply_migrations(engine)
        repo = WatchesRepository(make_session_factory(engine))
        created = _create_threshold_watch(repo)
        assert repo.set_last_state(created.id, last_state=True) is True
        engine.dispose()

        reopened_engine = make_engine(db_path)
        apply_migrations(reopened_engine)
        reopened_repo = WatchesRepository(make_session_factory(reopened_engine))
        loaded = reopened_repo.get(created.id)
        reopened_engine.dispose()

        assert loaded is not None
        assert loaded.last_state is True
        assert loaded.params == IndicatorThresholdParams(indicator="rsi", operator="<", level=30.0)


class TestAlertsHistory:
    def test_insert_requires_known_watch_and_aware_fired_at(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        created = _create_threshold_watch(watches)
        with pytest.raises(ValueError, match="unknown watch_id"):
            alerts.insert(watch_id=9999, fired_at=FIRED_AT, payload={})
        with pytest.raises(ValueError, match="timezone-aware"):
            alerts.insert(watch_id=created.id, fired_at=datetime(2026, 7, 2), payload={})
        inserted = alerts.insert(
            watch_id=created.id, fired_at=FIRED_AT, payload={"condition": "RSI(14) 28.4 < 30"}
        )
        assert inserted.id > 0
        assert inserted.payload == {"condition": "RSI(14) 28.4 < 30"}
        assert inserted.fired_at == FIRED_AT

    def test_list_is_newest_first_with_paging_and_totals(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        created = _create_threshold_watch(watches)
        ids = [
            alerts.insert(
                watch_id=created.id,
                fired_at=FIRED_AT + timedelta(days=i),
                payload={"i": i},
            ).id
            for i in range(5)
        ]
        page, total = alerts.list(limit=2)
        assert total == 5
        assert [a.id for a in page] == [ids[4], ids[3]]
        page, total = alerts.list(offset=2, limit=2)
        assert [a.id for a in page] == [ids[2], ids[1]]
        page, total = alerts.list(offset=4, limit=2)
        assert [a.id for a in page] == [ids[0]]

    def test_identical_fired_at_breaks_ties_by_id_desc(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        created = _create_threshold_watch(watches)
        first = alerts.insert(watch_id=created.id, fired_at=FIRED_AT, payload={"n": 1})
        second = alerts.insert(watch_id=created.id, fired_at=FIRED_AT, payload={"n": 2})
        page, _ = alerts.list(limit=10)
        assert [a.id for a in page] == [second.id, first.id]

    def test_watch_id_filter_scopes_history(
        self, watches: WatchesRepository, alerts: AlertsRepository
    ) -> None:
        one = _create_threshold_watch(watches)
        two = _create_threshold_watch(watches, symbol="ETH-USD")
        alerts.insert(watch_id=one.id, fired_at=FIRED_AT, payload={})
        alerts.insert(watch_id=two.id, fired_at=FIRED_AT, payload={})
        page, total = alerts.list(watch_id=one.id, limit=10)
        assert total == 1
        assert [a.watch_id for a in page] == [one.id]

    def test_paging_bounds_are_validated(self, alerts: AlertsRepository) -> None:
        with pytest.raises(ValueError, match="offset"):
            alerts.list(offset=-1, limit=1)
        with pytest.raises(ValueError, match="limit"):
            alerts.list(limit=0)
