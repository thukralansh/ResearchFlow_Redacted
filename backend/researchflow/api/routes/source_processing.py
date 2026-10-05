"""Primary PDF processing, status, and exact provenance chunk routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from researchflow.api.dependencies import (
    ActorDependency,
    PdfParserDependency,
    StorageDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.schemas.source_processing import (
    ProcessSourceRequest,
    SourceChunkResponse,
    SourceProcessingResponse,
    SourceProcessingRunResponse,
)
from researchflow.application.source_processing import (
    ProcessSourceCommand,
    SourceProcessingQueryService,
    SourceProcessingService,
    SourceProcessingView,
)
from researchflow.application.staleness import StalenessService

router = APIRouter(prefix="/projects/{project_id}/sources/{source_id}/processing", tags=["sources"])


def _response(view: SourceProcessingView) -> SourceProcessingResponse:
    return SourceProcessingResponse(
        run=SourceProcessingRunResponse.model_validate(view.run, from_attributes=True),
        chunks=tuple(
            SourceChunkResponse.model_validate(chunk, from_attributes=True) for chunk in view.chunks
        ),
        message=view.message,
    )


@router.post("", response_model=SourceProcessingResponse)
async def process_source(
    project_id: UUID,
    source_id: UUID,
    body: ProcessSourceRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    storage: StorageDependency,
    parser: PdfParserDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> SourceProcessingResponse:
    view = await SourceProcessingService(
        actor=actor,
        uow_factory=uow_factory,
        storage=storage,
        parser=parser,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    ).process(
        ProcessSourceCommand(
            project_id=project_id,
            source_id=source_id,
            research_question_id=body.research_question_id,
            question_source_relationship_id=body.question_source_relationship_id,
            request_id=request_id,
        )
    )
    return _response(view)


@router.get("", response_model=list[SourceProcessingResponse])
async def list_processing_runs(
    project_id: UUID,
    source_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[SourceProcessingResponse]:
    views = await SourceProcessingQueryService(actor=actor, uow_factory=uow_factory).list_runs(
        project_id, source_id
    )
    return [_response(view) for view in views]


@router.get("/{run_id}", response_model=SourceProcessingResponse)
async def get_processing_run(
    project_id: UUID,
    source_id: UUID,
    run_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> SourceProcessingResponse:
    view = await SourceProcessingQueryService(actor=actor, uow_factory=uow_factory).get_run(
        project_id, source_id, run_id
    )
    return _response(view)
