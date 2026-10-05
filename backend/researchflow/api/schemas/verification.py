"""Strict VerificationCheck generation, result, and response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from researchflow.api.schemas.implementation import (
    BoundedText,
    ImplementationStepProposal,
    ImplementationStepResponse,
    PlanReference,
    SectionReference,
)
from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.domain.implementation import IMPLEMENTATION_MAX_CHECKS
from researchflow.domain.verification import VerificationTestType


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VerificationCheckProposal(_StrictModel):
    client_id: Annotated[str, Field(pattern=r"^check-[0-9]{4}$")]
    step_client_id: Annotated[str, Field(pattern=r"^step-[0-9]{4}$")]
    section_ref: SectionReference
    sequence_index: Annotated[int, Field(ge=1, le=IMPLEMENTATION_MAX_CHECKS)]
    condition: BoundedText
    test_type: VerificationTestType
    expected_result: BoundedText
    failure_meaning: BoundedText
    likely_causes: Annotated[list[BoundedText], Field(min_length=1, max_length=16)]
    references: Annotated[list[PlanReference], Field(min_length=1, max_length=64)]
    warning_refs: Annotated[list[str], Field(max_length=128)]

    @field_validator("likely_causes")
    @classmethod
    def unique_causes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("verification_likely_cause_duplicate")
        return value

    @field_validator("references")
    @classmethod
    def unique_references(cls, value: list[PlanReference]) -> list[PlanReference]:
        if len({item.ref for item in value}) != len(value):
            raise ValueError("verification_reference_duplicate")
        return value

    @field_validator("warning_refs")
    @classmethod
    def unique_warning_refs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("verification_warning_reference_duplicate")
        return value


class StepsAndChecksGenerationResult(_StrictModel):
    steps: Annotated[list[ImplementationStepProposal], Field(min_length=1, max_length=128)]
    checks: Annotated[list[VerificationCheckProposal], Field(min_length=1, max_length=512)]


class RecordVerificationResultRequest(_StrictModel):
    command_id: UUID
    expected_result_status: str | None
    result_status: str
    actual_result: BoundedText
    rationale: BoundedText


class VerificationCheckResponse(_StrictModel):
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    blueprint_section_id: UUID | None
    implementation_step_id: UUID
    lineage_id: UUID
    version: int
    sequence_index: int
    condition: str
    test_type: VerificationTestType
    expected_result: str
    failure_meaning: str
    likely_causes: tuple[str, ...]
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    actual_result: str | None
    result_status: str | None
    result_actor_id: UUID | None
    result_recorded_at: datetime | None
    created_by_type: str
    created_by_id: UUID | None


class ArtifactDependencyResponse(_StrictModel):
    id: UUID
    artifact_type: str
    artifact_id: UUID
    dependency_type: str
    dependency_id: UUID
    dependency_role: str
    criticality: str


class ImplementationPlanResponse(_StrictModel):
    blueprint_id: UUID
    steps: tuple[ImplementationStepResponse, ...]
    checks: tuple[VerificationCheckResponse, ...]
    dependencies: tuple[ArtifactDependencyResponse, ...]
    event: OperationEventResponse | None = None
    replayed: bool = False


class VerificationResultResponse(_StrictModel):
    check: VerificationCheckResponse
    event: OperationEventResponse
    replayed: bool
