"""Alembic-only PostgreSQL reflection compatibility policy."""

from collections.abc import MutableMapping
from typing import Any, Final, Literal

from researchflow.config import Settings
from researchflow.infrastructure.database import models as database_models
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base

MIGRATION_REFLECTION_SEARCH_PATH: Final = "extensions,pg_catalog"

# The name filter must compare against a complete metadata registry.
assert database_models.__all__

ReflectedObjectType = Literal[
    "schema",
    "table",
    "column",
    "index",
    "unique_constraint",
    "foreign_key_constraint",
]
ParentName = Literal["schema_name", "table_name", "schema_qualified_table_name"]


def migration_engine_options(settings: Settings) -> dict[str, Any]:
    """Keep public outside Alembic's search path so FK reflection retains it."""
    postgres_options = (
        "-c timezone=UTC "
        f"-c statement_timeout={settings.database_statement_timeout_ms} "
        f"-c search_path={MIGRATION_REFLECTION_SEARCH_PATH}"
    )
    return {
        "pool_pre_ping": True,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "connect_args": {
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": postgres_options,
        },
    }


def include_canonical_name(
    name: str | None,
    type_: ReflectedObjectType,
    parent_names: MutableMapping[ParentName, str | None],
) -> bool:
    """Include only ResearchFlow-owned tables in the canonical public schema."""
    if type_ == "schema":
        return name == CANONICAL_SCHEMA
    if type_ == "table":
        return parent_names.get("schema_qualified_table_name") in Base.metadata.tables
    return True
