"""Typed transport contracts for direct/bounded artifact staleness."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DirectStalenessRequest(_StrictModel):
    cause_type: str = Field(min_length=1, max_length=100)
    cause_id: UUID
    operation_type: str = Field(min_length=1, max_length=200)
    after_id: UUID | None = None
    expected_preview_fingerprint: Fingerprint | None = None


class DirectStalenessEffectResponse(_StrictModel):
    dependency_id: UUID
    artifact_type: str
    artifact_id: UUID
    effect: Literal["stale", "blocked", "invalid"]
    reason_code: str
    reason: str


class DirectStalenessPreviewResponse(_StrictModel):
    policy_version: Literal["rf-direct-staleness-v1"]
    scope: Literal["direct"]
    complete: Literal[False]
    warning: str
    cause_type: str
    cause_id: UUID
    operation_type: str
    effects: tuple[DirectStalenessEffectResponse, ...]
    fingerprint: Fingerprint


class ArtifactInvalidationResponse(_StrictModel):
    id: UUID
    cause_type: str
    cause_id: UUID
    rule_code: str
    effect: Literal["stale", "blocked", "invalid"]
    reason: str


class ArtifactFreshnessResponse(_StrictModel):
    artifact_type: str
    artifact_id: UUID
    effective_status: Literal["current", "stale", "blocked", "invalid"]
    invalidations: tuple[ArtifactInvalidationResponse, ...]


class ResolveInvalidationRequest(_StrictModel):
    artifact_type: str = Field(min_length=1, max_length=100)
    artifact_id: UUID
    rationale: str = Field(min_length=1, max_length=4_000)
