"""Typed Project API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.api.schemas.research_questions import ResearchQuestionResponse
from researchflow.domain.projects import (
    PROJECT_DESCRIPTION_MAX,
    PROJECT_TITLE_MAX,
    REPRODUCTION_DEPTH_MAX,
    TARGET_OUTCOME_MAX,
)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=PROJECT_TITLE_MAX)
    primary_question: str = Field(min_length=1, max_length=8_000)
    scope: str | None = Field(default=None, max_length=8_000)
    target_outcome: str = Field(min_length=1, max_length=TARGET_OUTCOME_MAX)
    reproduction_depth: str = Field(min_length=1, max_length=REPRODUCTION_DEPTH_MAX)
    context_constraints: str | None = Field(default=None, max_length=PROJECT_DESCRIPTION_MAX)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    expected_lock_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=PROJECT_TITLE_MAX)
    target_outcome: str = Field(min_length=1, max_length=TARGET_OUTCOME_MAX)
    reproduction_depth: str = Field(min_length=1, max_length=REPRODUCTION_DEPTH_MAX)
    context_constraints: str | None = Field(default=None, max_length=PROJECT_DESCRIPTION_MAX)


class ProjectSummaryResponse(BaseModel):
    id: UUID
    primary_research_question_id: UUID
    title: str
    context_constraints: str | None
    target_outcome: str
    reproduction_depth: str
    lock_version: int
    created_at: datetime
    updated_at: datetime


class ProjectInquiryResponse(BaseModel):
    project: ProjectSummaryResponse
    primary_question: ResearchQuestionResponse
    current_questions: list[ResearchQuestionResponse]
