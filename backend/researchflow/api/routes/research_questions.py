"""Research Question HTTP routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from researchflow.api.dependencies import ActorDependency, UnitOfWorkFactoryDependency
from researchflow.api.schemas.research_questions import (
    ChangeInquiryStatusRequest,
    CreateSubquestionRequest,
    RefineQuestionRequest,
    ResearchQuestionResponse,
)
from researchflow.application.research_questions import (
    ChangeInquiryStatusCommand,
    CreateSubquestionCommand,
    RefineQuestionCommand,
    ResearchQuestionApplicationService,
)
from researchflow.application.staleness import StalenessService
from researchflow.ports.repositories import ResearchQuestionData

router = APIRouter(prefix="/projects/{project_id}/questions", tags=["research-questions"])


def _response(question: ResearchQuestionData) -> ResearchQuestionResponse:
    return ResearchQuestionResponse.model_validate(question, from_attributes=True)


def _service(
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> ResearchQuestionApplicationService:
    return ResearchQuestionApplicationService(
        actor=actor,
        uow_factory=uow_factory,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    )


@router.get("", response_model=list[ResearchQuestionResponse])
async def list_questions(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> list[ResearchQuestionResponse]:
    questions = await _service(actor, uow_factory).list_current(project_id)
    return [_response(question) for question in questions]


@router.post("", response_model=ResearchQuestionResponse, status_code=status.HTTP_201_CREATED)
async def create_subquestion(
    project_id: UUID,
    payload: CreateSubquestionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ResearchQuestionResponse:
    result = await _service(actor, uow_factory).create_subquestion(
        CreateSubquestionCommand(
            project_id=project_id,
            parent_question_id=payload.parent_question_id,
            question_text=payload.question_text,
            scope_text=payload.scope_text,
            priority=payload.priority,
            request_id=request_id,
        )
    )
    return _response(result)


@router.post(
    "/{question_id}/refinements",
    response_model=ResearchQuestionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def refine_question(
    project_id: UUID,
    question_id: UUID,
    payload: RefineQuestionRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ResearchQuestionResponse:
    result = await _service(actor, uow_factory).refine(
        RefineQuestionCommand(
            project_id=project_id,
            question_id=question_id,
            expected_version=payload.expected_version,
            expected_project_lock_version=payload.expected_project_lock_version,
            question_text=payload.question_text,
            scope_text=payload.scope_text,
            request_id=request_id,
        )
    )
    return _response(result)


@router.patch("/{question_id}/inquiry-status", response_model=ResearchQuestionResponse)
async def change_inquiry_status(
    project_id: UUID,
    question_id: UUID,
    payload: ChangeInquiryStatusRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ResearchQuestionResponse:
    result = await _service(actor, uow_factory).change_status(
        ChangeInquiryStatusCommand(
            project_id=project_id,
            question_id=question_id,
            expected_version=payload.expected_version,
            expected_status=payload.expected_status,
            target_status=payload.target_status,
            request_id=request_id,
        )
    )
    return _response(result)


@router.post("/{question_id}/reopen", response_model=ResearchQuestionResponse)
async def reopen_question(
    project_id: UUID,
    question_id: UUID,
    payload: ChangeInquiryStatusRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ResearchQuestionResponse:
    result = await _service(actor, uow_factory).reopen(
        ChangeInquiryStatusCommand(
            project_id=project_id,
            question_id=question_id,
            expected_version=payload.expected_version,
            expected_status=payload.expected_status,
            target_status=payload.target_status,
            request_id=request_id,
        )
    )
    return _response(result)
