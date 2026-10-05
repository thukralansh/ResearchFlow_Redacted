"""Project-scoped implementation-plan and VerificationCheck commands."""

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
from researchflow.api.schemas.implementation import (
    GenerateImplementationPlanRequest,
    ImplementationStepResponse,
    ReviseImplementationStepRequest,
    StepRevisionResponse,
)
from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.api.schemas.verification import (
    ArtifactDependencyResponse,
    ImplementationPlanResponse,
    RecordVerificationResultRequest,
    VerificationCheckResponse,
    VerificationResultResponse,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.implementation_steps import (
    GenerateImplementationPlanCommand,
    ImplementationPlan,
    ImplementationQueryService,
    ImplementationService,
    ReviseImplementationStepCommand,
)
from researchflow.application.verification import (
    RecordVerificationResultCommand,
    VerificationService,
)

router = APIRouter(prefix="/projects/{project_id}/verification", tags=["verification"])


def _plan_response(value: ImplementationPlan) -> ImplementationPlanResponse:
    return ImplementationPlanResponse(
        blueprint_id=value.blueprint_id,
        steps=tuple(
            ImplementationStepResponse.model_validate(item, from_attributes=True)
            for item in value.steps
        ),
        checks=tuple(
            VerificationCheckResponse.model_validate(item, from_attributes=True)
            for item in value.checks
        ),
        dependencies=tuple(
            ArtifactDependencyResponse.model_validate(item, from_attributes=True)
            for item in value.dependencies
        ),
        event=(
            None
            if value.event is None
            else OperationEventResponse.model_validate(value.event, from_attributes=True)
        ),
        replayed=value.replayed,
    )


def _service(
    *,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> ImplementationService:
    return ImplementationService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    )


@router.post("/blueprints/{blueprint_id}/generate", response_model=ImplementationPlanResponse)
async def generate_plan(
    project_id: UUID,
    blueprint_id: UUID,
    body: GenerateImplementationPlanRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ImplementationPlanResponse:
    result = await _service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).generate(
        GenerateImplementationPlanCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            blueprint_id=blueprint_id,
            model_policy=configured_model_policy(settings),
            request_id=request_id,
        )
    )
    return _plan_response(result)


@router.get("/blueprints/{blueprint_id}", response_model=ImplementationPlanResponse)
async def plan_detail(
    project_id: UUID,
    blueprint_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> ImplementationPlanResponse:
    return _plan_response(
        await ImplementationQueryService(actor=actor, uow_factory=uow_factory).detail(
            project_id, blueprint_id
        )
    )


@router.post("/steps/{step_id}/revisions", response_model=StepRevisionResponse)
async def revise_step(
    project_id: UUID,
    step_id: UUID,
    body: ReviseImplementationStepRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> StepRevisionResponse:
    result = await _service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).revise_step(
        ReviseImplementationStepCommand(
            project_id=project_id,
            step_id=step_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            title=body.title,
            description=body.description,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return StepRevisionResponse(
        step=ImplementationStepResponse.model_validate(result.step, from_attributes=True),
        invalidated_check_ids=result.invalidated_checks,
        event=OperationEventResponse.model_validate(result.event, from_attributes=True),
        replayed=result.replayed,
    )


@router.post("/checks/{check_id}/results", response_model=VerificationResultResponse)
async def record_result(
    project_id: UUID,
    check_id: UUID,
    body: RecordVerificationResultRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> VerificationResultResponse:
    result = await VerificationService(actor=actor, uow_factory=uow_factory).record_result(
        RecordVerificationResultCommand(
            project_id=project_id,
            check_id=check_id,
            command_id=body.command_id,
            expected_result_status=body.expected_result_status,
            result_status=body.result_status,
            actual_result=body.actual_result,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return VerificationResultResponse(
        check=VerificationCheckResponse.model_validate(result.check, from_attributes=True),
        event=OperationEventResponse.model_validate(result.event, from_attributes=True),
        replayed=result.replayed,
    )
