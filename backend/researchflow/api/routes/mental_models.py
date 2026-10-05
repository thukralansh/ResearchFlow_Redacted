"""Structured Mental Model generation, history, detail, and rendering routes."""

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
from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.api.schemas.mental_models import (
    MentalModelDetailResponse,
    MentalModelGenerateRequest,
    MentalModelGenerationResponse,
    MentalModelRenderingCommandResponse,
    MentalModelRenderingRefreshRequest,
    MentalModelSummaryResponse,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.mental_model_inspection import MentalModelInspectionService
from researchflow.application.mental_models import (
    ComposeInitialMentalModelCommand,
    MentalModelService,
    RefreshMentalModelRenderingCommand,
)

router = APIRouter(prefix="/projects/{project_id}/mental-models", tags=["mental-models"])


def _inspection(
    actor: ActorDependency, uow_factory: UnitOfWorkFactoryDependency
) -> MentalModelInspectionService:
    return MentalModelInspectionService(actor=actor, uow_factory=uow_factory)


def _generation(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    settings: SettingsDependency,
) -> MentalModelService:
    return MentalModelService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    )


@router.get("/current", response_model=MentalModelDetailResponse | None)
async def current_mental_model(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MentalModelDetailResponse | None:
    detail = await _inspection(actor, uow_factory).current(project_id)
    return (
        None
        if detail is None
        else MentalModelDetailResponse.model_validate(detail, from_attributes=True)
    )


@router.get("/history", response_model=tuple[MentalModelSummaryResponse, ...])
async def mental_model_history(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[MentalModelSummaryResponse, ...]:
    history = await _inspection(actor, uow_factory).history(project_id)
    return tuple(
        MentalModelSummaryResponse.model_validate(item, from_attributes=True) for item in history
    )


@router.post("/generate", response_model=MentalModelGenerationResponse)
async def generate_mental_model(
    project_id: UUID,
    body: MentalModelGenerateRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MentalModelGenerationResponse:
    generated = await _generation(actor, uow_factory, provider, prompts, settings).compose_initial(
        ComposeInitialMentalModelCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            method_model_id=body.method_model_id,
            model_policy=configured_model_policy(settings),
            request_id=request_id,
        )
    )
    detail = await _inspection(actor, uow_factory).detail(project_id, generated.model.id)
    return MentalModelGenerationResponse(
        detail=MentalModelDetailResponse.model_validate(detail, from_attributes=True),
        event=OperationEventResponse.model_validate(generated.event, from_attributes=True),
        replayed=generated.replayed,
    )


@router.get("/{mental_model_id}", response_model=MentalModelDetailResponse)
async def mental_model_detail(
    project_id: UUID,
    mental_model_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MentalModelDetailResponse:
    detail = await _inspection(actor, uow_factory).detail(project_id, mental_model_id)
    return MentalModelDetailResponse.model_validate(detail, from_attributes=True)


@router.post("/{mental_model_id}/renderings", response_model=MentalModelRenderingCommandResponse)
async def refresh_mental_model_rendering(
    project_id: UUID,
    mental_model_id: UUID,
    body: MentalModelRenderingRefreshRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MentalModelRenderingCommandResponse:
    result = await _generation(actor, uow_factory, provider, prompts, settings).refresh_rendering(
        RefreshMentalModelRenderingCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            mental_model_id=mental_model_id,
            model_policy=configured_model_policy(settings),
            request_id=request_id,
            explicit_rerun=body.explicit_rerun,
        )
    )
    return MentalModelRenderingCommandResponse.model_validate(result, from_attributes=True)
