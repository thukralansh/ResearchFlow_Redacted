"""Strict Judgment Request drafting and project-scoped inspection schemas."""

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.domain.blueprint_catalog import DependencyRole


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JudgmentEvidenceReference(_StrictModel):
    evidence_link_id: UUID
    excerpt: Annotated[str, Field(min_length=1, max_length=4_000)]


class JudgmentOptionDraft(_StrictModel):
    client_id: Annotated[str, Field(min_length=1, max_length=200)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    description: Annotated[str, Field(min_length=1, max_length=4_000)]
    supporting_evidence: list[JudgmentEvidenceReference]
    conflicting_evidence: list[JudgmentEvidenceReference]
    implementation_consequences: Annotated[str, Field(min_length=1, max_length=4_000)]

    @model_validator(mode="after")
    def evidence_present(self) -> "JudgmentOptionDraft":
        if not self.supporting_evidence and not self.conflicting_evidence:
            raise ValueError("judgment_option_evidence_missing")
        return self


class JudgmentRecommendation(_StrictModel):
    option_client_id: Annotated[str, Field(min_length=1, max_length=200)]
    rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    supporting_evidence: Annotated[list[JudgmentEvidenceReference], Field(min_length=1)]
    ai_authored: Literal[True]
    authoritative: Literal[False]


class JudgmentRequestDraft(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    decision_question: Annotated[str, Field(min_length=1, max_length=4_000)]
    why_human_required: Annotated[str, Field(min_length=1, max_length=4_000)]
    options: Annotated[list[JudgmentOptionDraft], Field(min_length=1, max_length=5)]
    recommendation: JudgmentRecommendation | None
    no_recommendation_reason: Annotated[str, Field(min_length=1, max_length=4_000)] | None

    @model_validator(mode="after")
    def recommendation_shape(self) -> "JudgmentRequestDraft":
        if (self.recommendation is None) == (self.no_recommendation_reason is None):
            raise ValueError("exactly_one_recommendation_outcome_required")
        ids = [item.client_id for item in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("judgment_option_client_id_duplicate")
        if self.recommendation is not None and self.recommendation.option_client_id not in ids:
            raise ValueError("recommendation_option_missing")
        return self


class AffectedDependencyInput(_StrictModel):
    dependency_type: Literal[
        "knowledge_object",
        "evidence_link",
        "grounding_assessment",
        "method_model",
        "method_model_assessment",
        "method_element",
        "paper_code_mapping",
        "mental_model",
        "mental_model_assessment",
    ]
    dependency_id: UUID
    dependency_role: DependencyRole
    required_for_objective: bool
    correctness_requires_resolution: bool
    material_consequence: bool
    rationale: Annotated[str, Field(min_length=1, max_length=4_000)]


class CreateJudgmentRequestBody(_StrictModel):
    workflow_run_id: UUID
    ambiguity_id: UUID
    affected_dependencies: Annotated[
        list[AffectedDependencyInput], Field(min_length=1, max_length=128)
    ]
    explicit_rerun: bool = False


class JudgmentRequestRecordResponse(_StrictModel):
    id: UUID
    project_id: UUID
    ambiguity_id: UUID
    lineage_id: UUID
    version: int
    title: str
    decision_question: str
    why_human_required: str
    status: str
    lifecycle_status: str
    workflow_run_id: UUID
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None


class JudgmentOptionResponse(_StrictModel):
    id: UUID
    judgment_request_id: UUID
    title: str
    description: str
    supporting_evidence: tuple[dict[str, Any], ...]
    conflicting_evidence: tuple[dict[str, Any], ...]
    implementation_consequences: str
    rank: int


class JudgmentDependencyResponse(_StrictModel):
    id: UUID
    project_id: UUID
    artifact_type: str
    artifact_id: UUID
    dependency_type: str
    dependency_id: UUID
    dependency_role: str
    criticality: str


class JudgmentEvidenceResponse(_StrictModel):
    id: UUID
    relation: str
    excerpt: str
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None
    review_status: str
    lifecycle_status: str
    source_chunk_id: UUID | None
    repository_component_id: UUID | None


class JudgmentRequestDetailResponse(_StrictModel):
    request: JudgmentRequestRecordResponse
    ambiguity: dict[str, Any]
    options: tuple[JudgmentOptionResponse, ...]
    evidence: tuple[JudgmentEvidenceResponse, ...]
    dependencies: tuple[JudgmentDependencyResponse, ...]
    recommendation: dict[str, Any] | None
    no_recommendation_reason: str | None


class JudgmentRequestSummaryResponse(_StrictModel):
    request: JudgmentRequestRecordResponse
    option_count: int
    highest_criticality: Literal["blocking", "material", "informational"]


class CreateJudgmentRequestResponse(_StrictModel):
    outcome: Literal["request_created", "no_judgment"]
    request: JudgmentRequestDetailResponse | None
    reason: str
    replayed: bool


class ResolveHumanJudgmentBody(_StrictModel):
    workflow_run_id: UUID
    command_id: UUID
    selected_option_id: UUID | None = None
    custom_decision: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    confidence: Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]
    method_model_affected: bool
    method_impact_rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    mapping_review_gate_event_id: UUID
    repository_applicable: bool
    confirmed: Literal[True]
    expected_current_judgment_id: UUID | None = None

    @model_validator(mode="after")
    def exact_decision(self) -> "ResolveHumanJudgmentBody":
        if (self.selected_option_id is None) == (self.custom_decision is None):
            raise ValueError("exactly_one_judgment_decision_required")
        return self


class HumanJudgmentResponse(_StrictModel):
    id: UUID
    project_id: UUID
    judgment_request_id: UUID
    lineage_id: UUID
    version: int
    selected_option_id: UUID | None
    custom_decision: str | None
    rationale: str
    confidence: Decimal
    lifecycle_status: str
    derived_from_id: UUID | None
    supersedes_id: UUID | None


class JudgmentResolutionResponse(_StrictModel):
    judgment: HumanJudgmentResponse
    reassessment_status: Literal["pending", "succeeded", "failed_retryable"]
    method_model_id: UUID | None
    method_model_assessment_id: UUID | None
    mental_model_id: UUID | None
    mental_model_assessment_id: UUID | None
    replayed: bool
    error: str | None


class HumanJudgmentHistoryItemResponse(_StrictModel):
    judgment: HumanJudgmentResponse
    dependencies: tuple[JudgmentDependencyResponse, ...]
    reassessment_status: Literal["pending", "succeeded", "failed_retryable"]
    method_model_id: UUID | None
    method_model_assessment_id: UUID | None
    mental_model_id: UUID | None
    mental_model_assessment_id: UUID | None
    status_event_id: UUID
    error: str | None
