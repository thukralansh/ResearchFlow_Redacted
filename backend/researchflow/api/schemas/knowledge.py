"""Transport contracts for immutable proposition review."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

Rationale = Annotated[str, Field(min_length=1, max_length=4_000)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReviewMutationRequest(_StrictModel):
    command_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: Rationale


class ClaimSubtypeRequest(_StrictModel):
    claim_type: ClaimType
    verification_status: VerificationStatus


class AssumptionSubtypeRequest(_StrictModel):
    assumption_type: AssumptionType
    risk_level: RiskLevel
    validation_status: ValidationStatus


class KnowledgeGapSubtypeRequest(_StrictModel):
    gap_type: KnowledgeGapType
    priority: PriorityLevel
    resolution_notes: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None


class ImplementationRequirementSubtypeRequest(_StrictModel):
    category: RequirementCategory
    explicitness: RequirementExplicitness
    priority: PriorityLevel
    satisfaction_status: SatisfactionStatus


class AmbiguityInterpretationRequest(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    description: Annotated[str, Field(min_length=1, max_length=4_000)]
    implementation_consequences: Annotated[str, Field(min_length=1, max_length=4_000)]
    rank: Annotated[int, Field(ge=1)]


class AmbiguitySubtypeRequest(_StrictModel):
    ambiguity_type: AmbiguityType
    impact_level: ImpactLevel
    resolution_status: ResolutionStatus
    interpretations: Annotated[
        tuple[AmbiguityInterpretationRequest, ...], Field(max_length=20)
    ] = ()


SubtypeRequest = (
    ClaimSubtypeRequest
    | AssumptionSubtypeRequest
    | KnowledgeGapSubtypeRequest
    | ImplementationRequirementSubtypeRequest
    | AmbiguitySubtypeRequest
)


class SemanticRevisionRequest(ReviewMutationRequest):
    object_type: KnowledgeObjectType
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    origin: KnowledgeOrigin
    epistemic_status: EpistemicStatus
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    subtype: SubtypeRequest
    reproposed_evidence_link_ids: Annotated[tuple[UUID, ...], Field(max_length=128)] = ()

    @model_validator(mode="after")
    def subtype_matches(self) -> SemanticRevisionRequest:
        expected = {
            KnowledgeObjectType.CLAIM: ClaimSubtypeRequest,
            KnowledgeObjectType.ASSUMPTION: AssumptionSubtypeRequest,
            KnowledgeObjectType.KNOWLEDGE_GAP: KnowledgeGapSubtypeRequest,
            KnowledgeObjectType.IMPLEMENTATION_REQUIREMENT: ImplementationRequirementSubtypeRequest,
            KnowledgeObjectType.AMBIGUITY: AmbiguitySubtypeRequest,
        }[self.object_type]
        if not isinstance(self.subtype, expected):
            raise ValueError("knowledge_subtype_mismatch")
        if len(set(self.reproposed_evidence_link_ids)) != len(self.reproposed_evidence_link_ids):
            raise ValueError("evidence_reproposal_duplicate")
        return self


class PresentationRevisionRequest(ReviewMutationRequest):
    display_text: Annotated[str, Field(min_length=1, max_length=4_000)]
    display_label: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class AcceptRevisionRequest(_StrictModel):
    command_id: UUID
    replacement_id: UUID
    expected_predecessor_version: Annotated[int, Field(ge=1)]
    expected_predecessor_lifecycle_status: KnowledgeLifecycleStatus
    expected_replacement_version: Annotated[int, Field(ge=1)]
    rationale: Rationale


class KnowledgeObjectResponse(_StrictModel):
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    object_type: str
    statement: str
    statement_normalization_version: str
    normalized_statement_hash: str
    origin: str
    epistemic_status: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    grounding_status: str
    grounding_assessment_id: UUID | None
    generation_record_id: UUID | None
    supersedes_id: UUID | None
    derived_from_id: UUID | None


class ReviewCommandResponse(_StrictModel):
    object: KnowledgeObjectResponse
    predecessor: KnowledgeObjectResponse | None
    event_id: UUID
    replayed: bool


class PresentationResponse(_StrictModel):
    id: UUID
    knowledge_object_id: UUID
    version: int
    display_text: str
    display_label: str | None
    created_by_type: str
    created_by_id: UUID | None
    supersedes_id: UUID | None


class PresentationCommandResponse(_StrictModel):
    object: KnowledgeObjectResponse
    presentation: PresentationResponse
    event_id: UUID
    replayed: bool


class EvidenceLinkResponse(_StrictModel):
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    knowledge_object_id: UUID
    source_chunk_id: UUID | None
    repository_component_id: UUID | None
    evidence_relation: str
    evidence_excerpt: str
    source_anchor_start_offset: int | None
    source_anchor_end_offset: int | None
    source_anchor_hash: str | None
    source_anchor_policy_version: str | None
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None


class GroundingAssessmentResponse(_StrictModel):
    id: UUID
    project_id: UUID
    knowledge_object_id: UUID
    evidence_set_fingerprint: str
    status: str
    supporting_count: int
    contradicting_count: int
    qualifying_count: int
    rationale: str
    validator_name: str
    validator_version: str


class EvidenceReviewResponse(_StrictModel):
    evidence: EvidenceLinkResponse
    object: KnowledgeObjectResponse
    grounding: GroundingAssessmentResponse
    event_id: UUID
    replayed: bool


class OperationEventResponse(_StrictModel):
    id: UUID
    operation_type: str
    reason: str | None
    input_refs: tuple[dict[str, object], ...]
    output_refs: tuple[dict[str, object], ...]
    idempotency_key: str | None
    input_fingerprint: str | None


class KnowledgeReviewDetailResponse(_StrictModel):
    object: KnowledgeObjectResponse
    subtype_payload: dict[str, Any]
    ambiguity_interpretations: tuple[dict[str, Any], ...]
    evidence: tuple[EvidenceLinkResponse, ...]
    presentations: tuple[PresentationResponse, ...]
    lineage: tuple[KnowledgeObjectResponse, ...]
    history: tuple[OperationEventResponse, ...]


ReviewAction = Literal["accept", "reject", "request-revision", "challenge", "reaffirm"]
