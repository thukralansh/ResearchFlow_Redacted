"""Strict question-conditioned MethodModel extraction contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from researchflow.domain.method_catalog import (
    MethodComponentType,
    MethodElementType,
    MethodEpistemicStatus,
    MethodEquationType,
    MethodMemberRole,
    MethodReviewSection,
    MethodSymbolType,
    ParameterRole,
    ValidityConditionType,
    VariableRole,
)
from researchflow.domain.method_validation import (
    classify_method_membership,
    source_method_evidence_relation_is_registered,
    validate_equation_expression,
    validate_symbol_uniqueness,
)
from researchflow.errors import DomainValidationError

type Confidence = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]
type ClientId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
type EvidenceRelationValue = Literal["supports", "contradicts", "qualifies"]
type DataFlowEndpointType = Literal["component", "symbol", "algorithm_step"]
type MethodRejectionReasonValue = Literal[
    "peripheral",
    "missing_evidence",
    "unsupported_reference",
    "invalid_element",
    "duplicate_symbol",
    "invalid_equation",
    "invalid_data_flow",
    "repository_context_absent",
    "out_of_scope",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodSourceEvidenceReference(_StrictModel):
    chunk_id: UUID
    excerpt: Annotated[str, Field(min_length=1, max_length=4_000)]
    relation: EvidenceRelationValue
    confidence: Confidence | None = None

    @field_validator("excerpt", mode="before")
    @classmethod
    def exact_nonblank_excerpt(cls, value: object) -> object:
        if isinstance(value, str) and value != value.strip():
            raise ValueError("evidence_excerpt_not_exact")
        return value


class ScalarParameterValue(_StrictModel):
    kind: Literal["scalar"]
    value: bool | int | float | Annotated[str, Field(min_length=1, max_length=500)]


class RangeParameterValue(_StrictModel):
    kind: Literal["range"]
    minimum: Decimal
    maximum: Decimal
    minimum_inclusive: bool
    maximum_inclusive: bool

    @model_validator(mode="after")
    def ordered_range(self) -> RangeParameterValue:
        if self.minimum > self.maximum:
            raise ValueError("parameter_range_inverted")
        return self


type ParameterValue = Annotated[
    ScalarParameterValue | RangeParameterValue, Field(discriminator="kind")
]


class DataTypeOrShapeCandidate(_StrictModel):
    data_type: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    shape: (
        Annotated[
            list[
                Annotated[int, Field(gt=0)]
                | Annotated[str, Field(min_length=1, max_length=100)]
                | None
            ],
            Field(max_length=16),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def require_type_or_shape(self) -> DataTypeOrShapeCandidate:
        if self.data_type is None and self.shape is None:
            raise ValueError("data_type_or_shape_empty")
        return self


class _MethodElementBase(_StrictModel):
    element_type: MethodElementType
    client_id: ClientId
    epistemic_status: MethodEpistemicStatus
    confidence: Confidence | None
    review_section: MethodReviewSection
    member_role: MethodMemberRole
    section_sequence_index: (
        Annotated[
            int,
            Field(ge=0),
        ]
        | None
    ) = Field(
        description=(
            "Every non-null index is unique across ALL element types in this review section, "
            "not per subtype. Equation + Symbol share equations_symbols; AlgorithmStep + "
            "DataFlow share algorithm_data_flow. DataFlows must not occupy AlgorithmStep "
            "slots. Nonalgorithm indices may be null and need not be contiguous. Each "
            "extraction batch has independent local indices; merge produces one newly "
            "coherent aggregate ordering, not concatenated constituent indices."
        )
    )
    required: Literal[True]
    inclusion_rationale: Annotated[str, Field(min_length=1, max_length=2_000)]
    evidence: Annotated[list[MethodSourceEvidenceReference], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def validate_common_element(self) -> _MethodElementBase:
        if self.epistemic_status in {
            MethodEpistemicStatus.REPOSITORY_OBSERVED,
            MethodEpistemicStatus.HUMAN_DECIDED,
        }:
            raise ValueError("epistemic_status_not_available_in_source_extraction")
        component_type = getattr(self, "component_type", None)
        classification = classify_method_membership(
            element_type=self.element_type,
            component_type=component_type,
        )
        if (
            self.review_section is not classification.review_section
            or self.member_role is not classification.member_role
            or self.required is not classification.required
        ):
            raise ValueError("method_membership_classification_mismatch")
        symbol_type = getattr(self, "symbol_type", None)
        if not any(reference.relation == "supports" for reference in self.evidence):
            raise ValueError("method_element_requires_supporting_evidence")
        if any(
            not source_method_evidence_relation_is_registered(
                element_type=self.element_type,
                symbol_type=symbol_type,
                relation=reference.relation,
            )
            for reference in self.evidence
        ):
            raise ValueError("method_evidence_relation_target_registry_mismatch")
        return self


class MethodComponentCandidate(_MethodElementBase):
    element_type: Literal[MethodElementType.COMPONENT]
    component_type: MethodComponentType
    name: Annotated[str, Field(min_length=1, max_length=500)]
    description: Annotated[str, Field(min_length=1, max_length=4_000)]


class MethodEquationCandidate(_MethodElementBase):
    element_type: Literal[MethodElementType.EQUATION]
    label: Annotated[str, Field(min_length=1, max_length=500)] | None
    latex_expression: Annotated[str, Field(min_length=1, max_length=8_000)]
    normalized_expression: Annotated[str, Field(min_length=1, max_length=8_000)] | None
    explanation: Annotated[str, Field(min_length=1, max_length=4_000)]
    equation_type: MethodEquationType
    symbol_client_ids: Annotated[list[ClientId], Field(max_length=32)]

    @model_validator(mode="after")
    def validate_expression_and_symbol_references(self) -> MethodEquationCandidate:
        if len(self.symbol_client_ids) != len(set(self.symbol_client_ids)):
            raise ValueError("duplicate_equation_symbol_reference")
        try:
            validate_equation_expression(self.latex_expression)
        except DomainValidationError as error:
            raise ValueError(str(error.public_details["reason"])) from error
        return self


class MethodSymbolCandidate(_MethodElementBase):
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
    def validate_symbol_role_family(self) -> MethodSymbolCandidate:
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


class MethodAlgorithmStepCandidate(_MethodElementBase):
    element_type: Literal[MethodElementType.ALGORITHM_STEP]
    sequence_index: Annotated[int, Field(ge=0)] = Field(
        description=(
            "Zero-based contiguous scientific AlgorithmStep order within this candidate "
            "aggregate; must equal section_sequence_index. DataFlows share the section "
            "namespace and must not occupy these slots."
        )
    )
    title: Annotated[str, Field(min_length=1, max_length=500)]
    action: Annotated[str, Field(min_length=1, max_length=4_000)]
    conditions: Annotated[str, Field(min_length=1, max_length=2_000)] | None

    @model_validator(mode="after")
    def sequence_matches_membership(self) -> MethodAlgorithmStepCandidate:
        if self.section_sequence_index != self.sequence_index:
            raise ValueError("algorithm_sequence_membership_mismatch")
        return self


class MethodDataFlowCandidate(_MethodElementBase):
    element_type: Literal[MethodElementType.DATA_FLOW]
    source_client_id: ClientId
    source_element_type: DataFlowEndpointType
    target_client_id: ClientId
    target_element_type: DataFlowEndpointType
    transformation: Annotated[str, Field(min_length=1, max_length=4_000)]
    data_type_or_shape: DataTypeOrShapeCandidate | None

    @model_validator(mode="after")
    def reject_self_reference(self) -> MethodDataFlowCandidate:
        if self.source_client_id == self.target_client_id:
            raise ValueError("data_flow_self_reference")
        return self


class MethodValidityConditionCandidate(_MethodElementBase):
    element_type: Literal[MethodElementType.VALIDITY_CONDITION]
    statement: Annotated[str, Field(min_length=1, max_length=4_000)]
    condition_type: ValidityConditionType
    consequence_if_violated: Annotated[str, Field(min_length=1, max_length=4_000)]


type MethodElementCandidate = Annotated[
    MethodComponentCandidate
    | MethodEquationCandidate
    | MethodSymbolCandidate
    | MethodAlgorithmStepCandidate
    | MethodDataFlowCandidate
    | MethodValidityConditionCandidate,
    Field(discriminator="element_type"),
]


class CandidateMethodModel(_StrictModel):
    client_id: ClientId
    name: Annotated[str, Field(min_length=1, max_length=500)]
    objective: Annotated[str, Field(min_length=1, max_length=4_000)]


class MethodSectionDisposition(_StrictModel):
    review_section: MethodReviewSection
    applicable: bool
    not_applicable_rationale: Annotated[str, Field(min_length=1, max_length=2_000)] | None

    @model_validator(mode="after")
    def rationale_matches_applicability(self) -> MethodSectionDisposition:
        if self.applicable == (self.not_applicable_rationale is not None):
            raise ValueError("section_applicability_rationale_mismatch")
        return self


class RejectedMethodExtractionItem(_StrictModel):
    client_id: ClientId
    proposed_element_type: MethodElementType
    reason: MethodRejectionReasonValue
    detail: Annotated[str, Field(min_length=1, max_length=2_000)]
    evidence: Annotated[list[MethodSourceEvidenceReference], Field(max_length=8)]


class MethodExtractionBatch(_StrictModel):
    method_model: CandidateMethodModel
    sections: Annotated[list[MethodSectionDisposition], Field(min_length=4, max_length=4)]
    elements: Annotated[list[MethodElementCandidate], Field(min_length=1, max_length=128)]
    rejections: Annotated[list[RejectedMethodExtractionItem], Field(max_length=64)]

    @model_validator(mode="after")
    def validate_aggregate_composition(self) -> MethodExtractionBatch:
        client_ids = [element.client_id for element in self.elements]
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("duplicate_method_element_client_id")
        rejected_ids = [item.client_id for item in self.rejections]
        if len(rejected_ids) != len(set(rejected_ids)) or set(client_ids) & set(rejected_ids):
            raise ValueError("duplicate_method_output_client_id")

        section_map = {section.review_section: section for section in self.sections}
        if len(section_map) != 4 or set(section_map) != set(MethodReviewSection):
            raise ValueError("required_method_section_missing")
        for section, disposition in section_map.items():
            member_count = sum(element.review_section is section for element in self.elements)
            if disposition.applicable != (member_count > 0):
                raise ValueError("method_section_membership_applicability_mismatch")

        sequence_slots = [
            (element.review_section, element.section_sequence_index)
            for element in self.elements
            if element.section_sequence_index is not None
        ]
        if len(sequence_slots) != len(set(sequence_slots)):
            raise ValueError("duplicate_method_section_sequence")
        algorithm_indices = sorted(
            element.sequence_index
            for element in self.elements
            if isinstance(element, MethodAlgorithmStepCandidate)
        )
        if algorithm_indices and algorithm_indices != list(range(len(algorithm_indices))):
            raise ValueError("algorithm_sequence_not_contiguous")

        by_id = {element.client_id: element for element in self.elements}
        for element in self.elements:
            if isinstance(element, MethodEquationCandidate):
                for symbol_id in element.symbol_client_ids:
                    if not isinstance(by_id.get(symbol_id), MethodSymbolCandidate):
                        raise ValueError("equation_symbol_reference_unresolved")
            elif isinstance(element, MethodDataFlowCandidate):
                for endpoint_id, endpoint_type in (
                    (element.source_client_id, element.source_element_type),
                    (element.target_client_id, element.target_element_type),
                ):
                    endpoint = by_id.get(endpoint_id)
                    if endpoint is None or endpoint.element_type.value != endpoint_type:
                        raise ValueError("data_flow_endpoint_unresolved")

        symbols = tuple(
            (element.client_id, element.symbol, element.canonical_name)
            for element in self.elements
            if isinstance(element, MethodSymbolCandidate)
        )
        try:
            validate_symbol_uniqueness(symbols)
        except DomainValidationError as error:
            raise ValueError(str(error.public_details["reason"])) from error
        return self


class MethodMergeAccountability(_StrictModel):
    constituent_generation_record_id: UUID
    input_client_id: ClientId
    disposition: Literal["retained", "consolidated", "excluded"]
    merged_client_id: ClientId | None
    reason: Annotated[str, Field(min_length=1, max_length=2_000)] | None

    @model_validator(mode="after")
    def disposition_is_complete(self) -> MethodMergeAccountability:
        if self.disposition == "excluded":
            if self.merged_client_id is not None or self.reason is None:
                raise ValueError("merge_exclusion_shape_invalid")
        elif self.merged_client_id is None or self.reason is not None:
            raise ValueError("merge_retention_shape_invalid")
        return self


class MethodMergeResult(MethodExtractionBatch):
    accountability: list[MethodMergeAccountability]


class MethodEvidenceValidationItem(_StrictModel):
    element_client_id: ClientId
    chunk_id: UUID
    excerpt: Annotated[str, Field(min_length=1, max_length=4_000)]
    relation: EvidenceRelationValue
    outcome: Literal["consistent", "inconsistent", "uncertain"]
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]


class MethodEvidenceValidationBatch(_StrictModel):
    items: Annotated[list[MethodEvidenceValidationItem], Field(max_length=1_024)]
