"""Structured-method queries with explicit accepted/current predicates."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import DomainValidationError, ResourceConflictError
from researchflow.infrastructure.database.models.evidence import (
    EvidenceLinkRecord,
    GroundingAssessmentRecord,
)
from researchflow.infrastructure.database.models.method_element import (
    MethodAlgorithmStepRecord,
    MethodComponentRecord,
    MethodDataFlowRecord,
    MethodElementRecord,
    MethodEquationRecord,
    MethodSymbolRecord,
    MethodValidityConditionRecord,
)
from researchflow.infrastructure.database.models.method_model import (
    MethodModelAssessmentRecord,
    MethodModelMemberRecord,
    MethodModelRecord,
    MethodModelRenderingRecord,
    MethodModelSectionReviewRecord,
)
from researchflow.infrastructure.database.models.relationship import ObjectLinkRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    MethodElementData,
    MethodEvidenceLinkData,
    MethodModelAssessmentData,
    MethodModelData,
    MethodModelMemberData,
    MethodModelRenderingData,
    MethodModelSectionReviewData,
    MethodObjectLinkData,
    NewMethodElementCandidate,
    NewMethodEvidenceLink,
    NewMethodGroundingAssessment,
    NewMethodModelAssessment,
    NewMethodModelCandidate,
    NewMethodModelMember,
    NewMethodModelRendering,
    NewMethodModelSectionReview,
    NewMethodObjectLink,
)


def _to_element(row: MethodElementRecord) -> MethodElementData:
    return MethodElementData(
        id=row.id,
        project_id=row.project_id,
        lineage_id=row.lineage_id,
        version=row.version,
        element_type=row.element_type,
        epistemic_status=row.epistemic_status,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        grounding_status=row.grounding_status_cache,
        grounding_assessment_id=row.grounding_assessment_id,
        generation_record_id=row.generation_record_id,
        supersedes_id=row.supersedes_id,
        derived_from_id=row.derived_from_id,
    )


def _to_model(row: MethodModelRecord) -> MethodModelData:
    return MethodModelData(
        id=row.id,
        project_id=row.project_id,
        research_question_id=row.research_question_id,
        lineage_id=row.lineage_id,
        version=row.version,
        name=row.name,
        objective=row.objective,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        freshness_status=row.freshness_status,
        composition_fingerprint=row.composition_fingerprint,
        current_assessment_id=row.current_assessment_id,
        generation_record_id=row.generation_record_id,
        supersedes_id=row.supersedes_id,
        derived_from_id=row.derived_from_id,
    )


def _to_method_evidence(row: EvidenceLinkRecord) -> MethodEvidenceLinkData:
    if row.method_element_id is None or (
        (row.source_chunk_id is None) == (row.repository_component_id is None)
    ):
        raise DomainValidationError(details={"reason": "method_evidence_shape_invalid"})
    return MethodEvidenceLinkData(
        id=row.id,
        project_id=row.project_id,
        lineage_id=row.lineage_id,
        version=row.version,
        method_element_id=row.method_element_id,
        source_chunk_id=row.source_chunk_id,
        repository_component_id=row.repository_component_id,
        evidence_relation=row.evidence_relation,
        evidence_excerpt=row.evidence_excerpt,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        generation_record_id=row.generation_record_id,
    )


def _to_model_assessment(row: MethodModelAssessmentRecord) -> MethodModelAssessmentData:
    return MethodModelAssessmentData(
        id=row.id,
        method_model_id=row.method_model_id,
        outcome=row.outcome,
        unsupported_required_count=row.unsupported_required_count,
        mixed_evidence_count=row.mixed_evidence_count,
        contradicted_count=row.contradicted_count,
        missing_provenance_count=row.missing_provenance_count,
        unresolved_scientific_ambiguity_count=row.unresolved_scientific_ambiguity_count,
        rationale=row.rationale,
    )


def _to_object_link(row: ObjectLinkRecord) -> MethodObjectLinkData:
    if row.source_method_element_id is None or row.target_method_element_id is None:
        raise DomainValidationError(details={"reason": "method_object_link_shape_invalid"})
    return MethodObjectLinkData(
        id=row.id,
        project_id=row.project_id,
        lineage_id=row.lineage_id,
        version=row.version,
        source_method_element_id=row.source_method_element_id,
        relationship_type=row.relationship_type,
        target_method_element_id=row.target_method_element_id,
        rationale=row.rationale,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        generation_record_id=row.generation_record_id,
    )


class SqlAlchemyMethodModelsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add_candidate_model(self, model: NewMethodModelCandidate) -> MethodModelData:
        if model.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = MethodModelRecord(
            id=model.id,
            project_id=model.project_id,
            research_question_id=model.research_question_id,
            lineage_id=model.lineage_id,
            version=model.version,
            name=model.name,
            objective=model.objective,
            review_status="candidate",
            lifecycle_status="current",
            freshness_status="current",
            confidence=model.confidence,
            composition_fingerprint=model.composition_fingerprint,
            generation_record_id=model.generation_record_id,
            derived_from_id=model.derived_from_id,
            created_by_type=model.created_by_type,
            created_by_id=model.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_model(row)

    async def add_candidate_element(self, element: NewMethodElementCandidate) -> MethodElementData:
        if element.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = MethodElementRecord(
            id=element.id,
            project_id=element.project_id,
            lineage_id=element.lineage_id,
            version=element.version,
            element_type=element.element_type,
            epistemic_status=element.epistemic_status,
            confidence=element.confidence,
            grounding_status_cache="unvalidated",
            review_status="candidate",
            lifecycle_status="current",
            generation_record_id=element.generation_record_id,
            derived_from_id=element.derived_from_id,
            created_by_type=element.created_by_type,
            created_by_id=element.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        payload = element.subtype_payload
        subtype: object
        if element.element_type == "component":
            subtype = MethodComponentRecord(method_element_id=element.id, **payload)
        elif element.element_type == "equation":
            subtype = MethodEquationRecord(method_element_id=element.id, **payload)
        elif element.element_type == "symbol":
            subtype = MethodSymbolRecord(method_element_id=element.id, **payload)
        elif element.element_type == "algorithm_step":
            subtype = MethodAlgorithmStepRecord(method_element_id=element.id, **payload)
        elif element.element_type == "data_flow":
            subtype = MethodDataFlowRecord(method_element_id=element.id, **payload)
        elif element.element_type == "validity_condition":
            subtype = MethodValidityConditionRecord(method_element_id=element.id, **payload)
        else:
            raise DomainValidationError(details={"reason": "method_element_subtype_invalid"})
        self._session.add(subtype)
        await execute_database(self._session.flush)
        return _to_element(row)

    async def add_member(self, member: NewMethodModelMember) -> MethodModelMemberData:
        row = MethodModelMemberRecord(
            id=member.id,
            method_model_id=member.method_model_id,
            method_element_id=member.method_element_id,
            review_section=member.review_section,
            member_role=member.member_role,
            sequence_index=member.sequence_index,
            required=member.required,
            inclusion_rationale=member.inclusion_rationale,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        element = await self.get_element_exact(member.method_element_id)
        if element is None:
            raise DomainValidationError(details={"reason": "method_member_element_missing"})
        return MethodModelMemberData(
            id=member.id,
            method_model_id=member.method_model_id,
            method_element_id=member.method_element_id,
            review_section=member.review_section,
            member_role=member.member_role,
            sequence_index=member.sequence_index,
            required=member.required,
            inclusion_rationale=member.inclusion_rationale,
            element=element,
            subtype_payload={},
        )

    async def add_object_link(self, link: NewMethodObjectLink) -> None:
        if link.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        self._session.add(
            ObjectLinkRecord(
                id=link.id,
                project_id=link.project_id,
                lineage_id=link.lineage_id,
                version=link.version,
                source_method_element_id=link.source_method_element_id,
                relationship_type=link.relationship_type,
                target_method_element_id=link.target_method_element_id,
                rationale=link.rationale,
                confidence=link.confidence,
                review_status=link.review_status,
                lifecycle_status=link.lifecycle_status,
                generation_record_id=link.generation_record_id,
                created_by_type=link.created_by_type,
                created_by_id=link.created_by_id,
            )
        )
        await execute_database(self._session.flush)

    async def list_object_links(
        self, source_element_ids: tuple[UUID, ...]
    ) -> tuple[MethodObjectLinkData, ...]:
        if not source_element_ids:
            return ()
        result = await execute_database(
            lambda: self._session.execute(
                select(ObjectLinkRecord)
                .where(
                    ObjectLinkRecord.project_id == self._project_id,
                    ObjectLinkRecord.source_method_element_id.in_(source_element_ids),
                    ObjectLinkRecord.target_method_element_id.is_not(None),
                )
                .order_by(ObjectLinkRecord.created_at, ObjectLinkRecord.id)
            )
        )
        return tuple(_to_object_link(row) for row in result.scalars())

    async def update_object_link_review_lifecycle(
        self,
        object_link_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
    ) -> MethodObjectLinkData:
        result = await execute_database(
            lambda: self._session.execute(
                update(ObjectLinkRecord)
                .where(
                    ObjectLinkRecord.id == object_link_id,
                    ObjectLinkRecord.project_id == self._project_id,
                    ObjectLinkRecord.version == expected_version,
                    ObjectLinkRecord.review_status == expected_review_status,
                    ObjectLinkRecord.lifecycle_status == expected_lifecycle_status,
                )
                .values(review_status=review_status, lifecycle_status=lifecycle_status)
                .returning(ObjectLinkRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_method_object_link"})
        return _to_object_link(row)

    async def add_evidence_link(self, link: NewMethodEvidenceLink) -> MethodEvidenceLinkData:
        if link.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = EvidenceLinkRecord(
            id=link.id,
            project_id=link.project_id,
            lineage_id=link.lineage_id,
            version=link.version,
            method_element_id=link.method_element_id,
            source_chunk_id=link.source_chunk_id,
            repository_component_id=link.repository_component_id,
            evidence_relation=link.evidence_relation,
            evidence_excerpt=link.evidence_excerpt,
            confidence=link.confidence,
            review_status=link.review_status,
            lifecycle_status=link.lifecycle_status,
            generation_record_id=link.generation_record_id,
            created_by_type=link.created_by_type,
            created_by_id=link.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_method_evidence(row)

    async def list_evidence_links(self, element_id: UUID) -> tuple[MethodEvidenceLinkData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord)
                .where(
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.method_element_id == element_id,
                )
                .order_by(EvidenceLinkRecord.created_at, EvidenceLinkRecord.id)
            )
        )
        return tuple(_to_method_evidence(row) for row in result.scalars())

    async def get_evidence_link_for_update(
        self, evidence_link_id: UUID, *, nowait: bool = False
    ) -> MethodEvidenceLinkData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord)
                .where(
                    EvidenceLinkRecord.id == evidence_link_id,
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.method_element_id.is_not(None),
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_method_evidence(row)

    async def get_evidence_link_exact(
        self, evidence_link_id: UUID
    ) -> MethodEvidenceLinkData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(EvidenceLinkRecord).where(
                    EvidenceLinkRecord.id == evidence_link_id,
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.method_element_id.is_not(None),
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_method_evidence(row)

    async def update_evidence_review_lifecycle(
        self,
        evidence_link_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
    ) -> MethodEvidenceLinkData:
        result = await execute_database(
            lambda: self._session.execute(
                update(EvidenceLinkRecord)
                .where(
                    EvidenceLinkRecord.id == evidence_link_id,
                    EvidenceLinkRecord.project_id == self._project_id,
                    EvidenceLinkRecord.method_element_id.is_not(None),
                    EvidenceLinkRecord.version == expected_version,
                    EvidenceLinkRecord.review_status == expected_review_status,
                    EvidenceLinkRecord.lifecycle_status == expected_lifecycle_status,
                )
                .values(review_status=review_status, lifecycle_status=lifecycle_status)
                .returning(EvidenceLinkRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_method_evidence"})
        return _to_method_evidence(row)

    async def add_grounding_assessment(self, assessment: NewMethodGroundingAssessment) -> None:
        if assessment.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        self._session.add(
            GroundingAssessmentRecord(
                id=assessment.id,
                project_id=assessment.project_id,
                method_element_id=assessment.method_element_id,
                evidence_set_fingerprint=assessment.evidence_set_fingerprint,
                status=assessment.status,
                supporting_count=assessment.supporting_count,
                contradicting_count=assessment.contradicting_count,
                qualifying_count=assessment.qualifying_count,
                rationale=assessment.rationale,
                validator_name=assessment.validator_name,
                validator_version=assessment.validator_version,
            )
        )
        await execute_database(self._session.flush)

    async def set_element_grounding_cache(
        self, *, element_id: UUID, assessment_id: UUID, grounding_status: str
    ) -> MethodElementData:
        await execute_database(
            lambda: self._session.execute(
                update(MethodElementRecord)
                .where(
                    MethodElementRecord.id == element_id,
                    MethodElementRecord.project_id == self._project_id,
                )
                .values(
                    grounding_assessment_id=assessment_id, grounding_status_cache=grounding_status
                )
            )
        )
        element = await self.get_element_exact(element_id)
        if element is None:
            raise DomainValidationError(details={"reason": "method_element_missing"})
        return element

    async def add_model_assessment(
        self, assessment: NewMethodModelAssessment
    ) -> MethodModelAssessmentData:
        row = MethodModelAssessmentRecord(
            id=assessment.id,
            method_model_id=assessment.method_model_id,
            composition_fingerprint=assessment.composition_fingerprint,
            member_grounding_fingerprint=assessment.member_grounding_fingerprint,
            source_evidence_fingerprint=assessment.source_evidence_fingerprint,
            repository_evidence_fingerprint=assessment.repository_evidence_fingerprint,
            unsupported_required_count=assessment.unsupported_required_count,
            mixed_evidence_count=assessment.mixed_evidence_count,
            contradicted_count=assessment.contradicted_count,
            missing_provenance_count=assessment.missing_provenance_count,
            unresolved_scientific_ambiguity_count=(
                assessment.unresolved_scientific_ambiguity_count
            ),
            outcome=assessment.outcome,
            rationale=assessment.rationale,
            generation_record_id=assessment.generation_record_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_model_assessment(row)

    async def set_current_assessment(
        self, *, method_model_id: UUID, assessment_id: UUID
    ) -> MethodModelData:
        await execute_database(
            lambda: self._session.execute(
                update(MethodModelRecord)
                .where(
                    MethodModelRecord.id == method_model_id,
                    MethodModelRecord.project_id == self._project_id,
                )
                .values(current_assessment_id=assessment_id)
            )
        )
        model = await self.get_model_exact(method_model_id)
        if model is None:
            raise DomainValidationError(details={"reason": "method_model_missing"})
        return model

    async def list_members(self, method_model_id: UUID) -> tuple[MethodModelMemberData, ...]:
        return await self._list_members(method_model_id, lock=False, nowait=False)

    async def list_members_for_update(
        self, method_model_id: UUID, *, nowait: bool = False
    ) -> tuple[MethodModelMemberData, ...]:
        return await self._list_members(method_model_id, lock=True, nowait=nowait)

    async def _list_members(
        self, method_model_id: UUID, *, lock: bool, nowait: bool
    ) -> tuple[MethodModelMemberData, ...]:
        statement = (
            select(MethodModelMemberRecord, MethodElementRecord)
            .join(
                MethodElementRecord,
                MethodElementRecord.id == MethodModelMemberRecord.method_element_id,
            )
            .where(
                MethodModelMemberRecord.method_model_id == method_model_id,
                MethodElementRecord.project_id == self._project_id,
            )
            .order_by(
                MethodModelMemberRecord.review_section,
                MethodModelMemberRecord.sequence_index.nulls_last(),
                MethodElementRecord.element_type,
                MethodElementRecord.id,
            )
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        members: list[MethodModelMemberData] = []
        for member, element in result.all():
            members.append(
                MethodModelMemberData(
                    id=member.id,
                    method_model_id=member.method_model_id,
                    method_element_id=member.method_element_id,
                    review_section=member.review_section,
                    member_role=member.member_role,
                    sequence_index=member.sequence_index,
                    required=member.required,
                    inclusion_rationale=member.inclusion_rationale,
                    element=_to_element(element),
                    subtype_payload=await self._subtype_payload(element),
                )
            )
        return tuple(members)

    async def get_element_for_update(
        self, element_id: UUID, *, nowait: bool = False
    ) -> MethodElementData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodElementRecord)
                .where(
                    MethodElementRecord.id == element_id,
                    MethodElementRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_element(row)

    async def latest_element_lineage_for_update(self, lineage_id: UUID) -> MethodElementData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodElementRecord)
                .where(
                    MethodElementRecord.project_id == self._project_id,
                    MethodElementRecord.lineage_id == lineage_id,
                )
                .order_by(MethodElementRecord.version.desc())
                .limit(1)
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_element(row)

    async def get_accepted_element_lineage_for_update(
        self, lineage_id: UUID
    ) -> MethodElementData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodElementRecord)
                .where(
                    MethodElementRecord.project_id == self._project_id,
                    MethodElementRecord.lineage_id == lineage_id,
                    MethodElementRecord.review_status == "accepted",
                    MethodElementRecord.lifecycle_status.in_(("current", "challenged")),
                )
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_element(row)

    async def get_model_for_update(
        self, model_id: UUID, *, nowait: bool = False
    ) -> MethodModelData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodModelRecord)
                .where(
                    MethodModelRecord.id == model_id,
                    MethodModelRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_model(row)

    async def latest_model_lineage_for_update(self, lineage_id: UUID) -> MethodModelData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodModelRecord)
                .where(
                    MethodModelRecord.project_id == self._project_id,
                    MethodModelRecord.lineage_id == lineage_id,
                )
                .order_by(MethodModelRecord.version.desc())
                .limit(1)
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_model(row)

    async def update_element_review_lifecycle(
        self,
        element_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> MethodElementData:
        values: dict[str, object] = {
            "review_status": review_status,
            "lifecycle_status": lifecycle_status,
        }
        if supersedes_id is not None:
            values["supersedes_id"] = supersedes_id
        result = await execute_database(
            lambda: self._session.execute(
                update(MethodElementRecord)
                .where(
                    MethodElementRecord.id == element_id,
                    MethodElementRecord.project_id == self._project_id,
                    MethodElementRecord.version == expected_version,
                    MethodElementRecord.review_status == expected_review_status,
                    MethodElementRecord.lifecycle_status == expected_lifecycle_status,
                )
                .values(**values)
                .returning(MethodElementRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_method_element"})
        return _to_element(row)

    async def update_model_review_lifecycle(
        self,
        model_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> MethodModelData:
        values: dict[str, object] = {
            "review_status": review_status,
            "lifecycle_status": lifecycle_status,
        }
        if supersedes_id is not None:
            values["supersedes_id"] = supersedes_id
        result = await execute_database(
            lambda: self._session.execute(
                update(MethodModelRecord)
                .where(
                    MethodModelRecord.id == model_id,
                    MethodModelRecord.project_id == self._project_id,
                    MethodModelRecord.version == expected_version,
                    MethodModelRecord.review_status == expected_review_status,
                    MethodModelRecord.lifecycle_status == expected_lifecycle_status,
                )
                .values(**values)
                .returning(MethodModelRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_method_model"})
        return _to_model(row)

    async def add_section_review(
        self, review: NewMethodModelSectionReview
    ) -> MethodModelSectionReviewData:
        row = MethodModelSectionReviewRecord(
            id=review.id,
            method_model_id=review.method_model_id,
            review_section=review.review_section,
            version=review.version,
            outcome=review.outcome,
            member_set_fingerprint=review.member_set_fingerprint,
            reviewer_id=review.reviewer_id,
            rationale=review.rationale,
            operation_event_id=review.operation_event_id,
            supersedes_id=review.supersedes_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return self._to_section_review(row)

    async def add_rendering(self, rendering: NewMethodModelRendering) -> MethodModelRenderingData:
        row = MethodModelRenderingRecord(
            id=rendering.id,
            method_model_id=rendering.method_model_id,
            rendering_type=rendering.rendering_type,
            content=rendering.content,
            prompt_version=rendering.prompt_version,
            provider=rendering.provider,
            model_version=rendering.model_version,
            generation_record_id=rendering.generation_record_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return MethodModelRenderingData(
            id=row.id,
            method_model_id=row.method_model_id,
            rendering_type=row.rendering_type,
            content=row.content,
            prompt_version=row.prompt_version,
            provider=row.provider,
            model_version=row.model_version,
            generation_record_id=row.generation_record_id,
        )

    async def list_current_section_reviews(
        self, method_model_id: UUID, *, for_update: bool = False
    ) -> tuple[MethodModelSectionReviewData, ...]:
        statement = (
            select(MethodModelSectionReviewRecord)
            .where(MethodModelSectionReviewRecord.method_model_id == method_model_id)
            .order_by(
                MethodModelSectionReviewRecord.review_section,
                MethodModelSectionReviewRecord.version.desc(),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        result = await execute_database(lambda: self._session.execute(statement))
        current: dict[str, MethodModelSectionReviewData] = {}
        for row in result.scalars():
            current.setdefault(row.review_section, self._to_section_review(row))
        return tuple(current[key] for key in sorted(current))

    async def get_current_section_review_for_update(
        self, method_model_id: UUID, review_section: str
    ) -> MethodModelSectionReviewData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodModelSectionReviewRecord)
                .where(
                    MethodModelSectionReviewRecord.method_model_id == method_model_id,
                    MethodModelSectionReviewRecord.review_section == review_section,
                )
                .order_by(MethodModelSectionReviewRecord.version.desc())
                .limit(1)
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else self._to_section_review(row)

    @staticmethod
    def _to_section_review(
        row: MethodModelSectionReviewRecord,
    ) -> MethodModelSectionReviewData:
        return MethodModelSectionReviewData(
            id=row.id,
            method_model_id=row.method_model_id,
            review_section=row.review_section,
            version=row.version,
            outcome=row.outcome,
            member_set_fingerprint=row.member_set_fingerprint,
            reviewer_id=row.reviewer_id,
            rationale=row.rationale,
            operation_event_id=row.operation_event_id,
            supersedes_id=row.supersedes_id,
        )

    async def get_assessment_exact(self, assessment_id: UUID) -> MethodModelAssessmentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodModelAssessmentRecord)
                .join(
                    MethodModelRecord,
                    MethodModelRecord.id == MethodModelAssessmentRecord.method_model_id,
                )
                .where(
                    MethodModelAssessmentRecord.id == assessment_id,
                    MethodModelRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_model_assessment(row)

    async def _subtype_payload(self, element: MethodElementRecord) -> dict[str, object]:
        mapping = {
            "component": MethodComponentRecord,
            "equation": MethodEquationRecord,
            "symbol": MethodSymbolRecord,
            "algorithm_step": MethodAlgorithmStepRecord,
            "data_flow": MethodDataFlowRecord,
            "validity_condition": MethodValidityConditionRecord,
        }
        subtype_type = mapping[element.element_type]
        subtype = await execute_database(lambda: self._session.get(subtype_type, element.id))
        if subtype is None:
            raise DomainValidationError(details={"reason": "method_subtype_missing"})
        excluded = {"method_element_id", "_sa_instance_state"}
        return {key: value for key, value in vars(subtype).items() if key not in excluded}

    async def get_element_exact(self, element_id: UUID) -> MethodElementData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodElementRecord).where(
                    MethodElementRecord.id == element_id,
                    MethodElementRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_element(row)

    async def get_element_payload_exact(self, element_id: UUID) -> dict[str, object] | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodElementRecord).where(
                    MethodElementRecord.id == element_id,
                    MethodElementRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else await self._subtype_payload(row)

    async def list_accepted_current_elements(self) -> tuple[MethodElementData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodElementRecord)
                .where(
                    MethodElementRecord.project_id == self._project_id,
                    MethodElementRecord.review_status == "accepted",
                    MethodElementRecord.lifecycle_status == "current",
                )
                .order_by(MethodElementRecord.created_at, MethodElementRecord.id)
            )
        )
        return tuple(_to_element(row) for row in result.scalars())

    async def get_model_exact(self, model_id: UUID) -> MethodModelData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodModelRecord).where(
                    MethodModelRecord.id == model_id,
                    MethodModelRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_model(row)

    async def list_models_for_question(
        self, research_question_id: UUID
    ) -> tuple[MethodModelData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(MethodModelRecord)
                .where(
                    MethodModelRecord.project_id == self._project_id,
                    MethodModelRecord.research_question_id == research_question_id,
                )
                .order_by(
                    MethodModelRecord.lineage_id,
                    MethodModelRecord.version.desc(),
                    MethodModelRecord.id,
                )
            )
        )
        return tuple(_to_model(row) for row in result.scalars())

    async def get_accepted_current_model(
        self, research_question_id: UUID
    ) -> MethodModelData | None:
        return await self._get_accepted_current_model(
            research_question_id, lock=False, nowait=False
        )

    async def get_accepted_current_model_for_update(
        self, research_question_id: UUID, *, nowait: bool = False
    ) -> MethodModelData | None:
        return await self._get_accepted_current_model(
            research_question_id, lock=True, nowait=nowait
        )

    async def _get_accepted_current_model(
        self, research_question_id: UUID, *, lock: bool, nowait: bool
    ) -> MethodModelData | None:
        statement = select(MethodModelRecord).where(
            MethodModelRecord.project_id == self._project_id,
            MethodModelRecord.research_question_id == research_question_id,
            MethodModelRecord.review_status == "accepted",
            MethodModelRecord.lifecycle_status == "current",
            MethodModelRecord.freshness_status == "current",
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_model(row)
