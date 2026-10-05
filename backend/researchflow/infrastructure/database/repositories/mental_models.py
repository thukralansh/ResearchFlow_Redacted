"""Mental Model queries that keep structured current state explicit."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.mental_model import (
    MentalModelAssessmentRecord,
    MentalModelMemberRecord,
    MentalModelRecord,
    MentalModelRenderingRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    MentalModelAssessmentData,
    MentalModelData,
    MentalModelMemberData,
    MentalModelRenderingData,
    NewMentalModel,
    NewMentalModelAssessment,
    NewMentalModelMember,
    NewMentalModelRendering,
)


def _to_assessment(row: MentalModelAssessmentRecord) -> MentalModelAssessmentData:
    return MentalModelAssessmentData(
        id=row.id,
        project_id=row.project_id,
        assessed_model_id=row.assessed_model_id,
        repository_snapshot_id=row.repository_snapshot_id,
        research_question_id=row.research_question_id,
        method_model_id=row.method_model_id,
        method_model_assessment_id=row.method_model_assessment_id,
        method_model_fingerprint=row.method_model_fingerprint,
        repository_evidence_fingerprint=row.repository_evidence_fingerprint,
        accepted_knowledge_fingerprint=row.accepted_knowledge_fingerprint,
        accepted_mapping_fingerprint=row.accepted_mapping_fingerprint,
        current_judgment_fingerprint=row.current_judgment_fingerprint,
        result=row.result,
        resulting_model_id=row.resulting_model_id,
        rationale=row.rationale,
        generation_record_id=row.generation_record_id,
    )


def _to_mental_model(row: MentalModelRecord) -> MentalModelData:
    return MentalModelData(
        id=row.id,
        project_id=row.project_id,
        research_question_id=row.research_question_id,
        method_model_id=row.method_model_id,
        lineage_id=row.lineage_id,
        version=row.version,
        title=row.title,
        status=row.status,
        confidence=row.confidence,
        composition_fingerprint=row.composition_fingerprint,
        generation_record_id=row.generation_record_id,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
    )


def _to_member(row: MentalModelMemberRecord) -> MentalModelMemberData:
    return MentalModelMemberData(
        id=row.id,
        mental_model_id=row.mental_model_id,
        knowledge_object_id=row.knowledge_object_id,
        human_judgment_id=row.human_judgment_id,
        evidence_link_id=row.evidence_link_id,
        role=row.role,
        inclusion_rationale=row.inclusion_rationale,
        confidence=row.confidence,
    )


def _to_rendering(row: MentalModelRenderingRecord) -> MentalModelRenderingData:
    return MentalModelRenderingData(
        id=row.id,
        mental_model_id=row.mental_model_id,
        rendering_type=row.rendering_type,
        content=row.content,
        prompt_version=row.prompt_version,
        provider=row.provider,
        model_version=row.model_version,
        generation_record_id=row.generation_record_id,
    )


class SqlAlchemyMentalModelsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, model: NewMentalModel) -> MentalModelData:
        if model.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = MentalModelRecord(
            id=model.id,
            project_id=model.project_id,
            research_question_id=model.research_question_id,
            method_model_id=model.method_model_id,
            lineage_id=model.lineage_id,
            version=model.version,
            title=model.title,
            status=model.status,
            confidence=model.confidence,
            composition_fingerprint=model.composition_fingerprint,
            generation_record_id=model.generation_record_id,
            derived_from_id=model.derived_from_id,
            supersedes_id=model.supersedes_id,
            created_by_type=model.created_by_type,
            created_by_id=model.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_mental_model(row)

    async def add_member(self, member: NewMentalModelMember) -> MentalModelMemberData:
        row = MentalModelMemberRecord(
            id=member.id,
            mental_model_id=member.mental_model_id,
            knowledge_object_id=member.knowledge_object_id,
            human_judgment_id=member.human_judgment_id,
            evidence_link_id=member.evidence_link_id,
            role=member.role,
            inclusion_rationale=member.inclusion_rationale,
            confidence=member.confidence,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_member(row)

    async def add_rendering(self, rendering: NewMentalModelRendering) -> MentalModelRenderingData:
        row = MentalModelRenderingRecord(
            id=rendering.id,
            mental_model_id=rendering.mental_model_id,
            rendering_type=rendering.rendering_type,
            content=rendering.content,
            prompt_version=rendering.prompt_version,
            provider=rendering.provider,
            model_version=rendering.model_version,
            generation_record_id=rendering.generation_record_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_rendering(row)

    async def add_assessment(
        self, assessment: NewMentalModelAssessment
    ) -> MentalModelAssessmentData:
        if assessment.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = MentalModelAssessmentRecord(
            id=assessment.id,
            project_id=assessment.project_id,
            assessed_model_id=assessment.assessed_model_id,
            repository_snapshot_id=assessment.repository_snapshot_id,
            research_question_id=assessment.research_question_id,
            method_model_id=assessment.method_model_id,
            method_model_assessment_id=assessment.method_model_assessment_id,
            method_model_fingerprint=assessment.method_model_fingerprint,
            repository_evidence_fingerprint=assessment.repository_evidence_fingerprint,
            accepted_knowledge_fingerprint=assessment.accepted_knowledge_fingerprint,
            accepted_mapping_fingerprint=assessment.accepted_mapping_fingerprint,
            current_judgment_fingerprint=assessment.current_judgment_fingerprint,
            result=assessment.result,
            resulting_model_id=assessment.resulting_model_id,
            rationale=assessment.rationale,
            generation_record_id=assessment.generation_record_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_assessment(row)

    async def get_assessment_exact(self, assessment_id: UUID) -> MentalModelAssessmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelAssessmentRecord).where(
                    MentalModelAssessmentRecord.id == assessment_id,
                    MentalModelAssessmentRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_assessment(row)

    async def get_assessment_by_generation(
        self, generation_record_id: UUID
    ) -> MentalModelAssessmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelAssessmentRecord).where(
                    MentalModelAssessmentRecord.project_id == self._project_id,
                    MentalModelAssessmentRecord.generation_record_id == generation_record_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_assessment(row)

    async def latest_assessment_for_model(self, model_id: UUID) -> MentalModelAssessmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelAssessmentRecord)
                .where(
                    MentalModelAssessmentRecord.project_id == self._project_id,
                    (
                        (MentalModelAssessmentRecord.assessed_model_id == model_id)
                        | (MentalModelAssessmentRecord.resulting_model_id == model_id)
                    ),
                )
                .order_by(
                    MentalModelAssessmentRecord.created_at.desc(),
                    MentalModelAssessmentRecord.id.desc(),
                )
                .limit(1)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_assessment(row)

    async def get_exact(self, model_id: UUID) -> MentalModelData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelRecord).where(
                    MentalModelRecord.id == model_id,
                    MentalModelRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_mental_model(row)

    async def get_exact_for_update(
        self, model_id: UUID, *, nowait: bool = False
    ) -> MentalModelData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelRecord)
                .where(
                    MentalModelRecord.id == model_id,
                    MentalModelRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_mental_model(row)

    async def get_by_generation_record(self, generation_record_id: UUID) -> MentalModelData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelRecord)
                .where(
                    MentalModelRecord.project_id == self._project_id,
                    MentalModelRecord.generation_record_id == generation_record_id,
                )
                .order_by(MentalModelRecord.created_at, MentalModelRecord.id)
            )
        )
        row = result.scalars().first()
        return None if row is None else _to_mental_model(row)

    async def list_members(self, model_id: UUID) -> tuple[MentalModelMemberData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelMemberRecord)
                .join(
                    MentalModelRecord,
                    MentalModelRecord.id == MentalModelMemberRecord.mental_model_id,
                )
                .where(
                    MentalModelMemberRecord.mental_model_id == model_id,
                    MentalModelRecord.project_id == self._project_id,
                )
                .order_by(MentalModelMemberRecord.role, MentalModelMemberRecord.id)
            )
        )
        return tuple(_to_member(row) for row in result.scalars())

    async def list_renderings(self, model_id: UUID) -> tuple[MentalModelRenderingData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelRenderingRecord)
                .join(
                    MentalModelRecord,
                    MentalModelRecord.id == MentalModelRenderingRecord.mental_model_id,
                )
                .where(
                    MentalModelRenderingRecord.mental_model_id == model_id,
                    MentalModelRecord.project_id == self._project_id,
                )
                .order_by(MentalModelRenderingRecord.created_at, MentalModelRenderingRecord.id)
            )
        )
        return tuple(_to_rendering(row) for row in result.scalars())

    async def mark_superseded(self, model_id: UUID) -> MentalModelData:
        result = await execute_database(
            lambda: self._session.execute(
                update(MentalModelRecord)
                .where(
                    MentalModelRecord.id == model_id,
                    MentalModelRecord.project_id == self._project_id,
                    MentalModelRecord.status == "current",
                )
                .values(status="superseded")
                .returning(MentalModelRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "mental_model_not_current"})
        return _to_mental_model(row)

    async def get_current(self) -> MentalModelData | None:
        return await self._get_current(lock=False, nowait=False)

    async def get_current_for_update(self, *, nowait: bool = False) -> MentalModelData | None:
        return await self._get_current(lock=True, nowait=nowait)

    async def list_history(self) -> tuple[MentalModelData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(MentalModelRecord)
                .where(MentalModelRecord.project_id == self._project_id)
                .order_by(
                    MentalModelRecord.lineage_id,
                    MentalModelRecord.version.desc(),
                    MentalModelRecord.id,
                )
            )
        )
        return tuple(_to_mental_model(row) for row in result.scalars())

    async def _get_current(self, *, lock: bool, nowait: bool) -> MentalModelData | None:
        statement = select(MentalModelRecord).where(
            MentalModelRecord.project_id == self._project_id,
            MentalModelRecord.status == "current",
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_mental_model(row)
