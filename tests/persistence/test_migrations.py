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
