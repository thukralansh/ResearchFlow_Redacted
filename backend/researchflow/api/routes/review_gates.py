"""Combined primary-source scientific-review gate routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from researchflow.api.dependencies import ActorDependency, UnitOfWorkFactoryDependency
from researchflow.api.schemas.review_gates import (
    CompletePrimarySourceReviewRequest,
    PrimarySourceReviewCompletionResponse,
    PrimarySourceReviewGateResponse,
)
from researchflow.application.review_gates import (
    CompletePrimarySourceReviewCommand,
    PrimarySourceReviewGateService,
)

router = APIRouter(prefix="/projects/{project_id}/review-gates", tags=["review-gates"])


@router.get("/primary-source", response_model=PrimarySourceReviewGateResponse)
async def primary_source_review_gate(
    project_id: UUID,
    method_model_id: Annotated[UUID, Query()],
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> PrimarySourceReviewGateResponse:
    view = await PrimarySourceReviewGateService(actor=actor, uow_factory=uow_factory).status(
        project_id, method_model_id
    )
    return PrimarySourceReviewGateResponse.model_validate(view, from_attributes=True)


@router.post("/primary-source/complete", response_model=PrimarySourceReviewCompletionResponse)
async def complete_primary_source_review_gate(
    project_id: UUID,
    body: CompletePrimarySourceReviewRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> PrimarySourceReviewCompletionResponse:
    result = await PrimarySourceReviewGateService(actor=actor, uow_factory=uow_factory).complete(
        CompletePrimarySourceReviewCommand(
            project_id=project_id,
            method_model_id=body.method_model_id,
            expected_gate_fingerprint=body.expected_gate_fingerprint,
            confirmed=body.confirmed,
            request_id=request_id,
        )
    )
    return PrimarySourceReviewCompletionResponse.model_validate(result, from_attributes=True)
