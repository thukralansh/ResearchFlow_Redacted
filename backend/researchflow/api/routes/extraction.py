"""Synchronous bounded proposition extraction and persisted status routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from researchflow.api.dependencies import (
    ActorDependency,
    PromptRegistryDependency,
    SettingsDependency,
    StructuredGenerationProviderDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.model_policy import configured_model_policy
from researchflow.api.schemas.extraction_runs import (
    ExecuteExtractionRequest,
    ExtractionBatchResponse,
    ExtractionExecutionResponse,
    ExtractionGenerationAttemptResponse,
    ExtractionRequestResponse,
    ExtractionStatusResponse,
    StartExtractionRequest,
)
from researchflow.application.extraction import (
    ExtractionRequestView,
    ExtractionSelection,
    ExtractionService,
    ExtractPropositionsCommand,
)
from researchflow.application.extraction_execution import (
    ExecuteExtractionCommand,
    ExtractionExecutionService,
)
from researchflow.application.generation import StructuredGenerationService

router = APIRouter(prefix="/projects/{project_id}/extractions", tags=["extractions"])


def _service(
    *,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    max_context_bytes: int,
) -> ExtractionService:
    return ExtractionService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=max_context_bytes,
        ),
    )


def _batch_response(value: object) -> ExtractionBatchResponse:
    return ExtractionBatchResponse.model_validate(value, from_attributes=True)


def _response(view: ExtractionRequestView) -> ExtractionRequestResponse:
    return ExtractionRequestResponse.model_validate(view, from_attributes=True)


@router.post("/execute", response_model=ExtractionExecutionResponse)
async def execute_extraction(
    project_id: UUID,
    body: ExecuteExtractionRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ExtractionExecutionResponse:
    extraction_service = _service(
        actor=actor,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
        max_context_bytes=settings.max_generation_context_bytes,
    )
    outcome = await ExtractionExecutionService(
        actor=actor,
        uow_factory=uow_factory,
        extraction_service=extraction_service,
    ).execute(
        ExecuteExtractionCommand(
            project_id=project_id,
            source_processing_run_id=body.source_processing_run_id,
            selection=ExtractionSelection(
                page_from=body.selection.page_from,
                page_to=body.selection.page_to,
                sections=body.selection.sections,
            ),
            model_policy=configured_model_policy(settings),
            idempotency_key=idempotency_key,
            max_batches=body.max_batches,
            workflow_run_id=body.workflow_run_id,
            continuation_cursor=body.continuation_cursor,
            explicit_retry=body.explicit_retry,
            request_id=request_id,
        )
    )
    return ExtractionExecutionResponse.model_validate(outcome, from_attributes=True)


@router.post("", response_model=ExtractionRequestResponse)
async def start_extraction(
    project_id: UUID,
    body: StartExtractionRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ExtractionRequestResponse:
    service = _service(
        actor=actor,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
        max_context_bytes=settings.max_generation_context_bytes,
    )
    view = await service.extract(
        ExtractPropositionsCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            source_processing_run_id=body.source_processing_run_id,
            selection=ExtractionSelection(
                page_from=body.selection.page_from,
                page_to=body.selection.page_to,
                sections=body.selection.sections,
            ),
            model_policy=configured_model_policy(settings),
            max_batches=body.max_batches,
            continuation_cursor=body.continuation_cursor,
            explicit_rerun=body.explicit_rerun,
            request_id=request_id,
        )
    )
    return _response(view)


@router.get("/{workflow_run_id}", response_model=ExtractionStatusResponse)
async def extraction_status(
    project_id: UUID,
    workflow_run_id: UUID,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> ExtractionStatusResponse:
    batches = await _service(
        actor=actor,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
        max_context_bytes=settings.max_generation_context_bytes,
    ).list_batches(project_id, workflow_run_id)
    attempts = await _service(
        actor=actor,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
        max_context_bytes=settings.max_generation_context_bytes,
    ).list_generation_attempts(project_id, workflow_run_id)
    return ExtractionStatusResponse(
        workflow_run_id=workflow_run_id,
        batches=tuple(_batch_response(batch) for batch in batches),
        generation_attempts=tuple(
            ExtractionGenerationAttemptResponse.model_validate(item, from_attributes=True)
            for item in attempts
        ),
    )
