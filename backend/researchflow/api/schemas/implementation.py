"""Strict ordered implementation-step generation and transport contracts."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.domain.implementation import IMPLEMENTATION_MAX_STEPS

type BoundedText = Annotated[str, Field(min_length=1, max_length=4_000)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlanReference(_StrictModel):
    ref: Annotated[str, Field(pattern=r"^ip[0-9]{4}$")]


class SectionReference(_StrictModel):
    ref: Annotated[str, Field(pattern=r"^bs[0-9]{4}$")]


class ImplementationStepProposal(_StrictModel):
    client_id: Annotated[str, Field(pattern=r"^step-[0-9]{4}$")]
    sequence_index: Annotated[int, Field(ge=1, le=IMPLEMENTATION_MAX_STEPS)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    description: BoundedText
    depends_on: Annotated[list[str], Field(max_length=IMPLEMENTATION_MAX_STEPS - 1)]
    requirement_refs: Annotated[list[PlanReference], Field(max_length=128)]
    references: Annotated[list[PlanReference], Field(min_length=1, max_length=64)]

    @field_validator("depends_on")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("implementation_step_dependency_duplicate")
        return value

    @field_validator("requirement_refs", "references")
    @classmethod
    def unique_references(cls, value: list[PlanReference]) -> list[PlanReference]:
        if len({item.ref for item in value}) != len(value):
            raise ValueError("implementation_reference_duplicate")
        return value


class GenerateImplementationPlanRequest(_StrictModel):
    workflow_run_id: UUID


class ReviseImplementationStepRequest(_StrictModel):
    command_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    description: BoundedText
    rationale: BoundedText


class ImplementationStepResponse(_StrictModel):
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    lineage_id: UUID
    version: int
    sequence_index: int
    title: str
    description: str
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_by_type: str
    created_by_id: UUID | None


class StepRevisionResponse(_StrictModel):
    step: ImplementationStepResponse
    invalidated_check_ids: tuple[UUID, ...]
    event: OperationEventResponse
    replayed: bool
