"""Repository Stage A start, history, and exact-run routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from researchflow.api.dependencies import (
    ActorDependency,
    GitHubProviderDependency,
    SettingsDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.schemas.repository_discovery import (
    DiscoverRepositoryRequest,
    RepositoryComponentResponse,
    RepositoryDiscoveryRunResponse,
    RepositoryDiscoveryViewResponse,
    RepositoryTreeEntryResponse,
)
from researchflow.application.repository_discovery import (
    DiscoverRepositoryCommand,
    RepositoryDiscoveryQueryService,
    RepositoryDiscoveryService,
    RepositoryDiscoveryView,
)
from researchflow.domain.repository_roles import DiscoveryLimits

router = APIRouter(
    prefix="/projects/{project_id}/repositories/discovery",
    tags=["repository-discovery"],
)


def _response(view: RepositoryDiscoveryView) -> RepositoryDiscoveryViewResponse:
    return RepositoryDiscoveryViewResponse(
        run=RepositoryDiscoveryRunResponse.model_validate(view.run, from_attributes=True),
        entries=[
            RepositoryTreeEntryResponse.model_validate(entry, from_attributes=True)
            for entry in view.entries
        ],
        components=[
            RepositoryComponentResponse.model_validate(component, from_attributes=True)
            for component in view.components
        ],
    )


@router.post("", response_model=RepositoryDiscoveryViewResponse)
async def discover_repository(
    project_id: UUID,
    body: DiscoverRepositoryRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    github: GitHubProviderDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> RepositoryDiscoveryViewResponse:
    view = await RepositoryDiscoveryService(
        actor=actor, uow_factory=uow_factory, github=github
    ).discover(
        DiscoverRepositoryCommand(
            project_id=project_id,
            repository_snapshot_id=body.repository_snapshot_id,
            paper_repository_relationship_id=body.paper_repository_relationship_id,
            discovery_version=settings.repository_discovery_version,
            limits=DiscoveryLimits(
                tree_entry_limit=settings.repository_tree_entry_limit,
                structural_file_limit=settings.repository_file_inspection_limit,
                max_file_bytes=settings.repository_max_file_bytes,
            ),
            request_id=request_id,
        )
    )
    return _response(view)


@router.get("", response_model=list[RepositoryDiscoveryViewResponse])
async def discovery_history(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[RepositoryDiscoveryViewResponse]:
    views = await RepositoryDiscoveryQueryService(actor=actor, uow_factory=uow_factory).history(
        project_id
    )
    return [_response(view) for view in views]


@router.get("/{run_id}", response_model=RepositoryDiscoveryViewResponse)
async def discovery_detail(
    project_id: UUID,
    run_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> RepositoryDiscoveryViewResponse:
    view = await RepositoryDiscoveryQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, run_id
    )
    return _response(view)
