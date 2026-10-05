"""Product-visible workflow lifecycle and channel-neutral interrupt commands."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.actor import ActorContext
from researchflow.errors import ExternalServiceError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewWorkflowInterrupt,
    NewWorkflowRun,
    OperationEventData,
    WorkflowInterruptData,
    WorkflowRunData,
)
from researchflow.ports.unit_of_work import UnitOfWorkFactory
from researchflow.ports.workflow_launcher import WorkflowCheckpointStore, WorkflowLauncher
from researchflow.workflows.state import (
    PROJECT_ANALYSIS_GRAPH_VERSION,
    ProjectWorkflowState,
    WorkflowControlStatus,
    WorkflowStage,
    node_idempotency_key,
)

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/workflows/v1")


@dataclass(frozen=True, slots=True)
class CreateWorkflowRunCommand:
    project_id: UUID
    workflow_type: str
    idempotency_key: str
    primary_research_question_id: UUID
    primary_source_processing_run_id: UUID | None = None
    primary_question_source_relationship_id: UUID | None = None
    paper_repository_relationship_id: UUID | None = None
    repository_snapshot_id: UUID | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveWorkflowInterruptCommand:
    project_id: UUID
    workflow_run_id: UUID
    interrupt_id: UUID
    expected_lock_version: int
    expected_context_fingerprint: str
    resolution_event_id: UUID
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateWorkflowInterruptCommand:
    project_id: UUID
    workflow_run_id: UUID
    interrupt_kind: str
    expected_context_fingerprint: str
    target_refs: tuple[dict[str, object], ...]
    command_id: UUID
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    workflow: WorkflowRunData
    event: OperationEventData
    replayed: bool


class WorkflowLifecycleNodeContext:
    """Synchronize canonical run lifecycle before LangGraph can checkpoint a node."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def commit_stage(
        self, state: ProjectWorkflowState, stage: WorkflowStage
    ) -> ProjectWorkflowState:
        target_status = {
            WorkflowStage.ENTRY: WorkflowControlStatus.RUNNING,
            WorkflowStage.FINAL: WorkflowControlStatus.COMPLETED,
            WorkflowStage.ERROR: WorkflowControlStatus.FAILED,
        }.get(stage, WorkflowControlStatus.RUNNING)
        key = node_idempotency_key(
            workflow_run_id=state.workflow_run_id,
            stage=stage.value,
            input_fingerprint=state.node_input_fingerprint,
            graph_version=state.graph_version,
        )
        async with self._uow_factory(state.project_id) as uow:
            workflow = await uow.workflows.get_for_update(state.workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            _validate_state_identity(workflow, state)
            prior = await uow.events.get_by_idempotency_key("workflow.node_committed", key)
            if prior is not None:
                if prior.input_fingerprint != state.node_input_fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "node_idempotency_fingerprint_mismatch"}
                    )
                replay_statuses = {target_status.value}
                if stage is WorkflowStage.PREPARE_SCIENTIFIC_REVIEW:
                    replay_statuses.add(WorkflowControlStatus.WAITING_FOR_REVIEW.value)
                if workflow.status == WorkflowControlStatus.QUEUED.value:
                    if workflow.current_stage != stage.value:
                        raise ResourceConflictError(
                            details={"reason": "workflow_event_state_diverged"}
                        )
                    await uow.workflows.update_progress(
                        workflow.id,
                        expected_lock_version=workflow.lock_version,
                        status=target_status.value,
                        current_stage=stage.value,
                        current_method_model_id=state.current_method_model_id,
                        current_mental_model_id=state.current_mental_model_id,
                    )
                elif (
                    workflow.status not in replay_statuses or workflow.current_stage != stage.value
                ):
                    raise ResourceConflictError(details={"reason": "workflow_event_state_diverged"})
                await uow.commit()
                return state.model_copy(
                    update={
                        "stage": stage,
                        "status": target_status,
                        "node_sequence": state.node_sequence + 1,
                    }
                )
            allowed = (
                {"queued", "failed"} if stage is WorkflowStage.ENTRY else {"running", "queued"}
            )
            if workflow.status not in allowed:
                raise ResourceConflictError(details={"reason": "workflow_transition_invalid"})
            updated = await uow.workflows.update_progress(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status=target_status.value,
                current_stage=stage.value,
                current_method_model_id=state.current_method_model_id,
                current_mental_model_id=state.current_mental_model_id,
                error_code=state.error_code if stage is WorkflowStage.ERROR else None,
                error_detail=(
                    {"graph_version": state.graph_version} if stage is WorkflowStage.ERROR else None
                ),
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"node:{key}"),
                    project_id=state.project_id,
                    workflow_run_id=state.workflow_run_id,
                    operation_type="workflow.node_committed",
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=(
                        {
                            "workflow_run_id": str(state.workflow_run_id),
                            "stage": stage.value,
                            "graph_version": state.graph_version,
                        },
                    ),
                    output_refs=(
                        {
                            "workflow_run_id": str(updated.id),
                            "status": updated.status,
                            "lock_version": updated.lock_version,
                        },
                    ),
                    reason="Canonical WorkflowRun transition committed before checkpoint advance.",
                    idempotency_key=key,
                    input_fingerprint=state.node_input_fingerprint,
                )
            )
            await uow.commit()
        return state.model_copy(
            update={
                "stage": stage,
                "status": target_status,
                "node_sequence": state.node_sequence + 1,
            }
        )

    async def fail(self, state: ProjectWorkflowState, *, error_code: str) -> None:
        await self.fail_run(state.project_id, state.workflow_run_id, error_code)

    async def fail_run(self, project_id: UUID, workflow_run_id: UUID, error_code: str) -> None:
        async with self._uow_factory(project_id) as uow:
            workflow = await uow.workflows.get_for_update(workflow_run_id)
            if workflow is None or workflow.status in {"completed", "cancelled"}:
                await uow.rollback()
                return
            updated = await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="failed",
                current_stage=workflow.current_stage,
                error_code=error_code,
                error_detail={"graph_version": workflow.graph_version},
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"failure:{workflow_run_id}:{updated.lock_version}"),
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    operation_type="workflow.failed",
                    created_by_type="system",
                    created_by_id=None,
                    output_refs=({"error_code": error_code},),
                    reason="Workflow shell execution failed; canonical scientific state is unchanged.",
                )
            )
            await uow.commit()

    async def create_interrupt(
        self, command: CreateWorkflowInterruptCommand
    ) -> WorkflowInterruptData:
        """Persist the graph-owned interrupt after its review scope has committed."""
        if command.interrupt_kind != "scientific_review":
            raise ResourceConflictError(details={"reason": "workflow_interrupt_kind_invalid"})
        _validate_target_refs(command.target_refs)
        interrupt_id = uuid5(_NAMESPACE, f"interrupt:{command.command_id}")
        async with self._uow_factory(command.project_id) as uow:
            existing = await uow.workflows.get_interrupt_exact(interrupt_id)
            if existing is not None:
                if (
                    existing.workflow_run_id != command.workflow_run_id
                    or existing.expected_context_fingerprint != command.expected_context_fingerprint
                    or existing.target_refs != command.target_refs
                ):
                    raise ResourceConflictError(
                        details={"reason": "workflow_interrupt_command_reused"}
                    )
                workflow = await uow.workflows.get_for_update(command.workflow_run_id)
                if workflow is None or existing.status != "pending":
                    raise ResourceConflictError(
                        details={"reason": "workflow_interrupt_replay_invalid"}
                    )
                if workflow.status == "running":
                    await uow.workflows.update_status(
                        workflow.id,
                        expected_lock_version=workflow.lock_version,
                        status="waiting_for_review",
                        current_stage=workflow.current_stage,
                    )
                elif workflow.status != "waiting_for_review":
                    raise ResourceConflictError(
                        details={"reason": "workflow_interrupt_replay_invalid"}
                    )
                await uow.commit()
                return existing
            workflow = await uow.workflows.get_for_update(command.workflow_run_id)
            if workflow is None or workflow.status != "running":
                raise ResourceConflictError(details={"reason": "workflow_not_interruptible"})
            interrupt = await uow.workflows.add_interrupt(
                NewWorkflowInterrupt(
                    id=interrupt_id,
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    interrupt_kind="scientific_review",
                    expected_context_fingerprint=command.expected_context_fingerprint,
                    target_refs=command.target_refs,
                    created_by_type="system",
                    created_by_id=None,
                )
            )
            await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="waiting_for_review",
                current_stage=workflow.current_stage,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"interrupt-created:{command.command_id}"),
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.interrupted",
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=command.target_refs,
                    output_refs=({"workflow_interrupt_id": str(interrupt.id)},),
                    reason="Workflow paused at the scientific human-review boundary.",
                    idempotency_key=str(command.command_id),
                    input_fingerprint=command.expected_context_fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return interrupt


class WorkflowRunService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        launcher: WorkflowLauncher,
        checkpoints: WorkflowCheckpointStore,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._launcher = launcher
        self._checkpoints = checkpoints

    async def create(self, command: CreateWorkflowRunCommand) -> WorkflowRunResult:
        if not command.idempotency_key.strip():
            raise ResourceConflictError(details={"reason": "idempotency_key_required"})
        fingerprint = _fingerprint(command)
        workflow_id = uuid5(
            _NAMESPACE,
            f"run:{command.project_id}:{command.workflow_type}:{command.idempotency_key}",
        )
        event_id = uuid5(_NAMESPACE, f"run-created:{workflow_id}")
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            if project is None or project.created_by_id != self._actor.actor_id:
                raise ResourceNotFoundError(details={"resource": "project"})
            if command.workflow_type != "full_analysis":
                raise ResourceConflictError(details={"reason": "workflow_type_unsupported"})
            question = await uow.research_questions.get_exact(command.primary_research_question_id)
            if question is None or question.id != project.primary_research_question_id:
                raise ResourceConflictError(details={"reason": "primary_question_not_current"})
            await _validate_input_refs(uow, command)
            prior = await uow.events.get_by_idempotency_key(
                "workflow.run_created", command.idempotency_key
            )
            if prior is not None:
                if prior.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                existing = await uow.workflows.get_exact(workflow_id)
                if existing is None:
                    raise ResourceConflictError(details={"reason": "workflow_event_state_diverged"})
                await uow.commit()
                return WorkflowRunResult(existing, prior, True)
            if await uow.workflows.get_active_full_analysis_for_update() is not None:
                raise ResourceConflictError(details={"reason": "active_workflow_exists"})
            workflow = await uow.workflows.add(
                NewWorkflowRun(
                    id=workflow_id,
                    project_id=command.project_id,
                    workflow_type=command.workflow_type,
                    status="queued",
                    current_stage=None,
                    langgraph_thread_id=str(workflow_id),
                    primary_research_question_id=command.primary_research_question_id,
                    primary_source_processing_run_id=command.primary_source_processing_run_id,
                    primary_question_source_relationship_id=(
                        command.primary_question_source_relationship_id
                    ),
                    paper_repository_relationship_id=command.paper_repository_relationship_id,
                    repository_snapshot_id=command.repository_snapshot_id,
                )
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=event_id,
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.run_created",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"workflow_type": command.workflow_type},),
                    output_refs=({"workflow_run_id": str(workflow.id)},),
                    reason="Created a durable project-analysis workflow shell.",
                    idempotency_key=command.idempotency_key,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        try:
            await self._launcher.launch(_state_from_workflow(workflow, fingerprint))
        except Exception as error:
            await self._record_launch_failure(command.project_id, workflow.id)
            raise ExternalServiceError(
                details={"reason": "workflow_launch_failed"}, cause=error
            ) from error
        return WorkflowRunResult(workflow, event, False)

    async def _record_launch_failure(self, project_id: UUID, workflow_run_id: UUID) -> None:
        async with self._uow_factory(project_id) as uow:
            workflow = await uow.workflows.get_for_update(workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="failed",
                current_stage=workflow.current_stage,
                error_code="workflow.launch_failed_before_checkpoint",
                error_detail={"graph_version": workflow.graph_version},
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"launch-failed:{workflow.id}"),
                    project_id=project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.launch_failed",
                    created_by_type="system",
                    created_by_id=None,
                    output_refs=({"error_code": "workflow.launch_failed_before_checkpoint"},),
                    reason="Launch failed after the WorkflowRun creation transaction committed.",
                )
            )
            await uow.commit()

    async def get(self, project_id: UUID, workflow_run_id: UUID) -> WorkflowRunData:
        async with self._uow_factory(project_id) as uow:
            await _require_owned_project(uow, self._actor)
            workflow = await uow.workflows.get_exact(workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            await uow.rollback()
            return workflow

    async def events(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> tuple[OperationEventData, ...]:
        await self.get(project_id, workflow_run_id)
        async with self._uow_factory(project_id) as uow:
            events = await uow.events.list_for_workflow(workflow_run_id)
            await uow.rollback()
            return events

    async def interrupts(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> tuple[WorkflowInterruptData, ...]:
        await self.get(project_id, workflow_run_id)
        async with self._uow_factory(project_id) as uow:
            values = await uow.workflows.list_interrupts(workflow_run_id)
            await uow.rollback()
            return values

    async def create_interrupt(
        self, command: CreateWorkflowInterruptCommand
    ) -> WorkflowInterruptData:
        if command.interrupt_kind not in {"scientific_review", "human_judgment"}:
            raise ResourceConflictError(details={"reason": "workflow_interrupt_kind_invalid"})
        if len(command.expected_context_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in command.expected_context_fingerprint
        ):
            raise ResourceConflictError(
                details={"reason": "workflow_interrupt_fingerprint_invalid"}
            )
        _validate_target_refs(command.target_refs)
        interrupt_id = uuid5(_NAMESPACE, f"interrupt:{command.command_id}")
        async with self._uow_factory(command.project_id) as uow:
            await _require_owned_project(uow, self._actor)
            existing = await uow.workflows.get_interrupt_exact(interrupt_id)
            if existing is not None:
                if (
                    existing.workflow_run_id != command.workflow_run_id
                    or existing.interrupt_kind != command.interrupt_kind
                    or existing.expected_context_fingerprint != command.expected_context_fingerprint
                    or existing.target_refs != command.target_refs
                ):
                    raise ResourceConflictError(
                        details={"reason": "workflow_interrupt_command_reused"}
                    )
                await uow.commit()
                return existing
            workflow = await uow.workflows.get_for_update(command.workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            if workflow.status != "running":
                raise ResourceConflictError(details={"reason": "workflow_not_interruptible"})
            interrupt = await uow.workflows.add_interrupt(
                NewWorkflowInterrupt(
                    id=interrupt_id,
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    interrupt_kind=command.interrupt_kind,
                    expected_context_fingerprint=command.expected_context_fingerprint,
                    target_refs=command.target_refs,
                    created_by_type="system",
                    created_by_id=None,
                )
            )
            waiting_status = (
                "waiting_for_review"
                if command.interrupt_kind == "scientific_review"
                else "waiting_for_judgment"
            )
            await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status=waiting_status,
                current_stage=workflow.current_stage,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"interrupt-created:{command.command_id}"),
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.interrupted",
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=command.target_refs,
                    output_refs=({"workflow_interrupt_id": str(interrupt.id)},),
                    reason="Workflow paused at a channel-neutral human authority boundary.",
                    idempotency_key=str(command.command_id),
                    input_fingerprint=command.expected_context_fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return interrupt

    async def cancel(
        self, project_id: UUID, workflow_run_id: UUID, *, expected_lock_version: int
    ) -> WorkflowRunData:
        async with self._uow_factory(project_id) as uow:
            await _require_owned_project(uow, self._actor)
            workflow = await uow.workflows.get_for_update(workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            if workflow.lock_version != expected_lock_version or workflow.status not in {
                "queued",
                "running",
                "waiting_for_review",
                "waiting_for_judgment",
            }:
                raise ResourceConflictError(details={"reason": "workflow_not_cancellable"})
            updated = await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="cancelled",
                current_stage=workflow.current_stage,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"cancel:{workflow.id}:{workflow.lock_version}"),
                    project_id=project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.cancelled",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    output_refs=({"status": "cancelled"},),
                    reason="Human cancelled the workflow shell.",
                )
            )
            await uow.commit()
        await self._launcher.cancel(workflow_run_id)
        return updated

    async def retry(
        self, project_id: UUID, workflow_run_id: UUID, *, expected_lock_version: int
    ) -> WorkflowRunData:
        async with self._uow_factory(project_id) as uow:
            await _require_owned_project(uow, self._actor)
            workflow = await uow.workflows.get_for_update(workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            if workflow.lock_version != expected_lock_version or workflow.status != "failed":
                raise ResourceConflictError(details={"reason": "workflow_not_retryable"})
            if workflow.graph_version != PROJECT_ANALYSIS_GRAPH_VERSION:
                raise ResourceConflictError(details={"reason": "workflow_graph_version_mismatch"})
            if not await self._checkpoints.has_checkpoint(
                thread_id=workflow.langgraph_thread_id,
                namespace=workflow.checkpoint_namespace,
            ):
                raise ResourceConflictError(details={"reason": "workflow_checkpoint_missing"})
            updated = await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="queued",
                current_stage=workflow.current_stage,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"retry:{workflow.id}:{workflow.lock_version}"),
                    project_id=project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.retry_requested",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    reason="Authorized deterministic retry from the durable checkpoint.",
                )
            )
            await uow.commit()
        await self._launcher.retry(
            project_id=project_id,
            workflow_run_id=workflow.id,
            thread_id=workflow.langgraph_thread_id,
            namespace=workflow.checkpoint_namespace,
        )
        return updated

    async def resolve_interrupt(
        self, command: ResolveWorkflowInterruptCommand
    ) -> WorkflowInterruptData:
        async with self._uow_factory(command.project_id) as uow:
            await _require_owned_project(uow, self._actor)
            interrupt = await uow.workflows.get_interrupt_for_update(command.interrupt_id)
            if interrupt is None or interrupt.workflow_run_id != command.workflow_run_id:
                raise ResourceNotFoundError(details={"resource": "workflow_interrupt"})
            if (
                interrupt.status == "resolved"
                and interrupt.resolution_event_id == command.resolution_event_id
                and interrupt.expected_context_fingerprint == command.expected_context_fingerprint
                and interrupt.lock_version == command.expected_lock_version + 1
            ):
                await uow.commit()
                return interrupt
            if (
                interrupt.lock_version != command.expected_lock_version
                or interrupt.status != "pending"
                or interrupt.expected_context_fingerprint != command.expected_context_fingerprint
            ):
                raise ResourceConflictError(details={"reason": "workflow_interrupt_stale"})
            event = await uow.events.get_exact(command.resolution_event_id)
            if event is None or event.workflow_run_id != command.workflow_run_id:
                raise ResourceConflictError(details={"reason": "resolution_event_invalid"})
            if interrupt.interrupt_kind == "scientific_review":
                _validate_scientific_review_resolution(interrupt, event)
            elif event.input_fingerprint != interrupt.expected_context_fingerprint:
                raise ResourceConflictError(details={"reason": "resolution_event_invalid"})
            resolved = await uow.workflows.update_interrupt(
                interrupt.id,
                expected_lock_version=interrupt.lock_version,
                status="resolved",
                resolution_event_id=event.id,
            )
            await uow.commit()
            return resolved

    async def resume(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        interrupt_id: UUID,
        *,
        expected_workflow_lock_version: int,
        expected_interrupt_lock_version: int,
        expected_context_fingerprint: str,
    ) -> WorkflowRunData:
        async with self._uow_factory(project_id) as uow:
            await _require_owned_project(uow, self._actor)
            workflow = await uow.workflows.get_for_update(workflow_run_id)
            interrupt = await uow.workflows.get_interrupt_for_update(interrupt_id)
            if workflow is None or interrupt is None or interrupt.workflow_run_id != workflow.id:
                raise ResourceNotFoundError(details={"resource": "workflow_resume_state"})
            if (
                interrupt.status == "resumed"
                and interrupt.expected_context_fingerprint == expected_context_fingerprint
                and interrupt.lock_version == expected_interrupt_lock_version + 1
                and workflow.lock_version >= expected_workflow_lock_version + 1
            ):
                await uow.commit()
                return workflow
            if (
                workflow.lock_version != expected_workflow_lock_version
                or interrupt.lock_version != expected_interrupt_lock_version
                or interrupt.status != "resolved"
                or interrupt.expected_context_fingerprint != expected_context_fingerprint
                or workflow.status not in {"waiting_for_review", "waiting_for_judgment"}
            ):
                raise ResourceConflictError(details={"reason": "workflow_resume_stale"})
            if workflow.graph_version != PROJECT_ANALYSIS_GRAPH_VERSION:
                raise ResourceConflictError(details={"reason": "workflow_graph_version_mismatch"})
            if not await self._checkpoints.has_checkpoint(
                thread_id=workflow.langgraph_thread_id,
                namespace=workflow.checkpoint_namespace,
            ):
                raise ResourceConflictError(details={"reason": "workflow_checkpoint_missing"})
            updated = await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="running",
                current_stage=workflow.current_stage,
            )
            await uow.workflows.update_interrupt(
                interrupt.id,
                expected_lock_version=interrupt.lock_version,
                status="resumed",
                resolution_event_id=interrupt.resolution_event_id,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"resume:{interrupt.id}:{interrupt.lock_version}"),
                    project_id=project_id,
                    workflow_run_id=workflow.id,
                    operation_type="workflow.resumed",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"workflow_interrupt_id": str(interrupt.id)},),
                    reason="Authorized resume after durable interrupt resolution.",
                )
            )
            await uow.commit()
        await self._launcher.resume(
            project_id=project_id,
            workflow_run_id=workflow.id,
            thread_id=workflow.langgraph_thread_id,
            namespace=workflow.checkpoint_namespace,
            interrupt_id=interrupt.id,
            context_fingerprint=interrupt.expected_context_fingerprint,
        )
        return updated


async def _require_owned_project(uow: object, actor: ActorContext) -> None:
    project = await uow.projects.get()  # type: ignore[attr-defined]
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})


async def _validate_input_refs(uow: object, command: CreateWorkflowRunCommand) -> None:
    if command.primary_source_processing_run_id is not None:
        run = await uow.sources.get_processing_run_exact(  # type: ignore[attr-defined]
            command.primary_source_processing_run_id
        )
        if run is None or run.status != "succeeded":
            raise ResourceConflictError(details={"reason": "processing_run_not_eligible"})
    if command.primary_question_source_relationship_id is not None:
        relationship = await uow.sources.get_question_source_exact(  # type: ignore[attr-defined]
            command.primary_question_source_relationship_id
        )
        if relationship is None or relationship.research_question_id != (
            command.primary_research_question_id
        ):
            raise ResourceConflictError(details={"reason": "question_source_mismatch"})
    if (command.paper_repository_relationship_id is None) != (
        command.repository_snapshot_id is None
    ):
        raise ResourceConflictError(details={"reason": "repository_context_pair_incomplete"})
    if command.paper_repository_relationship_id is not None:
        relationship = await uow.repositories.get_relationship_exact(  # type: ignore[attr-defined]
            command.paper_repository_relationship_id
        )
        if (
            relationship is None
            or relationship.repository_snapshot_id != command.repository_snapshot_id
        ):
            raise ResourceConflictError(details={"reason": "repository_context_mismatch"})


def _fingerprint(command: CreateWorkflowRunCommand) -> str:
    payload = {
        "graph_version": PROJECT_ANALYSIS_GRAPH_VERSION,
        "paper_repository_relationship_id": command.paper_repository_relationship_id,
        "primary_question_source_relationship_id": command.primary_question_source_relationship_id,
        "primary_research_question_id": command.primary_research_question_id,
        "primary_source_processing_run_id": command.primary_source_processing_run_id,
        "project_id": command.project_id,
        "repository_snapshot_id": command.repository_snapshot_id,
        "workflow_type": command.workflow_type,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _state_from_workflow(workflow: WorkflowRunData, input_fingerprint: str) -> ProjectWorkflowState:
    return ProjectWorkflowState(
        workflow_run_id=workflow.id,
        project_id=workflow.project_id,
        primary_research_question_id=workflow.primary_research_question_id,
        primary_source_processing_run_id=workflow.primary_source_processing_run_id,
        primary_question_source_relationship_id=workflow.primary_question_source_relationship_id,
        paper_repository_relationship_id=workflow.paper_repository_relationship_id,
        repository_snapshot_id=workflow.repository_snapshot_id,
        current_method_model_id=workflow.current_method_model_id,
        current_mental_model_id=workflow.current_mental_model_id,
        pending_judgment_request_id=workflow.pending_judgment_request_id,
        graph_version=workflow.graph_version,
        node_input_fingerprint=input_fingerprint,
    )


def _validate_state_identity(workflow: WorkflowRunData, state: ProjectWorkflowState) -> None:
    if (
        workflow.project_id != state.project_id
        or workflow.primary_research_question_id != state.primary_research_question_id
        or workflow.graph_version != state.graph_version
    ):
        raise ResourceConflictError(details={"reason": "workflow_checkpoint_identity_mismatch"})


def _validate_target_refs(refs: tuple[dict[str, object], ...]) -> None:
    if len(refs) > 128:
        raise ResourceConflictError(details={"reason": "workflow_interrupt_targets_too_many"})
    for reference in refs:
        if set(reference) - {"type", "id", "version"}:
            raise ResourceConflictError(
                details={"reason": "workflow_interrupt_target_payload_forbidden"}
            )
        if not isinstance(reference.get("type"), str) or not isinstance(reference.get("id"), str):
            raise ResourceConflictError(details={"reason": "workflow_interrupt_target_invalid"})
        target_id = reference["id"]
        try:
            UUID(str(target_id))
        except ValueError as error:
            raise ResourceConflictError(
                details={"reason": "workflow_interrupt_target_invalid"}
            ) from error
        if "version" in reference and not isinstance(reference["version"], int):
            raise ResourceConflictError(details={"reason": "workflow_interrupt_target_invalid"})


def _validate_scientific_review_resolution(
    interrupt: WorkflowInterruptData, event: OperationEventData
) -> None:
    if event.operation_type != "paper_scientific_review_completed":
        raise ResourceConflictError(details={"reason": "resolution_event_invalid"})
    target_ids = {
        str(item.get("id")) for item in interrupt.target_refs if item.get("type") == "method_model"
    }
    accepted_ids = {
        str(item.get("id"))
        for item in event.output_refs
        if item.get("type") == "accepted_method_model"
    }
    if len(target_ids) != 1 or accepted_ids != target_ids:
        raise ResourceConflictError(details={"reason": "resolution_event_invalid"})
