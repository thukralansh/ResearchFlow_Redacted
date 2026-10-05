"""Candidate MethodModel extraction, status, and immutable section pages."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from researchflow.api.dependencies import (
    ActorDependency,
    PromptRegistryDependency,
    SettingsDependency,
    StructuredGenerationProviderDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.model_policy import configured_model_policy
from researchflow.api.schemas.method_review import (
    MethodCompositionCommandResponse,
    MethodElementCommandResponse,
    MethodElementExclusionRequest,
    MethodElementMutationRequest,
    MethodElementSemanticRevisionRequest,
    MethodEvidenceCommandResponse,
    MethodModelAcceptanceRequest,
    MethodModelCommandResponse,
    MethodModelReviewResponse,
    MethodReviewDetailResponse,
    MethodSectionCommandResponse,
    MethodSectionReviewRequest,
)
from researchflow.api.schemas.method_runs import (
    MethodExtractionResponse,
    MethodExtractionStatusResponse,
    MethodGenerationAttemptResponse,
    MethodSectionElementResponse,
    MethodSectionPageResponse,
    StartMethodExtractionRequest,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.method_extraction import (
    ExtractMethodModelCommand,
    MethodExtractionService,
)
from researchflow.application.method_review import (
    AcceptMethodModelCommand,
    ExcludeMethodElementCommand,
    MethodElementRevisionCommand,
    MethodElementTransitionCommand,
    MethodEvidenceReviewCommand,
    MethodReviewService,
    ReviewMethodSectionCommand,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.method_catalog import MethodReviewSection

router = APIRouter(prefix="/projects/{project_id}/method-models", tags=["method-models"])


def _review_service(
    actor: ActorDependency, uow_factory: UnitOfWorkFactoryDependency
) -> MethodReviewService:
    return MethodReviewService(
        actor=actor,
        uow_factory=uow_factory,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    )


def _service(
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    max_context_bytes: int,
) -> MethodExtractionService:
    return MethodExtractionService(
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=max_context_bytes,
        ),
    )


@router.post("/extractions", response_model=MethodExtractionResponse)
async def start_method_extraction(
    project_id: UUID,
    body: StartMethodExtractionRequest,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodExtractionResponse:
    view = await _service(
        uow_factory, provider, prompts, settings.max_generation_context_bytes
    ).extract(
        ExtractMethodModelCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            source_processing_run_id=body.source_processing_run_id,
            model_policy=configured_model_policy(settings),
            explicit_rerun=body.explicit_rerun,
            request_id=request_id,
        )
    )
    return MethodExtractionResponse.model_validate(view, from_attributes=True)


@router.get("/extractions/{workflow_run_id}", response_model=MethodExtractionStatusResponse)
async def method_extraction_status(
    project_id: UUID,
    workflow_run_id: UUID,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> MethodExtractionStatusResponse:
    result, attempts = await _service(
        uow_factory, provider, prompts, settings.max_generation_context_bytes
    ).status(project_id, workflow_run_id)
    return MethodExtractionStatusResponse(
        workflow_run_id=workflow_run_id,
        result=(
            None
            if result is None
            else MethodExtractionResponse.model_validate(result, from_attributes=True)
        ),
        generation_attempts=tuple(
            MethodGenerationAttemptResponse.model_validate(item, from_attributes=True)
            for item in attempts
        ),
    )


@router.get(
    "/{method_model_id}/sections/{review_section}", response_model=MethodSectionPageResponse
)
async def method_section_page(
    project_id: UUID,
    method_model_id: UUID,
    review_section: str,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    cursor: Annotated[str | None, Query(max_length=2_000)] = None,
) -> MethodSectionPageResponse:
    page = await _service(
        uow_factory, provider, prompts, settings.max_generation_context_bytes
    ).section_page(project_id, method_model_id, review_section, cursor)
    return MethodSectionPageResponse(
        method_model_id=page.method_model_id,
        review_section=page.review_section,
        total_count=page.total_count,
        returned_count=page.returned_count,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
        elements=tuple(
            MethodSectionElementResponse(
                id=item.element.id,
                element_type=item.element.element_type,
                epistemic_status=item.element.epistemic_status,
                confidence=None
                if item.element.confidence is None
                else float(item.element.confidence),
                review_status=item.element.review_status,
                lifecycle_status=item.element.lifecycle_status,
                grounding_status=item.element.grounding_status,
                member_role=item.member_role,
                sequence_index=item.sequence_index,
                required=item.required,
                inclusion_rationale=item.inclusion_rationale,
                subtype_payload=item.subtype_payload,
            )
            for item in page.elements
        ),
    )


@router.get("/accepted-current", response_model=MethodModelReviewResponse | None)
async def accepted_current_method_model(
    project_id: UUID,
    research_question_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MethodModelReviewResponse | None:
    model = await _review_service(actor, uow_factory).accepted_current(
        project_id, research_question_id
    )
    return (
        None
        if model is None
        else MethodModelReviewResponse.model_validate(model, from_attributes=True)
    )


@router.get("/review-candidates", response_model=tuple[MethodModelReviewResponse, ...])
async def method_model_review_candidates(
    project_id: UUID,
    research_question_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[MethodModelReviewResponse, ...]:
    models = await _review_service(actor, uow_factory).list_models(project_id, research_question_id)
    return tuple(
        MethodModelReviewResponse.model_validate(item, from_attributes=True) for item in models
    )


@router.get("/{method_model_id}/review", response_model=MethodReviewDetailResponse)
async def method_review_detail(
    project_id: UUID,
    method_model_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> MethodReviewDetailResponse:
    result = await _review_service(actor, uow_factory).detail(project_id, method_model_id)
    return MethodReviewDetailResponse.model_validate(result, from_attributes=True)


async def _element_transition(
    action: str,
    *,
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: str | None,
) -> MethodElementCommandResponse:
    service = _review_service(actor, uow_factory)
    command = MethodElementTransitionCommand(
        project_id=project_id,
        method_model_id=method_model_id,
        element_id=element_id,
        command_id=body.command_id,
        expected_version=body.expected_version,
        expected_review_status=body.expected_review_status,
        expected_lifecycle_status=body.expected_lifecycle_status,
        rationale=body.rationale,
        request_id=request_id,
    )
    method_name = {
        "accept": "accept_element",
        "reject": "reject_element",
        "request_revision": "request_element_revision",
        "challenge": "challenge_element",
        "reaffirm": "reaffirm_element",
    }[action]
    result = await getattr(service, method_name)(command)
    return MethodElementCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{method_model_id}/elements/{element_id}/accept",
    response_model=MethodElementCommandResponse,
)
async def accept_method_element(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodElementCommandResponse:
    return await _element_transition(
        "accept",
        project_id=project_id,
        method_model_id=method_model_id,
        element_id=element_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{method_model_id}/elements/{element_id}/reject",
    response_model=MethodElementCommandResponse,
)
async def reject_method_element(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodElementCommandResponse:
    return await _element_transition(
        "reject",
        project_id=project_id,
        method_model_id=method_model_id,
        element_id=element_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{method_model_id}/elements/{element_id}/request-revision",
    response_model=MethodElementCommandResponse,
)
async def request_method_element_revision(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodElementCommandResponse:
    return await _element_transition(
        "request_revision",
        project_id=project_id,
        method_model_id=method_model_id,
        element_id=element_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{method_model_id}/elements/{element_id}/challenge",
    response_model=MethodElementCommandResponse,
)
async def challenge_method_element(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodElementCommandResponse:
    return await _element_transition(
        "challenge",
        project_id=project_id,
        method_model_id=method_model_id,
        element_id=element_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{method_model_id}/elements/{element_id}/reaffirm",
    response_model=MethodElementCommandResponse,
)
async def reaffirm_method_element(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodElementCommandResponse:
    return await _element_transition(
        "reaffirm",
        project_id=project_id,
        method_model_id=method_model_id,
        element_id=element_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{method_model_id}/elements/{element_id}/evidence/{evidence_link_id}/{action}",
    response_model=MethodEvidenceCommandResponse,
)
async def review_method_evidence(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    evidence_link_id: UUID,
    action: Literal["accept", "reject", "request-revision", "challenge", "reaffirm"],
    body: MethodElementMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodEvidenceCommandResponse:
    service = _review_service(actor, uow_factory)
    method_name = {
        "accept": "accept_evidence",
        "reject": "reject_evidence",
        "request-revision": "request_evidence_revision",
        "challenge": "challenge_evidence",
        "reaffirm": "reaffirm_evidence",
    }[action]
    result = await getattr(service, method_name)(
        MethodEvidenceReviewCommand(
            project_id=project_id,
            method_model_id=method_model_id,
            element_id=element_id,
            evidence_link_id=evidence_link_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return MethodEvidenceCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{method_model_id}/elements/{element_id}/semantic-revisions",
    response_model=MethodCompositionCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def revise_method_element(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementSemanticRevisionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodCompositionCommandResponse:
    payload = body.payload.model_dump(mode="python", exclude={"element_type"})
    result = await _review_service(actor, uow_factory).revise_element(
        MethodElementRevisionCommand(
            project_id=project_id,
            method_model_id=method_model_id,
            element_id=element_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            element_type=body.payload.element_type,
            epistemic_status=body.epistemic_status,
            confidence=body.confidence,
            subtype_payload=payload,
            request_id=request_id,
        )
    )
    return MethodCompositionCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{method_model_id}/elements/{element_id}/exclude",
    response_model=MethodCompositionCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def exclude_method_element(
    project_id: UUID,
    method_model_id: UUID,
    element_id: UUID,
    body: MethodElementExclusionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodCompositionCommandResponse:
    result = await _review_service(actor, uow_factory).exclude_element(
        ExcludeMethodElementCommand(
            project_id=project_id,
            method_model_id=method_model_id,
            element_id=element_id,
            command_id=body.command_id,
            expected_model_version=body.expected_model_version,
            expected_composition_fingerprint=body.expected_composition_fingerprint,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return MethodCompositionCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{method_model_id}/sections/{review_section}/reviews",
    response_model=MethodSectionCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def review_method_section(
    project_id: UUID,
    method_model_id: UUID,
    review_section: MethodReviewSection,
    body: MethodSectionReviewRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodSectionCommandResponse:
    result = await _review_service(actor, uow_factory).review_section(
        ReviewMethodSectionCommand(
            project_id=project_id,
            method_model_id=method_model_id,
            review_section=review_section,
            command_id=body.command_id,
            displayed_member_ids=body.displayed_member_ids,
            displayed_member_set_fingerprint=body.displayed_member_set_fingerprint,
            outcome=body.outcome,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return MethodSectionCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{method_model_id}/accept",
    response_model=MethodModelCommandResponse,
)
async def accept_method_model(
    project_id: UUID,
    method_model_id: UUID,
    body: MethodModelAcceptanceRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> MethodModelCommandResponse:
    result = await _review_service(actor, uow_factory).accept_model(
        AcceptMethodModelCommand(
            project_id=project_id,
            method_model_id=method_model_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            expected_composition_fingerprint=body.expected_composition_fingerprint,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return MethodModelCommandResponse.model_validate(result, from_attributes=True)
