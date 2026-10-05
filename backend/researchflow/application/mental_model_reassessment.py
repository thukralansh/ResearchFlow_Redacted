""" ordered post-mapping Mental Model reassessment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.api.schemas.mental_model_assessments import (
    MentalModelAssessmentCoverageResponse,
    MentalModelAssessmentRecordResponse,
    MentalModelMemberChange,
    MentalModelReassessmentBatch,
    MentalModelReassessmentResponse,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprint_catalog import DependencyRole
from researchflow.domain.knowledge_catalog import KnowledgeObjectType
from researchflow.domain.mental_model_reassessment import (
    MENTAL_MODEL_REASSESSMENT_POLICY_VERSION,
    MENTAL_MODEL_REASSESSMENT_PROMPT_VERSION,
    MENTAL_MODEL_REASSESSMENT_SCHEMA_NAME,
    MENTAL_MODEL_REASSESSMENT_SCHEMA_VERSION,
    MENTAL_MODEL_REASSESSMENT_TASK_NAME,
    MentalModelMemberSemantic,
    reassessment_fingerprint,
    semantic_composition_changed,
)
from researchflow.domain.mental_models import (
    MentalModelMemberProposal,
    composition_fingerprint,
    validate_member_proposal,
)
from researchflow.domain.model_catalog import MentalModelMemberRole
from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.providers import (
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    EvidenceLinkData,
    GenerationRecordData,
    HumanJudgmentData,
    KnowledgeObjectData,
    MentalModelAssessmentData,
    MentalModelData,
    MentalModelMemberData,
    MethodEvidenceLinkData,
    MethodModelAssessmentData,
    MethodModelData,
    NewMentalModel,
    NewMentalModelAssessment,
    NewMentalModelMember,
    NewMentalModelRendering,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
    ResearchQuestionData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/")
_SUCCEEDED = "mental_model.reassessment.succeeded"
_NOT_APPLICABLE = "mental_model.reassessment.not_applicable"
_EMPTY_FINGERPRINT = reassessment_fingerprint([])
type _TargetType = Literal["knowledge_object", "human_judgment", "evidence_link"]


@dataclass(frozen=True, slots=True)
class ReassessMentalModelCommand:
    project_id: UUID
    workflow_run_id: UUID
    mapping_review_gate_event_id: UUID
    method_model_reassessment_event_id: UUID
    applicable: bool
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    request_id: str | None = None
    trigger_human_judgment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _Context:
    project: ProjectData
    question: ResearchQuestionData
    model: MentalModelData
    members: tuple[MentalModelMemberData, ...]
    method_model: MethodModelData
    method_assessment: MethodModelAssessmentData
    mapping_gate: OperationEventData
    method_event: OperationEventData
    relationship_id: UUID | None
    snapshot_id: UUID | None
    knowledge: tuple[KnowledgeObjectData, ...]
    judgments: tuple[HumanJudgmentData, ...]
    knowledge_evidence: tuple[EvidenceLinkData, ...]
    method_evidence: tuple[MethodEvidenceLinkData, ...]
    mapping_ids: tuple[UUID, ...]
    method_fingerprint: str
    repository_fingerprint: str
    knowledge_fingerprint: str
    mapping_fingerprint: str
    judgment_fingerprint: str
    payload: dict[str, object]
    fingerprint: str


class MentalModelReassessmentService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: StructuredGenerationService,
        staleness_hook: StalenessService | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service
        self._staleness_hook = staleness_hook

    async def after_mapping(
        self, command: ReassessMentalModelCommand
    ) -> MentalModelReassessmentResponse:
        if not command.applicable:
            return await self._not_applicable(command)
        async with self._uow_factory(command.project_id) as uow:
            context = await _load_context(uow, self._actor, command, lock=False)
            await uow.rollback()
        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=MENTAL_MODEL_REASSESSMENT_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=MENTAL_MODEL_REASSESSMENT_PROMPT_VERSION,
                schema_name=MENTAL_MODEL_REASSESSMENT_SCHEMA_NAME,
                schema_version=MENTAL_MODEL_REASSESSMENT_SCHEMA_VERSION,
                output_type=MentalModelReassessmentBatch,
                messages=(
                    StructuredGenerationMessage(
                        role="user",
                        content=json.dumps(
                            context.payload,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                ),
                model_policy=command.model_policy,
                explicit_rerun=command.explicit_rerun,
            )
        )
        async with self._uow_factory(command.project_id) as uow:
            replay_event = await uow.events.get_exact(_id(outcome.generation_record.id, "event"))
            if replay_event is not None:
                response = await _replay(uow, replay_event)
                await uow.rollback()
                return response
            current = await _load_context(uow, self._actor, command, lock=True)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "mental_reassessment_stale_context"})
            response = await _persist(
                uow=uow,
                actor=self._actor,
                command=command,
                context=current,
                generation=outcome.generation_record,
                batch=outcome.value,
                replayed=outcome.replayed,
            )
            if response.outcome == "new_version_created" and self._staleness_hook is not None:
                await self._staleness_hook.artifact_changed(
                    uow=uow,
                    project_id=command.project_id,
                    cause_type="mental_model",
                    before_id=response.prior_mental_model_id,
                    after_id=response.resulting_mental_model_id,
                    operation_type="mental_model.replaced",
                )
            await uow.commit()
            return response

    async def _not_applicable(
        self, command: ReassessMentalModelCommand
    ) -> MentalModelReassessmentResponse:
        async with self._uow_factory(command.project_id) as uow:
            context = await _load_context(uow, self._actor, command, lock=True)
            fingerprint = context.fingerprint
            event_key = _command_key(command, fingerprint)
            existing = await uow.events.get_by_idempotency_key(_NOT_APPLICABLE, event_key)
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                response = await _replay(uow, existing)
                await uow.rollback()
                return response
            if context.model.method_model_id != context.method_model.id:
                raise ResourceConflictError(
                    details={"reason": "mental_model_method_model_not_current"}
                )
            rationale = (
                "Repository evidence is explicitly not applicable to this objective. The current "
                "project-level Mental Model remains assessed against the exact current question, "
                "accepted MethodModel, accepted project knowledge, and Human Judgments without "
                "fabricating repository state."
            )
            assessment = await uow.mental_models.add_assessment(
                _new_assessment(
                    context=context,
                    identifier=_id(
                        command.workflow_run_id,
                        f"not-applicable-assessment:{fingerprint}",
                    ),
                    result="no_change",
                    resulting_model_id=None,
                    rationale=rationale,
                    generation_record_id=None,
                )
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=_id(command.workflow_run_id, f"not-applicable-event:{fingerprint}"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_NOT_APPLICABLE,
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=_event_inputs(context),
                    output_refs=(
                        _model_ref(context.model),
                        {"type": "mental_model_assessment", "id": str(assessment.id)},
                    ),
                    reason=rationale,
                    idempotency_key=event_key,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return _response(
                context=context,
                assessment=assessment,
                event=event,
                generation=None,
                outcome="not_applicable",
                resulting_model=context.model,
                prior_members=context.members,
                resulting_members=context.members,
                replayed=False,
                authority_disclosure=None,
            )


class MentalModelAssessmentQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def detail(
        self, project_id: UUID, generation_record_id: UUID
    ) -> MentalModelReassessmentResponse:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            generation = await uow.generations.get_exact(generation_record_id)
            event = await uow.events.get_exact(_id(generation_record_id, "event"))
            if (
                generation is None
                or generation.task_name != MENTAL_MODEL_REASSESSMENT_TASK_NAME
                or event is None
                or event.operation_type != _SUCCEEDED
            ):
                raise ResourceNotFoundError(details={"resource": "mental_model_reassessment"})
            response = await _replay(uow, event)
            await uow.rollback()
            return response

    async def current_coverage(self, project_id: UUID) -> MentalModelAssessmentCoverageResponse:
        async with self._uow_factory(project_id) as uow:
            project = _require_owned(self._actor, await uow.projects.get())
            model = await uow.mental_models.get_current()
            if model is None:
                raise ResourceNotFoundError(details={"resource": "mental_model"})
            assessment = await uow.mental_models.latest_assessment_for_model(model.id)
            covered = False
            reason = "No post-mapping assessment covers the current Mental Model."
            if assessment is not None:
                core = await _coverage_fingerprints(uow, project, model, assessment)
                covered = core
                reason = (
                    "The latest assessment covers the exact current project inputs."
                    if covered
                    else "The latest assessment no longer matches the exact current project inputs."
                )
            await uow.rollback()
            return MentalModelAssessmentCoverageResponse(
                current_mental_model_id=model.id,
                covered=covered,
                assessment=None if assessment is None else _assessment_response(assessment),
                reason=reason,
            )


async def _load_context(
    uow: UnitOfWork,
    actor: ActorContext,
    command: ReassessMentalModelCommand,
    *,
    lock: bool,
) -> _Context:
    project = _require_owned(
        actor, await (uow.projects.get_for_update() if lock else uow.projects.get())
    )
    workflow = await (
        uow.workflows.get_for_update(command.workflow_run_id)
        if lock
        else uow.workflows.get_exact(command.workflow_run_id)
    )
    question = await (
        uow.research_questions.get_current_primary_for_update()
        if lock
        else uow.research_questions.get_current_primary()
    )
    model = await (
        uow.mental_models.get_current_for_update() if lock else uow.mental_models.get_current()
    )
    if (
        workflow is None
        or question is None
        or model is None
        or workflow.primary_research_question_id != question.id
        or model.research_question_id != question.id
    ):
        raise ResourceConflictError(details={"reason": "mental_reassessment_context_missing"})
    mapping_gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
    method_event = await uow.events.get_exact(command.method_model_reassessment_event_id)
    if (
        mapping_gate is None
        or method_event is None
        or mapping_gate.workflow_run_id != workflow.id
        or method_event.workflow_run_id != workflow.id
    ):
        raise ResourceNotFoundError(details={"resource": "reassessment_prerequisite"})
    expected_gate = (
        "mapping.review_gate.completed"
        if command.applicable
        else "mapping.review_gate.not_applicable"
    )
    expected_method_event = (
        "method_model.reassessment.succeeded"
        if command.applicable
        else "method_model.reassessment.not_applicable"
    )
    if mapping_gate.operation_type != expected_gate or method_event.operation_type not in {
        expected_method_event,
        "method_model.reassessment.human_judgment",
        "human_judgment.method_reassessment.not_required",
    }:
        raise ResourceConflictError(details={"reason": "reassessment_applicability_mismatch"})
    if _reference_id(method_event.input_refs, "mapping_review_gate") != mapping_gate.id:
        raise ResourceConflictError(details={"reason": "method_reassessment_gate_mismatch"})
    method_model = await (
        uow.method_models.get_accepted_current_model_for_update(question.id)
        if lock
        else uow.method_models.get_accepted_current_model(question.id)
    )
    if method_model is None or method_model.current_assessment_id is None:
        raise ResourceConflictError(details={"reason": "current_assessed_method_model_required"})
    method_assessment = await uow.method_models.get_assessment_exact(
        method_model.current_assessment_id
    )
    if method_assessment is None:
        raise ResourceConflictError(details={"reason": "current_assessed_method_model_required"})
    use_output_refs = (
        command.applicable
        or method_event.operation_type == "method_model.reassessment.human_judgment"
        or method_event.operation_type == "human_judgment.method_reassessment.not_required"
    )
    event_model_id = _reference_id(
        method_event.output_refs if use_output_refs else method_event.input_refs,
        "method_model",
    )
    event_assessment_id = _reference_id(
        method_event.output_refs if use_output_refs else method_event.input_refs,
        "method_model_assessment",
    )
    if event_model_id != method_model.id or event_assessment_id != method_assessment.id:
        raise ResourceConflictError(details={"reason": "method_reassessment_not_current"})
    members = await uow.mental_models.list_members(model.id)
    knowledge = await uow.knowledge.list_accepted_current()
    judgments = await uow.judgments.list_current()
    if command.trigger_human_judgment_id is not None and command.trigger_human_judgment_id not in {
        item.id for item in judgments
    }:
        raise ResourceConflictError(details={"reason": "trigger_human_judgment_not_current"})
    mappings = await uow.mappings.list_accepted_current() if command.applicable else ()
    if command.applicable:
        _validate_mapping_gate_coverage(mapping_gate, mappings, knowledge)
    relationship = (
        await uow.repositories.get_accepted_current_relationship() if command.applicable else None
    )
    snapshot = (
        await uow.repositories.get_snapshot_exact(workflow.repository_snapshot_id)
        if command.applicable and workflow.repository_snapshot_id is not None
        else None
    )
    if command.applicable:
        if (
            relationship is None
            or snapshot is None
            or workflow.paper_repository_relationship_id != relationship.id
            or relationship.repository_snapshot_id != snapshot.id
            or snapshot.lifecycle_status != "current"
            or any(item.repository_snapshot_id != snapshot.id for item in mappings)
        ):
            raise ResourceConflictError(details={"reason": "repository_context_stale"})
    knowledge_evidence: list[EvidenceLinkData] = []
    for item in knowledge:
        knowledge_evidence.extend(
            link
            for link in await uow.knowledge.list_evidence_links(item.id)
            if link.review_status == "accepted" and link.lifecycle_status == "current"
        )
    method_members = await uow.method_models.list_members(method_model.id)
    method_evidence: list[MethodEvidenceLinkData] = []
    for member in method_members:
        if (
            member.element.review_status != "accepted"
            or member.element.lifecycle_status != "current"
        ):
            raise ResourceConflictError(details={"reason": "method_members_not_accepted"})
        method_evidence.extend(
            link
            for link in await uow.method_models.list_evidence_links(member.element.id)
            if link.review_status == "accepted" and link.lifecycle_status == "current"
        )
    method_fp = _method_fingerprint(project, question, method_model, method_assessment)
    repository_fp = _repository_fingerprint(relationship, snapshot)
    knowledge_fp = _knowledge_fingerprint(knowledge, tuple(knowledge_evidence))
    mapping_fp = _mapping_fingerprint(mappings)
    judgment_fp = _judgment_fingerprint(judgments)
    all_evidence: list[Any] = [*knowledge_evidence, *method_evidence]
    repository_payload: dict[str, object] | None = None
    if command.applicable:
        assert relationship is not None and snapshot is not None
        repository_payload = {
            "relationship_id": str(relationship.id),
            "relationship_type": relationship.relationship_type,
            "authority_level": relationship.authority_level,
            "snapshot_id": str(snapshot.id),
            "commit_sha": snapshot.commit_sha,
            "accepted_mappings": [_mapping_identity(item) for item in mappings],
        }
    payload: dict[str, object] = {
        "policy": MENTAL_MODEL_REASSESSMENT_POLICY_VERSION,
        "project": {
            "id": str(project.id),
            "research_question_id": str(question.id),
            "research_question": question.question_text,
            "objective": " ".join(
                value for value in (project.target_outcome, project.description) if value
            ),
            "scope": question.scope_text,
            "research_reproduction_depth": project.reproduction_depth,
        },
        "prerequisites": {
            "mapping_review_gate_event_id": str(mapping_gate.id),
            "mapping_review_gate_fingerprint": mapping_gate.input_fingerprint,
            "method_model_reassessment_event_id": str(method_event.id),
            "method_model_reassessment_fingerprint": method_event.input_fingerprint,
            "trigger_human_judgment_id": (
                None
                if command.trigger_human_judgment_id is None
                else str(command.trigger_human_judgment_id)
            ),
        },
        "current_mental_model": {
            "id": str(model.id),
            "lineage_id": str(model.lineage_id),
            "version": model.version,
            "method_model_id": str(model.method_model_id),
            "composition_fingerprint": model.composition_fingerprint,
            "members": [_member_identity(item) for item in members],
        },
        "accepted_method_model": {
            "id": str(method_model.id),
            "lineage_id": str(method_model.lineage_id),
            "version": method_model.version,
            "composition_fingerprint": method_model.composition_fingerprint,
            "assessment_id": str(method_assessment.id),
            "assessment_outcome": method_assessment.outcome,
        },
        "eligible_members": {
            "knowledge_objects": [_knowledge_identity(item) for item in knowledge],
            "human_judgments": [_judgment_identity(item) for item in judgments],
            "evidence_links": [
                _evidence_identity(item)
                for item in sorted(all_evidence, key=lambda item: str(item.id))
            ],
        },
        "repository": repository_payload,
        "fingerprints": {
            "method_model": method_fp,
            "repository_evidence": repository_fp,
            "accepted_knowledge": knowledge_fp,
            "accepted_mapping": mapping_fp,
            "current_judgment": judgment_fp,
        },
    }
    return _Context(
        project=project,
        question=question,
        model=model,
        members=members,
        method_model=method_model,
        method_assessment=method_assessment,
        mapping_gate=mapping_gate,
        method_event=method_event,
        relationship_id=None if relationship is None else relationship.id,
        snapshot_id=None if snapshot is None else snapshot.id,
        knowledge=knowledge,
        judgments=judgments,
        knowledge_evidence=tuple(knowledge_evidence),
        method_evidence=tuple(method_evidence),
        mapping_ids=tuple(item.id for item in mappings),
        method_fingerprint=method_fp,
        repository_fingerprint=repository_fp,
        knowledge_fingerprint=knowledge_fp,
        mapping_fingerprint=mapping_fp,
        judgment_fingerprint=judgment_fp,
        payload=payload,
        fingerprint=reassessment_fingerprint(payload),
    )


async def _persist(
    *,
    uow: UnitOfWork,
    actor: ActorContext,
    command: ReassessMentalModelCommand,
    context: _Context,
    generation: GenerationRecordData,
    batch: MentalModelReassessmentBatch,
    replayed: bool,
) -> MentalModelReassessmentResponse:
    proposals = _validate_proposals(context, batch)
    proposed_members = tuple(_proposal_data(context.model.id, item) for item in proposals)
    changed = semantic_composition_changed(
        prior_method_model_id=context.model.method_model_id,
        resulting_method_model_id=context.method_model.id,
        prior_members=tuple(_semantic(item) for item in context.members),
        proposed_members=tuple(_semantic(item) for item in proposed_members),
    )
    expected_result = "new_version_created" if changed else "no_change"
    if batch.result != expected_result:
        raise ResourceConflictError(details={"reason": "mental_reassessment_result_invalid"})
    prior_model = context.model
    resulting_model = prior_model
    resulting_members = context.members
    if changed:
        _validate_rendering(batch, proposals)
        await uow.mental_models.mark_superseded(prior_model.id)
        resulting_model = await uow.mental_models.add(
            NewMentalModel(
                id=_id(generation.id, "model"),
                project_id=prior_model.project_id,
                research_question_id=context.question.id,
                method_model_id=context.method_model.id,
                lineage_id=prior_model.lineage_id,
                version=prior_model.version + 1,
                title=prior_model.title,
                status="current",
                confidence=prior_model.confidence,
                composition_fingerprint=composition_fingerprint(
                    research_question_id=context.question.id,
                    method_model_id=context.method_model.id,
                    accepted_input_fingerprint=context.fingerprint,
                    members=proposals,
                ),
                generation_record_id=generation.id,
                derived_from_id=prior_model.id,
                supersedes_id=prior_model.id,
                created_by_type="ai",
                created_by_id=None,
            )
        )
        resulting_members = tuple(
            [
                await uow.mental_models.add_member(
                    _new_member(resulting_model.id, generation.id, index, proposal)
                )
                for index, proposal in enumerate(proposals)
            ]
        )
        await uow.mental_models.add_rendering(
            NewMentalModelRendering(
                id=_id(generation.id, "rendering"),
                mental_model_id=resulting_model.id,
                rendering_type="narrative",
                content="\n\n".join(item.content for item in batch.rendering or []),
                prompt_version=generation.prompt_version,
                provider=generation.provider,
                model_version=generation.model_version,
                generation_record_id=generation.id,
            )
        )
    assessment = await uow.mental_models.add_assessment(
        _new_assessment(
            context=context,
            identifier=_id(generation.id, "assessment"),
            result=expected_result,
            resulting_model_id=resulting_model.id if changed else None,
            rationale=batch.rationale,
            generation_record_id=generation.id,
        )
    )
    if changed:
        await _persist_dependencies(uow, context, resulting_model, assessment, generation.id)
    event = await uow.events.append(
        NewOperationEvent(
            id=_id(generation.id, "event"),
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            operation_type=_SUCCEEDED,
            created_by_type="ai",
            created_by_id=None,
            input_refs=_event_inputs(context),
            output_refs=(
                _model_ref(resulting_model),
                {"type": "mental_model_assessment", "id": str(assessment.id)},
                {
                    "type": "reassessment_result",
                    "id": expected_result,
                    "authority_disclosure": batch.authority_disclosure,
                },
            ),
            reason=batch.rationale,
            idempotency_key=(
                f"mental-reassessment-rerun:{generation.id}"
                if command.explicit_rerun
                else _command_key(command, context.fingerprint)
            ),
            input_fingerprint=context.fingerprint,
            request_id=command.request_id,
        )
    )
    return _response(
        context=context,
        assessment=assessment,
        event=event,
        generation=generation,
        outcome=expected_result,
        resulting_model=resulting_model,
        prior_members=context.members,
        resulting_members=resulting_members,
        replayed=replayed,
        authority_disclosure=batch.authority_disclosure,
    )


def _validate_proposals(
    context: _Context, batch: MentalModelReassessmentBatch
) -> tuple[MentalModelMemberProposal, ...]:
    knowledge = {item.id: item for item in context.knowledge}
    judgments = {item.id: item for item in context.judgments}
    all_evidence: tuple[Any, ...] = (*context.knowledge_evidence, *context.method_evidence)
    evidence = {item.id: item for item in all_evidence}
    proposals: list[MentalModelMemberProposal] = []
    cognitive = 0
    for item in batch.members:
        proposal = MentalModelMemberProposal(
            target_type=item.target_type,
            target_id=item.target_id,
            role=item.role,
            inclusion_rationale=item.inclusion_rationale,
            confidence=item.confidence,
        )
        if item.target_type == "knowledge_object":
            target = knowledge.get(item.target_id)
            if target is None:
                raise DomainValidationError(details={"reason": "mental_model_member_unknown"})
            validate_member_proposal(
                proposal, knowledge_type=KnowledgeObjectType(target.object_type)
            )
            cognitive += 1
        elif item.target_type == "human_judgment":
            if item.target_id not in judgments:
                raise DomainValidationError(details={"reason": "mental_model_member_unknown"})
            validate_member_proposal(proposal)
            cognitive += 1
        else:
            link = evidence.get(item.target_id)
            if link is None:
                raise DomainValidationError(details={"reason": "mental_model_evidence_unknown"})
            validate_member_proposal(proposal)
            if (
                item.role is MentalModelMemberRole.DECISIVE_SUPPORTING_EVIDENCE
                and link.evidence_relation != "supports"
            ) or (
                item.role is MentalModelMemberRole.CENTRAL_CONTRADICTING_EVIDENCE
                and link.evidence_relation != "contradicts"
            ):
                raise DomainValidationError(
                    details={"reason": "mental_model_evidence_relation_mismatch"}
                )
        proposals.append(proposal)
    if cognitive == 0:
        raise DomainValidationError(details={"reason": "mental_model_cognitive_members_empty"})
    return tuple(proposals)


def _validate_rendering(
    batch: MentalModelReassessmentBatch, proposals: tuple[MentalModelMemberProposal, ...]
) -> None:
    expected = {(item.target_type, item.target_id) for item in proposals}
    referenced = {
        (reference.target_type, reference.target_id)
        for paragraph in batch.rendering or []
        for reference in paragraph.member_targets
    }
    if referenced != expected:
        raise DomainValidationError(details={"reason": "mental_model_rendering_members_mismatch"})


def _validate_mapping_gate_coverage(
    gate: OperationEventData,
    mappings: tuple[Any, ...],
    knowledge: tuple[KnowledgeObjectData, ...],
) -> None:
    accepted_mapping_ids = {item.id for item in mappings}
    accepted_knowledge_ids = {item.id for item in knowledge}
    gate_mapping_ids: set[UUID] = set()
    for reference in gate.input_refs:
        family = reference.get("family")
        if family not in {"paper_code_mapping", "implementation_requirement", "ambiguity"}:
            continue
        identifier = UUID(str(reference.get("id")))
        expected_accepted = (
            reference.get("review_status") == "accepted"
            and reference.get("lifecycle_status") == "current"
        )
        if family == "paper_code_mapping":
            if expected_accepted:
                gate_mapping_ids.add(identifier)
            if (identifier in accepted_mapping_ids) != expected_accepted:
                raise ResourceConflictError(details={"reason": "mapping_gate_stale"})
        elif (identifier in accepted_knowledge_ids) != expected_accepted:
            raise ResourceConflictError(details={"reason": "mapping_gate_stale"})
    if gate_mapping_ids != accepted_mapping_ids:
        raise ResourceConflictError(details={"reason": "mapping_gate_stale"})


async def _persist_dependencies(
    uow: UnitOfWork,
    context: _Context,
    model: MentalModelData,
    assessment: MentalModelAssessmentData,
    generation_id: UUID,
) -> None:
    references: list[tuple[str, UUID, str, str]] = [
        (
            "research_question",
            context.question.id,
            DependencyRole.DERIVATION_INPUT.value,
            "blocking",
        ),
        (
            "method_model",
            context.method_model.id,
            DependencyRole.DERIVATION_INPUT.value,
            "blocking",
        ),
        (
            "method_model_assessment",
            context.method_assessment.id,
            DependencyRole.VALIDATION_TARGET.value,
            "blocking",
        ),
        ("mental_model", context.model.id, DependencyRole.DERIVATION_INPUT.value, "material"),
        (
            "mental_model_assessment",
            assessment.id,
            DependencyRole.VALIDATION_TARGET.value,
            "blocking",
        ),
    ]
    if context.relationship_id is not None and context.snapshot_id is not None:
        references.extend(
            (
                (
                    "paper_repository_relationship",
                    context.relationship_id,
                    DependencyRole.PROVENANCE_TRACE.value,
                    "material",
                ),
                (
                    "repository_snapshot",
                    context.snapshot_id,
                    DependencyRole.PROVENANCE_TRACE.value,
                    "material",
                ),
            )
        )
    references.extend(
        ("paper_code_mapping", item, DependencyRole.DERIVATION_INPUT.value, "material")
        for item in context.mapping_ids
    )
    references.extend(
        (
            "knowledge_object",
            item.id,
            DependencyRole.DERIVATION_INPUT.value,
            "material",
        )
        for item in context.knowledge
    )
    references.extend(
        (
            "human_judgment",
            item.id,
            DependencyRole.DERIVATION_INPUT.value,
            "material",
        )
        for item in context.judgments
    )
    accepted_evidence: tuple[Any, ...] = (
        *context.knowledge_evidence,
        *context.method_evidence,
    )
    references.extend(
        (
            "evidence_link",
            item.id,
            DependencyRole.PROVENANCE_TRACE.value,
            "material",
        )
        for item in accepted_evidence
    )
    distinct = tuple(dict.fromkeys(references))
    for index, (kind, identifier, role, criticality) in enumerate(distinct):
        await uow.dependencies.append_dependency(
            ArtifactDependencyData(
                id=_id(generation_id, f"dependency:{index}:{kind}:{identifier}"),
                project_id=model.project_id,
                artifact_type="mental_model",
                artifact_id=model.id,
                dependency_type=kind,
                dependency_id=identifier,
                dependency_role=role,
                criticality=criticality,
            )
        )


async def _replay(uow: UnitOfWork, event: OperationEventData) -> MentalModelReassessmentResponse:
    try:
        assessment_id = _reference_id(event.output_refs, "mental_model_assessment")
        assessment = await uow.mental_models.get_assessment_exact(assessment_id)
        prior_id = _reference_id(event.input_refs, "mental_model")
        resulting_id = _reference_id(event.output_refs, "mental_model")
        prior = await uow.mental_models.get_exact(prior_id)
        resulting = await uow.mental_models.get_exact(resulting_id)
        if assessment is None or prior is None or resulting is None:
            raise ValueError("replay_target_missing")
        context = await _replay_context(uow, event, prior)
        generation = (
            None
            if assessment.generation_record_id is None
            else await uow.generations.get_exact(assessment.generation_record_id)
        )
        if assessment.generation_record_id is not None and generation is None:
            raise ValueError("generation_missing")
        result_ref = next(
            (item for item in event.output_refs if item.get("type") == "reassessment_result"),
            {},
        )
        prior_members = await uow.mental_models.list_members(prior.id)
        resulting_members = await uow.mental_models.list_members(resulting.id)
        outcome = "not_applicable" if event.operation_type == _NOT_APPLICABLE else assessment.result
        authority = result_ref.get("authority_disclosure")
    except (ValueError, StopIteration, KeyError) as error:
        raise InternalError(details={"reason": "mental_reassessment_replay_invalid"}) from error
    return _response(
        context=context,
        assessment=assessment,
        event=event,
        generation=generation,
        outcome=outcome,
        resulting_model=resulting,
        prior_members=prior_members,
        resulting_members=resulting_members,
        replayed=True,
        authority_disclosure=authority if isinstance(authority, str) else None,
    )


async def _replay_context(
    uow: UnitOfWork, event: OperationEventData, prior: MentalModelData
) -> _Context:
    project = await uow.projects.get()
    question = await uow.research_questions.get_exact(
        _reference_id(event.input_refs, "research_question")
    )
    method_id = _reference_id(event.input_refs, "method_model")
    method = await uow.method_models.get_model_exact(method_id)
    method_assessment_id = _reference_id(event.input_refs, "method_model_assessment")
    method_assessment = await uow.method_models.get_assessment_exact(method_assessment_id)
    mapping_gate = await uow.events.get_exact(
        _reference_id(event.input_refs, "mapping_review_gate")
    )
    method_event = await uow.events.get_exact(
        _reference_id(event.input_refs, "method_model_reassessment")
    )
    if project is None or question is None or method is None or method_assessment is None:
        raise ValueError("replay_context_missing")
    if mapping_gate is None or method_event is None:
        raise ValueError("replay_context_missing")
    return _Context(
        project=project,
        question=question,
        model=prior,
        members=await uow.mental_models.list_members(prior.id),
        method_model=method,
        method_assessment=method_assessment,
        mapping_gate=mapping_gate,
        method_event=method_event,
        relationship_id=_optional_reference_id(event.input_refs, "paper_repository_relationship"),
        snapshot_id=_optional_reference_id(event.input_refs, "repository_snapshot"),
        knowledge=(),
        judgments=(),
        knowledge_evidence=(),
        method_evidence=(),
        mapping_ids=(),
        method_fingerprint="",
        repository_fingerprint="",
        knowledge_fingerprint="",
        mapping_fingerprint="",
        judgment_fingerprint="",
        payload={},
        fingerprint=event.input_fingerprint or "",
    )


async def _coverage_fingerprints(
    uow: UnitOfWork,
    project: ProjectData,
    model: MentalModelData,
    assessment: MentalModelAssessmentData,
) -> bool:
    return await mental_model_assessment_covers_current_state(uow, project, model, assessment)


async def mental_model_assessment_covers_current_state(
    uow: UnitOfWork,
    project: ProjectData,
    model: MentalModelData,
    assessment: MentalModelAssessmentData,
) -> bool:
    """Recompute the complete  coverage identity for downstream gates."""
    question = await uow.research_questions.get_current_primary()
    method = await uow.method_models.get_accepted_current_model(model.research_question_id)
    if question is None or method is None or method.current_assessment_id is None:
        return False
    method_assessment = await uow.method_models.get_assessment_exact(method.current_assessment_id)
    if method_assessment is None:
        return False
    knowledge = await uow.knowledge.list_accepted_current()
    evidence_values: list[EvidenceLinkData] = []
    for item in knowledge:
        evidence_values.extend(
            link
            for link in await uow.knowledge.list_evidence_links(item.id)
            if link.review_status == "accepted" and link.lifecycle_status == "current"
        )
    evidence = tuple(evidence_values)
    mappings = (
        await uow.mappings.list_accepted_current()
        if assessment.repository_snapshot_id is not None
        else ()
    )
    judgments = await uow.judgments.list_current()
    relationship = (
        await uow.repositories.get_accepted_current_relationship()
        if assessment.repository_snapshot_id is not None
        else None
    )
    snapshot = (
        None
        if assessment.repository_snapshot_id is None
        else await uow.repositories.get_snapshot_exact(assessment.repository_snapshot_id)
    )
    applies_to_model = (
        assessment.assessed_model_id == model.id
        if assessment.result == "no_change"
        else assessment.resulting_model_id == model.id
    )
    return bool(
        applies_to_model
        and assessment.research_question_id == question.id == model.research_question_id
        and assessment.method_model_id == method.id == model.method_model_id
        and assessment.method_model_assessment_id == method_assessment.id
        and assessment.method_model_fingerprint
        == _method_fingerprint(project, question, method, method_assessment)
        and assessment.repository_evidence_fingerprint
        == _repository_fingerprint(relationship, snapshot)
        and assessment.accepted_knowledge_fingerprint == _knowledge_fingerprint(knowledge, evidence)
        and assessment.accepted_mapping_fingerprint == _mapping_fingerprint(mappings)
        and assessment.current_judgment_fingerprint == _judgment_fingerprint(judgments)
    )


def _new_assessment(
    *,
    context: _Context,
    identifier: UUID,
    result: str,
    resulting_model_id: UUID | None,
    rationale: str,
    generation_record_id: UUID | None,
) -> NewMentalModelAssessment:
    return NewMentalModelAssessment(
        id=identifier,
        project_id=context.project.id,
        assessed_model_id=context.model.id,
        repository_snapshot_id=context.snapshot_id,
        research_question_id=context.question.id,
        method_model_id=context.method_model.id,
        method_model_assessment_id=context.method_assessment.id,
        method_model_fingerprint=context.method_fingerprint,
        repository_evidence_fingerprint=context.repository_fingerprint,
        accepted_knowledge_fingerprint=context.knowledge_fingerprint,
        accepted_mapping_fingerprint=context.mapping_fingerprint,
        current_judgment_fingerprint=context.judgment_fingerprint,
        result=result,
        resulting_model_id=resulting_model_id,
        rationale=rationale,
        generation_record_id=generation_record_id,
    )


def _response(
    *,
    context: _Context,
    assessment: MentalModelAssessmentData,
    event: OperationEventData,
    generation: GenerationRecordData | None,
    outcome: str,
    resulting_model: MentalModelData,
    prior_members: tuple[MentalModelMemberData, ...],
    resulting_members: tuple[MentalModelMemberData, ...],
    replayed: bool,
    authority_disclosure: str | None,
) -> MentalModelReassessmentResponse:
    before = {_target(item): _semantic(item) for item in prior_members}
    after = {_target(item): _semantic(item) for item in resulting_members}
    changed = sorted(
        (
            target
            for target in before.keys() | after.keys()
            if before.get(target) != after.get(target)
        ),
        key=lambda item: (item[0], str(item[1])),
    )
    unchanged = sorted(
        (target for target in before.keys() & after.keys() if before[target] == after[target]),
        key=lambda item: (item[0], str(item[1])),
    )
    return MentalModelReassessmentResponse(
        outcome=outcome,  # type: ignore[arg-type]
        generation_record_id=None if generation is None else generation.id,
        assessment=_assessment_response(assessment),
        prior_mental_model_id=context.model.id,
        resulting_mental_model_id=resulting_model.id,
        mapping_review_gate_event_id=context.mapping_gate.id,
        method_model_reassessment_event_id=context.method_event.id,
        event_id=event.id,
        replayed=replayed,
        changed_members=[
            MentalModelMemberChange(target_type=item[0], target_id=item[1]) for item in changed
        ],
        unchanged_members=[
            MentalModelMemberChange(target_type=item[0], target_id=item[1]) for item in unchanged
        ],
        rationale=assessment.rationale,
        authority_disclosure=authority_disclosure,
    )


def _assessment_response(item: MentalModelAssessmentData) -> MentalModelAssessmentRecordResponse:
    return MentalModelAssessmentRecordResponse.model_validate(item, from_attributes=True)


def _proposal_data(model_id: UUID, item: MentalModelMemberProposal) -> MentalModelMemberData:
    return MentalModelMemberData(
        id=_id(item.target_id, "comparison-member"),
        mental_model_id=model_id,
        knowledge_object_id=item.target_id if item.target_type == "knowledge_object" else None,
        human_judgment_id=item.target_id if item.target_type == "human_judgment" else None,
        evidence_link_id=item.target_id if item.target_type == "evidence_link" else None,
        role=item.role.value,
        inclusion_rationale=item.inclusion_rationale,
        confidence=item.confidence,
    )


def _new_member(
    model_id: UUID, generation_id: UUID, index: int, item: MentalModelMemberProposal
) -> NewMentalModelMember:
    data = _proposal_data(model_id, item)
    return NewMentalModelMember(
        id=_id(generation_id, f"member:{index}:{item.target_type}:{item.target_id}"),
        mental_model_id=model_id,
        knowledge_object_id=data.knowledge_object_id,
        human_judgment_id=data.human_judgment_id,
        evidence_link_id=data.evidence_link_id,
        role=data.role,
        inclusion_rationale=data.inclusion_rationale,
        confidence=data.confidence,
    )


def _semantic(item: MentalModelMemberData) -> MentalModelMemberSemantic:
    target_type, target_id = _target(item)
    return MentalModelMemberSemantic(
        target_type=target_type,
        target_id=target_id,
        role=item.role,
        inclusion_rationale=item.inclusion_rationale,
        confidence=item.confidence,
    )


def _target(item: MentalModelMemberData) -> tuple[_TargetType, UUID]:
    if item.knowledge_object_id is not None:
        return "knowledge_object", item.knowledge_object_id
    if item.human_judgment_id is not None:
        return "human_judgment", item.human_judgment_id
    if item.evidence_link_id is not None:
        return "evidence_link", item.evidence_link_id
    raise InternalError(details={"reason": "mental_model_member_target_missing"})


def _event_inputs(context: _Context) -> tuple[dict[str, object], ...]:
    values: list[dict[str, object]] = [
        _model_ref(context.model),
        {"type": "research_question", "id": str(context.question.id)},
        {"type": "method_model", "id": str(context.method_model.id)},
        {"type": "method_model_assessment", "id": str(context.method_assessment.id)},
        {"type": "mapping_review_gate", "id": str(context.mapping_gate.id)},
        {"type": "method_model_reassessment", "id": str(context.method_event.id)},
    ]
    if context.relationship_id is not None and context.snapshot_id is not None:
        values.extend(
            (
                {"type": "paper_repository_relationship", "id": str(context.relationship_id)},
                {"type": "repository_snapshot", "id": str(context.snapshot_id)},
            )
        )
    return tuple(values)


def _model_ref(model: MentalModelData) -> dict[str, object]:
    return {
        "type": "mental_model",
        "id": str(model.id),
        "lineage_id": str(model.lineage_id),
        "version": model.version,
        "composition_fingerprint": model.composition_fingerprint,
    }


def _reference_id(references: tuple[dict[str, object], ...], kind: str) -> UUID:
    identifier = _optional_reference_id(references, kind)
    if identifier is None:
        raise ResourceConflictError(details={"reason": "reassessment_reference_missing"})
    return identifier


def _optional_reference_id(references: tuple[dict[str, object], ...], kind: str) -> UUID | None:
    matches = [item for item in references if item.get("type") == kind]
    if not matches:
        return None
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise ResourceConflictError(details={"reason": "reassessment_reference_invalid"})
    return UUID(str(matches[0]["id"]))


def _command_key(command: ReassessMentalModelCommand, fingerprint: str) -> str:
    return (
        f"mental-reassessment:{command.workflow_run_id}:"
        f"{command.mapping_review_gate_event_id}:{command.method_model_reassessment_event_id}:"
        f"{fingerprint}"
    )


def _id(parent: UUID, value: str) -> UUID:
    return uuid5(_NAMESPACE, f"{parent}:{value}")


def _require_owned(actor: ActorContext, project: ProjectData | None) -> ProjectData:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
    return project


def _method_fingerprint(
    project: ProjectData,
    question: ResearchQuestionData,
    model: MethodModelData,
    assessment: MethodModelAssessmentData,
) -> str:
    return reassessment_fingerprint(
        {
            "project_objective": [
                project.target_outcome,
                project.description,
                project.reproduction_depth,
            ],
            "question": [
                question.id,
                question.version,
                question.question_text,
                question.scope_text,
            ],
            "method_model": [
                model.id,
                model.lineage_id,
                model.version,
                model.composition_fingerprint,
            ],
            "assessment": [
                assessment.id,
                assessment.outcome,
                assessment.unsupported_required_count,
                assessment.mixed_evidence_count,
                assessment.contradicted_count,
                assessment.missing_provenance_count,
                assessment.unresolved_scientific_ambiguity_count,
                assessment.rationale,
            ],
        }
    )


def _repository_fingerprint(relationship: Any | None, snapshot: Any | None) -> str:
    if relationship is None and snapshot is None:
        return _EMPTY_FINGERPRINT
    if relationship is None or snapshot is None:
        raise ResourceConflictError(details={"reason": "repository_context_incomplete"})
    return reassessment_fingerprint(
        {
            "relationship": [
                relationship.id,
                relationship.version,
                relationship.relationship_type,
                relationship.authority_level,
                relationship.lifecycle_status,
            ],
            "snapshot": [snapshot.id, snapshot.commit_sha, snapshot.lifecycle_status],
        }
    )


def _knowledge_fingerprint(
    knowledge: tuple[KnowledgeObjectData, ...], evidence: tuple[EvidenceLinkData, ...]
) -> str:
    return reassessment_fingerprint(
        {
            "knowledge": [_knowledge_identity(item) for item in knowledge],
            "accepted_evidence": [
                _evidence_identity(item) for item in sorted(evidence, key=lambda x: str(x.id))
            ],
        }
    )


def _mapping_fingerprint(mappings: tuple[Any, ...]) -> str:
    return reassessment_fingerprint([_mapping_identity(item) for item in mappings])


def _judgment_fingerprint(judgments: tuple[HumanJudgmentData, ...]) -> str:
    return reassessment_fingerprint([_judgment_identity(item) for item in judgments])


def _member_identity(item: MentalModelMemberData) -> dict[str, object]:
    target_type, target_id = _target(item)
    return {
        "target_type": target_type,
        "target_id": str(target_id),
        "role": item.role,
        "inclusion_rationale": item.inclusion_rationale,
        "confidence": item.confidence,
    }


def _knowledge_identity(item: KnowledgeObjectData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "object_type": item.object_type,
        "statement": item.statement,
        "origin": item.origin,
        "epistemic_status": item.epistemic_status,
        "grounding_status": item.grounding_status,
    }


def _judgment_identity(item: HumanJudgmentData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "judgment_request_id": str(item.judgment_request_id),
        "selected_option_id": None
        if item.selected_option_id is None
        else str(item.selected_option_id),
        "decision": item.custom_decision,
        "rationale": item.rationale,
        "confidence": item.confidence,
        "lifecycle_status": item.lifecycle_status,
    }


def _evidence_identity(item: Any) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "source_chunk_id": None if item.source_chunk_id is None else str(item.source_chunk_id),
        "repository_component_id": None
        if item.repository_component_id is None
        else str(item.repository_component_id),
        "relation": item.evidence_relation,
        "excerpt": item.evidence_excerpt,
    }


def _mapping_identity(item: Any) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "mapping_type": item.mapping_type,
        "explanation": item.explanation,
        "confidence": item.confidence,
        "repository_snapshot_id": str(item.repository_snapshot_id),
    }
