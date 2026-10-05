"""Strict question-conditioned proposition extraction contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from researchflow.domain.knowledge_catalog import normalize_statement, statement_fingerprint
from researchflow.domain.proposition_extraction import (
    QUALITY_POLICY_VERSION,
    PropositionAdmissionPolicy,
    PropositionQualityPolicy,
    source_evidence_relation_is_registered,
)
from researchflow.errors import DomainValidationError

type Confidence = Annotated[float, Field(ge=0, le=1)]
type ClientId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
type EvidenceRelationValue = Literal["supports", "contradicts", "qualifies"]
type RelevanceValue = Literal[
    "direct_answer", "scientific_mathematical_context", "implementation_critical", "peripheral"
]
type RejectionReasonValue = Literal[
    "peripheral",
    "multi_part",
    "missing_why_it_matters",
    "non_actionable_requirement",
    "trivial_ambiguity",
    "unsupported_reference",
    "duplicate",
    "invalid_subtype",
]
type DuplicateDecisionValue = Literal["same_meaning", "materially_distinct", "uncertain"]


class RelevanceClassification(StrEnum):
    """Question-relative relevance; never a persisted knowledge enum."""

    DIRECT_ANSWER = "direct_answer"
    SCIENTIFIC_MATHEMATICAL_CONTEXT = "scientific_mathematical_context"
    IMPLEMENTATION_CRITICAL = "implementation_critical"
    PERIPHERAL = "peripheral"


class ExtractionRejectionReason(StrEnum):
    """Deterministic contract rejection reasons retained in raw output history."""

    PERIPHERAL = "peripheral"
    MULTI_PART = "multi_part"
    MISSING_WHY_IT_MATTERS = "missing_why_it_matters"
    NON_ACTIONABLE_REQUIREMENT = "non_actionable_requirement"
    TRIVIAL_AMBIGUITY = "trivial_ambiguity"
    UNSUPPORTED_REFERENCE = "unsupported_reference"
    DUPLICATE = "duplicate"
    INVALID_SUBTYPE = "invalid_subtype"


class DuplicateSemanticDecision(StrEnum):
    """Bounded comparison result; it is not canonical or persisted vocabulary."""

    SAME_MEANING = "same_meaning"
    MATERIALLY_DISTINCT = "materially_distinct"
    UNCERTAIN = "uncertain"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceEvidenceReference(_StrictModel):
    evidence_id: Annotated[str, Field(pattern=r"^e[0-9]{3}$")]
    excerpt: Annotated[str, Field(min_length=1, max_length=4_000)]
    relation: EvidenceRelationValue
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def exact_nonblank_excerpt(self) -> SourceEvidenceReference:
        if self.excerpt != self.excerpt.strip():
            raise ValueError("evidence_excerpt_not_exact")
        return self


class AmbiguityInterpretationCandidate(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(min_length=1, max_length=2_000)]
    implementation_consequences: Annotated[str, Field(min_length=1, max_length=2_000)]
    rank: Annotated[int, Field(ge=1, le=5)]


class _CandidateBase(_StrictModel):
    object_type: str
    client_id: ClientId
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    why_it_matters: Annotated[str, Field(min_length=1, max_length=2_000)]
    relevance: RelevanceValue
    origin: Literal["source_extraction", "ai_inference"]
    epistemic_status: Literal["explicit", "inferred", "speculative"]
    confidence: Confidence | None
    evidence: Annotated[list[SourceEvidenceReference], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def validate_common_candidate(self, info: ValidationInfo) -> _CandidateBase:
        if _defer_domain_policy(info):
            return self
        normalized = normalize_statement(self.statement)
        if not normalized or normalized != self.statement:
            raise ValueError("statement_not_normalized")
        if self.relevance == RelevanceClassification.PERIPHERAL.value:
            raise ValueError("peripheral_requires_rejection")
        if self.epistemic_status == "explicit" and self.origin != "source_extraction":
            raise ValueError("explicit_must_be_source_extraction")
        if self.epistemic_status != "explicit" and self.origin != "ai_inference":
            raise ValueError("inference_must_be_ai_origin")
        if not any(reference.relation == "supports" for reference in self.evidence):
            raise ValueError("candidate_requires_supporting_evidence")
        if any(
            not source_evidence_relation_is_registered(
                object_type=self.object_type,
                relation=reference.relation,
            )
            for reference in self.evidence
        ):
            raise ValueError("evidence_relation_target_registry_mismatch")
        try:
            PropositionAdmissionPolicy.validate(
                statement=normalized,
                why_it_matters=self.why_it_matters,
                verification_criterion=getattr(self, "verification_criterion", None),
                requires_verification_criterion=(self.object_type == "implementation_requirement"),
            )
        except DomainValidationError as error:
            raise ValueError(
                str(error.public_details.get("reason", "invalid_admission"))
            ) from error
        return self

    def quality_warnings(self) -> tuple[str, ...]:
        findings = PropositionQualityPolicy.assess(
            object_type=self.object_type,
            statement=self.statement,
            verification_criterion=getattr(self, "verification_criterion", None),
            impact_level=getattr(self, "impact_level", None),
            epistemic_status=self.epistemic_status,
            explicitness=getattr(self, "explicitness", None),
        )
        return tuple(f"{QUALITY_POLICY_VERSION}:{finding}" for finding in findings)


class ClaimCandidate(_CandidateBase):
    object_type: Literal["claim"]
    claim_type: Literal[
        "scientific",
        "mathematical",
        "methodological",
        "empirical",
        "implementation",
        "limitation",
    ]
    verification_status: Literal["unverified"]


class AssumptionCandidate(_CandidateBase):
    object_type: Literal["assumption"]
    assumption_type: Literal[
        "theoretical",
        "mathematical",
        "computational",
        "data",
        "experimental",
        "implementation",
    ]
    risk_level: Literal["low", "medium", "high", "critical"]
    validation_status: Literal["unvalidated"]


class KnowledgeGapCandidate(_CandidateBase):
    object_type: Literal["knowledge_gap"]
    gap_type: Literal[
        "conceptual",
        "mathematical",
        "computational",
        "evidentiary",
        "literature",
        "data",
        "experimental",
        "repository",
    ]
    priority: Literal["low", "medium", "high", "critical"]
    resolution_notes: None = None

    @model_validator(mode="after")
    def reject_repository_gap_without_repository_context(
        self, info: ValidationInfo
    ) -> KnowledgeGapCandidate:
        if _defer_domain_policy(info):
            return self
        if self.gap_type == "repository" and not _repository_context_included(info):
            raise ValueError("repository_context_absent")
        return self


class ImplementationRequirementCandidate(_CandidateBase):
    object_type: Literal["implementation_requirement"]
    category: Literal[
        "mathematical",
        "data",
        "preprocessing",
        "algorithm",
        "architecture",
        "training",
        "evaluation",
        "environment",
        "compute",
        "reproducibility",
    ]
    explicitness: Literal[
        "explicit",
        "inferred_from_paper",
        "inferred_from_convention",
        "speculative",
    ]
    priority: Literal["low", "medium", "high", "critical"]
    satisfaction_status: Literal["unsatisfied"]
    verification_criterion: Annotated[str, Field(min_length=1, max_length=2_000)]

    @model_validator(mode="after")
    def consistent_explicitness(self, info: ValidationInfo) -> ImplementationRequirementCandidate:
        if _defer_domain_policy(info):
            return self
        expected = {
            "explicit": {"explicit"},
            "inferred": {"inferred_from_paper", "inferred_from_convention"},
            "speculative": {"speculative"},
        }[self.epistemic_status]
        if self.explicitness not in expected:
            raise ValueError("requirement_explicitness_mismatch")
        return self


class AmbiguityCandidate(_CandidateBase):
    object_type: Literal["ambiguity"]
    ambiguity_type: Literal[
        "missing_detail",
        "conflicting_sources",
        "undefined_terminology",
        "paper_code_discrepancy",
        "mathematical",
        "algorithmic",
        "data",
        "framework",
        "evaluation",
        "environment",
    ]
    impact_level: Literal["low", "medium", "high", "critical"]
    resolution_status: Literal["unresolved"]
    interpretations: Annotated[
        list[AmbiguityInterpretationCandidate], Field(min_length=1, max_length=5)
    ]
    single_interpretation_reason: Annotated[str, Field(min_length=1, max_length=1_000)] | None = (
        None
    )

    @model_validator(mode="after")
    def validate_interpretations(self, info: ValidationInfo) -> AmbiguityCandidate:
        if _defer_domain_policy(info):
            return self
        if self.ambiguity_type == "paper_code_discrepancy" and not _repository_context_included(
            info
        ):
            raise ValueError("repository_context_absent")
        ranks = [interpretation.rank for interpretation in self.interpretations]
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("ambiguity_interpretation_ranks_not_contiguous")
        if len(self.interpretations) == 1 and self.single_interpretation_reason is None:
            raise ValueError("single_interpretation_requires_reason")
        if len(self.interpretations) > 1 and self.single_interpretation_reason is not None:
            raise ValueError("single_interpretation_reason_not_applicable")
        return self


type PropositionCandidate = Annotated[
    ClaimCandidate
    | AssumptionCandidate
    | KnowledgeGapCandidate
    | ImplementationRequirementCandidate
    | AmbiguityCandidate,
    Field(discriminator="object_type"),
]


class RejectedExtractionItem(_StrictModel):
    client_id: ClientId
    proposed_object_type: Literal[
        "claim", "assumption", "knowledge_gap", "implementation_requirement", "ambiguity"
    ]
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    relevance: RelevanceValue
    reason_code: RejectionReasonValue
    reason_detail: Annotated[str, Field(min_length=1, max_length=2_000)]
    evidence: Annotated[list[SourceEvidenceReference], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def validate_evidence_relations(self, info: ValidationInfo) -> RejectedExtractionItem:
        if _defer_domain_policy(info):
            return self
        if any(
            not source_evidence_relation_is_registered(
                object_type=self.proposed_object_type,
                relation=reference.relation,
            )
            for reference in self.evidence
        ):
            raise ValueError("evidence_relation_target_registry_mismatch")
        return self


class PropositionExtractionBatch(_StrictModel):
    candidates: Annotated[list[PropositionCandidate], Field(max_length=100)]
    rejections: Annotated[list[RejectedExtractionItem], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_client_ids_and_statements(self, info: ValidationInfo) -> PropositionExtractionBatch:
        if _defer_domain_policy(info):
            return self
        client_ids = [item.client_id for item in (*self.candidates, *self.rejections)]
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("duplicate_client_id")
        fingerprints = [statement_fingerprint(item.statement) for item in self.candidates]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("duplicate_candidate_statement")
        return self


def _defer_domain_policy(info: ValidationInfo) -> bool:
    return bool(info.context and info.context.get("defer_application_domain_validation") is True)


def _repository_context_included(info: ValidationInfo) -> bool:
    return bool(info.context and info.context.get("repository_context_included") is True)


class DuplicateComparisonCandidate(_StrictModel):
    object_id: UUID
    object_type: Literal[
        "claim", "assumption", "knowledge_gap", "implementation_requirement", "ambiguity"
    ]
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    lexical_similarity: Annotated[float, Field(ge=0, le=1)]


class DuplicateComparisonRequest(_StrictModel):
    lexical_policy_version: Literal["rf-proposition-lexical-v1"]
    near_duplicate_threshold: Annotated[float, Field(gt=0, le=1)]
    proposed_client_id: ClientId
    proposed_statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    candidates: Annotated[list[DuplicateComparisonCandidate], Field(min_length=1, max_length=20)]


class DuplicateComparisonResult(_StrictModel):
    proposed_client_id: ClientId
    compared_object_id: UUID
    decision: DuplicateDecisionValue
    material_difference: Annotated[str, Field(min_length=1, max_length=1_000)] | None
    rationale: Annotated[str, Field(min_length=1, max_length=1_000)]

    @model_validator(mode="after")
    def material_difference_matches_decision(self) -> DuplicateComparisonResult:
        if (self.decision == DuplicateSemanticDecision.MATERIALLY_DISTINCT.value) != (
            self.material_difference is not None
        ):
            raise ValueError("duplicate_material_difference_mismatch")
        return self
