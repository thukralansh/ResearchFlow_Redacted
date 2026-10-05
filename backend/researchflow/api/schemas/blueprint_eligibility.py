"""Strict  Blueprint eligibility command and inspection contracts."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.api.schemas.knowledge import OperationEventResponse


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssessBlueprintEligibilityRequest(_StrictModel):
    workflow_run_id: UUID
    mapping_review_gate_event_id: UUID
    command_id: UUID


class BlueprintEligibilityAssessmentResponse(_StrictModel):
    id: UUID
    project_id: UUID
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    mental_model_id: UUID
    mental_model_assessment_id: UUID
    paper_repository_relationship_id: UUID | None
    repository_snapshot_id: UUID | None
    reproduction_depth: str
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligibility_rule_version: str
    mapping_coverage: dict[str, Any]
    status: str


class BlueprintEligibilityItemResponse(_StrictModel):
    id: UUID
    assessment_id: UUID
    sequence_index: int
    expected_dependency_type: str
    target_id: UUID | None
    criticality: str
    outcome: str
    reason_code: str
    explanation: str


class BlueprintEligibilityDetailResponse(_StrictModel):
    assessment: BlueprintEligibilityAssessmentResponse
    items: tuple[BlueprintEligibilityItemResponse, ...]


class BlueprintEligibilityCommandResponse(BlueprintEligibilityDetailResponse):
    event: OperationEventResponse
    replayed: bool
