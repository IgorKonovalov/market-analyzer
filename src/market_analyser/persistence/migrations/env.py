"""Alembic environment script — programmatic config (no alembic.ini).

The persistence engine builds an `AlembicConfig` in-code, sets `script_location`
to `market_analyser:persistence/migrations`, and runs `command.upgrade` with a
live engine connection attached as `config.attributes["connection"]`. This lets
in-memory SQLite databases survive migration in tests.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy.engine import Connection

from market_analyser.persistence.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = context.config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection: Connection | None = context.config.attributes.get("connection")
    if connection is None:
        # Fallback: build an engine from sqlalchemy.url. Used by alembic CLI invocations.
        from sqlalchemy import engine_from_config, pool

        cfg_section = context.config.get_section(context.config.config_ini_section) or {}
        connectable = engine_from_config(
            cfg_section,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as conn:
            _run(conn)
    else:
        _run(connection)


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
