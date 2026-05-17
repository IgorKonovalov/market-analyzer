"""SQLAlchemy engine factory + Alembic-driven schema bootstrap.

The engine is built once per sidecar process. Migrations are applied
programmatically via Alembic at app startup (per Plan 0001 phase 3); a broken
migration surfaces as a startup error, not a corrupted live state.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

MIGRATIONS_PACKAGE = "market_analyser:persistence/migrations"


def make_engine(db_path: Path | str) -> Engine:
    """Build a SQLAlchemy engine for the given SQLite path.

    Pass `:memory:` (with the special `StaticPool` wiring) for tests; pass a
    filesystem path for production.
    """
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
        return create_engine(url, future=True)
    if db_path == ":memory:":
        # Share a single connection so multiple sessions see the same data.
        return create_engine(
            "sqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(f"sqlite:///{db_path}", future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a sessionmaker bound to `engine`."""
    return sessionmaker(engine, expire_on_commit=False, future=True)


def apply_migrations(engine: Engine) -> None:
    """Apply Alembic migrations up to head against the supplied engine.

    Reuses the live engine's connection so in-memory SQLite databases survive
    the migration phase (a new engine wouldn't share the in-memory store).
    """
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS_PACKAGE)
    config.set_main_option("sqlalchemy.url", str(engine.url))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
