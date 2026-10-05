"""Strict transport contracts for the  workflow shell."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.api.schemas.knowledge import OperationEventResponse


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateWorkflowRunRequest(_StrictModel):
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    workflow_type: str = "full_analysis"
    primary_research_question_id: UUID
    primary_source_processing_run_id: UUID | None = None
    primary_question_source_relationship_id: UUID | None = None
    paper_repository_relationship_id: UUID | None = None
    repository_snapshot_id: UUID | None = None


class WorkflowMutationRequest(_StrictModel):
    expected_lock_version: Annotated[int, Field(ge=1)]


class ResolveWorkflowInterruptRequest(_StrictModel):
    expected_lock_version: Annotated[int, Field(ge=1)]
    expected_context_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    resolution_event_id: UUID


class ResumeWorkflowRequest(_StrictModel):
    interrupt_id: UUID
    expected_workflow_lock_version: Annotated[int, Field(ge=1)]
    expected_interrupt_lock_version: Annotated[int, Field(ge=1)]
    expected_context_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WorkflowRunResponse(_StrictModel):
    id: UUID
    project_id: UUID
    workflow_type: str
    status: str
    current_stage: str | None
    langgraph_thread_id: str
    graph_version: str
    checkpoint_namespace: str
    primary_research_question_id: UUID
    primary_source_processing_run_id: UUID | None
    primary_question_source_relationship_id: UUID | None
    paper_repository_relationship_id: UUID | None
    repository_snapshot_id: UUID | None
    current_method_model_id: UUID | None
    current_mental_model_id: UUID | None
    pending_judgment_request_id: UUID | None
    lock_version: int
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    last_error_code: str | None
    last_error_detail: dict[str, Any] | None


class WorkflowInterruptResponse(_StrictModel):
    id: UUID
    project_id: UUID
    workflow_run_id: UUID
    interrupt_kind: str
    status: str
    expected_context_fingerprint: str
    target_refs: tuple[dict[str, object], ...]
    resolution_event_id: UUID | None
    lock_version: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    resumed_at: datetime | None


class WorkflowRunCommandResponse(_StrictModel):
    workflow: WorkflowRunResponse
    event: OperationEventResponse
    replayed: bool
