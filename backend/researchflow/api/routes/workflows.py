"""Project-scoped polling and manual workflow recovery endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from researchflow.api.dependencies import (
    ActorDependency,
    UnitOfWorkFactoryDependency,
    WorkflowCheckpointStoreDependency,
    WorkflowLauncherDependency,
)
from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.api.schemas.workflows import (
    CreateWorkflowRunRequest,
    ResolveWorkflowInterruptRequest,
    ResumeWorkflowRequest,
    WorkflowInterruptResponse,
    WorkflowMutationRequest,
    WorkflowRunCommandResponse,
    WorkflowRunResponse,
)
from researchflow.application.workflow_runs import (
    CreateWorkflowRunCommand,
    ResolveWorkflowInterruptCommand,
    WorkflowRunService,
)

router = APIRouter(prefix="/projects/{project_id}/workflows", tags=["workflows"])


def _service(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> WorkflowRunService:
    return WorkflowRunService(
        actor=actor,
        uow_factory=uow_factory,
        launcher=launcher,
        checkpoints=checkpoints,
    )


@router.post("", response_model=WorkflowRunCommandResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_workflow(
    project_id: UUID,
    body: CreateWorkflowRunRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> WorkflowRunCommandResponse:
    result = await _service(actor, uow_factory, launcher, checkpoints).create(
        CreateWorkflowRunCommand(
            project_id=project_id,
            workflow_type=body.workflow_type,
            idempotency_key=body.idempotency_key,
            primary_research_question_id=body.primary_research_question_id,
            primary_source_processing_run_id=body.primary_source_processing_run_id,
            primary_question_source_relationship_id=(body.primary_question_source_relationship_id),
            paper_repository_relationship_id=body.paper_repository_relationship_id,
            repository_snapshot_id=body.repository_snapshot_id,
            request_id=request_id,
        )
    )
    return WorkflowRunCommandResponse(
        workflow=WorkflowRunResponse.model_validate(result.workflow, from_attributes=True),
        event=OperationEventResponse.model_validate(result.event, from_attributes=True),
        replayed=result.replayed,
    )


@router.get("/{workflow_run_id}", response_model=WorkflowRunResponse)
async def workflow_status(
    project_id: UUID,
    workflow_run_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> WorkflowRunResponse:
    value = await _service(actor, uow_factory, launcher, checkpoints).get(
        project_id, workflow_run_id
    )
    return WorkflowRunResponse.model_validate(value, from_attributes=True)


@router.get("/{workflow_run_id}/events", response_model=tuple[OperationEventResponse, ...])
async def workflow_events(
    project_id: UUID,
    workflow_run_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> tuple[OperationEventResponse, ...]:
    values = await _service(actor, uow_factory, launcher, checkpoints).events(
        project_id, workflow_run_id
    )
    return tuple(
        OperationEventResponse.model_validate(item, from_attributes=True) for item in values
    )


@router.get("/{workflow_run_id}/interrupts", response_model=tuple[WorkflowInterruptResponse, ...])
async def workflow_interrupts(
    project_id: UUID,
    workflow_run_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> tuple[WorkflowInterruptResponse, ...]:
    values = await _service(actor, uow_factory, launcher, checkpoints).interrupts(
        project_id, workflow_run_id
    )
    return tuple(
        WorkflowInterruptResponse.model_validate(item, from_attributes=True) for item in values
    )


@router.post("/{workflow_run_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_workflow(
    project_id: UUID,
    workflow_run_id: UUID,
    body: WorkflowMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> WorkflowRunResponse:
    value = await _service(actor, uow_factory, launcher, checkpoints).cancel(
        project_id, workflow_run_id, expected_lock_version=body.expected_lock_version
    )
    return WorkflowRunResponse.model_validate(value, from_attributes=True)


@router.post("/{workflow_run_id}/retry", response_model=WorkflowRunResponse)
async def retry_workflow(
    project_id: UUID,
    workflow_run_id: UUID,
    body: WorkflowMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> WorkflowRunResponse:
    value = await _service(actor, uow_factory, launcher, checkpoints).retry(
        project_id, workflow_run_id, expected_lock_version=body.expected_lock_version
    )
    return WorkflowRunResponse.model_validate(value, from_attributes=True)


@router.post(
    "/{workflow_run_id}/interrupts/{interrupt_id}/resolve",
    response_model=WorkflowInterruptResponse,
)
async def resolve_interrupt(
    project_id: UUID,
    workflow_run_id: UUID,
    interrupt_id: UUID,
    body: ResolveWorkflowInterruptRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> WorkflowInterruptResponse:
    value = await _service(actor, uow_factory, launcher, checkpoints).resolve_interrupt(
        ResolveWorkflowInterruptCommand(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            interrupt_id=interrupt_id,
            expected_lock_version=body.expected_lock_version,
            expected_context_fingerprint=body.expected_context_fingerprint,
            resolution_event_id=body.resolution_event_id,
            request_id=request_id,
        )
    )
    return WorkflowInterruptResponse.model_validate(value, from_attributes=True)


@router.post("/{workflow_run_id}/resume", response_model=WorkflowRunResponse)
async def resume_workflow(
    project_id: UUID,
    workflow_run_id: UUID,
    body: ResumeWorkflowRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    launcher: WorkflowLauncherDependency,
    checkpoints: WorkflowCheckpointStoreDependency,
) -> WorkflowRunResponse:
    value = await _service(actor, uow_factory, launcher, checkpoints).resume(
        project_id,
        workflow_run_id,
        body.interrupt_id,
        expected_workflow_lock_version=body.expected_workflow_lock_version,
        expected_interrupt_lock_version=body.expected_interrupt_lock_version,
        expected_context_fingerprint=body.expected_context_fingerprint,
    )
    return WorkflowRunResponse.model_validate(value, from_attributes=True)
