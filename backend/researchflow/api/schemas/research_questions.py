"""Typed Research Question API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.domain.research_questions import (
    QUESTION_SCOPE_MAX,
    QUESTION_TEXT_MAX,
    InquiryStatus,
    QuestionPriority,
)


class ResearchQuestionResponse(BaseModel):
    id: UUID
    project_id: UUID
    parent_question_id: UUID | None
    lineage_id: UUID
    version: int
    question_text: str
    question_type: str
    scope_text: str | None
    inquiry_status: InquiryStatus
    lifecycle_status: str
    priority: QuestionPriority
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_at: datetime
    updated_at: datetime


class RefineQuestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    expected_project_lock_version: int | None = Field(default=None, ge=1)
    question_text: str = Field(min_length=1, max_length=QUESTION_TEXT_MAX)
    scope_text: str | None = Field(default=None, max_length=QUESTION_SCOPE_MAX)


class CreateSubquestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    parent_question_id: UUID
    question_text: str = Field(min_length=1, max_length=QUESTION_TEXT_MAX)
    scope_text: str | None = Field(default=None, max_length=QUESTION_SCOPE_MAX)
    priority: QuestionPriority = QuestionPriority.MEDIUM


class ChangeInquiryStatusRequest(BaseModel):
    expected_version: int = Field(ge=1)
    expected_status: InquiryStatus
    target_status: InquiryStatus
