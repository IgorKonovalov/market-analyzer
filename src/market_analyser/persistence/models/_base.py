"""SQLAlchemy declarative base — extracted so per-domain model files can import
it without a circular dependency on the package `__init__.py`.

Adding a new ORM model: create a new module in this package, declare the class
against this `Base`, and re-export it from `__init__.py` so consumers can use
either the per-domain path or the legacy package-level import.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models in this package."""
