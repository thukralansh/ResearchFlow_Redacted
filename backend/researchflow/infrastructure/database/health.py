"""Redacted database connectivity health check."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    database_name: str
    current_schema: str
    timezone: str


async def check_database_health(engine: AsyncEngine) -> DatabaseHealth:
    """Read database identity in a transaction that is always rolled back."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            row = (
                await connection.execute(
                    text("select current_database(), current_schema(), current_setting('TimeZone')")
                )
            ).one()
        finally:
            await transaction.rollback()

    return DatabaseHealth(
        database_name=str(row[0]),
        current_schema=str(row[1]),
        timezone=str(row[2]),
    )
