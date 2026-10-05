"""Project-scoped  Blueprint generation and structured inspection routes."""

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
from researchflow.api.schemas.blueprints import (
    AcceptBlueprintRequest,
    BlueprintDependencyResponse,
    BlueprintDetailResponse,
    BlueprintGenerationResponse,
    BlueprintReviewResponse,
    BlueprintSectionResponse,
    GenerateBlueprintRequest,
    ImplementationBlueprintResponse,
    ReviseBlueprintRequest,
)
from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.application.blueprint_review import (
    AcceptBlueprintCommand,
    BlueprintReviewResult,
    BlueprintReviewService,
    ReviseBlueprintCommand,
)
from researchflow.application.blueprints import (
    BlueprintDetail,
    BlueprintQueryService,
    BlueprintService,
    GenerateBlueprintCommand,
)
from researchflow.application.generation import StructuredGenerationService

router = APIRouter(prefix="/projects/{project_id}/blueprints", tags=["blueprints"])


def _detail_response(value: BlueprintDetail) -> BlueprintDetailResponse:
    return BlueprintDetailResponse(
        blueprint=ImplementationBlueprintResponse.model_validate(
            value.blueprint, from_attributes=True
        ),
        sections=tuple(
            BlueprintSectionResponse.model_validate(item, from_attributes=True)
            for item in value.sections
        ),
        dependencies=tuple(
            BlueprintDependencyResponse.model_validate(item, from_attributes=True)
            for item in value.dependencies
        ),
        history=tuple(
            OperationEventResponse.model_validate(item, from_attributes=True)
            for item in value.history
        ),
    )


def _review_response(value: BlueprintReviewResult) -> BlueprintReviewResponse:
    detail = _detail_response(value)
    return BlueprintReviewResponse(
        **detail.model_dump(),
        event=OperationEventResponse.model_validate(value.event, from_attributes=True),
        replayed=value.replayed,
    )


def _review_service(
    *,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> BlueprintReviewService:
    return BlueprintReviewService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    )


@router.post("/generate", response_model=BlueprintGenerationResponse)
async def generate_blueprint(
    project_id: UUID,
    body: GenerateBlueprintRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> BlueprintGenerationResponse:
    result = await BlueprintService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    ).generate(
        GenerateBlueprintCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            eligibility_assessment_id=body.eligibility_assessment_id,
            model_policy=configured_model_policy(settings),
            request_id=request_id,
        )
    )
    detail = _detail_response(result)
    return BlueprintGenerationResponse(
        **detail.model_dump(),
        event=OperationEventResponse.model_validate(result.event, from_attributes=True),
        replayed=result.replayed,
    )


@router.get("", response_model=tuple[BlueprintDetailResponse, ...])
async def list_blueprints(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[BlueprintDetailResponse, ...]:
    values = await BlueprintQueryService(actor=actor, uow_factory=uow_factory).list(project_id)
    return tuple(_detail_response(item) for item in values)


@router.post("/{blueprint_id}/accept", response_model=BlueprintReviewResponse)
async def accept_blueprint(
    project_id: UUID,
    blueprint_id: UUID,
    body: AcceptBlueprintRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> BlueprintReviewResponse:
    result = await _review_service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).accept(
        AcceptBlueprintCommand(
            project_id=project_id,
            blueprint_id=blueprint_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_status=body.expected_status,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return _review_response(result)


@router.post("/{blueprint_id}/revisions", response_model=BlueprintReviewResponse)
async def revise_blueprint(
    project_id: UUID,
    blueprint_id: UUID,
    body: ReviseBlueprintRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> BlueprintReviewResponse:
    result = await _review_service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).request_revision(
        ReviseBlueprintCommand(
            project_id=project_id,
            blueprint_id=blueprint_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_status=body.expected_status,
            rationale=body.rationale,
            feedback=body.feedback,
            eligibility_assessment_id=body.eligibility_assessment_id,
            model_policy=configured_model_policy(settings),
            request_id=request_id,
        )
    )
    return _review_response(result)


@router.get("/{blueprint_id}", response_model=BlueprintDetailResponse)
async def blueprint_detail(
    project_id: UUID,
    blueprint_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> BlueprintDetailResponse:
    result = await BlueprintQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, blueprint_id
    )
    return _detail_response(result)
