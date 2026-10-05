"""Transport contracts for candidate MethodModel extraction and inspection."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StartMethodExtractionRequest(_StrictModel):
    workflow_run_id: UUID
    source_processing_run_id: UUID
    explicit_rerun: bool = False


class MethodElementResponse(_StrictModel):
    id: UUID
    client_id: str
    element_type: str
    review_section: str
    sequence_index: int | None
    grounding_status: str
    evidence_link_ids: tuple[UUID, ...]


class MethodExtractionResponse(_StrictModel):
    workflow_run_id: UUID
    status: Literal["completed", "completed_with_diagnostics"]
    method_model_id: UUID | None
    batch_generation_record_ids: tuple[UUID, ...]
    merge_generation_record_id: UUID | None
    elements: tuple[MethodElementResponse, ...]
    rejected_diagnostics: tuple[dict[str, object], ...]
    merge_accountability: tuple[dict[str, object], ...]
    assessment_id: UUID | None
    assessment_outcome: str | None
    error_code: str | None


class MethodGenerationAttemptResponse(_StrictModel):
    id: UUID
    task_name: Literal["extract_method_model", "merge_method_model"]
    input_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["succeeded", "failed"]
    error_code: str | None


class MethodExtractionStatusResponse(_StrictModel):
    workflow_run_id: UUID
    result: MethodExtractionResponse | None
    generation_attempts: tuple[MethodGenerationAttemptResponse, ...]


class MethodSectionElementResponse(_StrictModel):
    id: UUID
    element_type: str
    epistemic_status: str
    confidence: float | None
    review_status: str
    lifecycle_status: str
    grounding_status: str
    member_role: str
    sequence_index: int | None
    required: bool
    inclusion_rationale: str
    subtype_payload: dict[str, object]


class MethodSectionPageResponse(_StrictModel):
    method_model_id: UUID
    review_section: str
    total_count: int
    returned_count: int
    has_more: bool
    next_cursor: str | None
    elements: tuple[MethodSectionElementResponse, ...]
