"""Strict  mapping generation and inspection contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType

BoundedText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class MappingSearchScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inspection_run_id: UUID
    inspected_paths: list[BoundedText]
    coverage_status: BoundedText
    configured_limits_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    limitation: BoundedText


class ProposedPaperCodeMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: BoundedText
    paper_anchor_type: PaperAnchorType
    anchor_id: UUID
    anchor_evidence: BoundedText
    repository_component_id: UUID | None = None
    repository_evidence: BoundedText | None = None
    mapping_type: PaperCodeMappingType
    explanation: BoundedText
    confidence: Decimal = Field(ge=0, le=1)
    searched_scope: MappingSearchScope | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ProposedPaperCodeMapping:
        absent = self.mapping_type is PaperCodeMappingType.ABSENT_FROM_REPO
        if absent:
            if self.repository_component_id is not None or self.repository_evidence is not None:
                raise ValueError("absent_mapping_must_not_have_component")
            if self.searched_scope is None:
                raise ValueError("absent_mapping_requires_searched_scope")
            persisted = (
                f"{self.explanation} Bounded-scope limitation: {self.searched_scope.limitation}"
            )
            if len(persisted) > 4000:
                raise ValueError("absent_mapping_persisted_explanation_too_long")
        elif (
            self.repository_component_id is None
            or self.repository_evidence is None
            or self.searched_scope is not None
        ):
            raise ValueError("component_mapping_requires_dual_sided_evidence")
        return self


class MappingDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: BoundedText | None = None
    reason: BoundedText


class PaperCodeMappingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[ProposedPaperCodeMapping]
    diagnostics: list[MappingDiagnostic] = Field(default_factory=list)


class GenerateMappingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID
    applicable: bool = True
    applicability_rationale: BoundedText
    repository_inspection_run_id: UUID | None = None
    explicit_rerun: bool = False

    @model_validator(mode="after")
    def validate_branch(self) -> GenerateMappingsRequest:
        if self.applicable != (self.repository_inspection_run_id is not None):
            raise ValueError("mapping_generation_branch_shape_invalid")
        return self


class MappingAnchorResponse(BaseModel):
    anchor_type: str
    anchor_id: UUID
    text: str
    source_location: dict[str, object] | None = None


class MappingComponentResponse(BaseModel):
    id: UUID
    path: str
    symbol: str | None
    line_start: int | None
    line_end: int | None
    excerpt: str
    content_hash: str


class MappingScopeResponse(BaseModel):
    inspection_run_id: UUID
    inspected_paths: list[str]
    coverage_status: str
    configured_limits: dict[str, object]
    limitation: str


class PaperCodeMappingResponse(BaseModel):
    id: UUID
    lineage_id: UUID
    version: int
    mapping_type: str
    explanation: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    anchor: MappingAnchorResponse
    component: MappingComponentResponse | None
    searched_scope: MappingScopeResponse | None


class MappingReviewMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: BoundedText


class MappingSemanticRevisionRequest(MappingReviewMutationRequest):
    paper_anchor_type: PaperAnchorType
    anchor_id: UUID
    repository_component_id: UUID | None = None
    mapping_type: PaperCodeMappingType
    explanation: BoundedText
    confidence: Decimal | None = Field(default=None, ge=0, le=1)


class AcceptMappingRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command_id: UUID
    replacement_id: UUID
    expected_predecessor_version: Annotated[int, Field(ge=1)]
    expected_predecessor_lifecycle_status: KnowledgeLifecycleStatus
    expected_replacement_version: Annotated[int, Field(ge=1)]
    rationale: BoundedText


class PaperCodeMappingStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID
    repository_inspection_run_id: UUID
    lineage_id: UUID
    version: int
    paper_anchor_type: str
    source_chunk_id: UUID | None
    knowledge_object_id: UUID | None
    method_model_id: UUID | None
    method_component_id: UUID | None
    method_equation_id: UUID | None
    method_symbol_id: UUID | None
    method_algorithm_step_id: UUID | None
    method_data_flow_id: UUID | None
    method_validity_condition_id: UUID | None
    repository_component_id: UUID | None
    mapping_type: str
    explanation: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_by_type: str
    created_by_id: UUID | None


class MappingReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping: PaperCodeMappingStateResponse
    predecessor: PaperCodeMappingStateResponse | None
    event_id: UUID
    replayed: bool


class MappingGenerationResponse(BaseModel):
    outcome: Literal["applicable", "not_applicable"]
    generation_record_id: UUID | None
    replayed: bool
    mappings: list[PaperCodeMappingResponse]
    diagnostics: list[dict[str, object]]
