"""Project-scoped  mapping candidate routes."""

from typing import Annotated, Literal
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
from researchflow.api.schemas.mappings import (
    AcceptMappingRevisionRequest,
    GenerateMappingsRequest,
    MappingGenerationResponse,
    MappingReviewMutationRequest,
    MappingReviewResponse,
    MappingSemanticRevisionRequest,
    PaperCodeMappingResponse,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.mapping_generation import (
    GenerateMappingsCommand,
    MappingGenerationService,
    MappingQueryService,
)
from researchflow.application.mapping_review import (
    AcceptMappingRevisionCommand,
    MappingReviewService,
    MappingRevisionCommand,
    MappingTransitionCommand,
)
from researchflow.application.staleness import StalenessService

router = APIRouter(prefix="/projects/{project_id}/mappings", tags=["mappings"])


def _review_service(
    actor: ActorDependency, uow_factory: UnitOfWorkFactoryDependency
) -> MappingReviewService:
    return MappingReviewService(
        actor=actor,
        uow_factory=uow_factory,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    )


@router.post("/generate", response_model=MappingGenerationResponse)
async def generate_mappings(
    project_id: UUID,
    body: GenerateMappingsRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingGenerationResponse:
    return await MappingGenerationService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    ).generate(
        GenerateMappingsCommand(
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


@router.get("", response_model=list[PaperCodeMappingResponse])
async def list_mappings(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[PaperCodeMappingResponse]:
    return list(await MappingQueryService(actor=actor, uow_factory=uow_factory).list(project_id))


@router.get("/{mapping_id}", response_model=PaperCodeMappingResponse)
async def mapping_detail(
    project_id: UUID,
    mapping_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> PaperCodeMappingResponse:
    return await MappingQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, mapping_id
    )


async def _transition(
    action: Literal["accept", "reject", "request-revision", "challenge", "reaffirm"],
    *,
    project_id: UUID,
    mapping_id: UUID,
    body: MappingReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: str | None,
) -> MappingReviewResponse:
    service = _review_service(actor, uow_factory)
    handler = {
        "accept": service.accept,
        "reject": service.reject,
        "request-revision": service.request_revision,
        "challenge": service.challenge,
        "reaffirm": service.reaffirm,
    }[action]
    result = await handler(
        MappingTransitionCommand(
            project_id=project_id,
            mapping_id=mapping_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return MappingReviewResponse.model_validate(result, from_attributes=True)


@router.post("/{mapping_id}/accept", response_model=MappingReviewResponse)
async def accept_mapping(
    project_id: UUID,
    mapping_id: UUID,
    body: MappingReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    return await _transition(
        "accept",
        project_id=project_id,
        mapping_id=mapping_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{mapping_id}/reject", response_model=MappingReviewResponse)
async def reject_mapping(
    project_id: UUID,
    mapping_id: UUID,
    body: MappingReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    return await _transition(
        "reject",
        project_id=project_id,
        mapping_id=mapping_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{mapping_id}/request-revision", response_model=MappingReviewResponse)
async def request_mapping_revision(
    project_id: UUID,
    mapping_id: UUID,
    body: MappingReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    return await _transition(
        "request-revision",
        project_id=project_id,
        mapping_id=mapping_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{mapping_id}/challenge", response_model=MappingReviewResponse)
async def challenge_mapping(
    project_id: UUID,
    mapping_id: UUID,
    body: MappingReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    return await _transition(
        "challenge",
        project_id=project_id,
        mapping_id=mapping_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{mapping_id}/reaffirm", response_model=MappingReviewResponse)
async def reaffirm_mapping(
    project_id: UUID,
    mapping_id: UUID,
    body: MappingReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    return await _transition(
        "reaffirm",
        project_id=project_id,
        mapping_id=mapping_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{mapping_id}/semantic-revisions",
    response_model=MappingReviewResponse,
    status_code=201,
)
async def revise_mapping(
    project_id: UUID,
    mapping_id: UUID,
    body: MappingSemanticRevisionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    result = await _review_service(actor, uow_factory).create_semantic_revision(
        MappingRevisionCommand(
            project_id=project_id,
            mapping_id=mapping_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            paper_anchor_type=body.paper_anchor_type,
            anchor_id=body.anchor_id,
            repository_component_id=body.repository_component_id,
            mapping_type=body.mapping_type,
            explanation=body.explanation,
            confidence=body.confidence,
            request_id=request_id,
        )
    )
    return MappingReviewResponse.model_validate(result, from_attributes=True)


@router.post("/{mapping_id}/accept-revision", response_model=MappingReviewResponse)
async def accept_mapping_revision(
    project_id: UUID,
    mapping_id: UUID,
    body: AcceptMappingRevisionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MappingReviewResponse:
    result = await _review_service(actor, uow_factory).accept_revision(
        AcceptMappingRevisionCommand(
            project_id=project_id,
            predecessor_id=mapping_id,
            replacement_id=body.replacement_id,
            command_id=body.command_id,
            expected_predecessor_version=body.expected_predecessor_version,
            expected_predecessor_lifecycle_status=body.expected_predecessor_lifecycle_status,
            expected_replacement_version=body.expected_replacement_version,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return MappingReviewResponse.model_validate(result, from_attributes=True)
