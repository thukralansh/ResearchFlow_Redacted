"""Project-scoped direct staleness preview, apply, query, and resolution routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from researchflow.api.dependencies import ActorDependency, UnitOfWorkFactoryDependency
from researchflow.api.schemas.staleness import (
    ArtifactFreshnessResponse,
    DirectStalenessPreviewResponse,
    DirectStalenessRequest,
    ResolveInvalidationRequest,
)
from researchflow.application.staleness import (
    DirectStalenessCommand,
    ResolveInvalidationCommand,
    StalenessService,
)

router = APIRouter(prefix="/projects/{project_id}/staleness", tags=["staleness"])


def _service(actor: ActorDependency, uow_factory: UnitOfWorkFactoryDependency) -> StalenessService:
    return StalenessService(actor=actor, uow_factory=uow_factory)


@router.post("/preview", response_model=DirectStalenessPreviewResponse)
async def preview_direct(
    project_id: UUID,
    body: DirectStalenessRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> DirectStalenessPreviewResponse:
    value = await _service(actor, uow_factory).preview_direct(
        DirectStalenessCommand(
            project_id=project_id,
            cause_type=body.cause_type,
            cause_id=body.cause_id,
            operation_type=body.operation_type,
            after_id=body.after_id,
        )
    )
    return DirectStalenessPreviewResponse.model_validate(value, from_attributes=True)


@router.get("/current", response_model=ArtifactFreshnessResponse)
async def current_freshness(
    project_id: UUID,
    artifact_type: Annotated[str, Query(min_length=1, max_length=100)],
    artifact_id: Annotated[UUID, Query()],
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> ArtifactFreshnessResponse:
    value = await _service(actor, uow_factory).current(project_id, artifact_type, artifact_id)
    return ArtifactFreshnessResponse.model_validate(value, from_attributes=True)


@router.post("/invalidations/{invalidation_id}/resolve", response_model=ArtifactFreshnessResponse)
async def resolve_invalidation(
    project_id: UUID,
    invalidation_id: UUID,
    body: ResolveInvalidationRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ArtifactFreshnessResponse:
    value = await _service(actor, uow_factory).resolve(
        ResolveInvalidationCommand(
            project_id=project_id,
            artifact_type=body.artifact_type,
            artifact_id=body.artifact_id,
            invalidation_id=invalidation_id,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return ArtifactFreshnessResponse.model_validate(value, from_attributes=True)
