"""Strict  post-mapping MethodModel reassessment contracts."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodElementReplacement(_StrictModel):
    current_element_id: UUID
    replacement_element_id: UUID
    rationale: Annotated[str, Field(min_length=1, max_length=4_000)]


class MethodModelReassessmentBatch(_StrictModel):
    result: Literal["no_change", "new_version_created"]
    rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    replacements: Annotated[list[MethodElementReplacement], Field(max_length=128)]
    confidence: Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)] | None
    authority_disclosure: Annotated[str, Field(min_length=1, max_length=4_000)]

    @model_validator(mode="after")
    def exact_result_shape(self) -> "MethodModelReassessmentBatch":
        if (self.result == "no_change") != (not self.replacements):
            raise ValueError("method_reassessment_result_shape_invalid")
        current_ids = [item.current_element_id for item in self.replacements]
        replacement_ids = [item.replacement_element_id for item in self.replacements]
        if len(current_ids) != len(set(current_ids)) or len(replacement_ids) != len(
            set(replacement_ids)
        ):
            raise ValueError("method_reassessment_replacement_duplicate")
        return self


class MethodModelReassessmentRequest(_StrictModel):
    workflow_run_id: UUID
    mapping_review_gate_event_id: UUID
    applicable: bool = True
    explicit_rerun: bool = False


class SectionAuthorityResponse(_StrictModel):
    review_section: str
    source_section_review_id: UUID
    resulting_section_review_id: UUID
    source_reviewer_id: UUID
    unchanged_fingerprint: bool
    accepted_replacement_ids: list[UUID]
    rationale: str


class MethodModelReassessmentResponse(_StrictModel):
    outcome: Literal["not_applicable", "no_change", "new_version_created"]
    generation_record_id: UUID | None
    assessment_id: UUID | None
    prior_method_model_id: UUID
    resulting_method_model_id: UUID
    mapping_review_gate_event_id: UUID
    event_id: UUID
    replayed: bool
    changed_element_ids: list[UUID]
    unchanged_element_ids: list[UUID]
    section_authority: list[SectionAuthorityResponse]
    rationale: str
    authority_disclosure: str | None
