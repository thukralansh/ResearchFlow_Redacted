"""Blueprint eligibility and derived Blueprint persistence adapter."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.infrastructure.database.models.blueprint import (
    BlueprintEligibilityAssessmentRecord,
    BlueprintEligibilityItemRecord,
    BlueprintSectionRecord,
    ImplementationBlueprintRecord,
    ImplementationStepRecord,
    VerificationCheckRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    BlueprintEligibilityAssessmentData,
    BlueprintEligibilityItemData,
    BlueprintSectionData,
    ImplementationBlueprintData,
    ImplementationStepData,
    NewBlueprintEligibilityAssessment,
    NewBlueprintEligibilityItem,
    NewBlueprintSection,
    NewImplementationBlueprint,
    NewImplementationStep,
    NewVerificationCheck,
    VerificationCheckData,
)

_ITEM_TARGET_COLUMNS = {
    "research_question": "research_question_id",
    "research_question_source": "research_question_source_id",
    "source_processing_run": "source_processing_run_id",
    "paper_repository_relationship": "paper_repository_relationship_id",
    "repository_snapshot": "repository_snapshot_id",
    "repository_component": "repository_component_id",
    "knowledge_object": "knowledge_object_id",
    "evidence_link": "evidence_link_id",
    "grounding_assessment": "grounding_assessment_id",
    "method_model": "method_model_id",
    "method_model_assessment": "method_model_assessment_id",
    "method_element": "method_element_id",
    "paper_code_mapping": "paper_code_mapping_id",
    "mental_model": "mental_model_id",
    "mental_model_assessment": "mental_model_assessment_id",
    "human_judgment": "human_judgment_id",
    "blueprint_eligibility_assessment": "blueprint_eligibility_assessment_id",
    "implementation_blueprint": "implementation_blueprint_id",
    "blueprint_section": "blueprint_section_id",
    "implementation_step": "implementation_step_id",
    "verification_check": "verification_check_id",
}


def _to_eligibility(
    row: BlueprintEligibilityAssessmentRecord,
) -> BlueprintEligibilityAssessmentData:
    return BlueprintEligibilityAssessmentData(
        id=row.id,
        project_id=row.project_id,
        research_question_id=row.research_question_id,
        method_model_id=row.method_model_id,
        method_model_assessment_id=row.method_model_assessment_id,
        mental_model_id=row.mental_model_id,
        mental_model_assessment_id=row.mental_model_assessment_id,
        paper_repository_relationship_id=row.paper_repository_relationship_id,
        repository_snapshot_id=row.repository_snapshot_id,
        reproduction_depth=row.reproduction_depth,
        input_fingerprint=row.input_fingerprint,
        eligibility_rule_version=row.eligibility_rule_version,
        mapping_coverage=dict(row.mapping_coverage),
        status=row.status,
    )


def _to_eligibility_item(row: BlueprintEligibilityItemRecord) -> BlueprintEligibilityItemData:
    target_column = _ITEM_TARGET_COLUMNS[row.expected_dependency_type]
    return BlueprintEligibilityItemData(
        id=row.id,
        assessment_id=row.assessment_id,
        sequence_index=row.sequence_index,
        expected_dependency_type=row.expected_dependency_type,
        target_id=getattr(row, target_column),
        criticality=row.criticality,
        outcome=row.outcome,
        reason_code=row.reason_code,
        explanation=row.explanation,
    )


def _to_blueprint(row: ImplementationBlueprintRecord) -> ImplementationBlueprintData:
    return ImplementationBlueprintData(
        id=row.id,
        project_id=row.project_id,
        lineage_id=row.lineage_id,
        version=row.version,
        eligibility_assessment_id=row.eligibility_assessment_id,
        eligibility_status=row.eligibility_status,
        research_question_id=row.research_question_id,
        method_model_id=row.method_model_id,
        method_model_assessment_id=row.method_model_assessment_id,
        mental_model_id=row.mental_model_id,
        mental_model_assessment_id=row.mental_model_assessment_id,
        paper_repository_relationship_id=row.paper_repository_relationship_id,
        repository_snapshot_id=row.repository_snapshot_id,
        status=row.status,
        confidence=row.confidence,
        generation_record_id=row.generation_record_id,
        supersedes_id=row.supersedes_id,
    )


def _to_section(row: BlueprintSectionRecord) -> BlueprintSectionData:
    return BlueprintSectionData(
        id=row.id,
        project_id=row.project_id,
        blueprint_id=row.blueprint_id,
        sequence_index=row.sequence_index,
        section_type=row.section_type,
        content_schema_version=row.content_schema_version,
        structured_content=dict(row.structured_content),
        explicitness=row.explicitness,
        confidence=row.confidence,
        review_status=row.review_status,
    )


def _to_step(row: ImplementationStepRecord) -> ImplementationStepData:
    return ImplementationStepData(
        id=row.id,
        project_id=row.project_id,
        blueprint_id=row.blueprint_id,
        lineage_id=row.lineage_id,
        version=row.version,
        sequence_index=row.sequence_index,
        title=row.title,
        description=row.description,
        lifecycle_status=row.lifecycle_status,
        freshness_status=row.freshness_status,
        generation_record_id=row.generation_record_id,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
    )


def _to_check(row: VerificationCheckRecord) -> VerificationCheckData:
    return VerificationCheckData(
        id=row.id,
        project_id=row.project_id,
        blueprint_id=row.blueprint_id,
        blueprint_section_id=row.blueprint_section_id,
        implementation_step_id=row.implementation_step_id,
        lineage_id=row.lineage_id,
        version=row.version,
        sequence_index=row.sequence_index,
        condition=row.condition,
        test_type=row.test_type,
        expected_result=row.expected_result,
        failure_meaning=row.failure_meaning,
        likely_causes=tuple(str(item) for item in row.likely_causes),
        lifecycle_status=row.lifecycle_status,
        freshness_status=row.freshness_status,
        generation_record_id=row.generation_record_id,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
        actual_result=row.actual_result,
        result_status=row.result_status,
        result_actor_id=row.result_actor_id,
        result_recorded_at=row.result_recorded_at,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
    )


class SqlAlchemyBlueprintsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add_eligibility(
        self, assessment: NewBlueprintEligibilityAssessment
    ) -> BlueprintEligibilityAssessmentData:
        if assessment.project_id != self._project_id:
            raise DomainValidationError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_eligibility_exact(assessment.id)
        if existing is not None:
            if (
                existing.input_fingerprint == assessment.input_fingerprint
                and existing.eligibility_rule_version == assessment.eligibility_rule_version
                and existing.reproduction_depth == assessment.reproduction_depth
            ):
                return existing
            raise ResourceConflictError(details={"reason": "eligibility_id_reused"})
        row = BlueprintEligibilityAssessmentRecord(**asdict(assessment))
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_eligibility(row)

    async def add_eligibility_item(
        self, item: NewBlueprintEligibilityItem
    ) -> BlueprintEligibilityItemData:
        assessment = await self.get_eligibility_exact(item.assessment_id)
        if assessment is None:
            raise ResourceNotFoundError(details={"resource": "blueprint_eligibility_assessment"})
        target_column = _ITEM_TARGET_COLUMNS.get(item.expected_dependency_type)
        if target_column is None:
            raise DomainValidationError(details={"reason": "unsupported_dependency_type"})
        existing_result = await execute_database(
            lambda: self._session.execute(
                select(BlueprintEligibilityItemRecord).where(
                    BlueprintEligibilityItemRecord.id == item.id
                )
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            replay = _to_eligibility_item(existing)
            if replay == BlueprintEligibilityItemData(**asdict(item)):
                return replay
            raise ResourceConflictError(details={"reason": "eligibility_item_id_reused"})
        values = {
            "id": item.id,
            "assessment_id": item.assessment_id,
            "sequence_index": item.sequence_index,
            "expected_dependency_type": item.expected_dependency_type,
            "criticality": item.criticality,
            "outcome": item.outcome,
            "reason_code": item.reason_code,
            "explanation": item.explanation,
        }
        if item.target_id is not None:
            values[target_column] = item.target_id
        row = BlueprintEligibilityItemRecord(**values)
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_eligibility_item(row)

    async def get_eligibility_by_input(
        self, *, input_fingerprint: str, eligibility_rule_version: str, reproduction_depth: str
    ) -> BlueprintEligibilityAssessmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(BlueprintEligibilityAssessmentRecord).where(
                    BlueprintEligibilityAssessmentRecord.project_id == self._project_id,
                    BlueprintEligibilityAssessmentRecord.input_fingerprint == input_fingerprint,
                    BlueprintEligibilityAssessmentRecord.eligibility_rule_version
                    == eligibility_rule_version,
                    BlueprintEligibilityAssessmentRecord.reproduction_depth == reproduction_depth,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_eligibility(row)

    async def get_eligibility_exact(
        self, assessment_id: UUID
    ) -> BlueprintEligibilityAssessmentData | None:
        return await self._get_eligibility(assessment_id, lock=False, nowait=False)

    async def get_eligibility_for_update(
        self, assessment_id: UUID, *, nowait: bool = False
    ) -> BlueprintEligibilityAssessmentData | None:
        return await self._get_eligibility(assessment_id, lock=True, nowait=nowait)

    async def _get_eligibility(
        self, assessment_id: UUID, *, lock: bool, nowait: bool
    ) -> BlueprintEligibilityAssessmentData | None:
        statement = select(BlueprintEligibilityAssessmentRecord).where(
            BlueprintEligibilityAssessmentRecord.id == assessment_id,
            BlueprintEligibilityAssessmentRecord.project_id == self._project_id,
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_eligibility(row)

    async def get_exact(self, blueprint_id: UUID) -> ImplementationBlueprintData | None:
        return await self._get_exact(blueprint_id, lock=False, nowait=False)

    async def get_exact_for_update(
        self, blueprint_id: UUID, *, nowait: bool = False
    ) -> ImplementationBlueprintData | None:
        return await self._get_exact(blueprint_id, lock=True, nowait=nowait)

    async def _get_exact(
        self, blueprint_id: UUID, *, lock: bool, nowait: bool
    ) -> ImplementationBlueprintData | None:
        statement = select(ImplementationBlueprintRecord).where(
            ImplementationBlueprintRecord.id == blueprint_id,
            ImplementationBlueprintRecord.project_id == self._project_id,
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_blueprint(row)

    async def get_by_eligibility(self, assessment_id: UUID) -> ImplementationBlueprintData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(ImplementationBlueprintRecord)
                .where(
                    ImplementationBlueprintRecord.project_id == self._project_id,
                    ImplementationBlueprintRecord.eligibility_assessment_id == assessment_id,
                )
                .order_by(
                    ImplementationBlueprintRecord.version.desc(),
                    ImplementationBlueprintRecord.id.desc(),
                )
                .limit(1)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_blueprint(row)

    async def add(self, blueprint: NewImplementationBlueprint) -> ImplementationBlueprintData:
        if blueprint.project_id != self._project_id:
            raise DomainValidationError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_exact(blueprint.id)
        if existing is not None:
            if existing.generation_record_id == blueprint.generation_record_id:
                return existing
            raise ResourceConflictError(details={"reason": "blueprint_id_reused"})
        row = ImplementationBlueprintRecord(**asdict(blueprint))
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_blueprint(row)

    async def add_section(self, section: NewBlueprintSection) -> BlueprintSectionData:
        if section.project_id != self._project_id:
            raise DomainValidationError(details={"reason": "project_scope_mismatch"})
        result = await execute_database(
            lambda: self._session.execute(
                select(BlueprintSectionRecord).where(BlueprintSectionRecord.id == section.id)
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            replay = _to_section(existing)
            if replay == BlueprintSectionData(**asdict(section)):
                return replay
            raise ResourceConflictError(details={"reason": "blueprint_section_id_reused"})
        row = BlueprintSectionRecord(**asdict(section))
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_section(row)

    async def list_sections(self, blueprint_id: UUID) -> tuple[BlueprintSectionData, ...]:
        if await self.get_exact(blueprint_id) is None:
            raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
        result = await execute_database(
            lambda: self._session.execute(
                select(BlueprintSectionRecord)
                .where(BlueprintSectionRecord.blueprint_id == blueprint_id)
                .order_by(BlueprintSectionRecord.sequence_index)
            )
        )
        return tuple(_to_section(row) for row in result.scalars())

    async def list_history(self) -> tuple[ImplementationBlueprintData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(ImplementationBlueprintRecord)
                .where(ImplementationBlueprintRecord.project_id == self._project_id)
                .order_by(
                    ImplementationBlueprintRecord.lineage_id,
                    ImplementationBlueprintRecord.version.desc(),
                    ImplementationBlueprintRecord.id,
                )
            )
        )
        return tuple(_to_blueprint(row) for row in result.scalars())

    async def mark_superseded(
        self, blueprint_id: UUID, *, expected_status: str
    ) -> ImplementationBlueprintData:
        result = await execute_database(
            lambda: self._session.execute(
                update(ImplementationBlueprintRecord)
                .where(
                    ImplementationBlueprintRecord.id == blueprint_id,
                    ImplementationBlueprintRecord.project_id == self._project_id,
                    ImplementationBlueprintRecord.status == expected_status,
                )
                .values(status="superseded")
                .returning(ImplementationBlueprintRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "blueprint_not_current"})
        return _to_blueprint(row)

    async def update_status(
        self, blueprint_id: UUID, *, expected_status: str, status: str
    ) -> ImplementationBlueprintData:
        result = await execute_database(
            lambda: self._session.execute(
                update(ImplementationBlueprintRecord)
                .where(
                    ImplementationBlueprintRecord.id == blueprint_id,
                    ImplementationBlueprintRecord.project_id == self._project_id,
                    ImplementationBlueprintRecord.status == expected_status,
                )
                .values(status=status)
                .returning(ImplementationBlueprintRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "blueprint_status_changed"})
        return _to_blueprint(row)

    async def update_section_review_statuses(
        self, blueprint_id: UUID, *, expected_status: str, status: str
    ) -> tuple[BlueprintSectionData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                update(BlueprintSectionRecord)
                .where(
                    BlueprintSectionRecord.blueprint_id == blueprint_id,
                    BlueprintSectionRecord.project_id == self._project_id,
                    BlueprintSectionRecord.review_status == expected_status,
                )
                .values(review_status=status)
                .returning(BlueprintSectionRecord)
            )
        )
        rows = tuple(result.scalars())
        if not rows:
            raise ResourceConflictError(details={"reason": "blueprint_sections_not_reviewable"})
        return tuple(
            sorted((_to_section(row) for row in rows), key=lambda item: item.sequence_index)
        )

    async def list_eligibility_items(
        self, assessment_id: UUID
    ) -> tuple[BlueprintEligibilityItemData, ...]:
        assessment = await self.get_eligibility_exact(assessment_id)
        if assessment is None:
            raise ResourceNotFoundError(details={"resource": "blueprint_eligibility_assessment"})
        result = await execute_database(
            lambda: self._session.execute(
                select(BlueprintEligibilityItemRecord)
                .where(BlueprintEligibilityItemRecord.assessment_id == assessment_id)
                .order_by(BlueprintEligibilityItemRecord.sequence_index)
            )
        )
        return tuple(_to_eligibility_item(row) for row in result.scalars())

    async def list_eligibility_assessments(
        self,
    ) -> tuple[BlueprintEligibilityAssessmentData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(BlueprintEligibilityAssessmentRecord)
                .where(BlueprintEligibilityAssessmentRecord.project_id == self._project_id)
                .order_by(
                    BlueprintEligibilityAssessmentRecord.created_at.desc(),
                    BlueprintEligibilityAssessmentRecord.id.desc(),
                )
            )
        )
        return tuple(_to_eligibility(row) for row in result.scalars())

    async def get_current(self) -> ImplementationBlueprintData | None:
        return await self._get_current(lock=False, nowait=False)

    async def get_current_for_update(
        self, *, nowait: bool = False
    ) -> ImplementationBlueprintData | None:
        return await self._get_current(lock=True, nowait=nowait)

    async def _get_current(self, *, lock: bool, nowait: bool) -> ImplementationBlueprintData | None:
        statement = (
            select(ImplementationBlueprintRecord)
            .where(
                ImplementationBlueprintRecord.project_id == self._project_id,
                ImplementationBlueprintRecord.status.in_(
                    ("draft", "provisional", "review_ready", "accepted")
                ),
            )
            .order_by(
                ImplementationBlueprintRecord.created_at.desc(),
                ImplementationBlueprintRecord.id.desc(),
            )
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_blueprint(row)

    async def add_step(self, step: NewImplementationStep) -> ImplementationStepData:
        if step.project_id != self._project_id:
            raise DomainValidationError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_step_exact(step.id)
        if existing is not None:
            if existing.generation_record_id == step.generation_record_id:
                return existing
            raise ResourceConflictError(details={"reason": "implementation_step_id_reused"})
        row = ImplementationStepRecord(**asdict(step))
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_step(row)

    async def get_step_exact(self, step_id: UUID) -> ImplementationStepData | None:
        return await self._get_step(step_id, lock=False, nowait=False)

    async def get_step_for_update(
        self, step_id: UUID, *, nowait: bool = False
    ) -> ImplementationStepData | None:
        return await self._get_step(step_id, lock=True, nowait=nowait)

    async def _get_step(
        self, step_id: UUID, *, lock: bool, nowait: bool
    ) -> ImplementationStepData | None:
        statement = select(ImplementationStepRecord).where(
            ImplementationStepRecord.id == step_id,
            ImplementationStepRecord.project_id == self._project_id,
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_step(row)

    async def list_steps(self, blueprint_id: UUID) -> tuple[ImplementationStepData, ...]:
        if await self.get_exact(blueprint_id) is None:
            raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
        result = await execute_database(
            lambda: self._session.execute(
                select(ImplementationStepRecord)
                .where(
                    ImplementationStepRecord.project_id == self._project_id,
                    ImplementationStepRecord.blueprint_id == blueprint_id,
                )
                .order_by(
                    ImplementationStepRecord.sequence_index,
                    ImplementationStepRecord.version,
                    ImplementationStepRecord.id,
                )
            )
        )
        return tuple(_to_step(row) for row in result.scalars())

    async def mark_step_superseded(
        self, step_id: UUID, *, expected_version: int
    ) -> ImplementationStepData:
        result = await execute_database(
            lambda: self._session.execute(
                update(ImplementationStepRecord)
                .where(
                    ImplementationStepRecord.id == step_id,
                    ImplementationStepRecord.project_id == self._project_id,
                    ImplementationStepRecord.version == expected_version,
                    ImplementationStepRecord.lifecycle_status == "current",
                )
                .values(lifecycle_status="superseded")
                .returning(ImplementationStepRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "implementation_step_not_current"})
        return _to_step(row)

    async def add_check(self, check: NewVerificationCheck) -> VerificationCheckData:
        if check.project_id != self._project_id:
            raise DomainValidationError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_check_exact(check.id)
        if existing is not None:
            if existing.generation_record_id == check.generation_record_id:
                return existing
            raise ResourceConflictError(details={"reason": "verification_check_id_reused"})
        values = asdict(check)
        values["likely_causes"] = list(check.likely_causes)
        row = VerificationCheckRecord(**values)
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_check(row)

    async def get_check_exact(self, check_id: UUID) -> VerificationCheckData | None:
        return await self._get_check(check_id, lock=False, nowait=False)

    async def get_check_for_update(
        self, check_id: UUID, *, nowait: bool = False
    ) -> VerificationCheckData | None:
        return await self._get_check(check_id, lock=True, nowait=nowait)

    async def _get_check(
        self, check_id: UUID, *, lock: bool, nowait: bool
    ) -> VerificationCheckData | None:
        statement = select(VerificationCheckRecord).where(
            VerificationCheckRecord.id == check_id,
            VerificationCheckRecord.project_id == self._project_id,
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_check(row)

    async def list_checks(self, blueprint_id: UUID) -> tuple[VerificationCheckData, ...]:
        if await self.get_exact(blueprint_id) is None:
            raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
        result = await execute_database(
            lambda: self._session.execute(
                select(VerificationCheckRecord)
                .where(
                    VerificationCheckRecord.project_id == self._project_id,
                    VerificationCheckRecord.blueprint_id == blueprint_id,
                )
                .order_by(
                    VerificationCheckRecord.implementation_step_id,
                    VerificationCheckRecord.sequence_index,
                    VerificationCheckRecord.version,
                    VerificationCheckRecord.id,
                )
            )
        )
        return tuple(_to_check(row) for row in result.scalars())

    async def record_check_result(
        self,
        check_id: UUID,
        *,
        expected_result_status: str | None,
        result_status: str,
        actual_result: str,
        result_actor_id: UUID,
    ) -> VerificationCheckData:
        expected = (
            VerificationCheckRecord.result_status.is_(None)
            if expected_result_status is None
            else VerificationCheckRecord.result_status == expected_result_status
        )
        result = await execute_database(
            lambda: self._session.execute(
                update(VerificationCheckRecord)
                .where(
                    VerificationCheckRecord.id == check_id,
                    VerificationCheckRecord.project_id == self._project_id,
                    VerificationCheckRecord.lifecycle_status == "current",
                    VerificationCheckRecord.freshness_status == "current",
                    expected,
                )
                .values(
                    result_status=result_status,
                    actual_result=actual_result,
                    result_actor_id=result_actor_id,
                    result_recorded_at=func.clock_timestamp(),
                )
                .returning(VerificationCheckRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "verification_check_state_changed"})
        return _to_check(row)
