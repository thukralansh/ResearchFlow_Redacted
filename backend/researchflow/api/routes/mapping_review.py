""" mapping-derived knowledge and material review-gate routes."""

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
from researchflow.api.schemas.mapping_derivation import (
    MappingDerivationRequest,
    MappingDerivationResponse,
    MappingReviewGateCompleteRequest,
    MappingReviewGateResponse,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.mapping_derivation import (
    DeriveMappingKnowledgeCommand,
    MappingDerivationQueryService,
    MappingDerivationService,
)
from researchflow.application.mapping_review_gate import (
    CompleteMappingReviewGateCommand,
    MappingReviewGateService,
)

router = APIRouter(prefix="/projects/{project_id}/mapping-review", tags=["mapping-review"])


@router.post("/derive", response_model=MappingDerivationResponse)
async def derive_mapping_knowledge(
    project_id: UUID,
    body: MappingDerivationRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingDerivationResponse:
    return await MappingDerivationService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    ).derive(
        DeriveMappingKnowledgeCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            applicable=body.applicable,
            applicability_rationale=body.applicability_rationale,
            repository_inspection_run_id=body.repository_inspection_run_id,
            model_policy=configured_model_policy(settings),
            explicit_rerun=body.explicit_rerun,
            request_id=request_id,
        )
    )


@router.get("/derivations/{generation_record_id}", response_model=MappingDerivationResponse)
async def mapping_derivation_detail(
    project_id: UUID,
    generation_record_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MappingDerivationResponse:
    return await MappingDerivationQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, generation_record_id
    )


@router.post("/complete", response_model=MappingReviewGateResponse)
async def complete_mapping_review_gate(
    project_id: UUID,
    body: MappingReviewGateCompleteRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewGateResponse:
    return await MappingReviewGateService(actor=actor, uow_factory=uow_factory).complete(
        CompleteMappingReviewGateCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            command_id=body.command_id,
            applicable=body.applicable,
            derivation_generation_record_id=body.derivation_generation_record_id,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
