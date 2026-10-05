"""Applicable Repository Stage B inspection routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from researchflow.api.dependencies import (
    ActorDependency,
    GitHubProviderDependency,
    SettingsDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.schemas.repository_inspection import (
    InspectRepositoryRequest,
    RepositoryInspectionComponentResponse,
    RepositoryInspectionRunResponse,
    RepositoryInspectionViewResponse,
)
from researchflow.application.repository_inspection import (
    InspectRepositoryCommand,
    RepositoryInspectionQueryService,
    RepositoryInspectionService,
    RepositoryInspectionView,
)
from researchflow.domain.repository_ranking import InspectionLimits, RankingWeights

router = APIRouter(
    prefix="/projects/{project_id}/repositories/inspection",
    tags=["repository-inspection"],
)


def _response(view: RepositoryInspectionView) -> RepositoryInspectionViewResponse:
    return RepositoryInspectionViewResponse(
        outcome=view.outcome,
        applicability_rationale=view.applicability_rationale,
        run=(
            None
            if view.run is None
            else RepositoryInspectionRunResponse.model_validate(view.run, from_attributes=True)
        ),
        components=[
            RepositoryInspectionComponentResponse.model_validate(item, from_attributes=True)
            for item in view.components
        ],
        ranking=list(view.ranking),
    )


@router.post("", response_model=RepositoryInspectionViewResponse)
async def inspect_repository(
    project_id: UUID,
    body: InspectRepositoryRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    github: GitHubProviderDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> RepositoryInspectionViewResponse:
    view = await RepositoryInspectionService(
        actor=actor,
        uow_factory=uow_factory,
        github=github,
    ).inspect(
        InspectRepositoryCommand(
            project_id=project_id,
            applicable=body.applicable,
            applicability_rationale=body.applicability_rationale,
            repository_snapshot_id=body.repository_snapshot_id,
            paper_repository_relationship_id=body.paper_repository_relationship_id,
            discovery_run_id=body.discovery_run_id,
            ranking_version=settings.repository_ranking_version,
            analysis_version=settings.repository_analysis_version,
            limits=InspectionLimits(
                candidate_limit=settings.repository_stage_b_candidate_limit,
                inspection_limit=settings.repository_stage_b_inspection_limit,
                max_file_bytes=settings.repository_max_file_bytes,
                aggregate_bytes=settings.repository_stage_b_aggregate_bytes,
            ),
            weights=RankingWeights(),
            request_id=request_id,
        )
    )
    return _response(view)


@router.get("", response_model=list[RepositoryInspectionViewResponse])
async def inspection_history(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[RepositoryInspectionViewResponse]:
    views = await RepositoryInspectionQueryService(actor=actor, uow_factory=uow_factory).history(
        project_id
    )
    return [_response(view) for view in views]


@router.get("/{run_id}", response_model=RepositoryInspectionViewResponse)
async def inspection_detail(
    project_id: UUID,
    run_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> RepositoryInspectionViewResponse:
    view = await RepositoryInspectionQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, run_id
    )
    return _response(view)
