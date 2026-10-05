""" post-mapping MethodModel reassessment routes."""

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
from researchflow.api.schemas.method_model_reassessment import (
    MethodModelReassessmentRequest,
    MethodModelReassessmentResponse,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.method_model_reassessment import (
    MethodModelReassessmentQueryService,
    MethodModelReassessmentService,
    ReassessMethodModelCommand,
)
from researchflow.application.staleness import StalenessService

router = APIRouter(
    prefix="/projects/{project_id}/method-model-reassessments",
    tags=["method-model-reassessments"],
)


@router.post("", response_model=MethodModelReassessmentResponse)
async def reassess_method_model(
    project_id: UUID,
    body: MethodModelReassessmentRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodModelReassessmentResponse:
    service = MethodModelReassessmentService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    )
    return await service.after_mapping(
        ReassessMethodModelCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            mapping_review_gate_event_id=body.mapping_review_gate_event_id,
            applicable=body.applicable,
            model_policy=configured_model_policy(settings),
            explicit_rerun=body.explicit_rerun,
            request_id=request_id,
        )
    )


@router.get("/{generation_record_id}", response_model=MethodModelReassessmentResponse)
async def method_model_reassessment_detail(
    project_id: UUID,
    generation_record_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MethodModelReassessmentResponse:
    return await MethodModelReassessmentQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, generation_record_id
    )
