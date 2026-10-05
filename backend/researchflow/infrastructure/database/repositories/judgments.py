"""Judgment Request and immutable Human Judgment query adapter."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.judgment import (
    HumanJudgmentRecord,
    JudgmentOptionRecord,
    JudgmentRequestRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    HumanJudgmentData,
    JudgmentOptionData,
    JudgmentRequestData,
    NewHumanJudgment,
    NewJudgmentOption,
    NewJudgmentRequest,
)


def _to_request(row: JudgmentRequestRecord) -> JudgmentRequestData:
    return JudgmentRequestData(
        id=row.id,
        project_id=row.project_id,
        ambiguity_id=row.ambiguity_id,
        lineage_id=row.lineage_id,
        version=row.version,
        title=row.title,
        decision_question=row.decision_question,
        why_human_required=row.why_human_required,
        status=row.status,
        lifecycle_status=row.lifecycle_status,
        workflow_run_id=row.workflow_run_id,
        generation_record_id=row.generation_record_id,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
    )


def _to_option(row: JudgmentOptionRecord) -> JudgmentOptionData:
    return JudgmentOptionData(
        id=row.id,
        judgment_request_id=row.judgment_request_id,
        title=row.title,
        description=row.description,
        supporting_evidence=tuple(row.supporting_evidence),
        conflicting_evidence=tuple(row.conflicting_evidence),
        implementation_consequences=row.implementation_consequences,
        rank=row.rank,
    )


def _to_judgment(row: HumanJudgmentRecord) -> HumanJudgmentData:
    return HumanJudgmentData(
        id=row.id,
        project_id=row.project_id,
        judgment_request_id=row.judgment_request_id,
        lineage_id=row.lineage_id,
        version=row.version,
        selected_option_id=row.selected_option_id,
        custom_decision=row.custom_decision,
        rationale=row.rationale,
        confidence=row.confidence,
        lifecycle_status=row.lifecycle_status,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
    )


class SqlAlchemyJudgmentsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add_request(self, request: NewJudgmentRequest) -> JudgmentRequestData:
        if request.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = JudgmentRequestRecord(
            id=request.id,
            project_id=request.project_id,
            ambiguity_id=request.ambiguity_id,
            lineage_id=request.lineage_id,
            version=request.version,
            title=request.title,
            decision_question=request.decision_question,
            why_human_required=request.why_human_required,
            status="open",
            lifecycle_status="current",
            workflow_run_id=request.workflow_run_id,
            generation_record_id=request.generation_record_id,
            derived_from_id=request.derived_from_id,
            supersedes_id=request.supersedes_id,
            created_by_type=request.created_by_type,
            created_by_id=request.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_request(row)

    async def add_option(self, option: NewJudgmentOption) -> JudgmentOptionData:
        row = JudgmentOptionRecord(
            id=option.id,
            judgment_request_id=option.judgment_request_id,
            title=option.title,
            description=option.description,
            supporting_evidence=list(option.supporting_evidence),
            conflicting_evidence=list(option.conflicting_evidence),
            implementation_consequences=option.implementation_consequences,
            rank=option.rank,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_option(row)

    async def get_request_exact(self, request_id: UUID) -> JudgmentRequestData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(JudgmentRequestRecord).where(
                    JudgmentRequestRecord.id == request_id,
                    JudgmentRequestRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_request(row)

    async def get_open_request_for_ambiguity(
        self, ambiguity_id: UUID
    ) -> JudgmentRequestData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(JudgmentRequestRecord).where(
                    JudgmentRequestRecord.project_id == self._project_id,
                    JudgmentRequestRecord.ambiguity_id == ambiguity_id,
                    JudgmentRequestRecord.status == "open",
                    JudgmentRequestRecord.lifecycle_status == "current",
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_request(row)

    async def list_requests(self) -> tuple[JudgmentRequestData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(JudgmentRequestRecord)
                .where(JudgmentRequestRecord.project_id == self._project_id)
                .order_by(JudgmentRequestRecord.created_at, JudgmentRequestRecord.id)
            )
        )
        return tuple(_to_request(row) for row in result.scalars())

    async def list_options(self, request_id: UUID) -> tuple[JudgmentOptionData, ...]:
        if await self.get_request_exact(request_id) is None:
            return ()
        result = await execute_database(
            lambda: self._session.execute(
                select(JudgmentOptionRecord)
                .where(JudgmentOptionRecord.judgment_request_id == request_id)
                .order_by(JudgmentOptionRecord.rank, JudgmentOptionRecord.id)
            )
        )
        return tuple(_to_option(row) for row in result.scalars())

    async def get_request_for_update(
        self, request_id: UUID, *, nowait: bool = False
    ) -> JudgmentRequestData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(JudgmentRequestRecord)
                .where(
                    JudgmentRequestRecord.id == request_id,
                    JudgmentRequestRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_request(row)

    async def resolve_request(self, request_id: UUID) -> JudgmentRequestData:
        result = await execute_database(
            lambda: self._session.execute(
                update(JudgmentRequestRecord)
                .where(
                    JudgmentRequestRecord.id == request_id,
                    JudgmentRequestRecord.project_id == self._project_id,
                    JudgmentRequestRecord.status == "open",
                    JudgmentRequestRecord.lifecycle_status == "current",
                )
                .values(status="resolved", resolved_at=func.now())
                .returning(JudgmentRequestRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "judgment_request_not_open"})
        return _to_request(row)

    async def add_judgment(self, judgment: NewHumanJudgment) -> HumanJudgmentData:
        if judgment.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = HumanJudgmentRecord(
            id=judgment.id,
            project_id=judgment.project_id,
            judgment_request_id=judgment.judgment_request_id,
            lineage_id=judgment.lineage_id,
            version=judgment.version,
            selected_option_id=judgment.selected_option_id,
            custom_decision=judgment.custom_decision,
            rationale=judgment.rationale,
            confidence=judgment.confidence,
            lifecycle_status="current",
            derived_from_id=judgment.derived_from_id,
            supersedes_id=judgment.supersedes_id,
            created_by_id=judgment.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_judgment(row)

    async def supersede_judgment(
        self, judgment_id: UUID, *, expected_version: int
    ) -> HumanJudgmentData:
        result = await execute_database(
            lambda: self._session.execute(
                update(HumanJudgmentRecord)
                .where(
                    HumanJudgmentRecord.id == judgment_id,
                    HumanJudgmentRecord.project_id == self._project_id,
                    HumanJudgmentRecord.version == expected_version,
                    HumanJudgmentRecord.lifecycle_status == "current",
                )
                .values(lifecycle_status="superseded")
                .returning(HumanJudgmentRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_human_judgment"})
        return _to_judgment(row)

    async def get_judgment_exact(self, judgment_id: UUID) -> HumanJudgmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(HumanJudgmentRecord).where(
                    HumanJudgmentRecord.id == judgment_id,
                    HumanJudgmentRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_judgment(row)

    async def get_current_for_request(self, request_id: UUID) -> HumanJudgmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(HumanJudgmentRecord).where(
                    HumanJudgmentRecord.project_id == self._project_id,
                    HumanJudgmentRecord.judgment_request_id == request_id,
                    HumanJudgmentRecord.lifecycle_status == "current",
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_judgment(row)

    async def list_current(self) -> tuple[HumanJudgmentData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(HumanJudgmentRecord)
                .where(
                    HumanJudgmentRecord.project_id == self._project_id,
                    HumanJudgmentRecord.lifecycle_status == "current",
                )
                .order_by(HumanJudgmentRecord.created_at, HumanJudgmentRecord.id)
            )
        )
        return tuple(_to_judgment(row) for row in result.scalars())

    async def list_for_request(self, request_id: UUID) -> tuple[HumanJudgmentData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(HumanJudgmentRecord)
                .where(
                    HumanJudgmentRecord.project_id == self._project_id,
                    HumanJudgmentRecord.judgment_request_id == request_id,
                )
                .order_by(HumanJudgmentRecord.version, HumanJudgmentRecord.id)
            )
        )
        return tuple(_to_judgment(row) for row in result.scalars())
