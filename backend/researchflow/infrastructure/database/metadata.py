"""Shared SQLAlchemy metadata without product tables."""

from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

CANONICAL_SCHEMA: Final = "public"
LANGGRAPH_SCHEMA: Final = "langgraph"
TEST_SCHEMA: Final = "researchflow_test"

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for product records introduced by later migration sessions."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
