"""Project-scoped SQLAlchemy persistence adapter."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.project import ProjectRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import NewProject, ProjectData


def _to_project(row: ProjectRecord) -> ProjectData:
    return ProjectData(
        id=row.id,
        primary_research_question_id=row.primary_research_question_id,
        title=row.title,
        description=row.description,
        target_outcome=row.target_outcome,
        reproduction_depth=row.reproduction_depth,
        created_by_id=row.created_by_id,
        lock_version=row.lock_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyProjectsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, project: NewProject) -> ProjectData:
        if project.id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})

        existing = await self.get()
        if existing is not None:
            expected = (
                project.primary_research_question_id,
                project.title,
                project.description,
                project.target_outcome,
                project.reproduction_depth,
                project.created_by_id,
            )
            actual = (
                existing.primary_research_question_id,
                existing.title,
                existing.description,
                existing.target_outcome,
                existing.reproduction_depth,
                existing.created_by_id,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "project_id_reused"})

        row = ProjectRecord(
            id=project.id,
            primary_research_question_id=project.primary_research_question_id,
            title=project.title,
            description=project.description,
            target_outcome=project.target_outcome,
            reproduction_depth=project.reproduction_depth,
            created_by_id=project.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_project(row)

    async def get(self) -> ProjectData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(ProjectRecord).where(ProjectRecord.id == self._project_id)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_project(row)

    async def get_for_update(self, *, nowait: bool = False) -> ProjectData | None:
        statement = (
            select(ProjectRecord)
            .where(ProjectRecord.id == self._project_id)
            .with_for_update(nowait=nowait)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_project(row)

    async def update_configuration(
        self,
        *,
        expected_lock_version: int,
        title: str,
        description: str | None,
        target_outcome: str,
        reproduction_depth: str,
    ) -> ProjectData:
        statement = (
            update(ProjectRecord)
            .where(
                ProjectRecord.id == self._project_id,
                ProjectRecord.lock_version == expected_lock_version,
            )
            .values(
                title=title,
                description=description,
                target_outcome=target_outcome,
                reproduction_depth=reproduction_depth,
                lock_version=ProjectRecord.lock_version + 1,
                updated_at=func.now(),
            )
            .returning(ProjectRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            if await self.get() is None:
                raise ResourceConflictError(details={"reason": "project_not_found"})
            raise ResourceConflictError(details={"reason": "stale_lock_version"})
        return _to_project(row)

    async def replace_primary_research_question(
        self,
        *,
        expected_lock_version: int,
        expected_question_id: UUID,
        new_question_id: UUID,
    ) -> ProjectData:
        statement = (
            update(ProjectRecord)
            .where(
                ProjectRecord.id == self._project_id,
                ProjectRecord.lock_version == expected_lock_version,
                ProjectRecord.primary_research_question_id == expected_question_id,
            )
            .values(
                primary_research_question_id=new_question_id,
                lock_version=ProjectRecord.lock_version + 1,
                updated_at=func.now(),
            )
            .returning(ProjectRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            existing = await self.get()
            if existing is None:
                raise ResourceConflictError(details={"reason": "project_not_found"})
            if existing.lock_version != expected_lock_version:
                raise ResourceConflictError(details={"reason": "stale_lock_version"})
            raise ResourceConflictError(details={"reason": "stale_primary_question"})
        return _to_project(row)
