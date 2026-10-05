"""Async SQLAlchemy engine, session, and database-target safety boundaries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from ipaddress import ip_address
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from researchflow.config import (
    DatabaseConnectionMode,
    PersistenceEnvironment,
    Settings,
)
from researchflow.errors import ConfigurationError

_LOOPBACK_NAMES = frozenset({"localhost"})
_DEFAULT_POSTGRES_PORT = 5432
_DISPOSABLE_TEST_DATABASE_IDENTITY = ("loopback", 55322, "postgres")
_DISPOSABLE_TEST_API_PORT = 55321


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.casefold() in _LOOPBACK_NAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _normalized_database_identity(database_url: Any) -> tuple[str, int, str]:
    """Return the credential-independent identity of one PostgreSQL database."""
    host = database_url.host
    normalized_host = "loopback" if _is_loopback_host(host) else (host or "").casefold()
    return (
        normalized_host,
        database_url.port or _DEFAULT_POSTGRES_PORT,
        database_url.database or "",
    )


def _assert_safe_local_configuration(settings: Settings) -> tuple[Any, Any]:
    """Validate the shared local-only and app/test separation boundary."""
    database_url = make_url(settings.database_url.get_secret_value())
    test_database_url = make_url(settings.test_database_url.get_secret_value())

    if settings.persistence_environment is not PersistenceEnvironment.LOCAL:
        raise ConfigurationError(details={"reason": "test_target_not_local_environment"})
    if not _is_loopback_host(database_url.host):
        raise ConfigurationError(details={"reason": "database_target_not_loopback"})
    if not _is_loopback_host(test_database_url.host):
        raise ConfigurationError(details={"reason": "test_database_target_not_loopback"})
    if not _is_loopback_host(settings.supabase_url.host):
        raise ConfigurationError(details={"reason": "supabase_target_not_loopback"})
    if _normalized_database_identity(database_url) == _normalized_database_identity(
        test_database_url
    ):
        raise ConfigurationError(details={"reason": "test_database_matches_application_database"})
    return database_url, test_database_url


def assert_safe_local_test_target(settings: Settings) -> None:
    """Require the exact disposable full-stack database and API identities."""
    _, test_database_url = _assert_safe_local_configuration(settings)
    if _normalized_database_identity(test_database_url) != _DISPOSABLE_TEST_DATABASE_IDENTITY:
        raise ConfigurationError(details={"reason": "unexpected_disposable_test_database"})
    if (
        settings.supabase_url.scheme != "http"
        or settings.supabase_url.port != _DISPOSABLE_TEST_API_PORT
    ):
        raise ConfigurationError(details={"reason": "unexpected_disposable_test_api"})


def assert_safe_hosted_development_target(settings: Settings) -> None:
    """Require an explicit hosted profile before a hosted migration or smoke test."""
    database_url = make_url(settings.database_url.get_secret_value())

    if settings.persistence_environment is not PersistenceEnvironment.HOSTED_DEVELOPMENT:
        raise ConfigurationError(details={"reason": "hosted_profile_required"})
    if _is_loopback_host(database_url.host):
        raise ConfigurationError(details={"reason": "hosted_database_is_loopback"})
    if settings.supabase_project_ref not in (database_url.host or "") and not (
        database_url.username or ""
    ).endswith(settings.supabase_project_ref):
        raise ConfigurationError(details={"reason": "hosted_database_project_mismatch"})
    if settings.database_connection_mode is DatabaseConnectionMode.DIRECT:
        if (
            database_url.host != f"db.{settings.supabase_project_ref}.supabase.co"
            or database_url.port not in {None, 5432}
            or database_url.username != "postgres"
        ):
            raise ConfigurationError(details={"reason": "invalid_direct_database_target"})
    elif (
        database_url.host is None
        or not database_url.host.endswith(".pooler.supabase.com")
        or database_url.port != 5432
        or database_url.username != f"postgres.{settings.supabase_project_ref}"
    ):
        raise ConfigurationError(details={"reason": "invalid_session_pooler_target"})


def assert_safe_migration_target(settings: Settings) -> None:
    """Apply the corresponding fail-closed policy to every Alembic invocation."""
    if settings.persistence_environment is PersistenceEnvironment.LOCAL:
        _assert_safe_local_configuration(settings)
    else:
        assert_safe_hosted_development_target(settings)


def database_engine_options(settings: Settings) -> dict[str, Any]:
    """Translate typed settings into one stable SQLAlchemy engine policy."""
    postgres_options = (
        f"-c timezone=UTC -c statement_timeout={settings.database_statement_timeout_ms}"
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


def create_database_engine(
    settings: Settings,
    *,
    use_test_database: bool = False,
) -> AsyncEngine:
    """Create an async engine after applying test-target guards when requested."""
    if use_test_database:
        assert_safe_local_test_target(settings)
        database_url = settings.test_database_url.get_secret_value()
    else:
        database_url = settings.database_url.get_secret_value()
    return create_async_engine(database_url, **database_engine_options(settings))


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create sessions that never expire records implicitly after commit."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Own session cleanup while leaving commit decisions to application services."""
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
