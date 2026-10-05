"""Strict control-plane-only LangGraph state for project analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, NotRequired, TypedDict, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ANALYSIS_GRAPH_VERSION = "rf-project-analysis-graph-v1"
PROJECT_ANALYSIS_CHECKPOINT_NAMESPACE = "project-analysis"


class WorkflowStage(StrEnum):
    ENTRY = "entry"
    VALIDATE_INPUTS = "validate_inputs"
    PROCESS_PRIMARY_SOURCE = "process_primary_source"
    DISCOVER_REPOSITORY = "discover_repository"
    EXTRACT_PROPOSITIONS = "extract_propositions"
    EXTRACT_METHOD_MODEL = "extract_method_model"
    VALIDATE_GROUNDING = "validate_grounding"
    PREPARE_SCIENTIFIC_REVIEW = "prepare_scientific_review"
    AWAIT_SCIENTIFIC_REVIEW = "await_scientific_review"
    FINALIZE_ACCEPTED_METHOD = "finalize_accepted_method"
    COMPOSE_INITIAL_MENTAL_MODEL = "compose_initial_mental_model"
    FINAL = "final"
    ERROR = "error"


class WorkflowControlStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    WAITING_FOR_JUDGMENT = "waiting_for_judgment"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProjectWorkflowStatePayload(TypedDict):
    workflow_run_id: str
    project_id: str
    primary_research_question_id: str
    node_input_fingerprint: str
    graph_version: str
    stage: str
    status: str
    node_sequence: int
    primary_source_processing_run_id: NotRequired[str]
    primary_question_source_relationship_id: NotRequired[str]
    paper_repository_relationship_id: NotRequired[str]
    repository_snapshot_id: NotRequired[str]
    current_method_model_id: NotRequired[str]
    current_mental_model_id: NotRequired[str]
    pending_judgment_request_id: NotRequired[str]
    pending_workflow_interrupt_id: NotRequired[str]
    review_context_fingerprint: NotRequired[str]
    repository_discovery_run_id: NotRequired[str]
    proposition_extraction_complete: NotRequired[bool]
    error_code: NotRequired[str]


class ProjectWorkflowState(BaseModel):
    """Serializable IDs, fingerprints, enums, and bounded bookkeeping only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_run_id: UUID
    project_id: UUID
    primary_research_question_id: UUID
    primary_source_processing_run_id: UUID | None = None
    primary_question_source_relationship_id: UUID | None = None
    paper_repository_relationship_id: UUID | None = None
    repository_snapshot_id: UUID | None = None
    current_method_model_id: UUID | None = None
    current_mental_model_id: UUID | None = None
    pending_judgment_request_id: UUID | None = None
    pending_workflow_interrupt_id: UUID | None = None
    review_context_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    repository_discovery_run_id: UUID | None = None
    proposition_extraction_complete: bool = False
    graph_version: str = PROJECT_ANALYSIS_GRAPH_VERSION
    stage: WorkflowStage = WorkflowStage.ENTRY
    status: WorkflowControlStatus = WorkflowControlStatus.QUEUED
    node_input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_sequence: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=200)

    @field_validator("graph_version")
    @classmethod
    def supported_graph_version(cls, value: str) -> str:
        if value != PROJECT_ANALYSIS_GRAPH_VERSION:
            raise ValueError("workflow_graph_version_unsupported")
        return value

    def checkpoint_values(self) -> ProjectWorkflowStatePayload:
        """Return JSON/msgpack-safe primitives; never arbitrary domain objects."""
        return cast(
            ProjectWorkflowStatePayload,
            self.model_dump(mode="json", exclude_none=True),
        )


def node_idempotency_key(
    *, workflow_run_id: UUID, stage: str, input_fingerprint: str, graph_version: str
) -> str:
    payload = {
        "graph_version": graph_version,
        "input_fingerprint": input_fingerprint,
        "stage": stage,
        "workflow_run_id": str(workflow_run_id),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_checkpoint_state(value: Mapping[str, Any]) -> ProjectWorkflowState:
    """Fail closed on unknown/payload-bearing state, including restored checkpoints."""
    return ProjectWorkflowState.model_validate(value)
