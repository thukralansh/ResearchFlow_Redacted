"""Read-only actor-scoped Project discovery for the internal MVP."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from researchflow.infrastructure.database.models.project import ProjectRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.infrastructure.database.repositories.projects import _to_project
from researchflow.ports.repositories import ProjectData


class SqlAlchemyProjectDirectory:
    """List only Projects attributed to the trusted request actor."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_for_actor(self, actor_id: UUID) -> tuple[ProjectData, ...]:
        async with self._session_factory() as session:
            result = await execute_database(
                lambda: session.execute(
                    select(ProjectRecord)
                    .where(ProjectRecord.created_by_id == actor_id)
                    .order_by(ProjectRecord.updated_at.desc(), ProjectRecord.id)
                )
            )
            return tuple(_to_project(row) for row in result.scalars())
