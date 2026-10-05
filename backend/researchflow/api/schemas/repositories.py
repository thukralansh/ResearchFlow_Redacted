"""Typed public repository attachment and immutable snapshot contracts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AttachRepositoryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    research_question_id: UUID
    primary_paper_relationship_id: UUID
    source_id: UUID
    repository_url: str = Field(min_length=1, max_length=500)
    requested_ref: str | None = Field(default=None, max_length=255)
    relationship_type: str = Field(min_length=1, max_length=100)
    authority_level: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_text: str | None = Field(default=None, max_length=4_000)
    confidence: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    human_confirmed: bool
    replace_current: bool = False


class RepositoryResponse(BaseModel):
    id: UUID
    project_id: UUID
    owner: str
    name: str
    normalized_url: str
    default_branch: str | None
    description: str | None
    is_private: bool
    created_at: datetime
    updated_at: datetime


class RepositorySnapshotResponse(BaseModel):
    id: UUID
    repository_id: UUID
    requested_ref: str
    commit_sha: str
    status: str
    lifecycle_status: str
    supersedes_id: UUID | None
    fetched_at: datetime


class PaperRepositoryRelationshipResponse(BaseModel):
    id: UUID
    project_id: UUID
    source_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID | None
    lineage_id: UUID
    version: int
    relationship_type: str
    authority_level: str
    rationale: str
    evidence_text: str | None
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    created_by_type: str
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_at: datetime


class RepositorySnapshotViewResponse(BaseModel):
    repository: RepositoryResponse
    snapshot: RepositorySnapshotResponse
    relationship: PaperRepositoryRelationshipResponse
