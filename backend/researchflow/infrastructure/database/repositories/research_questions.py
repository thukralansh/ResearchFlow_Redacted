"""Research Question persistence with explicit current and historical queries."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.research_question import (
    ResearchQuestionRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import NewResearchQuestion, ResearchQuestionData


def _to_question(row: ResearchQuestionRecord) -> ResearchQuestionData:
    return ResearchQuestionData(
        id=row.id,
        project_id=row.project_id,
        parent_question_id=row.parent_question_id,
        lineage_id=row.lineage_id,
        version=row.version,
        question_text=row.question_text,
        question_type=row.question_type,
        scope_text=row.scope_text,
        inquiry_status=row.inquiry_status,
        lifecycle_status=row.lifecycle_status,
        priority=row.priority,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyResearchQuestionsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, question: NewResearchQuestion) -> ResearchQuestionData:
        if question.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_exact(question.id)
        if existing is not None:
            expected = (
                question.parent_question_id,
                question.lineage_id,
                question.version,
                question.question_text,
                question.question_type,
                question.scope_text,
                question.inquiry_status,
                question.lifecycle_status,
                question.priority,
                question.created_by_type,
                question.created_by_id,
                question.derived_from_id,
                question.supersedes_id,
            )
            actual = (
                existing.parent_question_id,
                existing.lineage_id,
                existing.version,
                existing.question_text,
                existing.question_type,
                existing.scope_text,
                existing.inquiry_status,
                existing.lifecycle_status,
                existing.priority,
                existing.created_by_type,
                existing.created_by_id,
                existing.derived_from_id,
                existing.supersedes_id,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "research_question_id_reused"})

        row = ResearchQuestionRecord(
            id=question.id,
            project_id=question.project_id,
            parent_question_id=question.parent_question_id,
            lineage_id=question.lineage_id,
            version=question.version,
            question_text=question.question_text,
            question_type=question.question_type,
            scope_text=question.scope_text,
            inquiry_status=question.inquiry_status,
            lifecycle_status=question.lifecycle_status,
            priority=question.priority,
            created_by_type=question.created_by_type,
            created_by_id=question.created_by_id,
            derived_from_id=question.derived_from_id,
            supersedes_id=question.supersedes_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_question(row)

    async def get_exact(self, question_id: UUID) -> ResearchQuestionData | None:
        return await self._get_exact(question_id, lock=False, nowait=False)

    async def get_exact_for_update(
        self, question_id: UUID, *, nowait: bool = False
    ) -> ResearchQuestionData | None:
        return await self._get_exact(question_id, lock=True, nowait=nowait)

    async def _get_exact(
        self, question_id: UUID, *, lock: bool, nowait: bool
    ) -> ResearchQuestionData | None:
        statement = select(ResearchQuestionRecord).where(
            ResearchQuestionRecord.id == question_id,
            ResearchQuestionRecord.project_id == self._project_id,
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_question(row)

    async def get_current_primary(self) -> ResearchQuestionData | None:
        return await self._get_current_primary(lock=False, nowait=False)

    async def get_current_primary_for_update(
        self, *, nowait: bool = False
    ) -> ResearchQuestionData | None:
        return await self._get_current_primary(lock=True, nowait=nowait)

    async def _get_current_primary(
        self, *, lock: bool, nowait: bool
    ) -> ResearchQuestionData | None:
        statement = select(ResearchQuestionRecord).where(
            ResearchQuestionRecord.project_id == self._project_id,
            ResearchQuestionRecord.question_type == "primary",
            ResearchQuestionRecord.lifecycle_status == "current",
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_question(row)

    async def list_current(self) -> tuple[ResearchQuestionData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(ResearchQuestionRecord)
                .where(
                    ResearchQuestionRecord.project_id == self._project_id,
                    ResearchQuestionRecord.lifecycle_status == "current",
                )
                .order_by(ResearchQuestionRecord.created_at, ResearchQuestionRecord.id)
            )
        )
        return tuple(_to_question(row) for row in result.scalars())

    async def mark_superseded(
        self, question_id: UUID, *, expected_version: int
    ) -> ResearchQuestionData:
        statement = (
            update(ResearchQuestionRecord)
            .where(
                ResearchQuestionRecord.id == question_id,
                ResearchQuestionRecord.project_id == self._project_id,
                ResearchQuestionRecord.version == expected_version,
                ResearchQuestionRecord.lifecycle_status == "current",
            )
            .values(lifecycle_status="superseded", updated_at=func.now())
            .returning(ResearchQuestionRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_research_question"})
        return _to_question(row)

    async def update_inquiry_status(
        self,
        question_id: UUID,
        *,
        expected_version: int,
        expected_status: str,
        new_status: str,
    ) -> ResearchQuestionData:
        statement = (
            update(ResearchQuestionRecord)
            .where(
                ResearchQuestionRecord.id == question_id,
                ResearchQuestionRecord.project_id == self._project_id,
                ResearchQuestionRecord.version == expected_version,
                ResearchQuestionRecord.inquiry_status == expected_status,
                ResearchQuestionRecord.lifecycle_status == "current",
            )
            .values(inquiry_status=new_status, updated_at=func.now())
            .returning(ResearchQuestionRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_research_question"})
        return _to_question(row)
