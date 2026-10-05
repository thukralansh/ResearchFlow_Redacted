"""Narrow SQLAlchemy-to-transport-neutral persistence error translation."""

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from researchflow.errors import InternalError, ResourceConflictError


def conflict(reason: str) -> ResourceConflictError:
    return ResourceConflictError(details={"reason": reason})


def internal_database_error(error: Exception) -> InternalError:
    return InternalError(details={"reason": "database_operation_failed"}, cause=error)


def translate_integrity_error(error: IntegrityError) -> ResourceConflictError:
    return conflict("persistence_constraint_conflict")


def translate_lock_error(error: DBAPIError) -> ResourceConflictError | InternalError:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate in {"40P01", "40001", "55P03"}:
        return conflict("concurrent_write_conflict")
    return internal_database_error(error)


async def execute_database[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Translate adapter failures without taking ownership of rollback."""
    try:
        return await operation()
    except IntegrityError as error:
        raise translate_integrity_error(error) from error
    except DBAPIError as error:
        raise translate_lock_error(error) from error
    except SQLAlchemyError as error:
        raise internal_database_error(error) from error
