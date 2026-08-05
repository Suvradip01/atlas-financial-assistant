"""
Atlas — SQLAlchemy Declarative Base.

All ORM models inherit from `Base`. This module is intentionally minimal:
it defines only the shared base and a custom naming convention so Alembic
can generate deterministic constraint names across all migrations.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint naming convention — prevents anonymous constraints
# that would cause Alembic to generate incompatible migrations across databases.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base for all Atlas ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
