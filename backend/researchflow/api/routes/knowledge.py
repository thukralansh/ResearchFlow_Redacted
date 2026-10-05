"""Human proposition review routes."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from researchflow.api.dependencies import ActorDependency, UnitOfWorkFactoryDependency
from researchflow.api.schemas.knowledge import (
    AcceptRevisionRequest,
    AmbiguitySubtypeRequest,
    AssumptionSubtypeRequest,
    ClaimSubtypeRequest,
    EvidenceLinkResponse,
    EvidenceReviewResponse,
    ImplementationRequirementSubtypeRequest,
    KnowledgeGapSubtypeRequest,
    KnowledgeObjectResponse,
    KnowledgeReviewDetailResponse,
    OperationEventResponse,
    PresentationCommandResponse,
    PresentationResponse,
    PresentationRevisionRequest,
    ReviewCommandResponse,
    ReviewMutationRequest,
    SemanticRevisionRequest,
)
from researchflow.application.knowledge_review import (
    AcceptRevisionCommand,
    EvidenceReviewCommand,
    KnowledgeReviewService,
    PresentationRevisionCommand,
    ReviewTransitionCommand,
    SemanticRevisionCommand,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.knowledge import (
    AmbiguityInterpretationPayload,
    AmbiguityPayload,
    AssumptionPayload,
    ClaimPayload,
    ImplementationRequirementPayload,
    KnowledgeGapPayload,
    KnowledgeObjectPayload,
)
from researchflow.domain.knowledge_catalog import KnowledgeObjectType, KnowledgeReviewStatus

router = APIRouter(prefix="/projects/{project_id}/knowledge", tags=["knowledge-review"])


def _service(
    actor: ActorDependency, uow_factory: UnitOfWorkFactoryDependency
) -> KnowledgeReviewService:
    return KnowledgeReviewService(
        actor=actor,
        uow_factory=uow_factory,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    )


def _transition(
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    request_id: str | None,
) -> ReviewTransitionCommand:
    return ReviewTransitionCommand(
        project_id=project_id,
        object_id=object_id,
        command_id=body.command_id,
        expected_version=body.expected_version,
        expected_review_status=body.expected_review_status,
        expected_lifecycle_status=body.expected_lifecycle_status,
        rationale=body.rationale,
        request_id=request_id,
    )


@router.get("", response_model=tuple[KnowledgeObjectResponse, ...])
async def list_knowledge(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    review_status: Annotated[KnowledgeReviewStatus | None, Query()] = None,
    object_type: Annotated[KnowledgeObjectType | None, Query()] = None,
) -> tuple[KnowledgeObjectResponse, ...]:
    items = await _service(actor, uow_factory).list_items(
        project_id,
        review_status=review_status,
        object_type=None if object_type is None else object_type.value,
    )
    return tuple(
        KnowledgeObjectResponse.model_validate(item, from_attributes=True) for item in items
    )


@router.get("/accepted-current", response_model=tuple[KnowledgeObjectResponse, ...])
async def list_accepted_current_knowledge(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[KnowledgeObjectResponse, ...]:
    """Expose accepted/current knowledge without moving synthesis policy into the UI."""
    items = await _service(actor, uow_factory).list_accepted_current(project_id)
    return tuple(
        KnowledgeObjectResponse.model_validate(item, from_attributes=True) for item in items
    )


@router.get("/{object_id}", response_model=KnowledgeReviewDetailResponse)
async def knowledge_detail(
    project_id: UUID,
    object_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> KnowledgeReviewDetailResponse:
    detail = await _service(actor, uow_factory).detail(project_id, object_id)
    return KnowledgeReviewDetailResponse(
        object=KnowledgeObjectResponse.model_validate(detail.object, from_attributes=True),
        subtype_payload=detail.payload.subtype_payload,
        ambiguity_interpretations=detail.payload.ambiguity_interpretations,
        evidence=tuple(
            EvidenceLinkResponse.model_validate(item, from_attributes=True)
            for item in detail.evidence
        ),
        presentations=tuple(
            PresentationResponse.model_validate(item, from_attributes=True)
            for item in detail.presentations
        ),
        lineage=tuple(
            KnowledgeObjectResponse.model_validate(item, from_attributes=True)
            for item in detail.lineage
        ),
        history=tuple(
            OperationEventResponse.model_validate(item, from_attributes=True)
            for item in detail.history
        ),
    )


async def _run_transition(
    action: str,
    *,
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: str | None,
) -> ReviewCommandResponse:
    service = _service(actor, uow_factory)
    method = {
        "accept": service.accept,
        "reject": service.reject,
        "request-revision": service.request_revision,
        "challenge": service.challenge,
        "reaffirm": service.reaffirm,
    }[action]
    result = await method(_transition(project_id, object_id, body, request_id))
    return ReviewCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{object_id}/evidence/{evidence_link_id}/{action}",
    response_model=EvidenceReviewResponse,
)
async def review_evidence(
    project_id: UUID,
    object_id: UUID,
    evidence_link_id: UUID,
    action: Literal["accept", "reject", "request-revision", "challenge", "reaffirm"],
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> EvidenceReviewResponse:
    service = _service(actor, uow_factory)
    method = {
        "accept": service.accept_evidence,
        "reject": service.reject_evidence,
        "request-revision": service.request_evidence_revision,
        "challenge": service.challenge_evidence,
        "reaffirm": service.reaffirm_evidence,
    }[action]
    result = await method(
        EvidenceReviewCommand(
            project_id=project_id,
            knowledge_object_id=object_id,
            evidence_link_id=evidence_link_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return EvidenceReviewResponse.model_validate(result, from_attributes=True)


@router.post("/{object_id}/accept", response_model=ReviewCommandResponse)
async def accept_knowledge(
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    return await _run_transition(
        "accept",
        project_id=project_id,
        object_id=object_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{object_id}/reject", response_model=ReviewCommandResponse)
async def reject_knowledge(
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    return await _run_transition(
        "reject",
        project_id=project_id,
        object_id=object_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{object_id}/request-revision", response_model=ReviewCommandResponse)
async def request_knowledge_revision(
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    return await _run_transition(
        "request-revision",
        project_id=project_id,
        object_id=object_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{object_id}/challenge", response_model=ReviewCommandResponse)
async def challenge_knowledge(
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    return await _run_transition(
        "challenge",
        project_id=project_id,
        object_id=object_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post("/{object_id}/reaffirm", response_model=ReviewCommandResponse)
async def reaffirm_knowledge(
    project_id: UUID,
    object_id: UUID,
    body: ReviewMutationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    return await _run_transition(
        "reaffirm",
        project_id=project_id,
        object_id=object_id,
        body=body,
        actor=actor,
        uow_factory=uow_factory,
        request_id=request_id,
    )


@router.post(
    "/{object_id}/semantic-revisions",
    response_model=ReviewCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def semantic_revision(
    project_id: UUID,
    object_id: UUID,
    body: SemanticRevisionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    subtype: object
    if isinstance(body.subtype, ClaimSubtypeRequest):
        subtype = ClaimPayload(**body.subtype.model_dump())
    elif isinstance(body.subtype, AssumptionSubtypeRequest):
        subtype = AssumptionPayload(**body.subtype.model_dump())
    elif isinstance(body.subtype, KnowledgeGapSubtypeRequest):
        subtype = KnowledgeGapPayload(**body.subtype.model_dump())
    elif isinstance(body.subtype, ImplementationRequirementSubtypeRequest):
        subtype = ImplementationRequirementPayload(**body.subtype.model_dump())
    elif isinstance(body.subtype, AmbiguitySubtypeRequest):
        subtype = AmbiguityPayload(
            ambiguity_type=body.subtype.ambiguity_type,
            impact_level=body.subtype.impact_level,
            resolution_status=body.subtype.resolution_status,
            interpretations=tuple(
                AmbiguityInterpretationPayload(**item.model_dump())
                for item in body.subtype.interpretations
            ),
        )
    else:
        raise AssertionError("validated subtype union is exhaustive")
    result = await _service(actor, uow_factory).create_semantic_revision(
        SemanticRevisionCommand(
            project_id=project_id,
            object_id=object_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            payload=KnowledgeObjectPayload(
                object_type=body.object_type,
                statement=body.statement,
                origin=body.origin,
                epistemic_status=body.epistemic_status,
                confidence=body.confidence,
                subtype=subtype,
            ),
            reproposed_evidence_link_ids=body.reproposed_evidence_link_ids,
            request_id=request_id,
        )
    )
    return ReviewCommandResponse.model_validate(result, from_attributes=True)


@router.post(
    "/{object_id}/presentation-revisions",
    response_model=PresentationCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def presentation_revision(
    project_id: UUID,
    object_id: UUID,
    body: PresentationRevisionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> PresentationCommandResponse:
    result = await _service(actor, uow_factory).create_presentation_revision(
        PresentationRevisionCommand(
            project_id=project_id,
            object_id=object_id,
            command_id=body.command_id,
            expected_version=body.expected_version,
            expected_review_status=body.expected_review_status,
            expected_lifecycle_status=body.expected_lifecycle_status,
            rationale=body.rationale,
            display_text=body.display_text,
            display_label=body.display_label,
            request_id=request_id,
        )
    )
    return PresentationCommandResponse.model_validate(result, from_attributes=True)


@router.post("/{object_id}/accept-revision", response_model=ReviewCommandResponse)
async def accept_semantic_revision(
    project_id: UUID,
    object_id: UUID,
    body: AcceptRevisionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ReviewCommandResponse:
    result = await _service(actor, uow_factory).accept_revision(
        AcceptRevisionCommand(
            project_id=project_id,
            predecessor_id=object_id,
            replacement_id=body.replacement_id,
            command_id=body.command_id,
            expected_predecessor_version=body.expected_predecessor_version,
            expected_predecessor_lifecycle_status=body.expected_predecessor_lifecycle_status,
            expected_replacement_version=body.expected_replacement_version,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return ReviewCommandResponse.model_validate(result, from_attributes=True)
