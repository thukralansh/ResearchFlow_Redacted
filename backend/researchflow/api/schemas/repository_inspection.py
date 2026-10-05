"""Typed Repository Stage B inspection API contracts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class InspectRepositoryRequest(BaseModel):
    applicable: bool = True
    applicability_rationale: str = Field(min_length=1, max_length=4_000)
    repository_snapshot_id: UUID | None = None
    paper_repository_relationship_id: UUID | None = None
    discovery_run_id: UUID | None = None

    @model_validator(mode="after")
    def validate_branch_shape(self) -> "InspectRepositoryRequest":
        refs = (
            self.repository_snapshot_id,
            self.paper_repository_relationship_id,
            self.discovery_run_id,
        )
        if (self.applicable and not all(value is not None for value in refs)) or (
            not self.applicable and any(value is not None for value in refs)
        ):
            raise ValueError("repository_branch_shape_invalid")
        return self


class RepositoryInspectionRunResponse(BaseModel):
    id: UUID
    repository_snapshot_id: UUID
    discovery_run_id: UUID
    guidance_fingerprint: str
    ranking_version: str
    analysis_version: str
    configured_limits: dict[str, object]
    selected_paths: list[str]
    inspected_paths: list[str]
    coverage_status: str
    status: str
    error_code: str | None
    error_detail: str | None
    created_at: datetime


class RepositoryInspectionComponentResponse(BaseModel):
    id: UUID
    repository_snapshot_id: UUID
    repository_inspection_run_id: UUID | None
    analysis_stage: str
    path: str
    component_type: str
    language: str | None
    symbol: str | None
    line_start: int | None
    line_end: int | None
    content_excerpt: str
    content_hash: str
    relevance_score: Decimal | None


class RepositoryInspectionViewResponse(BaseModel):
    outcome: str
    applicability_rationale: str
    run: RepositoryInspectionRunResponse | None
    components: list[RepositoryInspectionComponentResponse]
    ranking: list[dict[str, object]]
