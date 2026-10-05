"""Proposition candidate, review, evidence, and grounding persistence."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import DomainValidationError, ResourceConflictError
from researchflow.infrastructure.database.models.evidence import (
    EvidenceLinkRecord,
    GroundingAssessmentRecord,
)
from researchflow.infrastructure.database.models.knowledge import (
    AmbiguityInterpretationRecord,
    AmbiguityRecord,
    AssumptionRecord,
    ClaimRecord,
    ImplementationRequirementRecord,
    KnowledgeGapRecord,
    KnowledgeObjectPresentationRecord,
    KnowledgeObjectRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    EvidenceLinkData,
    GroundingAssessmentData,
    KnowledgeObjectData,
    KnowledgeObjectPayloadData,
    KnowledgePresentationData,
    NewEvidenceLink,
    NewGroundingAssessment,
    NewKnowledgeCandidate,
    NewKnowledgePresentation,
    NewKnowledgeRevision,
)


def _to_knowledge(row: KnowledgeObjectRecord) -> KnowledgeObjectData:
    return KnowledgeObjectData(
        id=row.id,
        project_id=row.project_id,
        lineage_id=row.lineage_id,
        version=row.version,
        object_type=row.object_type,
        statement=row.statement,
        statement_normalization_version=row.statement_normalization_version,
        normalized_statement_hash=row.normalized_statement_hash,
        origin=row.origin,
        epistemic_status=row.epistemic_status,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        grounding_status=row.grounding_status_cache,
        grounding_assessment_id=row.grounding_assessment_id,
        generation_record_id=row.generation_record_id,
        supersedes_id=row.supersedes_id,
        derived_from_id=getattr(row, "derived_from_id", None),
    )


def _to_presentation(row: KnowledgeObjectPresentationRecord) -> KnowledgePresentationData:
    return KnowledgePresentationData(
        id=row.id,
        knowledge_object_id=row.knowledge_object_id,
        version=row.version,
        display_text=row.display_text,
        display_label=row.display_label,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
        supersedes_id=row.supersedes_id,
    )


def _to_evidence(row: EvidenceLinkRecord) -> EvidenceLinkData:
    if row.knowledge_object_id is None or (
        (row.source_chunk_id is None) == (row.repository_component_id is None)
    ):
        raise DomainValidationError(details={"reason": "proposition_evidence_shape_invalid"})
    return EvidenceLinkData(
        id=row.id,
        project_id=row.project_id,
        lineage_id=row.lineage_id,
        version=row.version,
        knowledge_object_id=row.knowledge_object_id,
        source_chunk_id=row.source_chunk_id,
        repository_component_id=row.repository_component_id,
        evidence_relation=row.evidence_relation,
        evidence_excerpt=row.evidence_excerpt,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        generation_record_id=row.generation_record_id,
        source_anchor_start_offset=row.source_anchor_start_offset,
        source_anchor_end_offset=row.source_anchor_end_offset,
        source_anchor_hash=row.source_anchor_hash,
        source_anchor_policy_version=row.source_anchor_policy_version,
    )


def _to_grounding(row: GroundingAssessmentRecord) -> GroundingAssessmentData:
    if row.knowledge_object_id is None:
        raise DomainValidationError(details={"reason": "proposition_grounding_shape_invalid"})
    return GroundingAssessmentData(
        id=row.id,
        project_id=row.project_id,
        knowledge_object_id=row.knowledge_object_id,
        evidence_set_fingerprint=row.evidence_set_fingerprint,
        status=row.status,
        supporting_count=row.supporting_count,
        contradicting_count=row.contradicting_count,
        qualifying_count=row.qualifying_count,
        rationale=row.rationale,
        validator_name=row.validator_name,
        validator_version=row.validator_version,
    )


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add_candidate(self, candidate: NewKnowledgeCandidate) -> KnowledgeObjectData:
        if candidate.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = KnowledgeObjectRecord(
            id=candidate.id,
            project_id=candidate.project_id,
            lineage_id=candidate.lineage_id,
            version=candidate.version,
            object_type=candidate.object_type,
            statement=candidate.statement,
            statement_normalization_version=candidate.statement_normalization_version,
            normalized_statement_hash=candidate.normalized_statement_hash,
            origin=candidate.origin,
            epistemic_status=candidate.epistemic_status,
            confidence=candidate.confidence,
            grounding_status_cache="unvalidated",
            review_status="candidate",
            lifecycle_status="current",
            generation_record_id=candidate.generation_record_id,
            created_by_type=candidate.created_by_type,
            created_by_id=candidate.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        await self._add_subtype(
            object_id=candidate.id,
            object_type=candidate.object_type,
            payload=candidate.subtype_payload,
            interpretations=candidate.ambiguity_interpretations,
        )
        return _to_knowledge(row)

    async def get_exact(self, object_id: UUID) -> KnowledgeObjectData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord).where(
                    KnowledgeObjectRecord.id == object_id,
                    KnowledgeObjectRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_knowledge(row)

    async def get_exact_for_update(
        self, object_id: UUID, *, nowait: bool = False
    ) -> KnowledgeObjectData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord)
                .where(
                    KnowledgeObjectRecord.id == object_id,
                    KnowledgeObjectRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_knowledge(row)

    async def get_payload(self, object_id: UUID) -> KnowledgeObjectPayloadData | None:
        object_result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord).where(
                    KnowledgeObjectRecord.id == object_id,
                    KnowledgeObjectRecord.project_id == self._project_id,
                )
            )
        )
        knowledge = object_result.scalar_one_or_none()
        if knowledge is None:
            return None
        model_by_type = {
            "claim": ClaimRecord,
            "assumption": AssumptionRecord,
            "knowledge_gap": KnowledgeGapRecord,
            "implementation_requirement": ImplementationRequirementRecord,
            "ambiguity": AmbiguityRecord,
        }
        model = model_by_type.get(knowledge.object_type)
        if model is None:
            raise DomainValidationError(details={"reason": "knowledge_subtype_invalid"})
        subtype_result = await execute_database(
            lambda: self._session.execute(
                select(model).where(cast(Any, model).knowledge_object_id == object_id)
            )
        )
        subtype = subtype_result.scalar_one_or_none()
        if subtype is None:
            raise DomainValidationError(details={"reason": "knowledge_subtype_missing"})
        payload = {
            column.name: getattr(subtype, column.name)
            for column in subtype.__table__.columns
            if column.name != "knowledge_object_id"
        }
        interpretations: tuple[dict[str, object], ...] = ()
        if knowledge.object_type == "ambiguity":
            interpretation_result = await execute_database(
                lambda: self._session.execute(
                    select(AmbiguityInterpretationRecord)
                    .where(AmbiguityInterpretationRecord.ambiguity_id == object_id)
                    .order_by(AmbiguityInterpretationRecord.rank)
                )
            )
            interpretations = tuple(
                {
                    "title": row.title,
                    "description": row.description,
                    "implementation_consequences": row.implementation_consequences,
                    "rank": row.rank,
                }
                for row in interpretation_result.scalars()
            )
        return KnowledgeObjectPayloadData(
            knowledge_object_id=object_id,
            subtype_payload=payload,
            ambiguity_interpretations=interpretations,
        )

    async def update_ambiguity_resolution(
        self, object_id: UUID, *, expected_status: str, resolution_status: str
    ) -> KnowledgeObjectPayloadData:
        result = await execute_database(
            lambda: self._session.execute(
                update(AmbiguityRecord)
                .where(
                    AmbiguityRecord.knowledge_object_id == object_id,
                    AmbiguityRecord.resolution_status == expected_status,
                )
                .values(resolution_status=resolution_status)
                .returning(AmbiguityRecord)
            )
        )
        if result.scalar_one_or_none() is None:
            raise ResourceConflictError(details={"reason": "ambiguity_resolution_stale"})
        payload = await self.get_payload(object_id)
        if payload is None:
            raise ResourceConflictError(details={"reason": "ambiguity_missing"})
        return payload

    async def list_review_items(
        self,
        *,
        review_status: str | None = None,
        object_type: str | None = None,
    ) -> tuple[KnowledgeObjectData, ...]:
        statement = select(KnowledgeObjectRecord).where(
            KnowledgeObjectRecord.project_id == self._project_id
        )
        if review_status is not None:
            statement = statement.where(KnowledgeObjectRecord.review_status == review_status)
        if object_type is not None:
            statement = statement.where(KnowledgeObjectRecord.object_type == object_type)
        statement = statement.order_by(KnowledgeObjectRecord.created_at, KnowledgeObjectRecord.id)
        result = await execute_database(lambda: self._session.execute(statement))
        return tuple(_to_knowledge(row) for row in result.scalars())

    async def list_by_normalized_identity(
        self,
        *,
        object_type: str,
        statement_normalization_version: str,
        normalized_statement_hash: str,
    ) -> tuple[KnowledgeObjectData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord)
                .where(
                    KnowledgeObjectRecord.project_id == self._project_id,
                    KnowledgeObjectRecord.object_type == object_type,
                    KnowledgeObjectRecord.statement_normalization_version
                    == statement_normalization_version,
                    KnowledgeObjectRecord.normalized_statement_hash == normalized_statement_hash,
                )
                .order_by(KnowledgeObjectRecord.created_at, KnowledgeObjectRecord.id)
            )
        )
        return tuple(_to_knowledge(row) for row in result.scalars())

    async def list_active_dedup_candidates(
        self, *, object_type: str
    ) -> tuple[KnowledgeObjectData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord)
                .where(
                    KnowledgeObjectRecord.project_id == self._project_id,
                    KnowledgeObjectRecord.object_type == object_type,
                    KnowledgeObjectRecord.lifecycle_status == "current",
                    KnowledgeObjectRecord.review_status.in_(
                        ("candidate", "needs_revision", "accepted")
                    ),
                )
                .order_by(KnowledgeObjectRecord.created_at, KnowledgeObjectRecord.id)
            )
        )
        return tuple(_to_knowledge(row) for row in result.scalars())

    async def list_accepted_current(self) -> tuple[KnowledgeObjectData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord)
                .where(
                    KnowledgeObjectRecord.project_id == self._project_id,
                    KnowledgeObjectRecord.review_status == "accepted",
                    KnowledgeObjectRecord.lifecycle_status == "current",
                )
                .order_by(KnowledgeObjectRecord.created_at, KnowledgeObjectRecord.id)
            )
        )
        return tuple(_to_knowledge(row) for row in result.scalars())

    async def list_lineage(self, lineage_id: UUID) -> tuple[KnowledgeObjectData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord)
                .where(
                    KnowledgeObjectRecord.project_id == self._project_id,
                    KnowledgeObjectRecord.lineage_id == lineage_id,
                )
                .order_by(KnowledgeObjectRecord.version)
            )
        )
        return tuple(_to_knowledge(row) for row in result.scalars())

    async def list_lineage_for_update(
        self, lineage_id: UUID, *, nowait: bool = False
    ) -> tuple[KnowledgeObjectData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord)
                .where(
                    KnowledgeObjectRecord.project_id == self._project_id,
                    KnowledgeObjectRecord.lineage_id == lineage_id,
                )
                .order_by(KnowledgeObjectRecord.version)
                .with_for_update(nowait=nowait)
            )
        )
        return tuple(_to_knowledge(row) for row in result.scalars())

    async def update_review_lifecycle(
        self,
        object_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> KnowledgeObjectData:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord).where(
                    KnowledgeObjectRecord.id == object_id,
                    KnowledgeObjectRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "knowledge_object_missing"})
        if (
            row.version != expected_version
            or row.review_status != expected_review_status
            or row.lifecycle_status != expected_lifecycle_status
        ):
            raise ResourceConflictError(details={"reason": "stale_knowledge_object"})
        row.review_status = review_status
        row.lifecycle_status = lifecycle_status
        row.supersedes_id = supersedes_id
        row.updated_at = datetime.now(UTC)
        await execute_database(self._session.flush)
        return _to_knowledge(row)

    async def add_revision(self, revision: NewKnowledgeRevision) -> KnowledgeObjectData:
        if revision.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = KnowledgeObjectRecord(
            id=revision.id,
            project_id=revision.project_id,
            lineage_id=revision.lineage_id,
            version=revision.version,
            object_type=revision.object_type,
            statement=revision.statement,
            statement_normalization_version=revision.statement_normalization_version,
            normalized_statement_hash=revision.normalized_statement_hash,
            origin=revision.origin,
            epistemic_status=revision.epistemic_status,
            confidence=revision.confidence,
            grounding_status_cache="unsupported",
            review_status="candidate",
            lifecycle_status="current",
            derived_from_id=revision.derived_from_id,
            generation_record_id=None,
            created_by_type=revision.created_by_type,
            created_by_id=revision.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        await self._add_subtype(
            object_id=revision.id,
            object_type=revision.object_type,
            payload=revision.subtype_payload,
            interpretations=revision.ambiguity_interpretations,
        )
        return _to_knowledge(row)

    async def add_presentation(
        self, presentation: NewKnowledgePresentation
    ) -> KnowledgePresentationData:
        row = KnowledgeObjectPresentationRecord(
            id=presentation.id,
            knowledge_object_id=presentation.knowledge_object_id,
            version=presentation.version,
            display_text=presentation.display_text,
            display_label=presentation.display_label,
            created_by_type=presentation.created_by_type,
            created_by_id=presentation.created_by_id,
            supersedes_id=presentation.supersedes_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_presentation(row)

    async def get_current_presentation_for_update(
        self, knowledge_object_id: UUID, *, nowait: bool = False
    ) -> KnowledgePresentationData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectPresentationRecord)
                .where(KnowledgeObjectPresentationRecord.knowledge_object_id == knowledge_object_id)
                .order_by(KnowledgeObjectPresentationRecord.version.desc())
                .limit(1)
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_presentation(row)

    async def list_presentations(
        self, knowledge_object_id: UUID
    ) -> tuple[KnowledgePresentationData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectPresentationRecord)
                .where(KnowledgeObjectPresentationRecord.knowledge_object_id == knowledge_object_id)
                .order_by(KnowledgeObjectPresentationRecord.version)
            )
        )
        return tuple(_to_presentation(row) for row in result.scalars())

    async def _add_subtype(
        self,
        *,
        object_id: UUID,
        object_type: str,
        payload: dict[str, object],
        interpretations: tuple[dict[str, object], ...],
    ) -> None:
        subtype: object
        if object_type == "claim":
            subtype = ClaimRecord(knowledge_object_id=object_id, **payload)
        elif object_type == "assumption":
            subtype = AssumptionRecord(knowledge_object_id=object_id, **payload)
        elif object_type == "knowledge_gap":
            subtype = KnowledgeGapRecord(knowledge_object_id=object_id, **payload)
        elif object_type == "implementation_requirement":
            subtype = ImplementationRequirementRecord(knowledge_object_id=object_id, **payload)
        elif object_type == "ambiguity":
            subtype = AmbiguityRecord(knowledge_object_id=object_id, **payload)
        else:
            raise DomainValidationError(details={"reason": "knowledge_subtype_invalid"})
        self._session.add(subtype)
        await execute_database(self._session.flush)
        for interpretation in interpretations:
            self._session.add(
                AmbiguityInterpretationRecord(ambiguity_id=object_id, **interpretation)
            )
        await execute_database(self._session.flush)

    async def add_evidence_link(self, link: NewEvidenceLink) -> EvidenceLinkData:
        if link.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = EvidenceLinkRecord(
            id=link.id,
            project_id=link.project_id,
            lineage_id=link.lineage_id,
            version=link.version,
            knowledge_object_id=link.knowledge_object_id,
            source_chunk_id=link.source_chunk_id,
            repository_component_id=link.repository_component_id,
            evidence_relation=link.evidence_relation,
            evidence_excerpt=link.evidence_excerpt,
            source_anchor_start_offset=link.source_anchor_start_offset,
            source_anchor_end_offset=link.source_anchor_end_offset,
            source_anchor_hash=link.source_anchor_hash,
            source_anchor_policy_version=link.source_anchor_policy_version,
            confidence=link.confidence,
            review_status=link.review_status,
            lifecycle_status=link.lifecycle_status,
            generation_record_id=link.generation_record_id,
            created_by_type=link.created_by_type,
            created_by_id=link.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_evidence(row)

    async def find_evidence_link(
        self,
        *,
        knowledge_object_id: UUID,
        source_chunk_id: UUID | None,
        repository_component_id: UUID | None,
        evidence_relation: str,
        evidence_excerpt: str,
    ) -> EvidenceLinkData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord).where(
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.knowledge_object_id == knowledge_object_id,
                    EvidenceLinkRecord.source_chunk_id == source_chunk_id,
                    EvidenceLinkRecord.repository_component_id == repository_component_id,
                    EvidenceLinkRecord.evidence_relation == evidence_relation,
                    EvidenceLinkRecord.evidence_excerpt == evidence_excerpt,
                    EvidenceLinkRecord.lifecycle_status == "current",
                    EvidenceLinkRecord.review_status.in_(
                        ("candidate", "needs_revision", "accepted")
                    ),
                )
            )
        )
        row = result.scalars().first()
        return None if row is None else _to_evidence(row)

    async def list_evidence_links(self, knowledge_object_id: UUID) -> tuple[EvidenceLinkData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord)
                .where(
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.knowledge_object_id == knowledge_object_id,
                )
                .order_by(EvidenceLinkRecord.created_at, EvidenceLinkRecord.id)
            )
        )
        return tuple(_to_evidence(row) for row in result.scalars())

    async def get_evidence_link_for_update(
        self, evidence_link_id: UUID, *, nowait: bool = False
    ) -> EvidenceLinkData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord)
                .where(
                    EvidenceLinkRecord.id == evidence_link_id,
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.knowledge_object_id.is_not(None),
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_evidence(row)

    async def get_evidence_link_exact(self, evidence_link_id: UUID) -> EvidenceLinkData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord).where(
                    EvidenceLinkRecord.id == evidence_link_id,
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.knowledge_object_id.is_not(None),
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_evidence(row)

    async def update_evidence_review_lifecycle(
        self,
        evidence_link_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
    ) -> EvidenceLinkData:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord).where(
                    EvidenceLinkRecord.id == evidence_link_id,
                    EvidenceLinkRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if row is None or row.knowledge_object_id is None:
            raise ResourceConflictError(details={"reason": "evidence_link_missing"})
        if (
            row.version != expected_version
            or row.review_status != expected_review_status
            or row.lifecycle_status != expected_lifecycle_status
        ):
            raise ResourceConflictError(details={"reason": "stale_evidence_link"})
        row.review_status = review_status
        row.lifecycle_status = lifecycle_status
        await execute_database(self._session.flush)
        return _to_evidence(row)

    async def add_grounding_assessment(
        self, assessment: NewGroundingAssessment
    ) -> GroundingAssessmentData:
        if assessment.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = GroundingAssessmentRecord(
            id=assessment.id,
            project_id=assessment.project_id,
            knowledge_object_id=assessment.knowledge_object_id,
            evidence_set_fingerprint=assessment.evidence_set_fingerprint,
            status=assessment.status,
            supporting_count=assessment.supporting_count,
            contradicting_count=assessment.contradicting_count,
            qualifying_count=assessment.qualifying_count,
            rationale=assessment.rationale,
            validator_name=assessment.validator_name,
            validator_version=assessment.validator_version,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_grounding(row)

    async def get_grounding_assessment(self, assessment_id: UUID) -> GroundingAssessmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(GroundingAssessmentRecord).where(
                    GroundingAssessmentRecord.id == assessment_id,
                    GroundingAssessmentRecord.project_id == self._project_id,
                    GroundingAssessmentRecord.knowledge_object_id.is_not(None),
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_grounding(row)

    async def set_grounding_cache(
        self,
        *,
        knowledge_object_id: UUID,
        grounding_assessment_id: UUID,
        grounding_status: str,
    ) -> KnowledgeObjectData:
        result = await execute_database(
            lambda: self._session.execute(
                select(KnowledgeObjectRecord).where(
                    KnowledgeObjectRecord.id == knowledge_object_id,
                    KnowledgeObjectRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "knowledge_object_missing"})
        row.grounding_assessment_id = grounding_assessment_id
        row.grounding_status_cache = grounding_status
        await execute_database(self._session.flush)
        return _to_knowledge(row)
