"""Alembic migration environment using ResearchFlow's async engine policy."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from researchflow.config import Settings
from researchflow.infrastructure.database import models as database_models
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.migration import (
    include_canonical_name,
    migration_engine_options,
)
from researchflow.infrastructure.database.session import (
    assert_safe_local_test_target,
    assert_safe_migration_target,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Importing the package registers every mapped table with shared metadata.
assert database_models.__all__


def _migration_url(settings: Settings) -> str:
    """Select an explicitly guarded application or disposable-test target."""
    if context.config.attributes.get("use_test_database") is True:
        assert_safe_local_test_target(settings)
        return settings.test_database_url.get_secret_value()
    assert_safe_migration_target(settings)
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    settings = Settings()
    context.configure(
        url=_migration_url(settings),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
        include_name=include_canonical_name,
        version_table_schema=CANONICAL_SCHEMA,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
        include_name=include_canonical_name,
        version_table_schema=CANONICAL_SCHEMA,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    settings = Settings()
    engine = create_async_engine(
        _migration_url(settings),
        **migration_engine_options(settings),
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_migrations_online())
