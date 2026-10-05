""" immutable Human Judgment resolution and retryable reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.actor import ActorContext
from researchflow.errors import ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import StructuredGenerationModelPolicy
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    HumanJudgmentData,
    NewHumanJudgment,
    NewOperationEvent,
    OperationEventData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/")
_RECORDED = "human_judgment.recorded"
_PENDING = "human_judgment.reassessment.pending"
_SUCCEEDED = "human_judgment.reassessment.succeeded"
_FAILED = "human_judgment.reassessment.failed"


@dataclass(frozen=True, slots=True)
class ResolveJudgmentCommand:
    project_id: UUID
    request_id: UUID
    workflow_run_id: UUID
    command_id: UUID
    selected_option_id: UUID | None
    custom_decision: str | None
    rationale: str
    confidence: Decimal
    method_model_affected: bool
    method_impact_rationale: str
    mapping_review_gate_event_id: UUID
    repository_applicable: bool
    confirmed: bool
    expected_current_judgment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    method_model_id: UUID
    method_model_assessment_id: UUID
    mental_model_id: UUID
    mental_model_assessment_id: UUID
    method_event_id: UUID
    mental_event_id: UUID


class JudgmentReassessor(Protocol):
    async def reconcile(
        self, command: ResolveJudgmentCommand, judgment: HumanJudgmentData, fingerprint: str
    ) -> ReconciliationResult: ...


class HumanJudgmentStalenessHook(Protocol):
    """ seam; dependency traversal remains deferred to ."""

    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork,
        project_id: UUID,
        cause_type: str,
        before_id: UUID,
        after_id: UUID,
        operation_type: str,
    ) -> None: ...


class NoOpHumanJudgmentStalenessHook:
    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork | None = None,
        project_id: UUID,
        cause_type: str = "human_judgment",
        lineage_id: UUID | None = None,
        before_id: UUID,
        after_id: UUID,
        operation_type: str,
    ) -> None:
        del uow, project_id, cause_type, lineage_id, before_id, after_id, operation_type


class OrderedJudgmentReassessor:
    """Channel-neutral adapter over the existing ordered reassessment services."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        method_service: Any,
        mental_service: Any,
        model_policy: StructuredGenerationModelPolicy,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._method = method_service
        self._mental = mental_service
        self._policy = model_policy

    async def reconcile(
        self, command: ResolveJudgmentCommand, judgment: HumanJudgmentData, fingerprint: str
    ) -> ReconciliationResult:
        from researchflow.application.mental_model_reassessment import ReassessMentalModelCommand
        from researchflow.application.method_model_reassessment import ReassessMethodModelCommand

        if command.method_model_affected:
            method = await self._method.after_mapping(
                ReassessMethodModelCommand(
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    mapping_review_gate_event_id=command.mapping_review_gate_event_id,
                    applicable=command.repository_applicable,
                    model_policy=self._policy,
                    trigger_human_judgment_id=judgment.id,
                )
            )
            if method.assessment_id is None:
                raise ResourceConflictError(details={"reason": "method_reassessment_missing"})
            method_event_id = method.event_id
            method_model_id = method.resulting_method_model_id
            method_assessment_id = method.assessment_id
        else:
            async with self._uow_factory(command.project_id) as uow:
                workflow = await uow.workflows.get_exact(command.workflow_run_id)
                gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
                model = (
                    None
                    if workflow is None
                    else await uow.method_models.get_accepted_current_model(
                        workflow.primary_research_question_id
                    )
                )
                if (
                    workflow is None
                    or gate is None
                    or model is None
                    or model.current_assessment_id is None
                ):
                    raise ResourceConflictError(
                        details={"reason": "judgment_reassessment_context_missing"}
                    )
                method_event = await uow.events.append(
                    NewOperationEvent(
                        id=_id(judgment.id, "method-not-required"),
                        project_id=command.project_id,
                        workflow_run_id=command.workflow_run_id,
                        operation_type="human_judgment.method_reassessment.not_required",
                        created_by_type="system",
                        created_by_id=None,
                        input_refs=(
                            {"type": "mapping_review_gate", "id": str(gate.id)},
                            {"type": "human_judgment", "id": str(judgment.id)},
                        ),
                        output_refs=(
                            {"type": "method_model", "id": str(model.id)},
                            {
                                "type": "method_model_assessment",
                                "id": str(model.current_assessment_id),
                            },
                        ),
                        reason="Explicit method-impact classification found no MethodModel effect.",
                        idempotency_key=f"human-judgment-method-skip:{judgment.id}",
                        input_fingerprint=fingerprint,
                    )
                )
                await uow.commit()
                method_event_id, method_model_id, method_assessment_id = (
                    method_event.id,
                    model.id,
                    model.current_assessment_id,
                )
        mental = await self._mental.after_mapping(
            ReassessMentalModelCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                mapping_review_gate_event_id=command.mapping_review_gate_event_id,
                method_model_reassessment_event_id=method_event_id,
                applicable=command.repository_applicable,
                model_policy=self._policy,
                trigger_human_judgment_id=judgment.id,
            )
        )
        return ReconciliationResult(
            method_model_id,
            method_assessment_id,
            mental.resulting_mental_model_id,
            mental.assessment.id,
            method_event_id,
            mental.event_id,
        )


@dataclass(frozen=True, slots=True)
class JudgmentResolutionResult:
    judgment: HumanJudgmentData
    reassessment_status: Literal["pending", "succeeded", "failed_retryable"]
    method_model_id: UUID | None
    method_model_assessment_id: UUID | None
    mental_model_id: UUID | None
    mental_model_assessment_id: UUID | None
    replayed: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class HumanJudgmentHistoryItem:
    judgment: HumanJudgmentData
    dependencies: tuple[ArtifactDependencyData, ...]
    reassessment_status: Literal["pending", "succeeded", "failed_retryable"]
    method_model_id: UUID | None
    method_model_assessment_id: UUID | None
    mental_model_id: UUID | None
    mental_model_assessment_id: UUID | None
    status_event_id: UUID
    error: str | None


class HumanJudgmentQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def history(
        self, project_id: UUID, request_id: UUID
    ) -> tuple[HumanJudgmentHistoryItem, ...]:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            request = await uow.judgments.get_request_exact(request_id)
            if project is None or project.created_by_id != self._actor.actor_id or request is None:
                raise ResourceNotFoundError(details={"resource": "judgment_request"})
            judgments = await uow.judgments.list_for_request(request_id)
            events = await uow.events.list_for_workflow(request.workflow_run_id)
            result: list[HumanJudgmentHistoryItem] = []
            for judgment in judgments:
                relevant = tuple(
                    item
                    for item in events
                    if item.operation_type in {_PENDING, _FAILED, _SUCCEEDED}
                    and _has_ref(item, judgment.id)
                )
                succeeded = next(
                    (item for item in reversed(relevant) if item.operation_type == _SUCCEEDED),
                    None,
                )
                failed = next(
                    (item for item in reversed(relevant) if item.operation_type == _FAILED), None
                )
                pending = next(
                    (item for item in reversed(relevant) if item.operation_type == _PENDING), None
                )
                status_event = succeeded or failed or pending
                if status_event is None:
                    raise ResourceConflictError(
                        details={"reason": "judgment_reassessment_status_missing"}
                    )
                status: Literal["pending", "succeeded", "failed_retryable"] = (
                    "succeeded"
                    if succeeded is not None
                    else "failed_retryable"
                    if failed is not None
                    else "pending"
                )
                refs = {
                    str(item.get("type")): UUID(str(item["id"]))
                    for item in status_event.output_refs
                    if item.get("id")
                }
                result.append(
                    HumanJudgmentHistoryItem(
                        judgment=judgment,
                        dependencies=await uow.dependencies.list_dependencies(
                            "human_judgment", judgment.id
                        ),
                        reassessment_status=status,
                        method_model_id=refs.get("method_model"),
                        method_model_assessment_id=refs.get("method_model_assessment"),
                        mental_model_id=refs.get("mental_model"),
                        mental_model_assessment_id=refs.get("mental_model_assessment"),
                        status_event_id=status_event.id,
                        error=status_event.reason if status == "failed_retryable" else None,
                    )
                )
            await uow.rollback()
            return tuple(result)


class HumanJudgmentService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        reassessor: JudgmentReassessor,
        staleness_hook: HumanJudgmentStalenessHook | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._reassessor = reassessor
        self._staleness_hook = staleness_hook or NoOpHumanJudgmentStalenessHook()

    async def resolve(self, command: ResolveJudgmentCommand) -> JudgmentResolutionResult:
        fingerprint = _fingerprint(command)
        judgment, replayed = await self._record(command, fingerprint)
        return await self._reconcile(command, judgment, fingerprint, replayed)

    async def retry(self, command: ResolveJudgmentCommand) -> JudgmentResolutionResult:
        fingerprint = _fingerprint(command)
        async with self._uow_factory(command.project_id) as uow:
            event = await uow.events.get_by_idempotency_key(
                _RECORDED, f"human-judgment-command:{command.command_id}"
            )
            judgment = (
                None
                if event is None
                else await uow.judgments.get_judgment_exact(
                    _ref(event.output_refs, "human_judgment")
                )
            )
            if event is None or judgment is None or event.input_fingerprint != fingerprint:
                raise ResourceConflictError(details={"reason": "judgment_retry_context_mismatch"})
            current = await uow.judgments.get_current_for_request(command.request_id)
            if current is None or current.id != judgment.id:
                raise ResourceConflictError(details={"reason": "judgment_retry_stale"})
            await uow.rollback()
        return await self._reconcile(command, judgment, fingerprint, True)

    async def _record(
        self, command: ResolveJudgmentCommand, fingerprint: str
    ) -> tuple[HumanJudgmentData, bool]:
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            if project is None or project.created_by_id != self._actor.actor_id:
                raise ResourceNotFoundError(details={"resource": "project"})
            replay = await uow.events.get_by_idempotency_key(
                _RECORDED, f"human-judgment-command:{command.command_id}"
            )
            if replay is not None:
                if replay.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_id_reused"})
                judgment = await uow.judgments.get_judgment_exact(
                    _ref(replay.output_refs, "human_judgment")
                )
                if judgment is None:
                    raise ResourceConflictError(details={"reason": "judgment_replay_missing"})
                await uow.rollback()
                return judgment, True
            request = await uow.judgments.get_request_for_update(command.request_id)
            if request is None or request.workflow_run_id != command.workflow_run_id:
                raise ResourceNotFoundError(details={"resource": "judgment_request"})
            ambiguity = await uow.knowledge.get_exact(request.ambiguity_id)
            gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
            expected_gate = (
                "mapping.review_gate.completed"
                if command.repository_applicable
                else "mapping.review_gate.not_applicable"
            )
            if (
                ambiguity is None
                or ambiguity.object_type != "ambiguity"
                or ambiguity.review_status != "accepted"
                or ambiguity.lifecycle_status != "current"
            ):
                raise ResourceConflictError(details={"reason": "judgment_ambiguity_not_current"})
            if (
                gate is None
                or gate.workflow_run_id != command.workflow_run_id
                or gate.operation_type != expected_gate
            ):
                raise ResourceConflictError(details={"reason": "judgment_mapping_gate_invalid"})
            options = await uow.judgments.list_options(request.id)
            _validate(
                command,
                request_status=request.status,
                request_lifecycle_status=request.lifecycle_status,
                option_ids={item.id for item in options},
            )
            current = await uow.judgments.get_current_for_request(request.id)
            revision = command.expected_current_judgment_id is not None
            if revision:
                if current is None or current.id != command.expected_current_judgment_id:
                    raise ResourceConflictError(details={"reason": "stale_human_judgment"})
                await uow.judgments.supersede_judgment(current.id, expected_version=current.version)
                lineage_id, version = current.lineage_id, current.version + 1
            else:
                if current is not None or request.status != "open":
                    raise ResourceConflictError(details={"reason": "judgment_request_resolved"})
                lineage_id, version = _id(command.command_id, "lineage"), 1
            judgment = await uow.judgments.add_judgment(
                NewHumanJudgment(
                    id=_id(command.command_id, "judgment"),
                    project_id=command.project_id,
                    judgment_request_id=request.id,
                    lineage_id=lineage_id,
                    version=version,
                    selected_option_id=command.selected_option_id,
                    custom_decision=command.custom_decision,
                    rationale=command.rationale,
                    confidence=command.confidence,
                    created_by_id=self._actor.actor_id,
                    derived_from_id=None if current is None else current.id,
                    supersedes_id=None if current is None else current.id,
                )
            )
            if not revision:
                await uow.judgments.resolve_request(request.id)
                await uow.knowledge.update_ambiguity_resolution(
                    request.ambiguity_id,
                    expected_status="unresolved",
                    resolution_status="resolved",
                )
            dependencies = await uow.dependencies.list_dependencies("judgment_request", request.id)
            refs = [("knowledge_object", request.ambiguity_id, "validation_target", "blocking")]
            refs.extend(
                (item.dependency_type, item.dependency_id, item.dependency_role, item.criticality)
                for item in dependencies
            )
            unique_refs = tuple(dict.fromkeys(refs))
            for index, (kind, identifier, role, criticality) in enumerate(unique_refs):
                await uow.dependencies.append_dependency(
                    ArtifactDependencyData(
                        id=_id(judgment.id, f"dependency:{index}:{kind}:{identifier}"),
                        project_id=command.project_id,
                        artifact_type="human_judgment",
                        artifact_id=judgment.id,
                        dependency_type=kind,
                        dependency_id=identifier,
                        dependency_role=role,
                        criticality=criticality,
                    )
                )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="human_judgment" if current is not None else "knowledge_object",
                before_id=request.ambiguity_id if current is None else current.id,
                after_id=judgment.id,
                operation_type=(
                    "human_judgment.revised" if revision else "human_judgment.ambiguity_resolved"
                ),
            )
            inputs = _inputs(command, request.ambiguity_id)
            await uow.events.append(
                NewOperationEvent(
                    id=_id(judgment.id, "recorded"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_RECORDED,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=inputs,
                    output_refs=({"type": "human_judgment", "id": str(judgment.id)},),
                    reason=command.rationale,
                    idempotency_key=f"human-judgment-command:{command.command_id}",
                    input_fingerprint=fingerprint,
                )
            )
            await uow.events.append(
                NewOperationEvent(
                    id=_id(judgment.id, "pending"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_PENDING,
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=inputs,
                    output_refs=({"type": "human_judgment", "id": str(judgment.id)},),
                    reason="Human decision is durable; ordered model reassessment is pending.",
                    idempotency_key=f"human-judgment-reassessment:{judgment.id}",
                    input_fingerprint=fingerprint,
                )
            )
            await uow.commit()
            return judgment, False

    async def _reconcile(
        self,
        command: ResolveJudgmentCommand,
        judgment: HumanJudgmentData,
        fingerprint: str,
        replayed: bool,
    ) -> JudgmentResolutionResult:
        async with self._uow_factory(command.project_id) as uow:
            succeeded = await uow.events.get_by_idempotency_key(
                _SUCCEEDED, f"human-judgment-reassessment:{judgment.id}"
            )
            if succeeded is not None:
                await uow.rollback()
                return _result(judgment, succeeded, "succeeded", True, None)
            await uow.rollback()
        try:
            result = await self._reassessor.reconcile(command, judgment, fingerprint)
            async with self._uow_factory(command.project_id) as uow:
                current = await uow.judgments.get_current_for_request(command.request_id)
                await uow.rollback()
            if current is None or current.id != judgment.id:
                raise ResourceConflictError(details={"reason": "judgment_reassessment_stale"})
        except Exception as error:
            diagnostic = type(error).__name__
            await self._status_event(command, judgment, fingerprint, _FAILED, (), diagnostic)
            return JudgmentResolutionResult(
                judgment, "failed_retryable", None, None, None, None, replayed, diagnostic
            )
        outputs: tuple[dict[str, object], ...] = (
            {"type": "method_model", "id": str(result.method_model_id)},
            {"type": "method_model_assessment", "id": str(result.method_model_assessment_id)},
            {"type": "mental_model", "id": str(result.mental_model_id)},
            {"type": "mental_model_assessment", "id": str(result.mental_model_assessment_id)},
            {"type": "method_model_reassessment", "id": str(result.method_event_id)},
            {"type": "mental_model_reassessment", "id": str(result.mental_event_id)},
        )
        event = await self._status_event(
            command,
            judgment,
            fingerprint,
            _SUCCEEDED,
            outputs,
            "Ordered model reassessment succeeded.",
        )
        return _result(judgment, event, "succeeded", replayed, None)

    async def _status_event(
        self,
        command: ResolveJudgmentCommand,
        judgment: HumanJudgmentData,
        fingerprint: str,
        operation: str,
        outputs: tuple[dict[str, object], ...],
        reason: str,
    ) -> OperationEventData:
        async with self._uow_factory(command.project_id) as uow:
            event = await uow.events.append(
                NewOperationEvent(
                    id=_id(judgment.id, operation),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=operation,
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=({"type": "human_judgment", "id": str(judgment.id)},),
                    output_refs=outputs,
                    reason=reason,
                    idempotency_key=f"human-judgment-reassessment:{judgment.id}",
                    input_fingerprint=fingerprint,
                )
            )
            await uow.commit()
            return event


def _validate(
    command: ResolveJudgmentCommand,
    *,
    request_status: str,
    request_lifecycle_status: str,
    option_ids: set[UUID],
) -> None:
    if not command.confirmed:
        raise ResourceConflictError(details={"reason": "human_judgment_confirmation_required"})
    if request_lifecycle_status != "current":
        raise ResourceConflictError(details={"reason": "judgment_request_not_current"})
    if (command.selected_option_id is None) == (command.custom_decision is None):
        raise ResourceConflictError(details={"reason": "exactly_one_judgment_decision_required"})
    if command.selected_option_id is not None and command.selected_option_id not in option_ids:
        raise ResourceConflictError(details={"reason": "judgment_option_invalid"})
    if (
        not command.rationale.strip()
        or not command.method_impact_rationale.strip()
        or len(command.rationale) > 4_000
        or len(command.method_impact_rationale) > 4_000
    ):
        raise ResourceConflictError(details={"reason": "judgment_rationale_required"})
    if command.custom_decision is not None and (
        not command.custom_decision.strip() or len(command.custom_decision) > 4_000
    ):
        raise ResourceConflictError(details={"reason": "custom_judgment_invalid"})
    if command.confidence < 0 or command.confidence > 1:
        raise ResourceConflictError(details={"reason": "judgment_confidence_invalid"})
    if command.expected_current_judgment_id is None and request_status != "open":
        raise ResourceConflictError(details={"reason": "judgment_request_resolved"})


def _inputs(command: ResolveJudgmentCommand, ambiguity_id: UUID) -> tuple[dict[str, object], ...]:
    return (
        {"type": "judgment_request", "id": str(command.request_id)},
        {"type": "knowledge_object", "id": str(ambiguity_id)},
        {"type": "mapping_review_gate", "id": str(command.mapping_review_gate_event_id)},
        {
            "type": "method_impact",
            "affected": command.method_model_affected,
            "rationale": command.method_impact_rationale,
        },
    )


def _fingerprint(command: ResolveJudgmentCommand) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "policy": "rf-human-judgment-v1",
                "project_id": str(command.project_id),
                "request_id": str(command.request_id),
                "workflow_run_id": str(command.workflow_run_id),
                "selected_option_id": None
                if command.selected_option_id is None
                else str(command.selected_option_id),
                "custom_decision": command.custom_decision,
                "rationale": command.rationale,
                "confidence": str(command.confidence),
                "method_model_affected": command.method_model_affected,
                "method_impact_rationale": command.method_impact_rationale,
                "mapping_review_gate_event_id": str(command.mapping_review_gate_event_id),
                "repository_applicable": command.repository_applicable,
                "confirmed": command.confirmed,
                "expected_current_judgment_id": None
                if command.expected_current_judgment_id is None
                else str(command.expected_current_judgment_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _ref(refs: tuple[dict[str, object], ...], kind: str) -> UUID:
    values = [UUID(str(item["id"])) for item in refs if item.get("type") == kind and item.get("id")]
    if len(values) != 1:
        raise ResourceConflictError(details={"reason": "judgment_reference_invalid"})
    return values[0]


def _has_ref(event: OperationEventData, identifier: UUID) -> bool:
    return any(
        item.get("type") == "human_judgment" and item.get("id") == str(identifier)
        for item in (*event.input_refs, *event.output_refs)
    )


def _id(seed: UUID, value: str) -> UUID:
    return uuid5(_NAMESPACE, f"{seed}:{value}")


def _result(
    judgment: HumanJudgmentData,
    event: OperationEventData,
    status: Literal["pending", "succeeded", "failed_retryable"],
    replayed: bool,
    error: str | None,
) -> JudgmentResolutionResult:
    refs = {
        str(item.get("type")): UUID(str(item["id"])) for item in event.output_refs if item.get("id")
    }
    return JudgmentResolutionResult(
        judgment,
        status,
        refs.get("method_model"),
        refs.get("method_model_assessment"),
        refs.get("mental_model"),
        refs.get("mental_model_assessment"),
        replayed,
        error,
    )
