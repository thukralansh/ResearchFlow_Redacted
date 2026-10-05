"""Accepted-only structured Mental Model composition and rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from researchflow.api.schemas.mental_models import (
    MentalModelCompositionResult,
    MentalModelRenderingResult,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationOutcome,
)
from researchflow.application.review_gates import (
    PRIMARY_SOURCE_REVIEW_EVENT,
    PrimarySourceReviewGateService,
    PrimarySourceReviewGateView,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprint_catalog import DependencyCriticality, DependencyRole
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeObjectType,
    KnowledgeReviewStatus,
)
from researchflow.domain.mental_model_roles import MENTAL_MODEL_ROLE_POLICY_VERSION
from researchflow.domain.mental_models import (
    MENTAL_MODEL_COMPOSITION_POLICY_VERSION,
    MentalModelMemberProposal,
    composition_fingerprint,
    validate_member_proposal,
)
from researchflow.domain.method_catalog import FreshnessStatus
from researchflow.domain.model_catalog import MentalModelMemberRole
from researchflow.errors import (
    DomainValidationError,
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
    MentalModelData,
    MentalModelMemberData,
    MentalModelRenderingData,
    MethodEvidenceLinkData,
    MethodModelAssessmentData,
    MethodModelData,
    MethodModelMemberData,
    NewMentalModel,
    NewMentalModelMember,
    NewMentalModelRendering,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
    ResearchQuestionData,
    ResearchQuestionSourceData,
    SourceProcessingRunData,
    WorkflowRunData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

MENTAL_MODEL_COMPOSE_TASK: Final = "compose_mental_model"
MENTAL_MODEL_RENDER_TASK: Final = "render_mental_model"
MENTAL_MODEL_PROMPT_VERSION: Final = "v1"
MENTAL_MODEL_COMPOSITION_SCHEMA: Final = "mental_model_composition"
MENTAL_MODEL_RENDERING_SCHEMA: Final = "mental_model_rendering"
MENTAL_MODEL_SCHEMA_VERSION: Final = "v1"
MENTAL_MODEL_RENDERING_POLICY_VERSION: Final = "rf-mental-model-rendering-v1"
MENTAL_MODEL_COMPOSE_OPERATION: Final = "mental_model_composed"
MENTAL_MODEL_RENDER_OPERATION: Final = "mental_model_rendered"
_IDENTITY_NAMESPACE: Final = uuid5(NAMESPACE_URL, "https://researchflow.local/mental-model")


@dataclass(frozen=True, slots=True)
class ComposeInitialMentalModelCommand:
    project_id: UUID
    workflow_run_id: UUID
    method_model_id: UUID
    model_policy: StructuredGenerationModelPolicy
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshMentalModelRenderingCommand:
    project_id: UUID
    workflow_run_id: UUID
    mental_model_id: UUID
    model_policy: StructuredGenerationModelPolicy
    request_id: str | None = None
    explicit_rerun: bool = True


@dataclass(frozen=True, slots=True)
class MentalModelCompositionView:
    model: MentalModelData
    members: tuple[MentalModelMemberData, ...]
    rendering: MentalModelRenderingData
    dependencies: tuple[ArtifactDependencyData, ...]
    event: OperationEventData
    replayed: bool


@dataclass(frozen=True, slots=True)
class MentalModelRenderingView:
    model: MentalModelData
    rendering: MentalModelRenderingData
    event: OperationEventData
    replayed: bool


class MentalModelGenerationService(Protocol):
    async def generate_structured[OutputT: BaseModel](
        self, command: GenerateStructuredCommand[OutputT]
    ) -> StructuredGenerationOutcome[OutputT]: ...


class MentalModelReviewGate(Protocol):
    async def status(
        self, project_id: UUID, method_model_id: UUID
    ) -> PrimarySourceReviewGateView: ...


@dataclass(frozen=True, slots=True)
class _AcceptedInputs:
    project: ProjectData
    question: ResearchQuestionData
    workflow: WorkflowRunData
    primary_paper: ResearchQuestionSourceData
    processing_run: SourceProcessingRunData
    review_event: OperationEventData
    method_model: MethodModelData
    method_assessment: MethodModelAssessmentData
    method_members: tuple[MethodModelMemberData, ...]
    knowledge: tuple[KnowledgeObjectData, ...]
    judgments: tuple[HumanJudgmentData, ...]
    knowledge_evidence: dict[UUID, tuple[EvidenceLinkData, ...]]
    method_evidence: tuple[MethodEvidenceLinkData, ...]
    payload: dict[str, object]
    fingerprint: str


class MentalModelService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: MentalModelGenerationService,
        review_gate_service: MentalModelReviewGate | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service
        self._review_gate = review_gate_service or PrimarySourceReviewGateService(
            actor=actor, uow_factory=uow_factory
        )

    async def compose_initial(
        self, command: ComposeInitialMentalModelCommand
    ) -> MentalModelCompositionView:
        inputs = await self._load_inputs(command, lock=False)
        composition_outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=MENTAL_MODEL_COMPOSE_TASK,
                input_fingerprint=inputs.fingerprint,
                prompt_version=MENTAL_MODEL_PROMPT_VERSION,
                schema_name=MENTAL_MODEL_COMPOSITION_SCHEMA,
                schema_version=MENTAL_MODEL_SCHEMA_VERSION,
                output_type=MentalModelCompositionResult,
                messages=(_json_message(inputs.payload),),
                model_policy=command.model_policy,
            )
        )
        proposals = self._validate_composition(inputs, composition_outcome.value)
        model_id = uuid5(_IDENTITY_NAMESPACE, f"model:{composition_outcome.generation_record.id}")
        member_ids = {
            (item.target_type, item.target_id): uuid5(
                _IDENTITY_NAMESPACE,
                f"member:{model_id}:{item.target_type}:{item.target_id}",
            )
            for item in proposals
        }
        structured_fingerprint = composition_fingerprint(
            research_question_id=inputs.question.id,
            method_model_id=inputs.method_model.id,
            accepted_input_fingerprint=inputs.fingerprint,
            members=proposals,
        )
        rendering_payload = _rendering_payload(
            inputs=inputs,
            composition=composition_outcome.value,
            model_id=model_id,
            member_ids=member_ids,
            structured_fingerprint=structured_fingerprint,
        )
        rendering_input_fingerprint = _fingerprint(rendering_payload)
        rendering_outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=MENTAL_MODEL_RENDER_TASK,
                input_fingerprint=rendering_input_fingerprint,
                prompt_version=MENTAL_MODEL_PROMPT_VERSION,
                schema_name=MENTAL_MODEL_RENDERING_SCHEMA,
                schema_version=MENTAL_MODEL_SCHEMA_VERSION,
                output_type=MentalModelRenderingResult,
                messages=(_json_message(rendering_payload),),
                model_policy=command.model_policy,
            )
        )
        content = _validate_rendering(rendering_outcome.value, frozenset(member_ids.values()))
        return await self._persist_initial(
            command=command,
            original_inputs=inputs,
            composition_record=composition_outcome.generation_record,
            rendering_record=rendering_outcome.generation_record,
            composition=composition_outcome.value,
            proposals=proposals,
            model_id=model_id,
            member_ids=member_ids,
            structured_fingerprint=structured_fingerprint,
            rendering_content=content,
        )

    async def refresh_rendering(
        self, command: RefreshMentalModelRenderingCommand
    ) -> MentalModelRenderingView:
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            model = await uow.mental_models.get_exact(command.mental_model_id)
            if model is None:
                raise ResourceNotFoundError(details={"resource": "mental_model"})
            if model.status != "current":
                raise ResourceConflictError(details={"reason": "mental_model_not_current"})
            members = await uow.mental_models.list_members(model.id)
            payload = await self._existing_rendering_payload(uow, model, members)
            await uow.rollback()
        fingerprint = _fingerprint(payload)
        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=MENTAL_MODEL_RENDER_TASK,
                input_fingerprint=fingerprint,
                prompt_version=MENTAL_MODEL_PROMPT_VERSION,
                schema_name=MENTAL_MODEL_RENDERING_SCHEMA,
                schema_version=MENTAL_MODEL_SCHEMA_VERSION,
                output_type=MentalModelRenderingResult,
                messages=(_json_message(payload),),
                model_policy=command.model_policy,
                explicit_rerun=command.explicit_rerun,
            )
        )
        content = _validate_rendering(outcome.value, frozenset(item.id for item in members))
        event_key = _fingerprint(
            {
                "generation_record_id": str(outcome.generation_record.id),
                "mental_model_id": str(model.id),
                "policy": MENTAL_MODEL_RENDERING_POLICY_VERSION,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            locked = await uow.mental_models.get_exact_for_update(model.id)
            if locked is None or locked.status != "current":
                raise ResourceConflictError(details={"reason": "mental_model_changed"})
            current_members = await uow.mental_models.list_members(model.id)
            current_payload = await self._existing_rendering_payload(uow, locked, current_members)
            if _fingerprint(current_payload) != fingerprint:
                raise ResourceConflictError(details={"reason": "mental_model_changed"})
            existing = await uow.events.get_by_idempotency_key(
                MENTAL_MODEL_RENDER_OPERATION, event_key
            )
            if existing is not None:
                renderings = await uow.mental_models.list_renderings(model.id)
                rendering = _rendering_from_event(existing, renderings)
                await uow.rollback()
                return MentalModelRenderingView(locked, rendering, existing, True)
            rendering = await uow.mental_models.add_rendering(
                _new_rendering(model.id, outcome.generation_record, content)
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_IDENTITY_NAMESPACE, f"render-event:{outcome.generation_record.id}"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=MENTAL_MODEL_RENDER_OPERATION,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=({"type": "mental_model", "id": str(model.id)},),
                    output_refs=({"type": "mental_model_rendering", "id": str(rendering.id)},),
                    reason="Refreshed a noncanonical rendering without changing model identity.",
                    idempotency_key=event_key,
                    input_fingerprint=event_key,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return MentalModelRenderingView(locked, rendering, event, False)

    async def _persist_initial(
        self,
        *,
        command: ComposeInitialMentalModelCommand,
        original_inputs: _AcceptedInputs,
        composition_record: GenerationRecordData,
        rendering_record: GenerationRecordData,
        composition: MentalModelCompositionResult,
        proposals: tuple[MentalModelMemberProposal, ...],
        model_id: UUID,
        member_ids: dict[tuple[str, UUID], UUID],
        structured_fingerprint: str,
        rendering_content: str,
    ) -> MentalModelCompositionView:
        event_key = _fingerprint(
            {
                "composition_generation_record_id": str(composition_record.id),
                "persistence_policy": MENTAL_MODEL_COMPOSITION_POLICY_VERSION,
                "rendering_generation_record_id": str(rendering_record.id),
            }
        )
        await self._require_current_review_gate(command)
        async with self._uow_factory(command.project_id) as uow:
            locked_inputs = await self._snapshot(uow, command, lock=True)
            if locked_inputs.fingerprint != original_inputs.fingerprint:
                raise ResourceConflictError(details={"reason": "mental_model_inputs_changed"})
            existing_event = await uow.events.get_by_idempotency_key(
                MENTAL_MODEL_COMPOSE_OPERATION, event_key
            )
            if existing_event is not None:
                model = await uow.mental_models.get_by_generation_record(composition_record.id)
                if model is None:
                    raise ResourceConflictError(details={"reason": "mental_model_replay_corrupt"})
                members = await uow.mental_models.list_members(model.id)
                renderings = await uow.mental_models.list_renderings(model.id)
                rendering = _rendering_from_event(existing_event, renderings)
                dependencies = await uow.dependencies.list_dependencies("mental_model", model.id)
                await uow.rollback()
                return MentalModelCompositionView(
                    model, members, rendering, dependencies, existing_event, True
                )
            current = await uow.mental_models.get_current_for_update()
            if current is not None:
                raise ResourceConflictError(details={"reason": "initial_mental_model_exists"})
            model = await uow.mental_models.add(
                NewMentalModel(
                    id=model_id,
                    project_id=command.project_id,
                    research_question_id=locked_inputs.question.id,
                    method_model_id=locked_inputs.method_model.id,
                    lineage_id=model_id,
                    version=1,
                    title=composition.title,
                    status="current",
                    confidence=composition.confidence,
                    composition_fingerprint=structured_fingerprint,
                    generation_record_id=composition_record.id,
                )
            )
            members = tuple(
                [
                    await uow.mental_models.add_member(
                        _new_member(model.id, member_ids[(item.target_type, item.target_id)], item)
                    )
                    for item in proposals
                ]
            )
            rendering = await uow.mental_models.add_rendering(
                _new_rendering(model.id, rendering_record, rendering_content)
            )
            dependencies = await self._persist_dependencies(uow, model.id, locked_inputs, proposals)
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_IDENTITY_NAMESPACE, f"compose-event:{composition_record.id}"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=MENTAL_MODEL_COMPOSE_OPERATION,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=(
                        {"type": "research_question", "id": str(locked_inputs.question.id)},
                        {"type": "method_model", "id": str(locked_inputs.method_model.id)},
                        {
                            "type": "scientific_review_event",
                            "id": str(locked_inputs.review_event.id),
                        },
                        {"type": "accepted_input_fingerprint", "value": locked_inputs.fingerprint},
                    ),
                    output_refs=(
                        {"type": "mental_model", "id": str(model.id)},
                        {"type": "mental_model_rendering", "id": str(rendering.id)},
                    ),
                    reason="Composed accepted-only project understanding with exact dependencies.",
                    idempotency_key=event_key,
                    input_fingerprint=event_key,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return MentalModelCompositionView(model, members, rendering, dependencies, event, False)

    async def _load_inputs(
        self, command: ComposeInitialMentalModelCommand, *, lock: bool
    ) -> _AcceptedInputs:
        await self._require_current_review_gate(command)
        async with self._uow_factory(command.project_id) as uow:
            snapshot = await self._snapshot(uow, command, lock=lock)
            await uow.rollback()
            return snapshot

    async def _require_current_review_gate(self, command: ComposeInitialMentalModelCommand) -> None:
        gate = await self._review_gate.status(command.project_id, command.method_model_id)
        if not gate.ready or gate.completed_event is None:
            raise ResourceConflictError(details={"reason": "primary_source_review_incomplete"})

    async def _snapshot(
        self,
        uow: UnitOfWork,
        command: ComposeInitialMentalModelCommand,
        *,
        lock: bool,
    ) -> _AcceptedInputs:
        project = self._require_owned(
            await (uow.projects.get_for_update() if lock else uow.projects.get())
        )
        question = await (
            uow.research_questions.get_current_primary_for_update()
            if lock
            else uow.research_questions.get_current_primary()
        )
        if question is None or question.id != project.primary_research_question_id:
            raise ResourceConflictError(details={"reason": "primary_question_not_current"})
        workflow = await (
            uow.workflows.get_for_update(command.workflow_run_id)
            if lock
            else uow.workflows.get_exact(command.workflow_run_id)
        )
        if workflow is None or workflow.primary_research_question_id != question.id:
            raise ResourceConflictError(details={"reason": "workflow_question_mismatch"})
        paper = await (
            uow.sources.get_current_primary_paper_for_update()
            if lock
            else uow.sources.get_current_primary_paper()
        )
        if (
            paper is None
            or paper.id != workflow.primary_question_source_relationship_id
            or paper.research_question_id != question.id
            or paper.review_status != KnowledgeReviewStatus.ACCEPTED.value
            or paper.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
        ):
            raise ResourceConflictError(details={"reason": "accepted_primary_paper_missing"})
        if workflow.primary_source_processing_run_id is None:
            raise ResourceConflictError(details={"reason": "source_processing_missing"})
        processing_run = await uow.sources.get_processing_run_exact(
            workflow.primary_source_processing_run_id
        )
        if (
            processing_run is None
            or processing_run.source_id != paper.source_id
            or processing_run.status != "succeeded"
        ):
            raise ResourceConflictError(details={"reason": "source_processing_not_current"})
        method_model = await (
            uow.method_models.get_accepted_current_model_for_update(question.id)
            if lock
            else uow.method_models.get_accepted_current_model(question.id)
        )
        if method_model is None or method_model.id != command.method_model_id:
            raise ResourceConflictError(details={"reason": "accepted_method_model_missing"})
        if method_model.freshness_status != FreshnessStatus.CURRENT.value:
            raise ResourceConflictError(details={"reason": "method_model_stale"})
        members = await (
            uow.method_models.list_members_for_update(method_model.id)
            if lock
            else uow.method_models.list_members(method_model.id)
        )
        if any(
            item.element.review_status != KnowledgeReviewStatus.ACCEPTED.value
            or item.element.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
            for item in members
        ):
            raise ResourceConflictError(details={"reason": "method_members_not_accepted"})
        if method_model.current_assessment_id is None:
            raise ResourceConflictError(details={"reason": "method_assessment_missing"})
        assessment = await uow.method_models.get_assessment_exact(
            method_model.current_assessment_id
        )
        if assessment is None or assessment.outcome == "insufficient":
            raise ResourceConflictError(details={"reason": "method_assessment_insufficient"})
        review_events = await uow.events.list_for_workflow(
            workflow.id, operation_type=PRIMARY_SOURCE_REVIEW_EVENT
        )
        review_event = next(
            (
                event
                for event in reversed(review_events)
                if any(
                    ref.get("type") == "accepted_method_model"
                    and ref.get("id") == str(method_model.id)
                    for ref in event.output_refs
                )
            ),
            None,
        )
        if review_event is None:
            raise ResourceConflictError(details={"reason": "primary_source_review_incomplete"})
        knowledge = await uow.knowledge.list_accepted_current()
        judgments = await uow.judgments.list_current()
        evidence: dict[UUID, tuple[EvidenceLinkData, ...]] = {}
        for item in knowledge:
            eligible = tuple(
                link
                for link in await uow.knowledge.list_evidence_links(item.id)
                if link.review_status == KnowledgeReviewStatus.ACCEPTED.value
                and link.lifecycle_status == KnowledgeLifecycleStatus.CURRENT.value
            )
            for link in eligible:
                provenance_exists = (
                    await uow.sources.get_chunk_exact(link.source_chunk_id) is not None
                    if link.source_chunk_id is not None
                    else link.repository_component_id is not None
                    and await uow.repositories.get_component_exact(link.repository_component_id)
                    is not None
                )
                if not provenance_exists:
                    raise ResourceConflictError(details={"reason": "evidence_provenance_missing"})
            evidence[item.id] = eligible
        method_payload = []
        all_method_evidence: list[MethodEvidenceLinkData] = []
        for member in members:
            accepted_evidence = tuple(
                link
                for link in await uow.method_models.list_evidence_links(member.element.id)
                if link.review_status == KnowledgeReviewStatus.ACCEPTED.value
                and link.lifecycle_status == KnowledgeLifecycleStatus.CURRENT.value
            )
            for method_link in accepted_evidence:
                provenance_exists = (
                    await uow.sources.get_chunk_exact(method_link.source_chunk_id) is not None
                    if method_link.source_chunk_id is not None
                    else method_link.repository_component_id is not None
                    and await uow.repositories.get_component_exact(
                        method_link.repository_component_id
                    )
                    is not None
                )
                if not provenance_exists:
                    raise ResourceConflictError(details={"reason": "evidence_provenance_missing"})
            all_method_evidence.extend(accepted_evidence)
            method_payload.append(
                {
                    "element": _method_member_identity(member),
                    "evidence": [_method_evidence_identity(link) for link in accepted_evidence],
                }
            )
        knowledge_payload = []
        for item in knowledge:
            subtype = await uow.knowledge.get_payload(item.id)
            if subtype is None:
                raise ResourceConflictError(details={"reason": "knowledge_subtype_missing"})
            knowledge_payload.append(
                {
                    "knowledge_object": _knowledge_identity(item),
                    "subtype_payload": subtype.subtype_payload,
                    "ambiguity_interpretations": subtype.ambiguity_interpretations,
                    "accepted_evidence": [_evidence_identity(link) for link in evidence[item.id]],
                }
            )
        payload: dict[str, object] = {
            "composition_policy_version": MENTAL_MODEL_COMPOSITION_POLICY_VERSION,
            "member_role_policy_version": MENTAL_MODEL_ROLE_POLICY_VERSION,
            "project": {
                "id": str(project.id),
                "objective": project.target_outcome,
                "research_reproduction_depth": project.reproduction_depth,
            },
            "research_question": {
                "id": str(question.id),
                "version": question.version,
                "question": question.question_text,
                "scope": question.scope_text,
            },
            "primary_paper_relationship": {
                "id": str(paper.id),
                "version": paper.version,
                "source_id": str(paper.source_id),
                "rationale": paper.rationale,
                "relevance_scope": paper.relevance_scope,
            },
            "source_processing_run": {
                "id": str(processing_run.id),
                "source_hash": processing_run.source_hash,
                "parser": [processing_run.parser_name, processing_run.parser_version],
                "chunker": [processing_run.chunker_name, processing_run.chunker_version],
                "normalization_version": processing_run.normalization_version,
            },
            "scientific_review": {
                "event_id": str(review_event.id),
                "fingerprint": review_event.input_fingerprint,
            },
            "method_model": {
                "id": str(method_model.id),
                "lineage_id": str(method_model.lineage_id),
                "version": method_model.version,
                "name": method_model.name,
                "objective": method_model.objective,
                "composition_fingerprint": method_model.composition_fingerprint,
                "assessment_id": str(assessment.id),
                "assessment_outcome": assessment.outcome,
                "members": method_payload,
            },
            "accepted_project_knowledge": knowledge_payload,
            "current_human_judgments": [_judgment_identity(item) for item in judgments],
        }
        return _AcceptedInputs(
            project=project,
            question=question,
            workflow=workflow,
            primary_paper=paper,
            processing_run=processing_run,
            review_event=review_event,
            method_model=method_model,
            method_assessment=assessment,
            method_members=members,
            knowledge=knowledge,
            judgments=judgments,
            knowledge_evidence=evidence,
            method_evidence=tuple(all_method_evidence),
            payload=payload,
            fingerprint=_fingerprint(payload),
        )

    def _validate_composition(
        self, inputs: _AcceptedInputs, result: MentalModelCompositionResult
    ) -> tuple[MentalModelMemberProposal, ...]:
        knowledge = {item.id: item for item in inputs.knowledge}
        judgments = {item.id: item for item in inputs.judgments}
        evidence = {link.id: link for links in inputs.knowledge_evidence.values() for link in links}
        method_evidence = {link.id: link for link in inputs.method_evidence}
        proposals: list[MentalModelMemberProposal] = []
        cognitive = 0
        for item in result.members:
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
                link = evidence.get(item.target_id) or method_evidence.get(item.target_id)
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

    async def _persist_dependencies(
        self,
        uow: UnitOfWork,
        model_id: UUID,
        inputs: _AcceptedInputs,
        proposals: tuple[MentalModelMemberProposal, ...],
    ) -> tuple[ArtifactDependencyData, ...]:
        references: list[tuple[str, UUID, str, str]] = [
            ("research_question", inputs.question.id, "derivation_input", "blocking"),
            (
                "research_question_source",
                inputs.primary_paper.id,
                "provenance_trace",
                "material",
            ),
            (
                "source_processing_run",
                inputs.processing_run.id,
                "provenance_trace",
                "material",
            ),
            ("method_model", inputs.method_model.id, "derivation_input", "blocking"),
            (
                "method_model_assessment",
                inputs.method_assessment.id,
                "validation_target",
                "blocking",
            ),
        ]
        references.extend(
            (
                item.target_type,
                item.target_id,
                "derivation_input" if item.target_type != "evidence_link" else "provenance_trace",
                "material",
            )
            for item in proposals
        )
        persisted: list[ArtifactDependencyData] = []
        for dependency_type, dependency_id, role, criticality in references:
            identity = f"{model_id}:{dependency_type}:{dependency_id}:{role}"
            persisted.append(
                await uow.dependencies.append_dependency(
                    ArtifactDependencyData(
                        id=uuid5(_IDENTITY_NAMESPACE, f"dependency:{identity}"),
                        project_id=inputs.project.id,
                        artifact_type="mental_model",
                        artifact_id=model_id,
                        dependency_type=dependency_type,
                        dependency_id=dependency_id,
                        dependency_role=DependencyRole(role).value,
                        criticality=DependencyCriticality(criticality).value,
                    )
                )
            )
        return tuple(persisted)

    async def _existing_rendering_payload(
        self,
        uow: UnitOfWork,
        model: MentalModelData,
        members: tuple[MentalModelMemberData, ...],
    ) -> dict[str, object]:
        values: list[dict[str, object]] = []
        for member in members:
            content: dict[str, object]
            if member.knowledge_object_id is not None:
                knowledge = await uow.knowledge.get_exact(member.knowledge_object_id)
                if knowledge is None:
                    raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
                content = {"statement": knowledge.statement, "object_type": knowledge.object_type}
            elif member.human_judgment_id is not None:
                judgment = await uow.judgments.get_judgment_exact(member.human_judgment_id)
                if judgment is None:
                    raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
                content = _judgment_identity(judgment)
            else:
                if member.evidence_link_id is None:
                    raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
                evidence = None
                for item in await uow.knowledge.list_accepted_current():
                    evidence = next(
                        (
                            link
                            for link in await uow.knowledge.list_evidence_links(item.id)
                            if link.id == member.evidence_link_id
                        ),
                        None,
                    )
                    if evidence is not None:
                        break
                if evidence is not None:
                    content = _evidence_identity(evidence)
                else:
                    method_evidence = await uow.method_models.get_evidence_link_for_update(
                        member.evidence_link_id
                    )
                    if method_evidence is None:
                        raise ResourceConflictError(
                            details={"reason": "mental_model_member_missing"}
                        )
                    content = _method_evidence_identity(method_evidence)
            values.append(
                {
                    "member_id": str(member.id),
                    "role": member.role,
                    "inclusion_rationale": member.inclusion_rationale,
                    "content": content,
                }
            )
        method = await uow.method_models.get_model_exact(model.method_model_id)
        if method is None:
            raise ResourceConflictError(details={"reason": "mental_model_method_missing"})
        return {
            "rendering_policy_version": MENTAL_MODEL_RENDERING_POLICY_VERSION,
            "mental_model": {
                "id": str(model.id),
                "title": model.title,
                "composition_fingerprint": model.composition_fingerprint,
                "method_model": {
                    "id": str(method.id),
                    "name": method.name,
                    "objective": method.objective,
                },
                "members": values,
            },
        }

    def _require_owned(self, project: ProjectData | None) -> ProjectData:
        if project is None:
            raise ResourceNotFoundError(details={"resource": "project"})
        if project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        return project


def _json_message(payload: dict[str, object]) -> StructuredGenerationMessage:
    return StructuredGenerationMessage(
        role="user", content=json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    )


def _fingerprint(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _knowledge_identity(item: KnowledgeObjectData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "object_type": item.object_type,
        "statement": item.statement,
        "origin": item.origin,
        "epistemic_status": item.epistemic_status,
        "confidence": item.confidence,
        "grounding_status": item.grounding_status,
        "grounding_assessment_id": None
        if item.grounding_assessment_id is None
        else str(item.grounding_assessment_id),
    }


def _evidence_identity(item: EvidenceLinkData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "target_id": str(item.knowledge_object_id),
        "source_chunk_id": str(item.source_chunk_id),
        "relation": item.evidence_relation,
        "excerpt": item.evidence_excerpt,
        "confidence": item.confidence,
    }


def _method_evidence_identity(item: MethodEvidenceLinkData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "target_id": str(item.method_element_id),
        "source_chunk_id": str(item.source_chunk_id),
        "relation": item.evidence_relation,
        "excerpt": item.evidence_excerpt,
        "confidence": item.confidence,
    }


def _method_member_identity(item: MethodModelMemberData) -> dict[str, object]:
    return {
        "member_id": str(item.id),
        "method_element_id": str(item.element.id),
        "lineage_id": str(item.element.lineage_id),
        "version": item.element.version,
        "element_type": item.element.element_type,
        "epistemic_status": item.element.epistemic_status,
        "grounding_status": item.element.grounding_status,
        "review_section": item.review_section,
        "member_role": item.member_role,
        "required": item.required,
        "inclusion_rationale": item.inclusion_rationale,
        "subtype_payload": item.subtype_payload,
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
        "custom_decision": item.custom_decision,
        "rationale": item.rationale,
        "confidence": item.confidence,
    }


def _rendering_payload(
    *,
    inputs: _AcceptedInputs,
    composition: MentalModelCompositionResult,
    model_id: UUID,
    member_ids: dict[tuple[str, UUID], UUID],
    structured_fingerprint: str,
) -> dict[str, object]:
    knowledge = {item.id: item for item in inputs.knowledge}
    judgments = {item.id: item for item in inputs.judgments}
    evidence = {link.id: link for links in inputs.knowledge_evidence.values() for link in links}
    method_evidence = {link.id: link for link in inputs.method_evidence}
    values: list[dict[str, object]] = []
    for item in composition.members:
        if item.target_type == "knowledge_object":
            target: object = _knowledge_identity(knowledge[item.target_id])
        elif item.target_type == "human_judgment":
            target = _judgment_identity(judgments[item.target_id])
        else:
            proposition_link = evidence.get(item.target_id)
            target = (
                _evidence_identity(proposition_link)
                if proposition_link is not None
                else _method_evidence_identity(method_evidence[item.target_id])
            )
        values.append(
            {
                "member_id": str(member_ids[(item.target_type, item.target_id)]),
                "role": item.role.value,
                "inclusion_rationale": item.inclusion_rationale,
                "target": target,
            }
        )
    return {
        "rendering_policy_version": MENTAL_MODEL_RENDERING_POLICY_VERSION,
        "mental_model": {
            "id": str(model_id),
            "title": composition.title,
            "composition_fingerprint": structured_fingerprint,
            "research_question": inputs.question.question_text,
            "project_objective": inputs.project.target_outcome,
            "method_model": {
                "id": str(inputs.method_model.id),
                "name": inputs.method_model.name,
                "objective": inputs.method_model.objective,
            },
            "members": values,
        },
    }


def _validate_rendering(
    result: MentalModelRenderingResult, expected_member_ids: frozenset[UUID]
) -> str:
    referenced = frozenset(
        member_id for paragraph in result.paragraphs for member_id in paragraph.member_ids
    )
    if referenced != expected_member_ids:
        raise DomainValidationError(details={"reason": "mental_model_rendering_members_mismatch"})
    return "\n\n".join(paragraph.content for paragraph in result.paragraphs)


def _new_member(
    mental_model_id: UUID, member_id: UUID, item: MentalModelMemberProposal
) -> NewMentalModelMember:
    return NewMentalModelMember(
        id=member_id,
        mental_model_id=mental_model_id,
        knowledge_object_id=item.target_id if item.target_type == "knowledge_object" else None,
        human_judgment_id=item.target_id if item.target_type == "human_judgment" else None,
        evidence_link_id=item.target_id if item.target_type == "evidence_link" else None,
        role=item.role.value,
        inclusion_rationale=item.inclusion_rationale,
        confidence=item.confidence,
    )


def _new_rendering(
    model_id: UUID, record: GenerationRecordData, content: str
) -> NewMentalModelRendering:
    return NewMentalModelRendering(
        id=uuid5(_IDENTITY_NAMESPACE, f"rendering:{record.id}"),
        mental_model_id=model_id,
        rendering_type="narrative",
        content=content,
        prompt_version=record.prompt_version,
        provider=record.provider,
        model_version=record.model_version,
        generation_record_id=record.id,
    )


def _rendering_from_event(
    event: OperationEventData, renderings: tuple[MentalModelRenderingData, ...]
) -> MentalModelRenderingData:
    rendering_id = next(
        (
            UUID(str(reference["id"]))
            for reference in event.output_refs
            if reference.get("type") == "mental_model_rendering"
        ),
        None,
    )
    rendering = next((item for item in renderings if item.id == rendering_id), None)
    if rendering is None:
        raise ResourceConflictError(details={"reason": "mental_model_replay_corrupt"})
    return rendering
