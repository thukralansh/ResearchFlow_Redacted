"""Typed Repository Stage A discovery API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DiscoverRepositoryRequest(BaseModel):
    repository_snapshot_id: UUID
    paper_repository_relationship_id: UUID


class RepositoryDiscoveryRunResponse(BaseModel):
    id: UUID
    repository_snapshot_id: UUID
    discovery_version: str
    tree_hash: str | None
    configured_limits: dict[str, object]
    coverage_status: str
    status: str
    error_code: str | None
    error_detail: str | None
    created_at: datetime


class RepositoryTreeEntryResponse(BaseModel):
    id: UUID
    discovery_run_id: UUID
    path: str
    entry_type: str
    language: str | None
    coarse_role: str | None
    size_bytes: int | None
    excluded_reason: str | None


class RepositoryComponentResponse(BaseModel):
    id: UUID
    repository_snapshot_id: UUID
    analysis_stage: str
    repository_discovery_run_id: UUID | None
    path: str
    component_type: str
    language: str | None
    line_start: int | None
    line_end: int | None
    content_excerpt: str
    content_hash: str


class RepositoryDiscoveryViewResponse(BaseModel):
    run: RepositoryDiscoveryRunResponse
    entries: list[RepositoryTreeEntryResponse]
    components: list[RepositoryComponentResponse]
