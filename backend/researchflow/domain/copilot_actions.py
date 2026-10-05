"""Versioned allowlisted Copilot action envelopes for FS-COP-04."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.domain.blueprint_catalog import VerificationResultStatus
from researchflow.domain.knowledge import (
    AmbiguityInterpretationPayload,
    AmbiguityPayload,
    AssumptionPayload,
    ClaimPayload,
    ImplementationRequirementPayload,
    KnowledgeGapPayload,
    KnowledgeObjectPayload,
)
from researchflow.domain.knowledge_catalog import (
    AmbiguityType,
    AssumptionType,
    ClaimType,
    EpistemicStatus,
    ImpactLevel,
    KnowledgeGapType,
    KnowledgeLifecycleStatus,
    KnowledgeObjectType,
    KnowledgeOrigin,
    KnowledgeReviewStatus,
    PriorityLevel,
    RequirementCategory,
    RequirementExplicitness,
    ResolutionStatus,
    RiskLevel,
    SatisfactionStatus,
    ValidationStatus,
    VerificationStatus,
)
from researchflow.domain.method_catalog import (
    EvidenceRelation,
    MethodComponentType,
    MethodElementType,
    MethodEpistemicStatus,
    MethodEquationType,
    MethodSymbolType,
    ParameterRole,
    ValidityConditionType,
    VariableRole,
)
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType

COPILOT_ACTION_POLICY_VERSION: Final = "rf-copilot-action-v1"
COPILOT_ACTION_SCHEMA_VERSION: Final = "v1"
COPILOT_PREVIEW_POLICY_VERSION: Final = "rf-copilot-impact-preview-v1"


class CopilotActionType(StrEnum):
    CREATE_KNOWLEDGE_CANDIDATE = "knowledge_candidate.create"
    CREATE_EVIDENCE_CANDIDATE = "evidence_candidate.create"
    REVISE_KNOWLEDGE_OBJECT = "knowledge_object.revise"
    REVIEW_KNOWLEDGE_OBJECT = "knowledge_object.review"
    REVIEW_EVIDENCE_LINK = "evidence_link.review"
    REVISE_METHOD_ELEMENT = "method_element.revise"
    REVIEW_METHOD_ELEMENT = "method_element.review"
    REVIEW_METHOD_EVIDENCE = "method_evidence.review"
    REVIEW_METHOD_MODEL = "method_model.review"
    REVISE_MAPPING = "paper_code_mapping.revise"
    REVIEW_MAPPING = "paper_code_mapping.review"
    REVIEW_BLUEPRINT = "blueprint.review"
    RESOLVE_HUMAN_JUDGMENT = "human_judgment.resolve"
    RECORD_VERIFICATION_RESULT = "verification_result.record"
    PROPOSE_SOURCE_INGESTION = "source_ingestion.propose"
    PROPOSE_REPOSITORY_INGESTION = "repository_ingestion.propose"


class ConfirmationClass(StrEnum):
    LIGHTWEIGHT = "lightweight"
    STRONG = "strong"
    SPECIALIZED_HANDOFF = "specialized_handoff"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ProposedEvidence(_StrictModel):
    source_kind: str
    source_id: UUID
    relation: EvidenceRelation
    excerpt: Annotated[str, Field(min_length=1, max_length=4_000)]
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None


class ClaimSubtype(_StrictModel):
    claim_type: ClaimType
    verification_status: VerificationStatus


class AssumptionSubtype(_StrictModel):
    assumption_type: AssumptionType
    risk_level: RiskLevel
    validation_status: ValidationStatus


class KnowledgeGapSubtype(_StrictModel):
    gap_type: KnowledgeGapType
    priority: PriorityLevel
    resolution_notes: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None


class ImplementationRequirementSubtype(_StrictModel):
    category: RequirementCategory
    explicitness: RequirementExplicitness
    priority: PriorityLevel
    satisfaction_status: SatisfactionStatus


class AmbiguityInterpretationSubtype(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    description: Annotated[str, Field(min_length=1, max_length=4_000)]
    implementation_consequences: Annotated[str, Field(min_length=1, max_length=4_000)]
    rank: Annotated[int, Field(ge=1)]


class AmbiguitySubtype(_StrictModel):
    ambiguity_type: AmbiguityType
    impact_level: ImpactLevel
    resolution_status: ResolutionStatus
    interpretations: Annotated[tuple[AmbiguityInterpretationSubtype, ...], Field(max_length=8)] = ()


type KnowledgeSubtype = (
    ClaimSubtype
    | AssumptionSubtype
    | KnowledgeGapSubtype
    | ImplementationRequirementSubtype
    | AmbiguitySubtype
)


class KnowledgeCandidatePayload(_StrictModel):
    object_type: KnowledgeObjectType
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    origin: KnowledgeOrigin
    epistemic_status: EpistemicStatus
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    subtype: KnowledgeSubtype
    evidence: Annotated[tuple[ProposedEvidence, ...], Field(min_length=1, max_length=24)]

    @model_validator(mode="after")
    def validate_semantics(self) -> KnowledgeCandidatePayload:
        validated_knowledge_payload(self)
        return self


class EvidenceCandidatePayload(_StrictModel):
    knowledge_object_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    evidence: ProposedEvidence


class ReviewTargetPayload(_StrictModel):
    target_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    operation: str

    @model_validator(mode="after")
    def validate_operation(self) -> ReviewTargetPayload:
        if self.operation not in {
            "accept",
            "reject",
            "request_revision",
            "challenge",
            "reaffirm",
        }:
            raise ValueError("copilot_review_operation_invalid")
        return self


class KnowledgeRevisionPayload(ReviewTargetPayload):
    object_type: KnowledgeObjectType
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    origin: KnowledgeOrigin
    epistemic_status: EpistemicStatus
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    subtype: KnowledgeSubtype
    reproposed_evidence_link_ids: Annotated[tuple[UUID, ...], Field(max_length=128)] = ()

    @model_validator(mode="after")
    def validate_semantics(self) -> KnowledgeRevisionPayload:
        validated_knowledge_payload(self)
        return self


class EvidenceReviewPayload(ReviewTargetPayload):
    knowledge_object_id: UUID


class MethodElementReviewPayload(ReviewTargetPayload):
    method_model_id: UUID


class MethodComponentSubtype(_StrictModel):
    component_type: MethodComponentType
    name: Annotated[str, Field(min_length=1, max_length=500)]
    description: Annotated[str, Field(min_length=1, max_length=4_000)]


class MethodEquationSubtype(_StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    latex_expression: Annotated[str, Field(min_length=1, max_length=8_000)]
    normalized_expression: Annotated[str, Field(min_length=1, max_length=8_000)] | None = None
    explanation: Annotated[str, Field(min_length=1, max_length=4_000)]
    equation_type: MethodEquationType


class ScalarMethodValue(_StrictModel):
    kind: Literal["scalar"]
    value: bool | int | float | Annotated[str, Field(min_length=1, max_length=500)]


class RangeMethodValue(_StrictModel):
    kind: Literal["range"]
    minimum: Decimal
    maximum: Decimal
    minimum_inclusive: bool
    maximum_inclusive: bool


type MethodParameterValue = Annotated[
    ScalarMethodValue | RangeMethodValue, Field(discriminator="kind")
]


class MethodSymbolSubtype(_StrictModel):
    symbol_type: MethodSymbolType
    symbol: Annotated[str, Field(min_length=1, max_length=500)]
    canonical_name: Annotated[str, Field(min_length=1, max_length=500)]
    definition: Annotated[str, Field(min_length=1, max_length=4_000)]
    units: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    domain_or_data_type: Annotated[str, Field(min_length=1, max_length=1_000)] | None = None
    constraints: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None
    role: Annotated[str, Field(min_length=1, max_length=1_000)]
    variable_role: VariableRole | None = None
    parameter_role: ParameterRole | None = None
    value_or_range: MethodParameterValue | None = None


class MethodAlgorithmStepSubtype(_StrictModel):
    sequence_index: Annotated[int, Field(ge=0)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    action: Annotated[str, Field(min_length=1, max_length=4_000)]
    conditions: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None


class MethodEndpointSubtype(_StrictModel):
    element_id: UUID
    element_type: Literal[
        MethodElementType.COMPONENT,
        MethodElementType.SYMBOL,
        MethodElementType.ALGORITHM_STEP,
    ]


class MethodDataTypeOrShapeSubtype(_StrictModel):
    data_type: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    shape: (
        Annotated[
            tuple[
                Annotated[int, Field(gt=0)]
                | Annotated[str, Field(min_length=1, max_length=100)]
                | None,
                ...,
            ],
            Field(max_length=16),
        ]
        | None
    ) = None


class MethodDataFlowSubtype(_StrictModel):
    source: MethodEndpointSubtype
    target: MethodEndpointSubtype
    transformation: Annotated[str, Field(min_length=1, max_length=4_000)]
    data_type_or_shape: MethodDataTypeOrShapeSubtype | None = None


class MethodValidityConditionSubtype(_StrictModel):
    statement: Annotated[str, Field(min_length=1, max_length=4_000)]
    condition_type: ValidityConditionType
    consequence_if_violated: Annotated[str, Field(min_length=1, max_length=4_000)]


type MethodElementSubtype = (
    MethodComponentSubtype
    | MethodEquationSubtype
    | MethodSymbolSubtype
    | MethodAlgorithmStepSubtype
    | MethodDataFlowSubtype
    | MethodValidityConditionSubtype
)


class MethodElementRevisionPayload(MethodElementReviewPayload):
    element_type: MethodElementType
    epistemic_status: MethodEpistemicStatus
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    subtype_payload: MethodElementSubtype

    @model_validator(mode="after")
    def validate_subtype(self) -> MethodElementRevisionPayload:
        expected: dict[MethodElementType, type[BaseModel]] = {
            MethodElementType.COMPONENT: MethodComponentSubtype,
            MethodElementType.EQUATION: MethodEquationSubtype,
            MethodElementType.SYMBOL: MethodSymbolSubtype,
            MethodElementType.ALGORITHM_STEP: MethodAlgorithmStepSubtype,
            MethodElementType.DATA_FLOW: MethodDataFlowSubtype,
            MethodElementType.VALIDITY_CONDITION: MethodValidityConditionSubtype,
        }
        if not isinstance(self.subtype_payload, expected[self.element_type]):
            raise ValueError("method_element_subtype_mismatch")
        return self


class MethodEvidenceReviewPayload(ReviewTargetPayload):
    method_model_id: UUID
    element_id: UUID


class MethodModelReviewPayload(ReviewTargetPayload):
    expected_composition_fingerprint: str

    @model_validator(mode="after")
    def accept_only(self) -> MethodModelReviewPayload:
        if self.operation != "accept":
            raise ValueError("copilot_method_model_operation_invalid")
        return self


class MappingReviewPayload(ReviewTargetPayload):
    pass


class MappingRevisionPayload(MappingReviewPayload):
    paper_anchor_type: PaperAnchorType
    anchor_id: UUID
    repository_component_id: UUID | None = None
    mapping_type: PaperCodeMappingType
    explanation: Annotated[str, Field(min_length=1, max_length=4_000)]
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None


class BlueprintReviewPayload(_StrictModel):
    blueprint_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_status: str
    operation: str
    feedback: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    eligibility_assessment_id: UUID | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> BlueprintReviewPayload:
        if self.operation not in {"accept", "request_revision"}:
            raise ValueError("copilot_blueprint_operation_invalid")
        if self.operation == "request_revision" and self.feedback is None:
            raise ValueError("copilot_blueprint_feedback_required")
        return self


class HumanJudgmentPayload(_StrictModel):
    judgment_request_id: UUID
    workflow_run_id: UUID
    selected_option_id: UUID | None = None
    proposed_custom_decision: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    confidence: Annotated[Decimal, Field(ge=0, le=1)]
    method_model_affected: bool
    method_impact_rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    mapping_review_gate_event_id: UUID
    repository_applicable: bool
    expected_current_judgment_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one_decision(self) -> HumanJudgmentPayload:
        if (self.selected_option_id is None) == (self.proposed_custom_decision is None):
            raise ValueError("human_judgment_decision_not_exactly_one")
        return self


class VerificationResultPayload(_StrictModel):
    check_id: UUID
    expected_result_status: str | None = None
    result_status: VerificationResultStatus
    actual_result: Annotated[str, Field(min_length=1, max_length=4_000)]


class SpecializedIngestionProposalPayload(_StrictModel):
    resource_kind: str
    title: Annotated[str, Field(min_length=1, max_length=500)]
    locator: Annotated[str, Field(min_length=1, max_length=4_000)]
    relationship: Annotated[str, Field(min_length=1, max_length=200)] | None = None


type ActionPayload = (
    KnowledgeCandidatePayload
    | EvidenceCandidatePayload
    | ReviewTargetPayload
    | KnowledgeRevisionPayload
    | EvidenceReviewPayload
    | MethodElementReviewPayload
    | MethodElementRevisionPayload
    | MethodEvidenceReviewPayload
    | MethodModelReviewPayload
    | MappingReviewPayload
    | MappingRevisionPayload
    | BlueprintReviewPayload
    | HumanJudgmentPayload
    | VerificationResultPayload
    | SpecializedIngestionProposalPayload
)


class CopilotProposedAction(_StrictModel):
    action_type: CopilotActionType
    schema_version: str = COPILOT_ACTION_SCHEMA_VERSION
    manifest_id: UUID
    payload: ActionPayload
    explanation: Annotated[str, Field(min_length=1, max_length=4_000)]


_PAYLOAD_TYPES: Final[dict[CopilotActionType, type[BaseModel]]] = {
    CopilotActionType.CREATE_KNOWLEDGE_CANDIDATE: KnowledgeCandidatePayload,
    CopilotActionType.CREATE_EVIDENCE_CANDIDATE: EvidenceCandidatePayload,
    CopilotActionType.REVISE_KNOWLEDGE_OBJECT: KnowledgeRevisionPayload,
    CopilotActionType.REVIEW_KNOWLEDGE_OBJECT: ReviewTargetPayload,
    CopilotActionType.REVIEW_EVIDENCE_LINK: EvidenceReviewPayload,
    CopilotActionType.REVISE_METHOD_ELEMENT: MethodElementRevisionPayload,
    CopilotActionType.REVIEW_METHOD_ELEMENT: MethodElementReviewPayload,
    CopilotActionType.REVIEW_METHOD_EVIDENCE: MethodEvidenceReviewPayload,
    CopilotActionType.REVIEW_METHOD_MODEL: MethodModelReviewPayload,
    CopilotActionType.REVISE_MAPPING: MappingRevisionPayload,
    CopilotActionType.REVIEW_MAPPING: MappingReviewPayload,
    CopilotActionType.REVIEW_BLUEPRINT: BlueprintReviewPayload,
    CopilotActionType.RESOLVE_HUMAN_JUDGMENT: HumanJudgmentPayload,
    CopilotActionType.RECORD_VERIFICATION_RESULT: VerificationResultPayload,
    CopilotActionType.PROPOSE_SOURCE_INGESTION: SpecializedIngestionProposalPayload,
    CopilotActionType.PROPOSE_REPOSITORY_INGESTION: SpecializedIngestionProposalPayload,
}


def validate_action_payload(action: CopilotProposedAction) -> ActionPayload:
    model = _PAYLOAD_TYPES[action.action_type]
    encoded = json.dumps(
        action.payload.model_dump(mode="json", exclude_none=False),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    payload = model.model_validate_json(encoded, strict=True)
    if isinstance(payload, SpecializedIngestionProposalPayload):
        expected_kind = (
            "source"
            if action.action_type is CopilotActionType.PROPOSE_SOURCE_INGESTION
            else "repository"
        )
        if payload.resource_kind != expected_kind:
            raise ValueError("copilot_ingestion_resource_kind_invalid")
    return payload  # type: ignore[return-value]


def confirmation_class_for(action_type: CopilotActionType) -> ConfirmationClass:
    if action_type in {
        CopilotActionType.CREATE_KNOWLEDGE_CANDIDATE,
        CopilotActionType.CREATE_EVIDENCE_CANDIDATE,
        CopilotActionType.REVISE_KNOWLEDGE_OBJECT,
        CopilotActionType.REVISE_METHOD_ELEMENT,
        CopilotActionType.REVISE_MAPPING,
    }:
        return ConfirmationClass.LIGHTWEIGHT
    if action_type in {
        CopilotActionType.PROPOSE_SOURCE_INGESTION,
        CopilotActionType.PROPOSE_REPOSITORY_INGESTION,
    }:
        return ConfirmationClass.SPECIALIZED_HANDOFF
    return ConfirmationClass.STRONG


def validated_knowledge_payload(
    value: KnowledgeRevisionPayload | KnowledgeCandidatePayload,
) -> KnowledgeObjectPayload:
    raw = value.subtype.model_dump(mode="json", exclude_none=False)
    subtype: (
        ClaimPayload
        | AssumptionPayload
        | KnowledgeGapPayload
        | ImplementationRequirementPayload
        | AmbiguityPayload
    )
    if value.object_type is KnowledgeObjectType.CLAIM:
        subtype = ClaimPayload(
            claim_type=ClaimType(str(raw["claim_type"])),
            verification_status=VerificationStatus(str(raw["verification_status"])),
        )
    elif value.object_type is KnowledgeObjectType.ASSUMPTION:
        subtype = AssumptionPayload(
            assumption_type=AssumptionType(str(raw["assumption_type"])),
            risk_level=RiskLevel(str(raw["risk_level"])),
            validation_status=ValidationStatus(str(raw["validation_status"])),
        )
    elif value.object_type is KnowledgeObjectType.KNOWLEDGE_GAP:
        subtype = KnowledgeGapPayload(
            gap_type=KnowledgeGapType(str(raw["gap_type"])),
            priority=PriorityLevel(str(raw["priority"])),
            resolution_notes=(
                None if raw.get("resolution_notes") is None else str(raw["resolution_notes"])
            ),
        )
    elif value.object_type is KnowledgeObjectType.IMPLEMENTATION_REQUIREMENT:
        subtype = ImplementationRequirementPayload(
            category=RequirementCategory(str(raw["category"])),
            explicitness=RequirementExplicitness(str(raw["explicitness"])),
            priority=PriorityLevel(str(raw["priority"])),
            satisfaction_status=SatisfactionStatus(str(raw["satisfaction_status"])),
        )
    else:
        interpretations = raw.get("interpretations", [])
        if not isinstance(interpretations, list):
            raise ValueError("ambiguity_interpretations_invalid")
        subtype = AmbiguityPayload(
            ambiguity_type=AmbiguityType(str(raw["ambiguity_type"])),
            impact_level=ImpactLevel(str(raw["impact_level"])),
            resolution_status=ResolutionStatus(str(raw["resolution_status"])),
            interpretations=tuple(
                AmbiguityInterpretationPayload(
                    title=str(item["title"]),
                    description=str(item["description"]),
                    implementation_consequences=str(item["implementation_consequences"]),
                    rank=int(item["rank"]),
                )
                for item in interpretations
                if isinstance(item, dict)
            ),
        )
        if len(subtype.interpretations) != len(interpretations):
            raise ValueError("ambiguity_interpretations_invalid")
    return KnowledgeObjectPayload(
        object_type=value.object_type,
        statement=value.statement,
        origin=value.origin,
        epistemic_status=value.epistemic_status,
        confidence=value.confidence,
        subtype=subtype,
    ).validated()
