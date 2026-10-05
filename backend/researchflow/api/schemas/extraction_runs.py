"""Transport contracts for bounded proposition extraction requests."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractionSelectionRequest(_StrictModel):
    page_from: Annotated[int, Field(ge=1)] | None = None
    page_to: Annotated[int, Field(ge=1)] | None = None
    sections: Annotated[tuple[str, ...], Field(max_length=50)] = ()

    @model_validator(mode="after")
    def valid_range(self) -> ExtractionSelectionRequest:
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_from > self.page_to
        ):
            raise ValueError("page_filter_invalid")
        if len(self.sections) != len(set(self.sections)) or any(
            not section.strip() for section in self.sections
        ):
            raise ValueError("section_filter_invalid")
        return self


class StartExtractionRequest(_StrictModel):
    workflow_run_id: UUID
    source_processing_run_id: UUID
    selection: ExtractionSelectionRequest = Field(default_factory=ExtractionSelectionRequest)
    max_batches: Annotated[int, Field(ge=1, le=16)] = 16
    continuation_cursor: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None
    explicit_rerun: bool = False


class ExecuteExtractionRequest(_StrictModel):
    source_processing_run_id: UUID
    selection: ExtractionSelectionRequest = Field(default_factory=ExtractionSelectionRequest)
    max_batches: Annotated[int, Field(ge=1, le=16)] = 16
    workflow_run_id: UUID | None = None
    continuation_cursor: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None
    explicit_retry: bool = False

    @model_validator(mode="after")
    def valid_execution_mode(self) -> ExecuteExtractionRequest:
        if self.workflow_run_id is None and (
            self.continuation_cursor is not None or self.explicit_retry
        ):
            raise ValueError("existing_workflow_required")
        if self.workflow_run_id is not None and (
            (self.continuation_cursor is None) == (not self.explicit_retry)
        ):
            raise ValueError("exactly_one_continuation_or_retry_required")
        return self


class CandidateExtractionResponse(_StrictModel):
    client_id: str
    object_type: Literal[
        "claim", "assumption", "knowledge_gap", "implementation_requirement", "ambiguity"
    ]
    knowledge_object_id: UUID
    disposition: Literal["persisted", "exact_suppressed", "semantic_suppressed"]
    evidence_link_ids: tuple[UUID, ...]
    grounding_status: Literal[
        "unvalidated",
        "grounded",
        "partially_grounded",
        "mixed",
        "contradicted",
        "unsupported",
    ]
    warnings: tuple[str, ...]


class ExtractionBatchResponse(_StrictModel):
    batch_sequence: int
    chunk_ids: tuple[UUID, ...]
    status: Literal[
        "succeeded",
        "provider_failed",
        "validation_failed",
        "persistence_failed",
    ]
    generation_record_id: UUID | None
    comparison_generation_record_ids: tuple[UUID, ...]
    candidates: tuple[CandidateExtractionResponse, ...]
    rejected_diagnostics: tuple[dict[str, object], ...]
    dedup_diagnostics: tuple[dict[str, object], ...]
    error_code: str | None
    error_detail: dict[str, object] | None


class ExtractionRequestResponse(_StrictModel):
    workflow_run_id: UUID
    source_processing_run_id: UUID
    status: Literal["completed", "completed_with_diagnostics", "partial", "failed"]
    complete: bool
    eligible_chunk_count: int
    batches: tuple[ExtractionBatchResponse, ...]
    continuation_cursor: str | None


class ExtractionWorkflowResponse(_StrictModel):
    id: UUID
    status: Literal[
        "queued",
        "running",
        "waiting_for_review",
        "waiting_for_judgment",
        "completed",
        "failed",
        "cancelled",
    ]
    current_stage: str | None
    lock_version: int
    last_error_code: str | None
    last_error_detail: dict[str, object] | None


class ExtractionExecutionResponse(_StrictModel):
    workflow: ExtractionWorkflowResponse
    extraction: ExtractionRequestResponse
    replayed: bool


class ExtractionStatusResponse(_StrictModel):
    workflow_run_id: UUID
    batches: tuple[ExtractionBatchResponse, ...]
    generation_attempts: tuple[ExtractionGenerationAttemptResponse, ...]


class ExtractionGenerationAttemptResponse(_StrictModel):
    id: UUID
    task_name: Literal["extract_propositions", "compare_proposition_duplicates"]
    input_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["succeeded", "failed"]
    error_code: str | None
