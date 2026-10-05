"""Server-authoritative combined primary-source scientific-review gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.application.method_extraction_contracts import (
    METHOD_EXTRACTION_TASK,
    METHOD_MERGE_TASK,
)
from researchflow.application.proposition_extraction_contracts import (
    PROPOSITION_EXTRACTION_TASK,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import (
    FreshnessStatus,
    MethodComponentType,
    MethodElementType,
    MethodReviewSection,
    SectionReviewOutcome,
)
from researchflow.domain.method_review import (
    MethodMemberSnapshot,
    MethodReviewPolicy,
    SectionReviewSnapshot,
    method_composition_fingerprint,
)
from researchflow.domain.method_validation import classify_method_membership
from researchflow.errors import (
    DomainValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.repositories import (
    GenerationRecordData,
    KnowledgeObjectData,
    MethodModelAssessmentData,
    MethodModelData,
    MethodModelMemberData,
    MethodModelSectionReviewData,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
    ResearchQuestionData,
    ResearchQuestionSourceData,
    SourceData,
    SourceProcessingRunData,
    WorkflowRunData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

PRIMARY_SOURCE_REVIEW_GATE_POLICY_VERSION: Final = "rf-primary-source-review-gate-v1"
PRIMARY_SOURCE_REVIEW_EVENT: Final = "paper_scientific_review_completed"
_METHOD_TASKS: Final = frozenset({METHOD_EXTRACTION_TASK, METHOD_MERGE_TASK})
_REVIEW_GENERATION_TASKS: Final = _METHOD_TASKS | {PROPOSITION_EXTRACTION_TASK}


@dataclass(frozen=True, slots=True)
class CompletePrimarySourceReviewCommand:
    project_id: UUID
    method_model_id: UUID
    expected_gate_fingerprint: str
    confirmed: bool
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PrimarySourceReviewGateView:
    method_model_id: UUID
    workflow_run_id: UUID | None
    gate_fingerprint: str
    ready: bool
    blocking_reasons: tuple[str, ...]
    proposition_total: int
    proposition_adjudicated: int
    resolved_section_count: int
    method_model_accepted: bool
    completed_event: OperationEventData | None


@dataclass(frozen=True, slots=True)
class PrimarySourceReviewGateResult:
    event: OperationEventData
    gate_fingerprint: str
    accepted_method_model_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class _GateSnapshot:
    project: ProjectData
    question: ResearchQuestionData | None
    paper_relationship: ResearchQuestionSourceData | None
    source: SourceData | None
    processing_run: SourceProcessingRunData | None
    workflow: WorkflowRunData | None
    generations: tuple[GenerationRecordData, ...]
    propositions: tuple[KnowledgeObjectData, ...]
    model: MethodModelData | None
    members: tuple[MethodModelMemberData, ...]
    sections: tuple[MethodModelSectionReviewData, ...]
    assessment: MethodModelAssessmentData | None
    blockers: tuple[str, ...]
    proposition_adjudicated: int
    fingerprint: str


def _proposition_review_progress(
    propositions: tuple[KnowledgeObjectData, ...],
) -> tuple[tuple[KnowledgeObjectData, ...], int]:
    """Count immutable active versions; derived candidates remain separately reviewable."""
    active = tuple(
        item
        for item in propositions
        if item.lifecycle_status != KnowledgeLifecycleStatus.SUPERSEDED.value
    )
    adjudicated = sum(
        1
        for item in active
        if item.lifecycle_status == KnowledgeLifecycleStatus.CURRENT.value
        and item.review_status
        in {KnowledgeReviewStatus.ACCEPTED.value, KnowledgeReviewStatus.REJECTED.value}
    )
    return active, adjudicated


class PrimarySourceReviewGateService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def status(self, project_id: UUID, method_model_id: UUID) -> PrimarySourceReviewGateView:
        async with self._uow_factory(project_id) as uow:
            project = self._require_owned(await uow.projects.get())
            snapshot = await self._snapshot(uow, project, method_model_id, lock=False)
            event = await uow.events.get_by_idempotency_key(
                PRIMARY_SOURCE_REVIEW_EVENT, snapshot.fingerprint
            )
            await uow.rollback()
            return self._view(snapshot, event)

    async def complete(
        self, command: CompletePrimarySourceReviewCommand
    ) -> PrimarySourceReviewGateResult:
        if not command.confirmed:
            raise DomainValidationError(details={"reason": "review_confirmation_required"})
        async with self._uow_factory(command.project_id) as uow:
            project = self._require_owned(await uow.projects.get_for_update())
            snapshot = await self._snapshot(uow, project, command.method_model_id, lock=True)
            if snapshot.fingerprint != command.expected_gate_fingerprint:
                raise ResourceConflictError(details={"reason": "review_gate_fingerprint_stale"})
            if snapshot.blockers:
                raise ResourceConflictError(
                    details={
                        "reason": "primary_source_review_incomplete",
                        "blocking_reasons": ",".join(snapshot.blockers),
                    }
                )
            existing = await uow.events.get_by_idempotency_key(
                PRIMARY_SOURCE_REVIEW_EVENT, snapshot.fingerprint
            )
            if existing is not None:
                await uow.rollback()
                return PrimarySourceReviewGateResult(
                    existing, snapshot.fingerprint, command.method_model_id, True
                )
            if snapshot.workflow is None or snapshot.model is None:
                raise ResourceConflictError(details={"reason": "primary_source_review_incomplete"})
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"researchflow:{PRIMARY_SOURCE_REVIEW_EVENT}:"
                        f"{command.project_id}:{snapshot.fingerprint}",
                    ),
                    project_id=command.project_id,
                    workflow_run_id=snapshot.workflow.id,
                    operation_type=PRIMARY_SOURCE_REVIEW_EVENT,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=self._input_refs(snapshot),
                    output_refs=(
                        {
                            "type": "accepted_method_model",
                            "id": str(snapshot.model.id),
                            "lineage_id": str(snapshot.model.lineage_id),
                            "version": snapshot.model.version,
                            "composition_fingerprint": snapshot.model.composition_fingerprint,
                            "gate_fingerprint": snapshot.fingerprint,
                        },
                    ),
                    reason="Researcher confirmed the exact completed primary-source review set.",
                    idempotency_key=snapshot.fingerprint,
                    input_fingerprint=snapshot.fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return PrimarySourceReviewGateResult(
                event, snapshot.fingerprint, snapshot.model.id, False
            )

    async def _snapshot(
        self,
        uow: UnitOfWork,
        project: ProjectData,
        method_model_id: UUID,
        *,
        lock: bool,
    ) -> _GateSnapshot:
        blockers: list[str] = []
        model = (
            await uow.method_models.get_model_for_update(method_model_id)
            if lock
            else await uow.method_models.get_model_exact(method_model_id)
        )
        if model is None:
            raise ResourceNotFoundError(details={"resource": "method_model"})
        workflow = await self._workflow_for_model(uow, model, lock=lock)
        if workflow is None:
            blockers.append("method_model_workflow_missing")

        question = None
        paper = None
        source = None
        processing_run = None
        generations: tuple[GenerationRecordData, ...] = ()
        propositions: tuple[KnowledgeObjectData, ...] = ()
        if workflow is not None:
            question = (
                await uow.research_questions.get_exact_for_update(
                    workflow.primary_research_question_id
                )
                if lock
                else await uow.research_questions.get_exact(workflow.primary_research_question_id)
            )
            paper = (
                await uow.sources.get_current_primary_paper_for_update()
                if lock
                else await uow.sources.get_current_primary_paper()
            )
            if question is None or question.id != project.primary_research_question_id:
                blockers.append("primary_research_question_changed")
            if (
                paper is None
                or workflow.primary_question_source_relationship_id is None
                or paper.id != workflow.primary_question_source_relationship_id
                or paper.research_question_id != workflow.primary_research_question_id
            ):
                blockers.append("primary_paper_changed")
            if paper is not None:
                source = await uow.sources.get_exact(paper.source_id)
            if source is None:
                blockers.append("primary_source_missing")
            if workflow.primary_source_processing_run_id is not None:
                processing_run = await uow.sources.get_processing_run_exact(
                    workflow.primary_source_processing_run_id
                )
            if (
                processing_run is None
                or source is None
                or processing_run.source_id != source.id
                or processing_run.source_hash != source.content_hash
                or processing_run.status != "succeeded"
            ):
                blockers.append("source_processing_changed")
            generations = await uow.generations.list_for_workflow(workflow.id)
            propositions = await self._propositions(uow, generations, lock=lock)
            if not any(
                item.task_name == PROPOSITION_EXTRACTION_TASK and item.status == "succeeded"
                for item in generations
            ):
                blockers.append("proposition_extraction_missing")

        active_propositions, adjudicated = _proposition_review_progress(propositions)
        if adjudicated != len(active_propositions):
            blockers.append("proposition_review_incomplete")

        members = (
            await uow.method_models.list_members_for_update(model.id)
            if lock
            else await uow.method_models.list_members(model.id)
        )
        sections = await uow.method_models.list_current_section_reviews(model.id, for_update=lock)
        assessment = (
            None
            if model.current_assessment_id is None
            else await uow.method_models.get_assessment_exact(model.current_assessment_id)
        )
        if (
            model.research_question_id != project.primary_research_question_id
            or model.review_status != KnowledgeReviewStatus.ACCEPTED.value
            or model.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
            or model.freshness_status != FreshnessStatus.CURRENT.value
        ):
            blockers.append("method_model_not_accepted_current")
        if any(
            item.element.review_status != KnowledgeReviewStatus.ACCEPTED.value
            or item.element.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
            for item in members
        ):
            blockers.append("method_element_review_incomplete")
        if assessment is None or assessment.outcome == "insufficient":
            blockers.append("method_model_assessment_insufficient")
        try:
            snapshots = tuple(_member_snapshot(item) for item in members)
            if method_composition_fingerprint(snapshots) != model.composition_fingerprint:
                raise ResourceConflictError(details={"reason": "method_composition_changed"})
            MethodReviewPolicy.validate_model_acceptance(
                composition_members=snapshots,
                current_section_reviews=tuple(_section_snapshot(item) for item in sections),
            )
        except (DomainValidationError, ResourceConflictError):
            blockers.append("method_section_review_incomplete")

        payload = _fingerprint_payload(
            project=project,
            question=question,
            paper=paper,
            source=source,
            processing_run=processing_run,
            workflow=workflow,
            generations=generations,
            propositions=propositions,
            model=model,
            members=members,
            sections=sections,
            assessment=assessment,
        )
        return _GateSnapshot(
            project=project,
            question=question,
            paper_relationship=paper,
            source=source,
            processing_run=processing_run,
            workflow=workflow,
            generations=generations,
            propositions=propositions,
            model=model,
            members=members,
            sections=sections,
            assessment=assessment,
            blockers=tuple(dict.fromkeys(blockers)),
            proposition_adjudicated=adjudicated,
            fingerprint=_fingerprint(payload),
        )

    async def _workflow_for_model(
        self, uow: UnitOfWork, model: MethodModelData, *, lock: bool
    ) -> WorkflowRunData | None:
        cursor = model
        visited: set[UUID] = set()
        while cursor.generation_record_id is None:
            if cursor.derived_from_id is None or cursor.id in visited:
                return None
            visited.add(cursor.id)
            predecessor = await uow.method_models.get_model_exact(cursor.derived_from_id)
            if predecessor is None:
                return None
            cursor = predecessor
        generation = await uow.generations.get_exact(cursor.generation_record_id)
        if (
            generation is None
            or generation.task_name not in _METHOD_TASKS
            or generation.workflow_run_id is None
        ):
            return None
        return (
            await uow.workflows.get_for_update(generation.workflow_run_id)
            if lock
            else await uow.workflows.get_exact(generation.workflow_run_id)
        )

    async def _propositions(
        self,
        uow: UnitOfWork,
        generations: tuple[GenerationRecordData, ...],
        *,
        lock: bool,
    ) -> tuple[KnowledgeObjectData, ...]:
        generation_ids = {
            item.id
            for item in generations
            if item.task_name == PROPOSITION_EXTRACTION_TASK and item.status == "succeeded"
        }
        all_items = await uow.knowledge.list_review_items()
        lineages = sorted(
            {item.lineage_id for item in all_items if item.generation_record_id in generation_ids},
            key=str,
        )
        if lock:
            versions = [
                item
                for lineage_id in lineages
                for item in await uow.knowledge.list_lineage_for_update(lineage_id)
            ]
        else:
            versions = [item for item in all_items if item.lineage_id in set(lineages)]
        return tuple(
            sorted(versions, key=lambda item: (str(item.lineage_id), item.version, str(item.id)))
        )

    def _view(
        self, snapshot: _GateSnapshot, event: OperationEventData | None
    ) -> PrimarySourceReviewGateView:
        return PrimarySourceReviewGateView(
            method_model_id=snapshot.model.id if snapshot.model is not None else UUID(int=0),
            workflow_run_id=None if snapshot.workflow is None else snapshot.workflow.id,
            gate_fingerprint=snapshot.fingerprint,
            ready=not snapshot.blockers,
            blocking_reasons=snapshot.blockers,
            proposition_total=len(_proposition_review_progress(snapshot.propositions)[0]),
            proposition_adjudicated=snapshot.proposition_adjudicated,
            resolved_section_count=len(snapshot.sections),
            method_model_accepted=(
                snapshot.model is not None
                and snapshot.model.review_status == KnowledgeReviewStatus.ACCEPTED.value
                and snapshot.model.lifecycle_status == KnowledgeLifecycleStatus.CURRENT.value
            ),
            completed_event=event,
        )

    def _input_refs(self, snapshot: _GateSnapshot) -> tuple[dict[str, object], ...]:
        payload = _fingerprint_payload(
            project=snapshot.project,
            question=snapshot.question,
            paper=snapshot.paper_relationship,
            source=snapshot.source,
            processing_run=snapshot.processing_run,
            workflow=snapshot.workflow,
            generations=snapshot.generations,
            propositions=snapshot.propositions,
            model=snapshot.model,
            members=snapshot.members,
            sections=snapshot.sections,
            assessment=snapshot.assessment,
        )
        return tuple(
            {"type": key, **value} if isinstance(value, dict) else {"type": key, "items": value}
            for key, value in payload.items()
            if key != "policy_version" and value is not None
        )

    def _require_owned(self, project: ProjectData | None) -> ProjectData:
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        return project


def accepted_method_model_id_from_review_event(
    event: OperationEventData | None,
) -> UUID | None:
    """Workflow-facing interrupt seam: wait on absence, expose the proven model on success."""
    if event is None:
        return None
    if event.operation_type != PRIMARY_SOURCE_REVIEW_EVENT:
        raise DomainValidationError(details={"reason": "review_gate_event_type_invalid"})
    references = [item for item in event.output_refs if item.get("type") == "accepted_method_model"]
    if len(references) != 1 or not isinstance(references[0].get("id"), str):
        raise DomainValidationError(details={"reason": "review_gate_event_output_invalid"})
    try:
        return UUID(str(references[0]["id"]))
    except ValueError as error:
        raise DomainValidationError(
            details={"reason": "review_gate_event_output_invalid"}
        ) from error


def _member_snapshot(member: MethodModelMemberData) -> MethodMemberSnapshot:
    element_type = MethodElementType(member.element.element_type)
    classification = classify_method_membership(
        element_type=element_type,
        component_type=(
            MethodComponentType(str(member.subtype_payload["component_type"]))
            if element_type is MethodElementType.COMPONENT
            else None
        ),
    )
    return MethodMemberSnapshot(
        method_element_id=member.element.id,
        element_lineage_id=member.element.lineage_id,
        element_version=member.element.version,
        review_section=MethodReviewSection(member.review_section),
        member_role=classification.member_role,
        sequence_index=member.sequence_index,
        required=member.required,
        inclusion_rationale=member.inclusion_rationale,
        review_status=KnowledgeReviewStatus(member.element.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(member.element.lifecycle_status),
        freshness_status=FreshnessStatus.CURRENT,
    ).validated()


def _section_snapshot(review: MethodModelSectionReviewData) -> SectionReviewSnapshot:
    return SectionReviewSnapshot(
        review_section=MethodReviewSection(review.review_section),
        outcome=SectionReviewOutcome(review.outcome),
        member_set_fingerprint=review.member_set_fingerprint,
        rationale=review.rationale,
    )


def _fingerprint_payload(
    *,
    project: ProjectData,
    question: ResearchQuestionData | None,
    paper: ResearchQuestionSourceData | None,
    source: SourceData | None,
    processing_run: SourceProcessingRunData | None,
    workflow: WorkflowRunData | None,
    generations: tuple[GenerationRecordData, ...],
    propositions: tuple[KnowledgeObjectData, ...],
    model: MethodModelData | None,
    members: tuple[MethodModelMemberData, ...],
    sections: tuple[MethodModelSectionReviewData, ...],
    assessment: MethodModelAssessmentData | None,
) -> dict[str, object]:
    return {
        "policy_version": PRIMARY_SOURCE_REVIEW_GATE_POLICY_VERSION,
        "project": {
            "id": str(project.id),
            "objective": project.target_outcome,
            "depth": project.reproduction_depth,
            "primary_research_question_id": str(project.primary_research_question_id),
        },
        "research_question": None
        if question is None
        else {
            "id": str(question.id),
            "lineage_id": str(question.lineage_id),
            "version": question.version,
            "question_text": question.question_text,
            "scope_text": question.scope_text,
            "lifecycle_status": question.lifecycle_status,
        },
        "primary_paper_relationship": None
        if paper is None
        else {
            "id": str(paper.id),
            "lineage_id": str(paper.lineage_id),
            "version": paper.version,
            "source_id": str(paper.source_id),
            "research_question_id": str(paper.research_question_id),
            "review_status": paper.review_status,
            "lifecycle_status": paper.lifecycle_status,
        },
        "source": None
        if source is None
        else {"id": str(source.id), "content_hash": source.content_hash},
        "source_processing_run": None
        if processing_run is None
        else {
            "id": str(processing_run.id),
            "source_id": str(processing_run.source_id),
            "source_hash": processing_run.source_hash,
            "parser": f"{processing_run.parser_name}:{processing_run.parser_version}",
            "chunker": f"{processing_run.chunker_name}:{processing_run.chunker_version}",
            "normalization_version": processing_run.normalization_version,
            "status": processing_run.status,
        },
        "workflow": None if workflow is None else {"id": str(workflow.id)},
        "generation_records": [
            {
                "id": str(item.id),
                "task_name": item.task_name,
                "prompt_version": item.prompt_version,
                "schema_version": item.schema_version,
                "provider": item.provider,
                "model_version": item.model_version,
                "reasoning_version": item.reasoning_version,
                "input_fingerprint": item.input_fingerprint,
                "status": item.status,
            }
            for item in generations
            if item.task_name in _REVIEW_GENERATION_TASKS
        ],
        "propositions": [
            {
                "id": str(item.id),
                "lineage_id": str(item.lineage_id),
                "version": item.version,
                "object_type": item.object_type,
                "normalized_statement_hash": item.normalized_statement_hash,
                "review_status": item.review_status,
                "lifecycle_status": item.lifecycle_status,
                "grounding_status": item.grounding_status,
                "grounding_assessment_id": (
                    None
                    if item.grounding_assessment_id is None
                    else str(item.grounding_assessment_id)
                ),
            }
            for item in propositions
        ],
        "method_model": None
        if model is None
        else {
            "id": str(model.id),
            "lineage_id": str(model.lineage_id),
            "version": model.version,
            "research_question_id": str(model.research_question_id),
            "composition_fingerprint": model.composition_fingerprint,
            "review_status": model.review_status,
            "lifecycle_status": model.lifecycle_status,
            "freshness_status": model.freshness_status,
            "assessment_id": None
            if model.current_assessment_id is None
            else str(model.current_assessment_id),
        },
        "method_members": [
            {
                "member_id": str(item.id),
                "element_id": str(item.element.id),
                "lineage_id": str(item.element.lineage_id),
                "version": item.element.version,
                "section": item.review_section,
                "role": item.member_role,
                "required": item.required,
                "review_status": item.element.review_status,
                "lifecycle_status": item.element.lifecycle_status,
                "grounding_status": item.element.grounding_status,
            }
            for item in members
        ],
        "section_reviews": [
            {
                "id": str(item.id),
                "section": item.review_section,
                "version": item.version,
                "outcome": item.outcome,
                "member_set_fingerprint": item.member_set_fingerprint,
                "operation_event_id": str(item.operation_event_id),
            }
            for item in sections
        ],
        "method_model_assessment": None
        if assessment is None
        else {
            "id": str(assessment.id),
            "outcome": assessment.outcome,
            "unsupported_required_count": assessment.unsupported_required_count,
            "mixed_evidence_count": assessment.mixed_evidence_count,
            "contradicted_count": assessment.contradicted_count,
            "missing_provenance_count": assessment.missing_provenance_count,
            "unresolved_scientific_ambiguity_count": (
                assessment.unresolved_scientific_ambiguity_count
            ),
        },
    }


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
