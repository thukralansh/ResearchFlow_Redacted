"""Project HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status

from researchflow.api.dependencies import (
    ActorDependency,
    ProjectDirectoryDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.schemas.projects import (
    ProjectCreateRequest,
    ProjectInquiryResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
)
from researchflow.api.schemas.research_questions import ResearchQuestionResponse
from researchflow.application.projects import (
    CreateProjectCommand,
    ProjectApplicationService,
    ProjectInquiryView,
    UpdateProjectCommand,
)
from researchflow.domain.projects import IDEMPOTENCY_KEY_MAX
from researchflow.ports.repositories import ProjectData, ResearchQuestionData

router = APIRouter(prefix="/projects", tags=["projects"])


def _service(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    directory: ProjectDirectoryDependency,
) -> ProjectApplicationService:
    return ProjectApplicationService(actor=actor, uow_factory=uow_factory, directory=directory)


def _project_response(project: ProjectData) -> ProjectSummaryResponse:
    return ProjectSummaryResponse(
        id=project.id,
        primary_research_question_id=project.primary_research_question_id,
        title=project.title,
        context_constraints=project.description,
        target_outcome=project.target_outcome,
        reproduction_depth=project.reproduction_depth,
        lock_version=project.lock_version,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _question_response(question: ResearchQuestionData) -> ResearchQuestionResponse:
    return ResearchQuestionResponse.model_validate(question, from_attributes=True)


def _inquiry_response(view: ProjectInquiryView) -> ProjectInquiryResponse:
    return ProjectInquiryResponse(
        project=_project_response(view.project),
        primary_question=_question_response(view.primary_question),
        current_questions=[_question_response(question) for question in view.current_questions],
    )


@router.post("", response_model=ProjectInquiryResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    response: Response,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    directory: ProjectDirectoryDependency,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX),
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ProjectInquiryResponse:
    view = await _service(actor, uow_factory, directory).create(
        CreateProjectCommand(
            title=payload.title,
            primary_question=payload.primary_question,
            scope=payload.scope,
            target_outcome=payload.target_outcome,
            reproduction_depth=payload.reproduction_depth,
            context_constraints=payload.context_constraints,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    )
    response.headers["ETag"] = f'"{view.project.lock_version}"'
    return _inquiry_response(view)


@router.get("", response_model=list[ProjectSummaryResponse])
async def list_projects(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    directory: ProjectDirectoryDependency,
) -> list[ProjectSummaryResponse]:
    projects = await _service(actor, uow_factory, directory).list_projects()
    return [_project_response(project) for project in projects]


@router.get("/{project_id}", response_model=ProjectInquiryResponse)
async def get_project(
    project_id: UUID,
    response: Response,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    directory: ProjectDirectoryDependency,
) -> ProjectInquiryResponse:
    view = await _service(actor, uow_factory, directory).get(project_id)
    response.headers["ETag"] = f'"{view.project.lock_version}"'
    return _inquiry_response(view)


@router.patch("/{project_id}", response_model=ProjectInquiryResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    response: Response,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    directory: ProjectDirectoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ProjectInquiryResponse:
    view = await _service(actor, uow_factory, directory).update(
        UpdateProjectCommand(
            project_id=project_id,
            expected_lock_version=payload.expected_lock_version,
            title=payload.title,
            context_constraints=payload.context_constraints,
            target_outcome=payload.target_outcome,
            reproduction_depth=payload.reproduction_depth,
            request_id=request_id,
        )
    )
    response.headers["ETag"] = f'"{view.project.lock_version}"'
    return _inquiry_response(view)
