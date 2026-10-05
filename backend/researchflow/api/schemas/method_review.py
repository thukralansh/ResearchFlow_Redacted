"""Transport contracts for grouped MethodModel review."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.api.schemas.method_extraction import (
    DataTypeOrShapeCandidate,
    ParameterValue,
)
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import (
    MethodComponentType,
    MethodElementType,
    MethodEpistemicStatus,
    MethodEquationType,
    MethodSymbolType,
    ParameterRole,
    SectionReviewOutcome,
    ValidityConditionType,
    VariableRole,
)
from researchflow.domain.method_validation import validate_equation_expression
from researchflow.errors import DomainValidationError

Rationale = Annotated[str, Field(min_length=1, max_length=4_000)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Confidence = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodElementMutationRequest(_StrictModel):
    command_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: Rationale


class ComponentRevisionPayload(_StrictModel):
    element_type: Literal[MethodElementType.COMPONENT]
    component_type: MethodComponentType
    name: Annotated[str, Field(min_length=1, max_length=500)]
    description: Annotated[str, Field(min_length=1, max_length=4_000)]


class EquationRevisionPayload(_StrictModel):
    element_type: Literal[MethodElementType.EQUATION]
    label: Annotated[str, Field(min_length=1, max_length=500)] | None
    latex_expression: Annotated[str, Field(min_length=1, max_length=8_000)]
    normalized_expression: Annotated[str, Field(min_length=1, max_length=8_000)] | None
    explanation: Annotated[str, Field(min_length=1, max_length=4_000)]
    equation_type: MethodEquationType

    @model_validator(mode="after")
    def validate_expression(self) -> EquationRevisionPayload:
        try:
            validate_equation_expression(self.latex_expression)
        except DomainValidationError as error:
            raise ValueError(str(error.public_details["reason"])) from error
        return self


class SymbolRevisionPayload(_StrictModel):
    element_type: Literal[MethodElementType.SYMBOL]
    symbol_type: MethodSymbolType
    symbol: Annotated[str, Field(min_length=1, max_length=500)]
    canonical_name: Annotated[str, Field(min_length=1, max_length=500)]
    definition: Annotated[str, Field(min_length=1, max_length=4_000)]
    units: Annotated[str, Field(min_length=1, max_length=500)] | None
    domain_or_data_type: Annotated[str, Field(min_length=1, max_length=1_000)] | None
    constraints: Annotated[str, Field(min_length=1, max_length=2_000)] | None
    role: Annotated[str, Field(min_length=1, max_length=1_000)]
    variable_role: VariableRole | None
    parameter_role: ParameterRole | None
    value_or_range: ParameterValue | None

    @model_validator(mode="after")
    def validate_role_family(self) -> SymbolRevisionPayload:
        if self.symbol_type is MethodSymbolType.VARIABLE:
            if (
                self.variable_role is None
                or self.parameter_role is not None
                or self.value_or_range is not None
            ):
                raise ValueError("variable_role_family_invalid")
        elif self.parameter_role is None or self.variable_role is not None:
            raise ValueError("parameter_role_family_invalid")
        return self


class AlgorithmStepRevisionPayload(_StrictModel):
    element_type: Literal[MethodElementType.ALGORITHM_STEP]
    sequence_index: Annotated[int, Field(ge=0)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    action: Annotated[str, Field(min_length=1, max_length=4_000)]
    conditions: Annotated[str, Field(min_length=1, max_length=2_000)] | None


class DataFlowRevisionPayload(_StrictModel):
    element_type: Literal[MethodElementType.DATA_FLOW]
    source_element_id: UUID
    target_element_id: UUID
    transformation: Annotated[str, Field(min_length=1, max_length=4_000)]
    data_type_or_shape: DataTypeOrShapeCandidate | None

    @model_validator(mode="after")
    def reject_self_reference(self) -> DataFlowRevisionPayload:
        if self.source_element_id == self.target_element_id:
            raise ValueError("data_flow_self_reference")
        return self


class ValidityConditionRevisionPayload(_StrictModel):
    element_type: Literal[MethodElementType.VALIDITY_CONDITION]
    statement: Annotated[str, Field(min_length=1, max_length=4_000)]
    condition_type: ValidityConditionType
    consequence_if_violated: Annotated[str, Field(min_length=1, max_length=4_000)]


MethodElementRevisionPayload = Annotated[
    ComponentRevisionPayload
    | EquationRevisionPayload
    | SymbolRevisionPayload
    | AlgorithmStepRevisionPayload
    | DataFlowRevisionPayload
    | ValidityConditionRevisionPayload,
    Field(discriminator="element_type"),
]


class MethodElementSemanticRevisionRequest(MethodElementMutationRequest):
    epistemic_status: MethodEpistemicStatus
    confidence: Confidence | None = None
    payload: MethodElementRevisionPayload


class MethodElementExclusionRequest(_StrictModel):
    command_id: UUID
    expected_model_version: Annotated[int, Field(ge=1)]
    expected_composition_fingerprint: Fingerprint
    rationale: Rationale


class MethodSectionReviewRequest(_StrictModel):
    command_id: UUID
    displayed_member_ids: Annotated[tuple[UUID, ...], Field(max_length=128)]
    displayed_member_set_fingerprint: Fingerprint
    outcome: SectionReviewOutcome
    rationale: Rationale


class MethodModelAcceptanceRequest(_StrictModel):
    command_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    expected_composition_fingerprint: Fingerprint
    rationale: Rationale


class MethodElementReviewResponse(_StrictModel):
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    element_type: str
    epistemic_status: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    grounding_status: str
    supersedes_id: UUID | None
    grounding_assessment_id: UUID | None
    generation_record_id: UUID | None
    derived_from_id: UUID | None


class MethodModelReviewResponse(_StrictModel):
    id: UUID
    project_id: UUID
    research_question_id: UUID
    lineage_id: UUID
    version: int
    name: str
    objective: str
    review_status: str
    lifecycle_status: str
    freshness_status: str
    composition_fingerprint: Fingerprint
    current_assessment_id: UUID | None
    supersedes_id: UUID | None
    generation_record_id: UUID | None
    derived_from_id: UUID | None


class MethodEvidenceReviewResponse(_StrictModel):
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    method_element_id: UUID
    source_chunk_id: UUID | None
    repository_component_id: UUID | None
    evidence_relation: str
    evidence_excerpt: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None


class MethodObjectLinkReviewResponse(_StrictModel):
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    source_method_element_id: UUID
    relationship_type: str
    target_method_element_id: UUID
    rationale: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None


class MethodMemberReviewResponse(_StrictModel):
    id: UUID
    method_element_id: UUID
    review_section: str
    member_role: str
    sequence_index: int | None
    required: bool
    inclusion_rationale: str
    element: MethodElementReviewResponse
    subtype_payload: dict[str, object]
    evidence: tuple[MethodEvidenceReviewResponse, ...]
    object_links: tuple[MethodObjectLinkReviewResponse, ...]


class MethodSectionReviewResponse(_StrictModel):
    id: UUID
    method_model_id: UUID
    review_section: str
    version: int
    outcome: str
    member_set_fingerprint: Fingerprint
    reviewer_id: UUID
    rationale: str | None
    operation_event_id: UUID
    supersedes_id: UUID | None


class MethodAssessmentResponse(_StrictModel):
    id: UUID
    method_model_id: UUID
    outcome: str
    unsupported_required_count: int
    mixed_evidence_count: int
    contradicted_count: int
    missing_provenance_count: int
    unresolved_scientific_ambiguity_count: int
    rationale: str


class MethodReviewDetailResponse(_StrictModel):
    model: MethodModelReviewResponse
    members: tuple[MethodMemberReviewResponse, ...]
    section_reviews: tuple[MethodSectionReviewResponse, ...]
    assessment: MethodAssessmentResponse
    section_fingerprints: dict[str, Fingerprint]
    history: tuple[OperationEventResponse, ...]
    aggregate_ready: bool
    blocking_reason: str | None


class MethodElementCommandResponse(_StrictModel):
    element: MethodElementReviewResponse
    model: MethodModelReviewResponse
    assessment: MethodAssessmentResponse
    event_id: UUID
    replayed: bool


class MethodEvidenceCommandResponse(_StrictModel):
    evidence: MethodEvidenceReviewResponse
    element: MethodElementReviewResponse
    model: MethodModelReviewResponse
    assessment: MethodAssessmentResponse
    event_id: UUID
    replayed: bool


class MethodCompositionCommandResponse(_StrictModel):
    model: MethodModelReviewResponse
    replacement_element: MethodElementReviewResponse | None
    assessment: MethodAssessmentResponse
    event_id: UUID
    replayed: bool


class MethodSectionCommandResponse(_StrictModel):
    model: MethodModelReviewResponse
    section_review: MethodSectionReviewResponse
    accepted_element_ids: tuple[UUID, ...]
    assessment: MethodAssessmentResponse
    aggregate_ready: bool
    event_id: UUID
    replayed: bool


class MethodModelCommandResponse(_StrictModel):
    model: MethodModelReviewResponse
    assessment: MethodAssessmentResponse
    event_id: UUID
    replayed: bool
