""" ordered post-mapping MethodModel reassessment."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.api.schemas.method_model_reassessment import (
    MethodModelReassessmentBatch,
    MethodModelReassessmentResponse,
    SectionAuthorityResponse,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.application.method_grounding import (
    InitialMethodMember,
    assess_initial_method_model,
    assess_method_evidence,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.knowledge_catalog import KnowledgeLifecycleStatus, KnowledgeReviewStatus
from researchflow.domain.method_catalog import (
    MethodElementType,
    MethodMemberRole,
    MethodReviewSection,
    MethodSymbolType,
)
from researchflow.domain.method_model_reassessment import (
    METHOD_REASSESSMENT_POLICY_VERSION,
    METHOD_REASSESSMENT_PROMPT_VERSION,
    METHOD_REASSESSMENT_SCHEMA_NAME,
    METHOD_REASSESSMENT_SCHEMA_VERSION,
    METHOD_REASSESSMENT_SECTION_POLICY_VERSION,
    METHOD_REASSESSMENT_TASK_NAME,
    reassessment_fingerprint,
)
from researchflow.domain.method_review import (
    MethodMemberSnapshot,
    member_set_fingerprint,
    method_composition_fingerprint,
)
from researchflow.domain.method_validation import method_element_endpoint_type
from researchflow.errors import InternalError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import (
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    GenerationRecordData,
    MethodElementData,
    MethodModelAssessmentData,
    MethodModelData,
    MethodModelMemberData,
    MethodModelSectionReviewData,
    NewMethodModelAssessment,
    NewMethodModelCandidate,
    NewMethodModelMember,
    NewMethodModelRendering,
    NewMethodModelSectionReview,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/")
_SUCCEEDED = "method_model.reassessment.succeeded"
_NOT_APPLICABLE = "method_model.reassessment.not_applicable"
_JUDGMENT_REASSESSMENT = "method_model.reassessment.human_judgment"


@dataclass(frozen=True, slots=True)
class ReassessMethodModelCommand:
    project_id: UUID
    workflow_run_id: UUID
    mapping_review_gate_event_id: UUID
    applicable: bool
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    request_id: str | None = None
    trigger_human_judgment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _Context:
    project: ProjectData
    question_id: UUID
    model: MethodModelData
    members: tuple[MethodModelMemberData, ...]
    reviews: tuple[MethodModelSectionReviewData, ...]
    eligible_replacements: tuple[MethodElementData, ...]
    gate: OperationEventData
    relationship_id: UUID
    snapshot_id: UUID
    mapping_ids: tuple[UUID, ...]
    knowledge_ids: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]
    grounding_ids: tuple[UUID, ...]
    judgment_ids: tuple[UUID, ...]
    payload: dict[str, object]
    fingerprint: str


class MethodModelReassessmentService:
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
        self, command: ReassessMethodModelCommand
    ) -> MethodModelReassessmentResponse:
        if not command.applicable:
            if command.trigger_human_judgment_id is not None:
                return await self._after_judgment_without_repository(command)
            return await self._not_applicable(command)
        if not command.explicit_rerun:
            async with self._uow_factory(command.project_id) as uow:
                existing = await uow.events.get_by_idempotency_key(
                    _SUCCEEDED, _command_idempotency_key(command)
                )
                if existing is not None:
                    generation_id = _ref_id(existing.input_refs, "generation_record")
                    generation = await uow.generations.get_exact(generation_id)
                    if generation is None:
                        raise InternalError(
                            details={"reason": "method_reassessment_replay_generation_missing"}
                        )
                    response = await _replay(uow, generation, existing)
                    await uow.rollback()
                    return response
        async with self._uow_factory(command.project_id) as uow:
            context = await _load_context(uow, self._actor, command, lock=False)
            await uow.rollback()
        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=METHOD_REASSESSMENT_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=METHOD_REASSESSMENT_PROMPT_VERSION,
                schema_name=METHOD_REASSESSMENT_SCHEMA_NAME,
                schema_version=METHOD_REASSESSMENT_SCHEMA_VERSION,
                output_type=MethodModelReassessmentBatch,
                messages=(
                    StructuredGenerationMessage(
                        role="user",
                        content=json.dumps(context.payload, sort_keys=True, separators=(",", ":")),
                    ),
                ),
                model_policy=command.model_policy,
                explicit_rerun=command.explicit_rerun,
            )
        )
        async with self._uow_factory(command.project_id) as uow:
            replay = await uow.events.get_exact(_id(outcome.generation_record.id, "event"))
            if replay is not None:
                response = await _replay(uow, outcome.generation_record, replay)
                await uow.rollback()
                return response
            current = await _load_context(uow, self._actor, command, lock=True)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "method_reassessment_stale_context"})
            response = await _persist(
                uow=uow,
                actor=self._actor,
                command=command,
                context=current,
                generation=outcome.generation_record,
                proposal=outcome.value,
                replayed=outcome.replayed,
            )
            if response.outcome == "new_version_created" and self._staleness_hook is not None:
                await self._staleness_hook.artifact_changed(
                    uow=uow,
                    project_id=command.project_id,
                    cause_type="method_model",
                    before_id=response.prior_method_model_id,
                    after_id=response.resulting_method_model_id,
                    operation_type="method_model.replaced",
                )
            await uow.commit()
            return response

    async def _after_judgment_without_repository(
        self, command: ReassessMethodModelCommand
    ) -> MethodModelReassessmentResponse:
        judgment_id = command.trigger_human_judgment_id
        if judgment_id is None:
            raise ResourceConflictError(details={"reason": "trigger_human_judgment_required"})
        async with self._uow_factory(command.project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            workflow = await uow.workflows.get_for_update(command.workflow_run_id)
            gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
            question = await uow.research_questions.get_current_primary()
            judgment = await uow.judgments.get_judgment_exact(judgment_id)
            current_judgment = await uow.judgments.get_current_for_request(
                judgment.judgment_request_id if judgment is not None else UUID(int=0)
            )
            if (
                workflow is None
                or gate is None
                or question is None
                or gate.workflow_run_id != command.workflow_run_id
            ):
                raise ResourceNotFoundError(details={"resource": "reassessment_context"})
            if gate.operation_type != "mapping.review_gate.not_applicable":
                raise ResourceConflictError(
                    details={"reason": "mapping_gate_applicability_mismatch"}
                )
            if judgment is None or current_judgment is None or current_judgment.id != judgment.id:
                raise ResourceConflictError(
                    details={"reason": "trigger_human_judgment_not_current"}
                )
            model = await uow.method_models.get_accepted_current_model_for_update(question.id)
            if model is None or model.current_assessment_id is None:
                raise ResourceConflictError(
                    details={"reason": "current_assessed_method_model_required"}
                )
            members = await uow.method_models.list_members_for_update(model.id)
            reviews = await uow.method_models.list_current_section_reviews(
                model.id, for_update=True
            )
            _validate_source_sections(members, reviews)
            key = f"method-reassessment-judgment:{judgment.id}"
            existing = await uow.events.get_by_idempotency_key(_JUDGMENT_REASSESSMENT, key)
            if existing is not None:
                assessment_id = _ref_id(existing.output_refs, "method_model_assessment")
                if (
                    _ref_id(existing.input_refs, "mapping_review_gate") != gate.id
                    or _ref_id(existing.input_refs, "human_judgment") != judgment.id
                    or _ref_id(existing.input_refs, "method_model") != model.id
                    or model.current_assessment_id != assessment_id
                ):
                    raise ResourceConflictError(details={"reason": "judgment_reassessment_stale"})
                await uow.rollback()
                return MethodModelReassessmentResponse(
                    outcome="no_change",
                    generation_record_id=None,
                    assessment_id=assessment_id,
                    prior_method_model_id=model.id,
                    resulting_method_model_id=model.id,
                    mapping_review_gate_event_id=gate.id,
                    event_id=existing.id,
                    replayed=True,
                    changed_element_ids=[],
                    unchanged_element_ids=[item.method_element_id for item in members],
                    section_authority=[],
                    rationale=existing.reason or "Deterministic post-Judgment assessment.",
                    authority_disclosure=None,
                )
            fingerprint = reassessment_fingerprint(
                {
                    "policy": METHOD_REASSESSMENT_POLICY_VERSION,
                    "section_authority": METHOD_REASSESSMENT_SECTION_POLICY_VERSION,
                    "project_id": str(command.project_id),
                    "workflow_run_id": str(command.workflow_run_id),
                    "mapping_gate_id": str(gate.id),
                    "mapping_gate_fingerprint": gate.input_fingerprint,
                    "repository_applicable": False,
                    "method_model_id": str(model.id),
                    "method_model_composition": model.composition_fingerprint,
                    "prior_assessment_id": str(model.current_assessment_id),
                    "human_judgment_id": str(judgment.id),
                    "human_judgment_lineage_id": str(judgment.lineage_id),
                    "human_judgment_version": judgment.version,
                }
            )
            assessment = await _assess_post_judgment(
                uow=uow,
                model=model,
                members=members,
                question_id=question.id,
                gate=gate,
                judgment_id=judgment.id,
                fingerprint=fingerprint,
            )
            await uow.method_models.set_current_assessment(
                method_model_id=model.id, assessment_id=assessment.id
            )
            for index, (kind, identifier) in enumerate(
                (
                    ("method_model_assessment", assessment.id),
                    ("human_judgment", judgment.id),
                )
            ):
                await uow.dependencies.append_dependency(
                    ArtifactDependencyData(
                        id=_id(judgment.id, f"post-judgment-dependency:{index}:{identifier}"),
                        project_id=command.project_id,
                        artifact_type="method_model",
                        artifact_id=model.id,
                        dependency_type=kind,
                        dependency_id=identifier,
                        dependency_role="derivation_input",
                        criticality="blocking" if kind == "method_model_assessment" else "material",
                    )
                )
            rationale = (
                "The repository branch is not applicable. The accepted MethodModel composition "
                "was deterministically reassessed against the exact current Human Judgment; no "
                "unreviewed MethodElement revision or synthetic repository state was introduced."
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=_id(judgment.id, "post-judgment-method-event"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_JUDGMENT_REASSESSMENT,
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=(
                        {"type": "mapping_review_gate", "id": str(gate.id)},
                        {"type": "human_judgment", "id": str(judgment.id)},
                        _model_ref(model),
                    ),
                    output_refs=(
                        _model_ref(model),
                        {"type": "method_model_assessment", "id": str(assessment.id)},
                    ),
                    reason=rationale,
                    idempotency_key=key,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return MethodModelReassessmentResponse(
                outcome="no_change",
                generation_record_id=None,
                assessment_id=assessment.id,
                prior_method_model_id=model.id,
                resulting_method_model_id=model.id,
                mapping_review_gate_event_id=gate.id,
                event_id=event.id,
                replayed=False,
                changed_element_ids=[],
                unchanged_element_ids=[item.method_element_id for item in members],
                section_authority=[],
                rationale=rationale,
                authority_disclosure=None,
            )

    async def _not_applicable(
        self, command: ReassessMethodModelCommand
    ) -> MethodModelReassessmentResponse:
        async with self._uow_factory(command.project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            workflow = await uow.workflows.get_for_update(command.workflow_run_id)
            gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
            if workflow is None or gate is None or gate.workflow_run_id != command.workflow_run_id:
                raise ResourceNotFoundError(details={"resource": "mapping_review_gate"})
            if gate.operation_type != "mapping.review_gate.not_applicable":
                raise ResourceConflictError(
                    details={"reason": "mapping_gate_applicability_mismatch"}
                )
            model = await uow.method_models.get_accepted_current_model_for_update(
                workflow.primary_research_question_id
            )
            if model is None or model.current_assessment_id is None:
                raise ResourceConflictError(
                    details={"reason": "current_assessed_method_model_required"}
                )
            fingerprint = reassessment_fingerprint(
                {
                    "policy": METHOD_REASSESSMENT_POLICY_VERSION,
                    "applicable": False,
                    "project_id": str(command.project_id),
                    "workflow_run_id": str(command.workflow_run_id),
                    "gate_event_id": str(gate.id),
                    "gate_fingerprint": gate.input_fingerprint,
                    "method_model_id": str(model.id),
                    "method_model_assessment_id": str(model.current_assessment_id),
                }
            )
            existing = await uow.events.get_by_idempotency_key(
                _NOT_APPLICABLE, f"method-reassessment-na:{command.workflow_run_id}"
            )
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                await uow.rollback()
                return _not_applicable_response(model, gate, existing, replayed=True)
            event = await uow.events.append(
                NewOperationEvent(
                    id=_id(command.workflow_run_id, "not-applicable-event"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_NOT_APPLICABLE,
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=(
                        {"type": "mapping_review_gate", "id": str(gate.id)},
                        _model_ref(model),
                        {"type": "method_model_assessment", "id": str(model.current_assessment_id)},
                    ),
                    output_refs=(_model_ref(model),),
                    reason="Repository evidence was explicitly not applicable; no synthetic reassessment was created.",
                    idempotency_key=f"method-reassessment-na:{command.workflow_run_id}",
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return _not_applicable_response(model, gate, event, replayed=False)


class MethodModelReassessmentQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def detail(
        self, project_id: UUID, generation_record_id: UUID
    ) -> MethodModelReassessmentResponse:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            generation = await uow.generations.get_exact(generation_record_id)
            event = await uow.events.get_exact(_id(generation_record_id, "event"))
            if generation is None or event is None or event.operation_type != _SUCCEEDED:
                raise ResourceNotFoundError(details={"resource": "method_model_reassessment"})
            response = await _replay(uow, generation, event)
            await uow.rollback()
            return response


async def _load_context(
    uow: UnitOfWork,
    actor: ActorContext,
    command: ReassessMethodModelCommand,
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
    gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
    if workflow is None or gate is None or gate.workflow_run_id != command.workflow_run_id:
        raise ResourceNotFoundError(details={"resource": "mapping_review_gate"})
    if gate.operation_type != "mapping.review_gate.completed":
        raise ResourceConflictError(details={"reason": "mapping_gate_applicability_mismatch"})
    question = await uow.research_questions.get_current_primary()
    if question is None or workflow.primary_research_question_id != question.id:
        raise ResourceConflictError(details={"reason": "method_reassessment_question_stale"})
    model = await (
        uow.method_models.get_accepted_current_model_for_update(question.id)
        if lock
        else uow.method_models.get_accepted_current_model(question.id)
    )
    if model is None or model.current_assessment_id is None:
        raise ResourceConflictError(details={"reason": "current_assessed_method_model_required"})
    members = await (
        uow.method_models.list_members_for_update(model.id)
        if lock
        else uow.method_models.list_members(model.id)
    )
    reviews = await uow.method_models.list_current_section_reviews(model.id, for_update=lock)
    _validate_source_sections(members, reviews)
    accepted_elements = await uow.method_models.list_accepted_current_elements()
    eligible = tuple(
        candidate
        for member in members
        for candidate in accepted_elements
        if candidate.lineage_id == member.element.lineage_id
        and candidate.id != member.element.id
        and candidate.supersedes_id == member.element.id
    )
    for member in members:
        if member.element.review_status != "accepted":
            raise ResourceConflictError(details={"reason": "unreviewed_method_member"})
        if member.element.lifecycle_status == "challenged":
            raise ResourceConflictError(details={"reason": "unresolved_method_challenge"})
        if member.element.lifecycle_status not in {"current", "superseded"}:
            raise ResourceConflictError(
                details={"reason": "method_member_not_current_or_replaceable"}
            )
        if member.element.lifecycle_status == "superseded" and not any(
            item.lineage_id == member.element.lineage_id for item in eligible
        ):
            raise ResourceConflictError(details={"reason": "accepted_replacement_required"})
    relationship = await uow.repositories.get_accepted_current_relationship()
    snapshot = (
        None
        if workflow.repository_snapshot_id is None
        else await uow.repositories.get_snapshot_exact(workflow.repository_snapshot_id)
    )
    if (
        relationship is None
        or snapshot is None
        or relationship.repository_snapshot_id != snapshot.id
    ):
        raise ResourceConflictError(details={"reason": "repository_context_stale"})
    mappings = await uow.mappings.list_accepted_current()
    knowledge = await uow.knowledge.list_accepted_current()
    judgments = await uow.judgments.list_current()
    if command.trigger_human_judgment_id is not None and command.trigger_human_judgment_id not in {
        item.id for item in judgments
    }:
        raise ResourceConflictError(details={"reason": "trigger_human_judgment_not_current"})
    member_payload: list[dict[str, object]] = []
    evidence_ids: list[UUID] = []
    grounding_ids: list[UUID] = []
    for member in members:
        evidence = tuple(
            item
            for item in await uow.method_models.list_evidence_links(member.element.id)
            if item.review_status == "accepted" and item.lifecycle_status == "current"
        )
        evidence_ids.extend(item.id for item in evidence)
        if member.element.grounding_assessment_id is not None:
            grounding_ids.append(member.element.grounding_assessment_id)
        member_payload.append(
            {
                "member": _member_identity(member),
                "evidence": [_evidence_identity(item) for item in evidence],
            }
        )
    payload: dict[str, object] = {
        "policy": {
            "reassessment": METHOD_REASSESSMENT_POLICY_VERSION,
            "section_authority": METHOD_REASSESSMENT_SECTION_POLICY_VERSION,
        },
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
        "mapping_gate": {"id": str(gate.id), "fingerprint": gate.input_fingerprint},
        "method_model": {
            "id": str(model.id),
            "lineage_id": str(model.lineage_id),
            "version": model.version,
            "composition_fingerprint": model.composition_fingerprint,
            "assessment_id": str(model.current_assessment_id),
            "members": member_payload,
            "section_reviews": [_review_identity(item) for item in reviews],
        },
        "eligible_human_accepted_replacements": [
            _element_identity(item) for item in sorted(eligible, key=lambda item: str(item.id))
        ],
        "repository": {
            "relationship_id": str(relationship.id),
            "snapshot_id": str(snapshot.id),
            "commit_sha": snapshot.commit_sha,
            "authority_level": relationship.authority_level,
        },
        "accepted_mappings": [_mapping_identity(item) for item in mappings],
        "accepted_project_knowledge": [_knowledge_identity(item) for item in knowledge],
        "current_judgments": [_judgment_identity(item) for item in judgments],
    }
    return _Context(
        project=project,
        question_id=question.id,
        model=model,
        members=members,
        reviews=reviews,
        eligible_replacements=eligible,
        gate=gate,
        relationship_id=relationship.id,
        snapshot_id=snapshot.id,
        mapping_ids=tuple(item.id for item in mappings),
        knowledge_ids=tuple(item.id for item in knowledge),
        evidence_ids=tuple(sorted(set(evidence_ids), key=str)),
        grounding_ids=tuple(sorted(set(grounding_ids), key=str)),
        judgment_ids=tuple(item.id for item in judgments),
        payload=payload,
        fingerprint=reassessment_fingerprint(payload),
    )


async def _persist(
    *,
    uow: UnitOfWork,
    actor: ActorContext,
    command: ReassessMethodModelCommand,
    context: _Context,
    generation: GenerationRecordData,
    proposal: MethodModelReassessmentBatch,
    replayed: bool,
) -> MethodModelReassessmentResponse:
    required = {
        member.element.id: replacement
        for member in context.members
        if member.element.lifecycle_status == "superseded"
        for replacement in context.eligible_replacements
        if replacement.lineage_id == member.element.lineage_id
    }
    proposed = {
        item.current_element_id: item.replacement_element_id for item in proposal.replacements
    }
    if set(proposed) != set(required) or any(
        required[current].id != replacement for current, replacement in proposed.items()
    ):
        raise ResourceConflictError(details={"reason": "method_reassessment_replacements_invalid"})
    expected_result = "new_version_created" if required else "no_change"
    if proposal.result != expected_result:
        raise ResourceConflictError(details={"reason": "method_reassessment_result_invalid"})

    generation_id = generation.id
    prior_model = context.model
    resulting_model = prior_model
    resulting_members = context.members
    changed_ids: tuple[UUID, ...] = ()
    section_authority: tuple[SectionAuthorityResponse, ...] = ()
    event_id = _id(generation_id, "event")
    model_id = _id(generation_id, "model")
    if required:
        revised: list[MethodModelMemberData] = []
        for member in context.members:
            replacement = required.get(member.element.id)
            if replacement is None:
                if member.element.lifecycle_status != "current":
                    raise ResourceConflictError(details={"reason": "method_member_not_current"})
                revised.append(member)
                continue
            payload = await uow.method_models.get_element_payload_exact(replacement.id)
            if payload is None:
                raise ResourceConflictError(details={"reason": "method_element_payload_missing"})
            revised.append(
                replace(
                    member,
                    method_element_id=replacement.id,
                    element=replacement,
                    subtype_payload=payload,
                )
            )
        resulting_members = tuple(revised)
        latest = await uow.method_models.latest_model_lineage_for_update(prior_model.lineage_id)
        if latest is None:
            raise ResourceConflictError(details={"reason": "method_model_revision_not_latest"})
        if latest.id == prior_model.id:
            resulting_model = await uow.method_models.add_candidate_model(
                NewMethodModelCandidate(
                    id=model_id,
                    project_id=command.project_id,
                    research_question_id=context.question_id,
                    lineage_id=prior_model.lineage_id,
                    version=prior_model.version + 1,
                    name=prior_model.name,
                    objective=prior_model.objective,
                    confidence=None,
                    composition_fingerprint=method_composition_fingerprint(
                        tuple(_snapshot(item, accepted_current=True) for item in resulting_members)
                    ),
                    generation_record_id=generation.id,
                    derived_from_id=prior_model.id,
                    created_by_type="system",
                    created_by_id=None,
                )
            )
            for index, member in enumerate(resulting_members):
                await uow.method_models.add_member(
                    NewMethodModelMember(
                        id=_id(generation_id, f"member:{index}:{member.method_element_id}"),
                        method_model_id=resulting_model.id,
                        method_element_id=member.method_element_id,
                        review_section=member.review_section,
                        member_role=member.member_role,
                        sequence_index=member.sequence_index,
                        required=member.required,
                        inclusion_rationale=member.inclusion_rationale,
                    )
                )
        else:
            latest_members = await uow.method_models.list_members_for_update(latest.id)
            expected_fingerprint = method_composition_fingerprint(
                tuple(_snapshot(item, accepted_current=True) for item in resulting_members)
            )
            latest_fingerprint = method_composition_fingerprint(
                tuple(_snapshot(item, accepted_current=True) for item in latest_members)
            )
            if not (
                latest.version == prior_model.version + 1
                and latest.derived_from_id == prior_model.id
                and latest.review_status == "candidate"
                and latest.lifecycle_status == "current"
                and latest.name == prior_model.name
                and latest.objective == prior_model.objective
                and latest.research_question_id == prior_model.research_question_id
                and latest_fingerprint == expected_fingerprint
            ):
                raise ResourceConflictError(details={"reason": "method_model_revision_not_latest"})
            resulting_model = latest
            resulting_members = latest_members
        changed_ids = tuple(item.id for item in required.values())
        section_authority = _section_authority(
            context=context,
            resulting_members=resulting_members,
            generation_id=generation_id,
        )

    assessment = await _assess(uow, resulting_model, resulting_members, generation.id, context)
    section_refs: tuple[dict[str, object], ...] = tuple(
        {
            "type": "method_model_section_review",
            "id": str(item.id),
            "reviewer_id": str(item.reviewer_id),
            "member_set_fingerprint": item.member_set_fingerprint,
        }
        for item in context.reviews
    )
    event = await uow.events.append(
        NewOperationEvent(
            id=event_id,
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            operation_type=_SUCCEEDED,
            created_by_type="system",
            created_by_id=None,
            input_refs=(
                {"type": "generation_record", "id": str(generation.id)},
                {"type": "mapping_review_gate", "id": str(context.gate.id)},
                _model_ref(prior_model),
                *section_refs,
            ),
            output_refs=(
                _model_ref(resulting_model),
                {"type": "method_model_assessment", "id": str(assessment.id)},
                {
                    "type": "reassessment_result",
                    "id": proposal.result,
                    "changed_element_ids": [str(item) for item in changed_ids],
                    "unchanged_element_ids": [
                        str(item.method_element_id)
                        for item in resulting_members
                        if item.method_element_id not in changed_ids
                    ],
                    "section_authority": [
                        item.model_dump(mode="json") for item in section_authority
                    ],
                    "authority_disclosure": proposal.authority_disclosure,
                },
            ),
            reason=proposal.rationale,
            idempotency_key=(
                f"method-reassessment-rerun:{generation.id}"
                if command.explicit_rerun
                else _command_idempotency_key(command)
            ),
            input_fingerprint=context.fingerprint,
            request_id=command.request_id,
        )
    )
    if required:
        for authority in section_authority:
            await uow.method_models.add_section_review(
                NewMethodModelSectionReview(
                    id=authority.resulting_section_review_id,
                    method_model_id=resulting_model.id,
                    review_section=authority.review_section,
                    version=1,
                    outcome="accepted",
                    member_set_fingerprint=next(
                        member_set_fingerprint(
                            tuple(
                                _snapshot(item, accepted_current=True)
                                for item in resulting_members
                                if item.review_section == authority.review_section
                            )
                        )
                        for _ in (0,)
                    ),
                    reviewer_id=authority.source_reviewer_id,
                    rationale=authority.rationale,
                    operation_event_id=event.id,
                    supersedes_id=None,
                )
            )
        await uow.method_models.update_model_review_lifecycle(
            prior_model.id,
            expected_version=prior_model.version,
            expected_review_status=prior_model.review_status,
            expected_lifecycle_status=prior_model.lifecycle_status,
            review_status="accepted",
            lifecycle_status="superseded",
        )
        resulting_model = await uow.method_models.update_model_review_lifecycle(
            resulting_model.id,
            expected_version=resulting_model.version,
            expected_review_status=resulting_model.review_status,
            expected_lifecycle_status=resulting_model.lifecycle_status,
            review_status="accepted",
            lifecycle_status="current",
            supersedes_id=prior_model.id,
        )
    await uow.method_models.set_current_assessment(
        method_model_id=resulting_model.id, assessment_id=assessment.id
    )
    await uow.method_models.add_rendering(
        NewMethodModelRendering(
            id=_id(generation.id, "rendering"),
            method_model_id=resulting_model.id,
            rendering_type="narrative",
            content=json.dumps(
                {
                    "result": proposal.result,
                    "prior_method_model_id": str(prior_model.id),
                    "resulting_method_model_id": str(resulting_model.id),
                    "changed_element_ids": [str(item) for item in changed_ids],
                    "section_authority": [
                        item.model_dump(mode="json") for item in section_authority
                    ],
                    "rationale": proposal.rationale,
                    "authority_disclosure": proposal.authority_disclosure,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            prompt_version=METHOD_REASSESSMENT_PROMPT_VERSION,
            provider=generation.provider,
            model_version=generation.model_version,
            generation_record_id=generation.id,
        )
    )
    await _dependencies(uow, context, resulting_model, assessment, generation.id)
    unchanged = tuple(
        item.method_element_id
        for item in resulting_members
        if item.method_element_id not in changed_ids
    )
    return MethodModelReassessmentResponse(
        outcome=proposal.result,
        generation_record_id=generation.id,
        assessment_id=assessment.id,
        prior_method_model_id=prior_model.id,
        resulting_method_model_id=resulting_model.id,
        mapping_review_gate_event_id=context.gate.id,
        event_id=event.id,
        replayed=replayed,
        changed_element_ids=list(changed_ids),
        unchanged_element_ids=list(unchanged),
        section_authority=list(section_authority),
        rationale=proposal.rationale,
        authority_disclosure=proposal.authority_disclosure,
    )


async def _assess(
    uow: UnitOfWork,
    model: MethodModelData,
    members: tuple[MethodModelMemberData, ...],
    generation_id: UUID,
    context: _Context,
) -> MethodModelAssessmentData:
    assessed: list[InitialMethodMember] = []
    for member in members:
        payload = member.subtype_payload
        symbol_type = (
            MethodSymbolType(str(payload["symbol_type"]))
            if member.element.element_type == MethodElementType.SYMBOL.value
            else None
        )
        endpoint = method_element_endpoint_type(
            element_type=MethodElementType(member.element.element_type), symbol_type=symbol_type
        )
        links = await uow.method_models.list_evidence_links(member.element.id)
        grounding = assess_method_evidence(
            project_id=model.project_id,
            element_id=member.element.id,
            element_version=member.element.version,
            endpoint_type=endpoint,
            links=links,
        )
        assessed.append(
            InitialMethodMember(
                element_id=member.element.id,
                element_version=member.element.version,
                required=member.required,
                provenance_complete=bool(links),
                endpoint_type=endpoint,
                grounding=grounding,
            )
        )
    composition = method_composition_fingerprint(
        tuple(_snapshot(item, accepted_current=True) for item in members)
    )
    specification = assess_initial_method_model(
        method_model_id=model.id,
        composition_fingerprint=composition,
        objective_context_fingerprint=reassessment_fingerprint(
            {
                "project_id": str(model.project_id),
                "research_question_id": str(context.question_id),
                "objective": model.objective,
                "mapping_gate": str(context.gate.id),
            }
        ),
        members=tuple(assessed),
    )
    return await uow.method_models.add_model_assessment(
        NewMethodModelAssessment(
            id=_id(generation_id, "assessment"),
            method_model_id=model.id,
            composition_fingerprint=specification.composition_fingerprint,
            member_grounding_fingerprint=specification.member_grounding_fingerprint,
            source_evidence_fingerprint=specification.source_evidence_fingerprint,
            repository_evidence_fingerprint=specification.repository_evidence_fingerprint,
            unsupported_required_count=specification.unsupported_required_count,
            mixed_evidence_count=specification.mixed_evidence_count,
            contradicted_count=specification.contradicted_count,
            missing_provenance_count=specification.missing_provenance_count,
            unresolved_scientific_ambiguity_count=specification.unresolved_scientific_ambiguity_count,
            outcome=specification.outcome.value,
            rationale=specification.rationale,
            generation_record_id=generation_id,
        )
    )


async def _assess_post_judgment(
    *,
    uow: UnitOfWork,
    model: MethodModelData,
    members: tuple[MethodModelMemberData, ...],
    question_id: UUID,
    gate: OperationEventData,
    judgment_id: UUID,
    fingerprint: str,
) -> MethodModelAssessmentData:
    assessed: list[InitialMethodMember] = []
    for member in members:
        payload = member.subtype_payload
        symbol_type = (
            MethodSymbolType(str(payload["symbol_type"]))
            if member.element.element_type == MethodElementType.SYMBOL.value
            else None
        )
        endpoint = method_element_endpoint_type(
            element_type=MethodElementType(member.element.element_type), symbol_type=symbol_type
        )
        links = await uow.method_models.list_evidence_links(member.element.id)
        assessed.append(
            InitialMethodMember(
                element_id=member.element.id,
                element_version=member.element.version,
                required=member.required,
                provenance_complete=bool(links),
                endpoint_type=endpoint,
                grounding=assess_method_evidence(
                    project_id=model.project_id,
                    element_id=member.element.id,
                    element_version=member.element.version,
                    endpoint_type=endpoint,
                    links=links,
                ),
            )
        )
    composition = method_composition_fingerprint(
        tuple(_snapshot(item, accepted_current=True) for item in members)
    )
    specification = assess_initial_method_model(
        method_model_id=model.id,
        composition_fingerprint=composition,
        objective_context_fingerprint=reassessment_fingerprint(
            {
                "project_id": str(model.project_id),
                "research_question_id": str(question_id),
                "objective": model.objective,
                "mapping_gate": str(gate.id),
                "human_judgment_id": str(judgment_id),
                "reassessment_fingerprint": fingerprint,
            }
        ),
        members=tuple(assessed),
    )
    return await uow.method_models.add_model_assessment(
        NewMethodModelAssessment(
            id=_id(judgment_id, f"assessment:{fingerprint}"),
            method_model_id=model.id,
            composition_fingerprint=specification.composition_fingerprint,
            member_grounding_fingerprint=specification.member_grounding_fingerprint,
            source_evidence_fingerprint=specification.source_evidence_fingerprint,
            repository_evidence_fingerprint=None,
            unsupported_required_count=specification.unsupported_required_count,
            mixed_evidence_count=specification.mixed_evidence_count,
            contradicted_count=specification.contradicted_count,
            missing_provenance_count=specification.missing_provenance_count,
            unresolved_scientific_ambiguity_count=specification.unresolved_scientific_ambiguity_count,
            outcome=specification.outcome.value,
            rationale=(
                f"{METHOD_REASSESSMENT_POLICY_VERSION}; deterministic post-Judgment "
                f"assessment for HumanJudgment {judgment_id}; repository not applicable."
            ),
            generation_record_id=None,
        )
    )


def _section_authority(
    *, context: _Context, resulting_members: tuple[MethodModelMemberData, ...], generation_id: UUID
) -> tuple[SectionAuthorityResponse, ...]:
    result: list[SectionAuthorityResponse] = []
    replacements = {
        member.element.supersedes_id: member.element.id
        for member in resulting_members
        if member.element.supersedes_id is not None
    }
    for review in context.reviews:
        prior_members = tuple(
            _snapshot(item, accepted_current=True)
            for item in context.members
            if item.review_section == review.review_section
        )
        new_members = tuple(
            _snapshot(item, accepted_current=True)
            for item in resulting_members
            if item.review_section == review.review_section
        )
        prior_fingerprint = member_set_fingerprint(prior_members)
        if prior_fingerprint != review.member_set_fingerprint:
            raise ResourceConflictError(details={"reason": "source_section_review_stale"})
        changed = [
            replacement
            for current, replacement in replacements.items()
            if current is not None
            and any(
                item.method_element_id == current
                for item in context.members
                if item.review_section == review.review_section
            )
        ]
        unchanged = member_set_fingerprint(new_members) == review.member_set_fingerprint
        result.append(
            SectionAuthorityResponse(
                review_section=review.review_section,
                source_section_review_id=review.id,
                resulting_section_review_id=_id(generation_id, f"section:{review.review_section}"),
                source_reviewer_id=review.reviewer_id,
                unchanged_fingerprint=unchanged,
                accepted_replacement_ids=changed,
                rationale=(
                    f"{METHOD_REASSESSMENT_SECTION_POLICY_VERSION}; prior human review {review.id} "
                    f"by {review.reviewer_id}; prior fingerprint {review.member_set_fingerprint}; "
                    f"new fingerprint {member_set_fingerprint(new_members)}; accepted same-lineage "
                    f"replacement IDs {','.join(str(item) for item in changed) or 'none'}; all section "
                    "assignment, ordering, role, requiredness, and inclusion semantics are unchanged."
                ),
            )
        )
    return tuple(result)


async def _dependencies(
    uow: UnitOfWork,
    context: _Context,
    model: MethodModelData,
    assessment: MethodModelAssessmentData,
    generation_id: UUID,
) -> None:
    references: list[tuple[str, UUID, str]] = [
        ("research_question", context.question_id, "objective_context"),
        ("method_model_assessment", assessment.id, "current_reassessment"),
    ]
    if model.id != context.model.id:
        references.append(("method_model", context.model.id, "accepted_predecessor_model"))
    references.extend(
        [
            (
                "paper_repository_relationship",
                context.relationship_id,
                "qualitative_authority",
            ),
            ("repository_snapshot", context.snapshot_id, "immutable_repository_evidence"),
        ]
    )
    for identifier in context.mapping_ids:
        references.append(("paper_code_mapping", identifier, "accepted_mapping"))
    for identifier in context.knowledge_ids:
        references.append(("knowledge_object", identifier, "accepted_project_knowledge"))
    for identifier in context.evidence_ids:
        references.append(("evidence_link", identifier, "accepted_method_evidence"))
    for identifier in context.grounding_ids:
        references.append(("grounding_assessment", identifier, "cached_method_grounding"))
    for identifier in context.judgment_ids:
        references.append(("human_judgment", identifier, "current_human_judgment"))
    for index, (kind, identifier, _role) in enumerate(references):
        await uow.dependencies.append_dependency(
            ArtifactDependencyData(
                id=_id(generation_id, f"dependency:{index}:{kind}:{identifier}"),
                project_id=model.project_id,
                artifact_type="method_model",
                artifact_id=model.id,
                dependency_type=kind,
                dependency_id=identifier,
                dependency_role="derivation_input",
                criticality="blocking"
                if kind in {"research_question", "method_model_assessment"}
                else "material",
            )
        )


async def _replay(
    uow: UnitOfWork, generation: GenerationRecordData, event: OperationEventData
) -> MethodModelReassessmentResponse:
    try:
        proposal = MethodModelReassessmentBatch.model_validate(generation.raw_structured_output)
        prior_id = _ref_id(event.input_refs, "method_model")
        resulting_id = _ref_id(event.output_refs, "method_model")
        assessment_id = _ref_id(event.output_refs, "method_model_assessment")
        gate_id = _ref_id(event.input_refs, "mapping_review_gate")
        result_ref = next(
            item for item in event.output_refs if item.get("type") == "reassessment_result"
        )
    except (ValueError, StopIteration, KeyError) as error:
        raise InternalError(details={"reason": "method_reassessment_replay_invalid"}) from error
    if (
        await uow.method_models.get_model_exact(resulting_id) is None
        or await uow.method_models.get_assessment_exact(assessment_id) is None
    ):
        raise InternalError(details={"reason": "method_reassessment_replay_target_missing"})
    return MethodModelReassessmentResponse(
        outcome=proposal.result,
        generation_record_id=generation.id,
        assessment_id=assessment_id,
        prior_method_model_id=prior_id,
        resulting_method_model_id=resulting_id,
        mapping_review_gate_event_id=gate_id,
        event_id=event.id,
        replayed=True,
        changed_element_ids=[
            UUID(str(item)) for item in _list_value(result_ref, "changed_element_ids")
        ],
        unchanged_element_ids=[
            UUID(str(item)) for item in _list_value(result_ref, "unchanged_element_ids")
        ],
        section_authority=[
            SectionAuthorityResponse.model_validate(item)
            for item in _list_value(result_ref, "section_authority")
        ],
        rationale=proposal.rationale,
        authority_disclosure=proposal.authority_disclosure,
    )


def _not_applicable_response(
    model: MethodModelData, gate: OperationEventData, event: OperationEventData, *, replayed: bool
) -> MethodModelReassessmentResponse:
    return MethodModelReassessmentResponse(
        outcome="not_applicable",
        generation_record_id=None,
        assessment_id=model.current_assessment_id,
        prior_method_model_id=model.id,
        resulting_method_model_id=model.id,
        mapping_review_gate_event_id=gate.id,
        event_id=event.id,
        replayed=replayed,
        changed_element_ids=[],
        unchanged_element_ids=[],
        section_authority=[],
        rationale=event.reason or "Repository branch not applicable.",
        authority_disclosure=None,
    )


def _command_idempotency_key(command: ReassessMethodModelCommand) -> str:
    return (
        "method-reassessment-command:"
        f"{command.workflow_run_id}:{command.mapping_review_gate_event_id}:"
        f"{command.trigger_human_judgment_id or 'mapping'}"
    )


def _validate_source_sections(
    members: tuple[MethodModelMemberData, ...], reviews: tuple[MethodModelSectionReviewData, ...]
) -> None:
    expected = {item.value for item in MethodReviewSection}
    if {item.review_section for item in reviews} != expected or any(
        item.outcome != "accepted" for item in reviews
    ):
        raise ResourceConflictError(details={"reason": "complete_human_section_reviews_required"})
    for review in reviews:
        snapshots = tuple(
            _snapshot(item, accepted_current=True)
            for item in members
            if item.review_section == review.review_section
        )
        if member_set_fingerprint(snapshots) != review.member_set_fingerprint:
            raise ResourceConflictError(details={"reason": "source_section_review_stale"})


def _snapshot(member: MethodModelMemberData, *, accepted_current: bool) -> MethodMemberSnapshot:
    return MethodMemberSnapshot(
        method_element_id=member.element.id,
        element_lineage_id=member.element.lineage_id,
        element_version=member.element.version,
        review_section=MethodReviewSection(member.review_section),
        member_role=MethodMemberRole(member.member_role),
        sequence_index=member.sequence_index,
        required=member.required,
        inclusion_rationale=member.inclusion_rationale,
        review_status=KnowledgeReviewStatus.ACCEPTED
        if accepted_current
        else KnowledgeReviewStatus(member.element.review_status),
        lifecycle_status=KnowledgeLifecycleStatus.CURRENT
        if accepted_current
        else KnowledgeLifecycleStatus(member.element.lifecycle_status),
    )


def _member_identity(member: MethodModelMemberData) -> dict[str, object]:
    return {
        "element": _element_identity(member.element),
        "review_section": member.review_section,
        "member_role": member.member_role,
        "sequence_index": member.sequence_index,
        "required": member.required,
        "inclusion_rationale": member.inclusion_rationale,
        "subtype_payload": _json_value(member.subtype_payload),
    }


def _element_identity(item: MethodElementData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "lineage_id": str(item.lineage_id),
        "version": item.version,
        "element_type": item.element_type,
        "epistemic_status": item.epistemic_status,
        "review_status": item.review_status,
        "lifecycle_status": item.lifecycle_status,
        "grounding_status": item.grounding_status,
        "supersedes_id": None if item.supersedes_id is None else str(item.supersedes_id),
        "derived_from_id": None if item.derived_from_id is None else str(item.derived_from_id),
    }


def _evidence_identity(item: Any) -> dict[str, object]:
    return {
        "id": str(item.id),
        "version": item.version,
        "source_chunk_id": None if item.source_chunk_id is None else str(item.source_chunk_id),
        "repository_component_id": None
        if item.repository_component_id is None
        else str(item.repository_component_id),
        "relation": item.evidence_relation,
        "excerpt": item.evidence_excerpt,
        "review_status": item.review_status,
        "lifecycle_status": item.lifecycle_status,
    }


def _review_identity(item: MethodModelSectionReviewData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "review_section": item.review_section,
        "version": item.version,
        "outcome": item.outcome,
        "member_set_fingerprint": item.member_set_fingerprint,
        "reviewer_id": str(item.reviewer_id),
        "operation_event_id": str(item.operation_event_id),
    }


def _mapping_identity(item: Any) -> dict[str, object]:
    return {
        "id": str(item.id),
        "version": item.version,
        "mapping_type": item.mapping_type,
        "explanation": item.explanation,
    }


def _knowledge_identity(item: Any) -> dict[str, object]:
    return {
        "id": str(item.id),
        "version": item.version,
        "object_type": item.object_type,
        "statement": item.statement,
        "grounding_status": item.grounding_status,
    }


def _judgment_identity(item: Any) -> dict[str, object]:
    return {
        "id": str(item.id),
        "version": item.version,
        "request_id": str(item.judgment_request_id),
        "decision": item.custom_decision,
        "rationale": item.rationale,
    }


def _model_ref(model: MethodModelData) -> dict[str, object]:
    return {
        "type": "method_model",
        "id": str(model.id),
        "lineage_id": str(model.lineage_id),
        "version": model.version,
        "composition_fingerprint": model.composition_fingerprint,
    }


def _ref_id(references: tuple[dict[str, object], ...], kind: str) -> UUID:
    matches = [item for item in references if item.get("type") == kind]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise ValueError("reference_invalid")
    return UUID(str(matches[0]["id"]))


def _list_value(reference: dict[str, object], key: str) -> list[object]:
    value = reference.get(key)
    if not isinstance(value, list):
        raise ValueError("reference_invalid")
    return value


def _id(parent: UUID, value: str) -> UUID:
    return uuid5(_NAMESPACE, f"{parent}:{value}")


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _require_owned(actor: ActorContext, project: ProjectData | None) -> ProjectData:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
    return project
