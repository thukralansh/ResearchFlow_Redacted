"""Primary Paper upload and metadata routes."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, UploadFile, status

from researchflow.api.dependencies import (
    ActorDependency,
    SettingsDependency,
    StorageDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.schemas.sources import (
    PrimaryPaperResponse,
    QuestionSourceRelationshipResponse,
    SourceResponse,
    SourceUploadMetadata,
)
from researchflow.application.sources import (
    PrimaryPaperView,
    SourceApplicationService,
    SourceQueryService,
    UploadPrimaryPaperCommand,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.projects import IDEMPOTENCY_KEY_MAX

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


def _upload_service(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    storage: StorageDependency,
    settings: SettingsDependency,
) -> SourceApplicationService:
    return SourceApplicationService(
        actor=actor,
        uow_factory=uow_factory,
        storage=storage,
        storage_bucket=settings.supabase_storage_bucket,
        maximum_upload_bytes=settings.max_upload_bytes,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    )


def _query_service(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> SourceQueryService:
    return SourceQueryService(actor=actor, uow_factory=uow_factory)


def _response(view: PrimaryPaperView) -> PrimaryPaperResponse:
    return PrimaryPaperResponse(
        source=SourceResponse.model_validate(view.source, from_attributes=True),
        relationship=QuestionSourceRelationshipResponse.model_validate(
            view.relationship,
            from_attributes=True,
        ),
    )


@router.post(
    "/primary-paper",
    response_model=PrimaryPaperResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_primary_paper(
    project_id: UUID,
    file: Annotated[UploadFile, File()],
    research_question_id: Annotated[UUID, Form()],
    title: Annotated[str, Form()],
    relationship_rationale: Annotated[str, Form()],
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    storage: StorageDependency,
    settings: SettingsDependency,
    authors_json: Annotated[str, Form()] = "[]",
    identifiers_json: Annotated[str, Form()] = "{}",
    relevance_scope: Annotated[str | None, Form()] = None,
    confidence: Annotated[Decimal | None, Form()] = None,
    replace_current: Annotated[bool, Form()] = False,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX),
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> PrimaryPaperResponse:
    metadata = SourceUploadMetadata.from_form(
        title=title,
        authors_json=authors_json,
        identifiers_json=identifiers_json,
        relationship_rationale=relationship_rationale,
        relevance_scope=relevance_scope,
        confidence=confidence,
    )
    try:
        view = await _upload_service(actor, uow_factory, storage, settings).upload_primary_paper(
            UploadPrimaryPaperCommand(
                project_id=project_id,
                research_question_id=research_question_id,
                upload=file,
                original_filename=file.filename or "",
                mime_type=file.content_type or "application/octet-stream",
                title=metadata.title,
                authors=tuple(metadata.authors),
                identifiers=metadata.identifiers,
                relationship_rationale=metadata.relationship_rationale,
                relevance_scope=metadata.relevance_scope,
                confidence=metadata.confidence,
                replace_current=replace_current,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
        )
        return _response(view)
    finally:
        await file.close()


@router.get("", response_model=list[PrimaryPaperResponse])
async def list_primary_papers(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[PrimaryPaperResponse]:
    views = await _query_service(actor, uow_factory).list_primary_papers(project_id)
    return [_response(view) for view in views]


@router.get("/{source_id}", response_model=PrimaryPaperResponse)
async def get_source(
    project_id: UUID,
    source_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> PrimaryPaperResponse:
    view = await _query_service(actor, uow_factory).get(project_id, source_id)
    return _response(view)
