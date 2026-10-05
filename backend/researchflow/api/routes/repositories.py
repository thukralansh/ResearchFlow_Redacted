"""Human-confirmed repository role and immutable snapshot routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from researchflow.api.dependencies import (
    ActorDependency,
    GitHubGatewayDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.schemas.repositories import (
    AttachRepositoryRequest,
    PaperRepositoryRelationshipResponse,
    RepositoryResponse,
    RepositorySnapshotResponse,
    RepositorySnapshotViewResponse,
)
from researchflow.application.repositories import (
    AttachRepositoryCommand,
    RepositoryApplicationService,
    RepositoryQueryService,
    RepositorySnapshotView,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.projects import IDEMPOTENCY_KEY_MAX

router = APIRouter(prefix="/projects/{project_id}/repositories", tags=["repositories"])


def _response(view: RepositorySnapshotView) -> RepositorySnapshotViewResponse:
    return RepositorySnapshotViewResponse(
        repository=RepositoryResponse.model_validate(view.repository, from_attributes=True),
        snapshot=RepositorySnapshotResponse.model_validate(view.snapshot, from_attributes=True),
        relationship=PaperRepositoryRelationshipResponse.model_validate(
            view.relationship, from_attributes=True
        ),
    )


@router.post(
    "",
    response_model=RepositorySnapshotViewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def attach_repository(
    project_id: UUID,
    body: AttachRepositoryRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    github: GitHubGatewayDependency,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX),
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> RepositorySnapshotViewResponse:
    view = await RepositoryApplicationService(
        actor=actor,
        uow_factory=uow_factory,
        github=github,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    ).attach(
        AttachRepositoryCommand(
            project_id=project_id,
            research_question_id=body.research_question_id,
            primary_paper_relationship_id=body.primary_paper_relationship_id,
            source_id=body.source_id,
            repository_url=body.repository_url,
            requested_ref=body.requested_ref,
            relationship_type=body.relationship_type,
            authority_level=body.authority_level,
            rationale=body.rationale,
            evidence_text=body.evidence_text,
            confidence=body.confidence,
            human_confirmed=body.human_confirmed,
            replace_current=body.replace_current,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    )
    return _response(view)


@router.get("", response_model=list[RepositorySnapshotViewResponse])
async def repository_history(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[RepositorySnapshotViewResponse]:
    views = await RepositoryQueryService(actor=actor, uow_factory=uow_factory).history(project_id)
    return [_response(view) for view in views]
