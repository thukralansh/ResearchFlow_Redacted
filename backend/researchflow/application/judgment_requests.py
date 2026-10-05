""" deterministic Judgment-point classification and evidence-backed drafting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.api.schemas.judgments import (
    AffectedDependencyInput,
    JudgmentRequestDraft,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprint_catalog import DependencyCriticality
from researchflow.domain.dependency_criticality import (
    JUDGMENT_REQUEST_POLICY_VERSION,
    AffectedDependency,
    CriticalityAssessment,
    classify_dependency,
)
from researchflow.domain.judgment_points import (
    JUDGMENT_DRAFT_PROMPT_VERSION,
    JUDGMENT_DRAFT_SCHEMA_NAME,
    JUDGMENT_DRAFT_SCHEMA_VERSION,
    JUDGMENT_DRAFT_TASK_NAME,
    EvidenceState,
    assess_evidence_resolvability,
    needs_judgment,
)
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
    JudgmentOptionData,
    JudgmentRequestData,
    KnowledgeObjectData,
    NewJudgmentOption,
    NewJudgmentRequest,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/")
_REQUEST_CREATED = "judgment_request.created"
_NO_JUDGMENT = "judgment_request.not_required"


@dataclass(frozen=True, slots=True)
class CreateJudgmentRequestCommand:
    project_id: UUID
    workflow_run_id: UUID
    ambiguity_id: UUID
    affected_dependencies: tuple[AffectedDependencyInput, ...]
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class JudgmentRequestDetail:
    request: JudgmentRequestData
    ambiguity: dict[str, Any]
    options: tuple[JudgmentOptionData, ...]
    evidence: tuple[EvidenceLinkData, ...]
    dependencies: tuple[ArtifactDependencyData, ...]
    recommendation: dict[str, Any] | None
    no_recommendation_reason: str | None


@dataclass(frozen=True, slots=True)
class JudgmentRequestSummary:
    request: JudgmentRequestData
    option_count: int
    highest_criticality: Literal["blocking", "material", "informational"]


@dataclass(frozen=True, slots=True)
class JudgmentRequestOutcome:
    outcome: Literal["request_created", "no_judgment"]
    request: JudgmentRequestDetail | None
    reason: str
    replayed: bool
    event: OperationEventData


@dataclass(frozen=True, slots=True)
class _Context:
    ambiguity: KnowledgeObjectData
    payload: dict[str, object]
    evidence: tuple[EvidenceLinkData, ...]
    assessments: tuple[tuple[AffectedDependencyInput, CriticalityAssessment], ...]
    request_required: bool
    reason: str
    fingerprint: str
    generation_payload: dict[str, object]


class JudgmentRequestService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: StructuredGenerationService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def create(self, command: CreateJudgmentRequestCommand) -> JudgmentRequestOutcome:
        async with self._uow_factory(command.project_id) as uow:
            context = await _load_context(uow, self._actor, command, lock=False)
            existing = await uow.judgments.get_open_request_for_ambiguity(command.ambiguity_id)
            if existing is not None:
                detail = await _detail(uow, existing)
                event = await _existing_event(uow, existing)
                _require_replay_fingerprint(event, context.fingerprint)
                await uow.rollback()
                return JudgmentRequestOutcome(
                    "request_created", detail, "Existing open request replayed.", True, event
                )
            if not context.request_required:
                outcome = await _persist_no_judgment(uow, self._actor, command, context)
                await uow.commit()
                return outcome
            await uow.rollback()

        generation = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=JUDGMENT_DRAFT_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=JUDGMENT_DRAFT_PROMPT_VERSION,
                schema_name=JUDGMENT_DRAFT_SCHEMA_NAME,
                schema_version=JUDGMENT_DRAFT_SCHEMA_VERSION,
                output_type=JudgmentRequestDraft,
                messages=(
                    StructuredGenerationMessage(
                        role="user",
                        content=json.dumps(
                            context.generation_payload,
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
            current = await _load_context(uow, self._actor, command, lock=True)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "judgment_request_stale_context"})
            existing = await uow.judgments.get_open_request_for_ambiguity(command.ambiguity_id)
            if existing is not None:
                detail = await _detail(uow, existing)
                event = await _existing_event(uow, existing)
                _require_replay_fingerprint(event, current.fingerprint)
                await uow.rollback()
                return JudgmentRequestOutcome(
                    "request_created", detail, "Existing open request replayed.", True, event
                )
            validate_judgment_draft(generation.value, current.evidence)
            outcome = await _persist_request(
                uow, self._actor, command, current, generation.generation_record, generation.value
            )
            await uow.commit()
            return outcome


class JudgmentRequestQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def list(self, project_id: UUID) -> tuple[JudgmentRequestSummary, ...]:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            summaries = []
            for request in await uow.judgments.list_requests():
                options = await uow.judgments.list_options(request.id)
                dependencies = await uow.dependencies.list_dependencies(
                    "judgment_request", request.id
                )
                summaries.append(
                    JudgmentRequestSummary(
                        request=request,
                        option_count=len(options),
                        highest_criticality=_highest(dependencies),
                    )
                )
            await uow.rollback()
            return tuple(summaries)

    async def detail(self, project_id: UUID, request_id: UUID) -> JudgmentRequestDetail:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            request = await uow.judgments.get_request_exact(request_id)
            if request is None:
                raise ResourceNotFoundError(details={"resource": "judgment_request"})
            detail = await _detail(uow, request)
            await uow.rollback()
            return detail


async def _load_context(
    uow: UnitOfWork,
    actor: ActorContext,
    command: CreateJudgmentRequestCommand,
    *,
    lock: bool,
) -> _Context:
    project = _require_owned(actor, await uow.projects.get())
    if await uow.workflows.get_exact(command.workflow_run_id) is None:
        raise ResourceNotFoundError(details={"resource": "workflow_run"})
    ambiguity = (
        await uow.knowledge.get_exact_for_update(command.ambiguity_id)
        if lock
        else await uow.knowledge.get_exact(command.ambiguity_id)
    )
    if ambiguity is None or ambiguity.object_type != "ambiguity":
        raise ResourceNotFoundError(details={"resource": "accepted_ambiguity"})
    if ambiguity.review_status != "accepted" or ambiguity.lifecycle_status != "current":
        raise ResourceConflictError(details={"reason": "ambiguity_not_accepted_current"})
    payload_record = await uow.knowledge.get_payload(ambiguity.id)
    if payload_record is None:
        raise ResourceConflictError(details={"reason": "ambiguity_payload_missing"})
    payload = dict(payload_record.subtype_payload)
    payload["interpretations"] = list(payload_record.ambiguity_interpretations)
    if payload.get("resolution_status") != "unresolved":
        raise ResourceConflictError(details={"reason": "ambiguity_not_unresolved"})
    evidence = await uow.knowledge.list_evidence_links(ambiguity.id)
    affected = validate_affected_dependencies(command.affected_dependencies)
    assessments = tuple(
        (
            item,
            classify_dependency(
                AffectedDependency(
                    dependency_type=item.dependency_type,
                    dependency_id=str(item.dependency_id),
                    dependency_role=item.dependency_role,
                    required_for_objective=item.required_for_objective,
                    correctness_requires_resolution=item.correctness_requires_resolution,
                    material_consequence=item.material_consequence,
                    rationale=item.rationale,
                )
            ),
        )
        for item in affected
    )
    resolvability = assess_evidence_resolvability(
        evidence=tuple(
            EvidenceState(item.review_status, item.lifecycle_status, item.evidence_relation)
            for item in evidence
        ),
        interpretation_count=len(payload_record.ambiguity_interpretations),
    )
    required = needs_judgment(
        criticalities=tuple(item.criticality for _, item in assessments),
        resolvable=resolvability.resolvable,
    )
    if not any(
        item.criticality in {DependencyCriticality.MATERIAL, DependencyCriticality.BLOCKING}
        for _, item in assessments
    ):
        reason = "The ambiguity has no supplied material or blocking downstream consequence."
    elif resolvability.resolvable:
        reason = resolvability.rationale
    else:
        reason = (
            "The ambiguity affects applicable downstream state and accepted/current evidence "
            "cannot safely select among scientifically defensible consequences."
        )
    question = await uow.research_questions.get_current_primary()
    method = (
        None
        if question is None
        else await uow.method_models.get_accepted_current_model(question.id)
    )
    mental = await uow.mental_models.get_current()
    generation_payload: dict[str, object] = {
        "policy_version": JUDGMENT_REQUEST_POLICY_VERSION,
        "project": {
            "title": project.title,
            "objective": project.target_outcome,
            "research_reproduction_depth": project.reproduction_depth,
        },
        "research_question": None
        if question is None
        else {"id": str(question.id), "text": question.question_text, "scope": question.scope_text},
        "ambiguity": {
            "id": str(ambiguity.id),
            "statement": ambiguity.statement,
            "payload": payload,
        },
        "accepted_current_evidence": [
            _evidence_payload(item)
            for item in evidence
            if item.review_status == "accepted" and item.lifecycle_status == "current"
        ],
        "affected_dependencies": [
            {
                **item.model_dump(mode="json"),
                "criticality": assessment.criticality.value,
                "classification_rationale": assessment.rationale,
            }
            for item, assessment in assessments
        ],
        "current_method_model": None
        if method is None
        else {"id": str(method.id), "version": method.version, "objective": method.objective},
        "current_mental_model": None
        if mental is None
        else {"id": str(mental.id), "version": mental.version, "status": mental.status},
        "custom_human_decision_always_available": True,
    }
    fingerprint = _hash(generation_payload)
    return _Context(
        ambiguity, payload, evidence, assessments, required, reason, fingerprint, generation_payload
    )


def validate_affected_dependencies(
    values: tuple[AffectedDependencyInput, ...],
) -> tuple[AffectedDependencyInput, ...]:
    if not values:
        raise DomainValidationError(details={"reason": "affected_dependency_missing"})
    keys = [(item.dependency_type, item.dependency_id) for item in values]
    if len(keys) != len(set(keys)):
        raise DomainValidationError(details={"reason": "affected_dependency_duplicate"})
    return tuple(sorted(values, key=lambda item: (item.dependency_type, str(item.dependency_id))))


def validate_judgment_draft(
    draft: JudgmentRequestDraft, evidence: tuple[EvidenceLinkData, ...]
) -> None:
    eligible = {
        item.id: item
        for item in evidence
        if item.review_status == "accepted" and item.lifecycle_status == "current"
    }
    consequence_keys: list[str] = []
    for option in draft.options:
        consequence_keys.append(" ".join(option.implementation_consequences.split()).casefold())
        _validate_refs(option.supporting_evidence, eligible, {"supports", "qualifies"})
        _validate_refs(option.conflicting_evidence, eligible, {"contradicts"})
    if len(consequence_keys) != len(set(consequence_keys)):
        raise DomainValidationError(details={"reason": "judgment_options_not_materially_distinct"})
    if draft.recommendation is not None:
        _validate_refs(
            draft.recommendation.supporting_evidence, eligible, {"supports", "qualifies"}
        )


def _validate_refs(
    references: list[Any], eligible: dict[UUID, EvidenceLinkData], relations: set[str]
) -> None:
    for reference in references:
        item = eligible.get(reference.evidence_link_id)
        if item is None or item.evidence_relation not in relations:
            raise DomainValidationError(details={"reason": "judgment_evidence_reference_invalid"})
        if item.evidence_excerpt != reference.excerpt:
            raise DomainValidationError(details={"reason": "judgment_evidence_excerpt_mismatch"})


async def _persist_request(
    uow: UnitOfWork,
    actor: ActorContext,
    command: CreateJudgmentRequestCommand,
    context: _Context,
    generation: GenerationRecordData,
    draft: JudgmentRequestDraft,
) -> JudgmentRequestOutcome:
    request_id = _id(generation.id, f"request:{context.fingerprint}")
    request = await uow.judgments.add_request(
        NewJudgmentRequest(
            id=request_id,
            project_id=command.project_id,
            ambiguity_id=context.ambiguity.id,
            lineage_id=request_id,
            version=1,
            title=draft.title,
            decision_question=draft.decision_question,
            why_human_required=draft.why_human_required,
            workflow_run_id=command.workflow_run_id,
            generation_record_id=generation.id,
            created_by_type="ai",
            created_by_id=None,
        )
    )
    options = tuple(
        [
            await uow.judgments.add_option(
                NewJudgmentOption(
                    id=_id(request.id, f"option:{rank}:{option.client_id}"),
                    judgment_request_id=request.id,
                    title=option.title,
                    description=option.description,
                    supporting_evidence=tuple(
                        item.model_dump(mode="json") for item in option.supporting_evidence
                    ),
                    conflicting_evidence=tuple(
                        item.model_dump(mode="json") for item in option.conflicting_evidence
                    ),
                    implementation_consequences=option.implementation_consequences,
                    rank=rank,
                )
            )
            for rank, option in enumerate(draft.options, start=1)
        ]
    )
    dependencies = []
    for item, assessment in context.assessments:
        dependencies.append(
            await uow.dependencies.append_dependency(
                ArtifactDependencyData(
                    id=_id(request.id, f"dependency:{item.dependency_type}:{item.dependency_id}"),
                    project_id=command.project_id,
                    artifact_type="judgment_request",
                    artifact_id=request.id,
                    dependency_type=item.dependency_type,
                    dependency_id=item.dependency_id,
                    dependency_role=item.dependency_role.value,
                    criticality=assessment.criticality.value,
                )
            )
        )
    event = await uow.events.append(
        NewOperationEvent(
            id=_id(request.id, "event"),
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            operation_type=_REQUEST_CREATED,
            created_by_type="ai",
            created_by_id=None,
            input_refs=_event_inputs(context),
            output_refs=({"type": "judgment_request", "id": str(request.id)},),
            reason=context.reason,
            idempotency_key=f"{_REQUEST_CREATED}:{context.fingerprint}",
            input_fingerprint=context.fingerprint,
            request_id=command.request_id,
        )
    )
    detail = JudgmentRequestDetail(
        request=request,
        ambiguity=_ambiguity_view(context.ambiguity, context.payload),
        options=options,
        evidence=context.evidence,
        dependencies=tuple(dependencies),
        recommendation=None
        if draft.recommendation is None
        else draft.recommendation.model_dump(mode="json"),
        no_recommendation_reason=draft.no_recommendation_reason,
    )
    return JudgmentRequestOutcome("request_created", detail, context.reason, False, event)


async def _persist_no_judgment(
    uow: UnitOfWork,
    actor: ActorContext,
    command: CreateJudgmentRequestCommand,
    context: _Context,
) -> JudgmentRequestOutcome:
    key = f"{_NO_JUDGMENT}:{context.fingerprint}"
    existing = await uow.events.get_by_idempotency_key(_NO_JUDGMENT, key)
    if existing is not None:
        return JudgmentRequestOutcome(
            "no_judgment", None, existing.reason or context.reason, True, existing
        )
    event = await uow.events.append(
        NewOperationEvent(
            id=_id(command.workflow_run_id, f"no-judgment:{context.fingerprint}"),
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            operation_type=_NO_JUDGMENT,
            created_by_type="system",
            created_by_id=None,
            input_refs=_event_inputs(context),
            reason=context.reason,
            idempotency_key=key,
            input_fingerprint=context.fingerprint,
            request_id=command.request_id,
        )
    )
    return JudgmentRequestOutcome("no_judgment", None, context.reason, False, event)


async def _detail(uow: UnitOfWork, request: JudgmentRequestData) -> JudgmentRequestDetail:
    ambiguity = await uow.knowledge.get_exact(request.ambiguity_id)
    payload = await uow.knowledge.get_payload(request.ambiguity_id)
    if ambiguity is None or payload is None:
        raise ResourceConflictError(details={"reason": "judgment_request_ambiguity_missing"})
    subtype = dict(payload.subtype_payload)
    subtype["interpretations"] = list(payload.ambiguity_interpretations)
    generation = (
        None
        if request.generation_record_id is None
        else await uow.generations.get_exact(request.generation_record_id)
    )
    raw = generation.raw_structured_output if generation is not None else None
    raw_dict = raw if isinstance(raw, dict) else {}
    return JudgmentRequestDetail(
        request=request,
        ambiguity=_ambiguity_view(ambiguity, subtype),
        options=await uow.judgments.list_options(request.id),
        evidence=await uow.knowledge.list_evidence_links(ambiguity.id),
        dependencies=await uow.dependencies.list_dependencies("judgment_request", request.id),
        recommendation=raw_dict.get("recommendation")
        if isinstance(raw_dict.get("recommendation"), dict)
        else None,
        no_recommendation_reason=raw_dict.get("no_recommendation_reason")
        if isinstance(raw_dict.get("no_recommendation_reason"), str)
        else None,
    )


async def _existing_event(uow: UnitOfWork, request: JudgmentRequestData) -> OperationEventData:
    event = await uow.events.get_exact(_id(request.id, "event"))
    if event is None:
        raise ResourceConflictError(details={"reason": "judgment_request_event_missing"})
    return event


def _require_replay_fingerprint(event: OperationEventData, fingerprint: str) -> None:
    if event.input_fingerprint != fingerprint:
        raise ResourceConflictError(details={"reason": "open_judgment_request_input_changed"})


def _event_inputs(context: _Context) -> tuple[dict[str, object], ...]:
    values: list[dict[str, object]] = [
        {
            "type": "knowledge_object",
            "id": str(context.ambiguity.id),
            "version": context.ambiguity.version,
        }
    ]
    values.extend(
        {
            "type": item.dependency_type,
            "id": str(item.dependency_id),
            "criticality": assessment.criticality.value,
            "criticality_rationale": assessment.rationale,
            "domain_rationale": item.rationale,
        }
        for item, assessment in context.assessments
    )
    return tuple(values)


def _evidence_payload(item: EvidenceLinkData) -> dict[str, object]:
    return {
        "id": str(item.id),
        "relation": item.evidence_relation,
        "excerpt": item.evidence_excerpt,
        "source_chunk_id": None if item.source_chunk_id is None else str(item.source_chunk_id),
        "repository_component_id": None
        if item.repository_component_id is None
        else str(item.repository_component_id),
    }


def _ambiguity_view(ambiguity: KnowledgeObjectData, payload: dict[str, object]) -> dict[str, Any]:
    return {
        "id": ambiguity.id,
        "lineage_id": ambiguity.lineage_id,
        "version": ambiguity.version,
        "statement": ambiguity.statement,
        "review_status": ambiguity.review_status,
        "lifecycle_status": ambiguity.lifecycle_status,
        "grounding_status": ambiguity.grounding_status,
        "payload": payload,
    }


def _highest(
    dependencies: tuple[ArtifactDependencyData, ...],
) -> Literal["blocking", "material", "informational"]:
    values = {item.criticality for item in dependencies}
    if "blocking" in values:
        return "blocking"
    if "material" in values:
        return "material"
    return "informational"


def _require_owned(actor: ActorContext, project: ProjectData | None) -> ProjectData:
    if project is None or getattr(project, "created_by_id", None) != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
    return project


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _id(seed: UUID, name: str) -> UUID:
    return uuid5(_NAMESPACE, f"{seed}:{name}")
