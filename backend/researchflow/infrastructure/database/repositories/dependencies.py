"""Exact dependency and append-only invalidation persistence adapter."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.infrastructure.database.models.blueprint import (
    BlueprintEligibilityAssessmentRecord,
    BlueprintSectionRecord,
    ImplementationBlueprintRecord,
    ImplementationStepRecord,
    VerificationCheckRecord,
)
from researchflow.infrastructure.database.models.dependency import (
    ArtifactDependencyRecord,
    ArtifactInvalidationRecord,
)
from researchflow.infrastructure.database.models.evidence import (
    EvidenceLinkRecord,
    GroundingAssessmentRecord,
)
from researchflow.infrastructure.database.models.judgment import (
    HumanJudgmentRecord,
    JudgmentRequestRecord,
)
from researchflow.infrastructure.database.models.knowledge import KnowledgeObjectRecord
from researchflow.infrastructure.database.models.mapping import PaperCodeMappingRecord
from researchflow.infrastructure.database.models.mental_model import (
    MentalModelAssessmentRecord,
    MentalModelRecord,
)
from researchflow.infrastructure.database.models.method_element import MethodElementRecord
from researchflow.infrastructure.database.models.method_model import (
    MethodModelAssessmentRecord,
    MethodModelRecord,
)
from researchflow.infrastructure.database.models.repository import (
    PaperRepositoryRelationshipRecord,
    RepositoryComponentRecord,
    RepositoryRecord,
    RepositorySnapshotRecord,
)
from researchflow.infrastructure.database.models.research_question import (
    ResearchQuestionRecord,
    ResearchQuestionSourceRecord,
)
from researchflow.infrastructure.database.models.source import (
    SourceProcessingRunRecord,
    SourceRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import ArtifactDependencyData, ArtifactInvalidationData

_ARTIFACT_COLUMNS: Mapping[str, str] = {
    "judgment_request": "judgment_request_id",
    "human_judgment": "human_judgment_id",
    "method_model": "method_model_id",
    "paper_code_mapping": "paper_code_mapping_id",
    "mental_model": "mental_model_id",
    "blueprint_eligibility_assessment": "blueprint_eligibility_assessment_id",
    "implementation_blueprint": "implementation_blueprint_id",
    "blueprint_section": "blueprint_section_id",
    "implementation_step": "implementation_step_id",
    "verification_check": "verification_check_id",
}

_INVALIDATION_ARTIFACT_COLUMNS: Mapping[str, str] = {
    key: value
    for key, value in _ARTIFACT_COLUMNS.items()
    if key not in {"judgment_request", "human_judgment"}
}

_REFERENCE_COLUMNS: Mapping[str, str] = {
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

_DIRECT_PROJECT_MODELS: Mapping[str, type[Any]] = {
    "judgment_request": JudgmentRequestRecord,
    "human_judgment": HumanJudgmentRecord,
    "research_question": ResearchQuestionRecord,
    "research_question_source": ResearchQuestionSourceRecord,
    "paper_repository_relationship": PaperRepositoryRelationshipRecord,
    "knowledge_object": KnowledgeObjectRecord,
    "evidence_link": EvidenceLinkRecord,
    "grounding_assessment": GroundingAssessmentRecord,
    "method_model": MethodModelRecord,
    "method_element": MethodElementRecord,
    "paper_code_mapping": PaperCodeMappingRecord,
    "mental_model": MentalModelRecord,
    "mental_model_assessment": MentalModelAssessmentRecord,
    "blueprint_eligibility_assessment": BlueprintEligibilityAssessmentRecord,
    "implementation_blueprint": ImplementationBlueprintRecord,
    "blueprint_section": BlueprintSectionRecord,
    "implementation_step": ImplementationStepRecord,
    "verification_check": VerificationCheckRecord,
}

_FRESHNESS_PROJECTIONS: Mapping[str, tuple[type[Any], str, Mapping[str, str]]] = {
    "method_model": (
        MethodModelRecord,
        "freshness_status",
        {value: value for value in ("current", "stale", "blocked", "invalid")},
    ),
    "paper_code_mapping": (
        PaperCodeMappingRecord,
        "freshness_status",
        {value: value for value in ("current", "stale", "blocked", "invalid")},
    ),
    "mental_model": (
        MentalModelRecord,
        "status",
        {"current": "current", "stale": "stale", "blocked": "stale", "invalid": "stale"},
    ),
    "implementation_blueprint": (
        ImplementationBlueprintRecord,
        "status",
        {"stale": "stale", "blocked": "stale", "invalid": "stale"},
    ),
    "implementation_step": (
        ImplementationStepRecord,
        "freshness_status",
        {value: value for value in ("current", "stale", "blocked", "invalid")},
    ),
    "verification_check": (
        VerificationCheckRecord,
        "freshness_status",
        {value: value for value in ("current", "stale", "blocked", "invalid")},
    ),
}

_SUPERSEDED_FIELDS: Mapping[str, tuple[type[Any], str]] = {
    "method_model": (MethodModelRecord, "lifecycle_status"),
    "paper_code_mapping": (PaperCodeMappingRecord, "lifecycle_status"),
    "mental_model": (MentalModelRecord, "status"),
    "implementation_blueprint": (ImplementationBlueprintRecord, "status"),
    "implementation_step": (ImplementationStepRecord, "lifecycle_status"),
    "verification_check": (VerificationCheckRecord, "lifecycle_status"),
}


def _mapped_column(mapping: Mapping[str, str], reference_type: str) -> str:
    try:
        return mapping[reference_type]
    except KeyError as error:
        raise DomainValidationError(details={"reason": "unsupported_artifact_type"}) from error


def _only_reference(row: object, columns: Mapping[str, str], prefix: str = "") -> tuple[str, UUID]:
    populated = [
        (reference_type, value)
        for reference_type, column in columns.items()
        if (value := getattr(row, f"{prefix}{column}")) is not None
    ]
    if len(populated) != 1:
        raise InternalError(details={"reason": "persistence_mapper_mismatch"})
    reference_type, reference_id = populated[0]
    if not isinstance(reference_id, UUID):
        raise InternalError(details={"reason": "persistence_mapper_mismatch"})
    return reference_type, reference_id


def _to_dependency(row: ArtifactDependencyRecord) -> ArtifactDependencyData:
    artifact_type, artifact_id = _only_reference(row, _ARTIFACT_COLUMNS)
    dependency_type, dependency_id = _only_reference(row, _REFERENCE_COLUMNS, "dependency_")
    return ArtifactDependencyData(
        id=row.id,
        project_id=row.project_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        dependency_type=dependency_type,
        dependency_id=dependency_id,
        dependency_role=row.dependency_role,
        criticality=row.criticality,
    )


def _to_invalidation(row: ArtifactInvalidationRecord) -> ArtifactInvalidationData:
    artifact_type, artifact_id = _only_reference(row, _INVALIDATION_ARTIFACT_COLUMNS)
    cause_type, cause_id = _only_reference(row, _REFERENCE_COLUMNS, "cause_")
    return ArtifactInvalidationData(
        id=row.id,
        project_id=row.project_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        cause_type=cause_type,
        cause_id=cause_id,
        rule_code=row.rule_code,
        effect=row.effect,
        reason=row.reason,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


class SqlAlchemyDependenciesRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def append_dependency(self, dependency: ArtifactDependencyData) -> ArtifactDependencyData:
        self._require_project(dependency.project_id)
        await self._require_reference_in_project(
            dependency.artifact_type, dependency.artifact_id, artifact=True
        )
        await self._require_reference_in_project(
            dependency.dependency_type, dependency.dependency_id, artifact=False
        )
        existing = await self._get_dependency_by_id(dependency.id)
        if existing is not None:
            if existing == dependency:
                return existing
            raise ResourceConflictError(details={"reason": "artifact_dependency_id_reused"})

        artifact_column = _mapped_column(_ARTIFACT_COLUMNS, dependency.artifact_type)
        dependency_column = "dependency_" + _mapped_column(
            _REFERENCE_COLUMNS, dependency.dependency_type
        )
        row = ArtifactDependencyRecord(
            id=dependency.id,
            project_id=dependency.project_id,
            dependency_role=dependency.dependency_role,
            criticality=dependency.criticality,
            **{artifact_column: dependency.artifact_id},
            **{dependency_column: dependency.dependency_id},
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_dependency(row)

    async def append_invalidation(
        self, invalidation: ArtifactInvalidationData
    ) -> ArtifactInvalidationData:
        self._require_project(invalidation.project_id)
        if invalidation.created_at is not None or invalidation.resolved_at is not None:
            raise DomainValidationError(details={"reason": "operational_timestamp_not_insertable"})
        await self._require_reference_in_project(
            invalidation.artifact_type, invalidation.artifact_id, artifact=True
        )
        await self._require_reference_in_project(
            invalidation.cause_type, invalidation.cause_id, artifact=False
        )
        existing = await self._get_invalidation_by_id(invalidation.id)
        if existing is not None:
            replay = ArtifactInvalidationData(
                id=existing.id,
                project_id=existing.project_id,
                artifact_type=existing.artifact_type,
                artifact_id=existing.artifact_id,
                cause_type=existing.cause_type,
                cause_id=existing.cause_id,
                rule_code=existing.rule_code,
                effect=existing.effect,
                reason=existing.reason,
            )
            if replay == invalidation:
                return existing
            raise ResourceConflictError(details={"reason": "artifact_invalidation_id_reused"})

        artifact_column = _mapped_column(_INVALIDATION_ARTIFACT_COLUMNS, invalidation.artifact_type)
        cause_column = "cause_" + _mapped_column(_REFERENCE_COLUMNS, invalidation.cause_type)
        row = ArtifactInvalidationRecord(
            id=invalidation.id,
            project_id=invalidation.project_id,
            rule_code=invalidation.rule_code,
            effect=invalidation.effect,
            reason=invalidation.reason,
            **{artifact_column: invalidation.artifact_id},
            **{cause_column: invalidation.cause_id},
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_invalidation(row)

    async def list_dependencies(
        self, artifact_type: str, artifact_id: UUID
    ) -> tuple[ArtifactDependencyData, ...]:
        artifact_column = _mapped_column(_ARTIFACT_COLUMNS, artifact_type)
        result = await execute_database(
            lambda: self._session.execute(
                select(ArtifactDependencyRecord)
                .where(
                    ArtifactDependencyRecord.project_id == self._project_id,
                    getattr(ArtifactDependencyRecord, artifact_column) == artifact_id,
                )
                .order_by(ArtifactDependencyRecord.created_at, ArtifactDependencyRecord.id)
            )
        )
        return tuple(_to_dependency(row) for row in result.scalars())

    async def list_unresolved_invalidations(
        self, artifact_type: str, artifact_id: UUID
    ) -> tuple[ArtifactInvalidationData, ...]:
        artifact_column = _mapped_column(_INVALIDATION_ARTIFACT_COLUMNS, artifact_type)
        result = await execute_database(
            lambda: self._session.execute(
                select(ArtifactInvalidationRecord)
                .where(
                    ArtifactInvalidationRecord.project_id == self._project_id,
                    getattr(ArtifactInvalidationRecord, artifact_column) == artifact_id,
                    ArtifactInvalidationRecord.resolved_at.is_(None),
                )
                .order_by(ArtifactInvalidationRecord.created_at, ArtifactInvalidationRecord.id)
            )
        )
        return tuple(_to_invalidation(row) for row in result.scalars())

    async def list_dependents(
        self, dependency_type: str, dependency_id: UUID
    ) -> tuple[ArtifactDependencyData, ...]:
        dependency_column = "dependency_" + _mapped_column(_REFERENCE_COLUMNS, dependency_type)
        await self._require_reference_in_project(dependency_type, dependency_id, artifact=False)
        result = await execute_database(
            lambda: self._session.execute(
                select(ArtifactDependencyRecord)
                .where(
                    ArtifactDependencyRecord.project_id == self._project_id,
                    getattr(ArtifactDependencyRecord, dependency_column) == dependency_id,
                )
                .order_by(ArtifactDependencyRecord.created_at, ArtifactDependencyRecord.id)
            )
        )
        return tuple(_to_dependency(row) for row in result.scalars())

    async def resolve_invalidation(self, invalidation_id: UUID) -> ArtifactInvalidationData:
        result = await execute_database(
            lambda: self._session.execute(
                select(ArtifactInvalidationRecord)
                .where(
                    ArtifactInvalidationRecord.id == invalidation_id,
                    ArtifactInvalidationRecord.project_id == self._project_id,
                )
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceNotFoundError(details={"resource": "artifact_invalidation"})
        if row.resolved_at is None:
            row.resolved_at = await execute_database(
                lambda: self._session.scalar(select(func.clock_timestamp()))
            )
            await execute_database(self._session.flush)
        return _to_invalidation(row)

    async def set_freshness_projection(
        self, artifact_type: str, artifact_id: UUID, effective_status: str
    ) -> None:
        if effective_status not in {"current", "stale", "blocked", "invalid"}:
            raise DomainValidationError(details={"reason": "invalid_freshness_projection"})
        await self._require_reference_in_project(artifact_type, artifact_id, artifact=True)
        target = _FRESHNESS_PROJECTIONS.get(artifact_type)
        if target is None:
            return
        model, attribute, projection = target
        projected = projection.get(effective_status)
        if projected is None:
            return
        await execute_database(
            lambda: self._session.execute(
                update(model)
                .where(model.id == artifact_id, model.project_id == self._project_id)
                .values({attribute: projected})
            )
        )

    async def artifact_is_superseded(self, artifact_type: str, artifact_id: UUID) -> bool:
        await self._require_reference_in_project(artifact_type, artifact_id, artifact=True)
        target = _SUPERSEDED_FIELDS.get(artifact_type)
        if target is None:
            return False
        model, attribute = target
        result = await execute_database(
            lambda: self._session.execute(
                select(getattr(model, attribute)).where(
                    model.id == artifact_id, model.project_id == self._project_id
                )
            )
        )
        return str(result.scalar_one()) == "superseded"

    def _require_project(self, project_id: UUID) -> None:
        if project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})

    async def _require_reference_in_project(
        self, reference_type: str, reference_id: UUID, *, artifact: bool
    ) -> None:
        if artifact:
            _mapped_column(_ARTIFACT_COLUMNS, reference_type)
        else:
            _mapped_column(_REFERENCE_COLUMNS, reference_type)
        statement = self._project_scope_statement(reference_type, reference_id)
        result = await execute_database(lambda: self._session.execute(statement))
        if result.scalar_one_or_none() is None:
            raise ResourceNotFoundError(details={"reason": "project_scoped_reference_not_found"})

    def _project_scope_statement(self, reference_type: str, reference_id: UUID) -> Select[Any]:
        if model := _DIRECT_PROJECT_MODELS.get(reference_type):
            return select(model.id).where(
                model.id == reference_id,
                model.project_id == self._project_id,
            )
        if reference_type == "source_processing_run":
            return (
                select(SourceProcessingRunRecord.id)
                .join(SourceRecord, SourceRecord.id == SourceProcessingRunRecord.source_id)
                .where(
                    SourceProcessingRunRecord.id == reference_id,
                    SourceRecord.project_id == self._project_id,
                )
            )
        if reference_type == "repository_snapshot":
            return (
                select(RepositorySnapshotRecord.id)
                .join(
                    RepositoryRecord, RepositoryRecord.id == RepositorySnapshotRecord.repository_id
                )
                .where(
                    RepositorySnapshotRecord.id == reference_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        if reference_type == "repository_component":
            return (
                select(RepositoryComponentRecord.id)
                .join(
                    RepositorySnapshotRecord,
                    RepositorySnapshotRecord.id == RepositoryComponentRecord.repository_snapshot_id,
                )
                .join(
                    RepositoryRecord, RepositoryRecord.id == RepositorySnapshotRecord.repository_id
                )
                .where(
                    RepositoryComponentRecord.id == reference_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        if reference_type == "method_model_assessment":
            return (
                select(MethodModelAssessmentRecord.id)
                .join(
                    MethodModelRecord,
                    MethodModelRecord.id == MethodModelAssessmentRecord.method_model_id,
                )
                .where(
                    MethodModelAssessmentRecord.id == reference_id,
                    MethodModelRecord.project_id == self._project_id,
                )
            )
        raise DomainValidationError(details={"reason": "unsupported_dependency_type"})

    async def _get_dependency_by_id(self, dependency_id: UUID) -> ArtifactDependencyData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(ArtifactDependencyRecord).where(
                    ArtifactDependencyRecord.id == dependency_id,
                    ArtifactDependencyRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_dependency(row)

    async def _get_invalidation_by_id(
        self, invalidation_id: UUID
    ) -> ArtifactInvalidationData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(ArtifactInvalidationRecord).where(
                    ArtifactInvalidationRecord.id == invalidation_id,
                    ArtifactInvalidationRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_invalidation(row)
