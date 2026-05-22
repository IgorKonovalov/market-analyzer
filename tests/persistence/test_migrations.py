"""Plan 0001 phase 3: migration up-and-down round-trip test.

A broken migration locks users out of their data (per ADR-0006). Every
migration must reverse cleanly — this is the test that catches "I forgot to
implement `downgrade()`".
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, inspect

from market_analyser.persistence.engine import MIGRATIONS_PACKAGE, make_engine


def _alembic_config(engine: Engine) -> AlembicConfig:
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS_PACKAGE)
    config.set_main_option("sqlalchemy.url", str(engine.url))
    return config


def test_migration_upgrades_and_downgrades_cleanly() -> None:
    engine = make_engine(":memory:")
    try:
        config = _alembic_config(engine)

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        inspector = inspect(engine)
        assert "bars" in inspector.get_table_names()

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
        inspector = inspect(engine)
        assert "bars" not in inspector.get_table_names()
    finally:
        engine.dispose()


def test_bars_table_has_expected_columns_after_upgrade() -> None:
    engine = make_engine(":memory:")
    try:
        config = _alembic_config(engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("bars")}
        assert columns == {
            "symbol",
            "timeframe",
            "event_ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "ingested_at",
        }
    finally:
        engine.dispose()


def test_upgrade_from_bars_only_to_head_adds_annotations_without_disturbing_bars() -> None:
    """A DB that already has `bars` (Plan 0001 phase 3 schema) upgrades cleanly
    to head (Plan 0006 phase 2) — annotations land, bars survive."""
    engine = make_engine(":memory:")
    try:
        config = _alembic_config(engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0001_bars_table")
        inspector = inspect(engine)
        assert "bars" in inspector.get_table_names()
        assert "annotations" not in inspector.get_table_names()

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        inspector = inspect(engine)
        assert "bars" in inspector.get_table_names()
        assert "annotations" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_annotations_table_has_expected_columns_and_index_after_upgrade() -> None:
    engine = make_engine(":memory:")
    try:
        config = _alembic_config(engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("annotations")}
        assert columns == {
            "id",
            "symbol",
            "timeframe",
            "event_ts",
            "kind",
            "label",
            "agent_id",
            "created_at",
        }
        index_names = {idx["name"] for idx in inspector.get_indexes("annotations")}
        assert "ix_annotations_symbol_timeframe_event_ts" in index_names
    finally:
        engine.dispose()


def test_backtest_runs_table_has_expected_columns_and_indexes_after_upgrade() -> None:
    """Plan 0008 phase 3: backtest_runs lands at head with the searchable
    projection columns and the three planned indexes."""
    engine = make_engine(":memory:")
    try:
        config = _alembic_config(engine)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        inspector = inspect(engine)
        assert "backtest_runs" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("backtest_runs")}
        assert columns == {
            "run_id",
            "strategy_id",
            "strategy_version",
            "symbol",
            "timeframe",
            "range_start",
            "range_end",
            "total_return",
            "sharpe",
            "max_drawdown",
            "win_rate",
            "trade_count",
            "finished_at",
            "artifact_path",
            "engine_version",
        }
        index_names = {idx["name"] for idx in inspector.get_indexes("backtest_runs")}
        assert {
            "ix_backtest_runs_finished_at",
            "ix_backtest_runs_symbol_timeframe",
            "ix_backtest_runs_strategy_id",
        } <= index_names
    finally:
        engine.dispose()


def test_backtest_runs_migration_is_reversible_single_step() -> None:
    """`upgrade head -> downgrade -1 -> upgrade head` leaves the schema
    identical to the first upgrade (Plan 0008 phase 3 done-when §163)."""
    engine = make_engine(":memory:")
    try:
        config = _alembic_config(engine)

        def snapshot() -> dict[str, set[str]]:
            insp = inspect(engine)
            return {
                table: {c["name"] for c in insp.get_columns(table)}
                for table in insp.get_table_names()
            }

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        head_first = snapshot()
        assert "backtest_runs" in head_first

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "-1")
        after_down = snapshot()
        assert "backtest_runs" not in after_down
        assert "annotations" in after_down  # other tables survive

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        head_second = snapshot()
        assert head_first == head_second
    finally:
        engine.dispose()
