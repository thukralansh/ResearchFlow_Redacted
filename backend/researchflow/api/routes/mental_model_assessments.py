""" post-mapping Mental Model reassessment routes."""

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
from researchflow.api.schemas.mental_model_assessments import (
    MentalModelAssessmentCoverageResponse,
    MentalModelReassessmentRequest,
    MentalModelReassessmentResponse,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.mental_model_reassessment import (
    MentalModelAssessmentQueryService,
    MentalModelReassessmentService,
    ReassessMentalModelCommand,
)
from researchflow.application.staleness import StalenessService

router = APIRouter(
    prefix="/projects/{project_id}/mental-model-assessments",
    tags=["mental-model-assessments"],
)


@router.post("", response_model=MentalModelReassessmentResponse)
async def reassess_mental_model(
    project_id: UUID,
    body: MentalModelReassessmentRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MentalModelReassessmentResponse:
    service = MentalModelReassessmentService(
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
        ReassessMentalModelCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            mapping_review_gate_event_id=body.mapping_review_gate_event_id,
            method_model_reassessment_event_id=body.method_model_reassessment_event_id,
            applicable=body.applicable,
            model_policy=configured_model_policy(settings),
            explicit_rerun=body.explicit_rerun,
            request_id=request_id,
        )
    )


@router.get("/current", response_model=MentalModelAssessmentCoverageResponse)
async def current_mental_model_assessment_coverage(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MentalModelAssessmentCoverageResponse:
    return await MentalModelAssessmentQueryService(
        actor=actor, uow_factory=uow_factory
    ).current_coverage(project_id)


@router.get("/{generation_record_id}", response_model=MentalModelReassessmentResponse)
async def mental_model_reassessment_detail(
    project_id: UUID,
    generation_record_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MentalModelReassessmentResponse:
    return await MentalModelAssessmentQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, generation_record_id
    )
