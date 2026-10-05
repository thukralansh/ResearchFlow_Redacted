"""Typed proposition payload and proposition-only presentation policies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from researchflow.domain.knowledge_catalog import (
    AmbiguityType,
    AssumptionType,
    ClaimType,
    EpistemicStatus,
    ImpactLevel,
    KnowledgeGapType,
    KnowledgeObjectType,
    KnowledgeOrigin,
    PriorityLevel,
    RequirementCategory,
    RequirementExplicitness,
    ResolutionStatus,
    RiskLevel,
    SatisfactionStatus,
    ValidationStatus,
    VerificationStatus,
)
from researchflow.errors import DomainValidationError, ResourceConflictError


def _required(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(details={"field": field, "reason": "blank"})
    return normalized


def _optional(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(details={"field": field, "reason": "blank"})
    return normalized


@dataclass(frozen=True, slots=True)
class ClaimPayload:
    claim_type: ClaimType
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class AssumptionPayload:
    assumption_type: AssumptionType
    risk_level: RiskLevel
    validation_status: ValidationStatus


@dataclass(frozen=True, slots=True)
class KnowledgeGapPayload:
    gap_type: KnowledgeGapType
    priority: PriorityLevel
    resolution_notes: str | None = None

    def validated(self) -> KnowledgeGapPayload:
        return replace(
            self,
            resolution_notes=_optional(self.resolution_notes, field="resolution_notes"),
        )


@dataclass(frozen=True, slots=True)
class ImplementationRequirementPayload:
    category: RequirementCategory
    explicitness: RequirementExplicitness
    priority: PriorityLevel
    satisfaction_status: SatisfactionStatus


@dataclass(frozen=True, slots=True)
class AmbiguityInterpretationPayload:
    title: str
    description: str
    implementation_consequences: str
    rank: int

    def validated(self) -> AmbiguityInterpretationPayload:
        if self.rank < 1:
            raise DomainValidationError(details={"field": "rank", "reason": "not_positive"})
        return replace(
            self,
            title=_required(self.title, field="title"),
            description=_required(self.description, field="description"),
            implementation_consequences=_required(
                self.implementation_consequences,
                field="implementation_consequences",
            ),
        )


@dataclass(frozen=True, slots=True)
class AmbiguityPayload:
    ambiguity_type: AmbiguityType
    impact_level: ImpactLevel
    resolution_status: ResolutionStatus
    interpretations: tuple[AmbiguityInterpretationPayload, ...] = ()

    def validated(self) -> AmbiguityPayload:
        interpretations = tuple(item.validated() for item in self.interpretations)
        ranks = tuple(item.rank for item in interpretations)
        if len(ranks) != len(set(ranks)):
            raise DomainValidationError(
                details={"field": "interpretations", "reason": "duplicate_rank"}
            )
        return replace(
            self, interpretations=tuple(sorted(interpretations, key=lambda item: item.rank))
        )


type KnowledgeSubtypePayload = (
    ClaimPayload
    | AssumptionPayload
    | KnowledgeGapPayload
    | ImplementationRequirementPayload
    | AmbiguityPayload
)

_SUBTYPE_BY_OBJECT_TYPE: dict[KnowledgeObjectType, type[KnowledgeSubtypePayload]] = {
    KnowledgeObjectType.CLAIM: ClaimPayload,
    KnowledgeObjectType.ASSUMPTION: AssumptionPayload,
    KnowledgeObjectType.KNOWLEDGE_GAP: KnowledgeGapPayload,
    KnowledgeObjectType.IMPLEMENTATION_REQUIREMENT: ImplementationRequirementPayload,
    KnowledgeObjectType.AMBIGUITY: AmbiguityPayload,
}


@dataclass(frozen=True, slots=True)
class KnowledgeObjectPayload:
    object_type: KnowledgeObjectType
    statement: str
    origin: KnowledgeOrigin
    epistemic_status: EpistemicStatus
    confidence: Decimal | None
    subtype: KnowledgeSubtypePayload

    def validated(self) -> KnowledgeObjectPayload:
        expected = _SUBTYPE_BY_OBJECT_TYPE[self.object_type]
        if not isinstance(self.subtype, expected):
            raise DomainValidationError(details={"reason": "knowledge_subtype_mismatch"})
        if self.confidence is not None and not Decimal("0") <= self.confidence <= Decimal("1"):
            raise DomainValidationError(
                details={"field": "confidence", "reason": "outside_unit_interval"}
            )
        subtype: KnowledgeSubtypePayload = self.subtype
        if isinstance(subtype, KnowledgeGapPayload):
            subtype = subtype.validated()
        elif isinstance(subtype, AmbiguityPayload):
            subtype = subtype.validated()
        return replace(
            self,
            statement=_required(self.statement, field="statement"),
            subtype=subtype,
        )


@dataclass(frozen=True, slots=True)
class KnowledgePresentationSnapshot:
    id: UUID
    knowledge_object_id: UUID
    version: int
    display_text: str
    display_label: str | None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError(details={"reason": "presentation_version_invalid"})


@dataclass(frozen=True, slots=True)
class KnowledgePresentationRevisionSpecification:
    id: UUID
    knowledge_object_id: UUID
    version: int
    display_text: str
    display_label: str | None
    supersedes_id: UUID | None
    stales_semantic_dependents: bool = False


def knowledge_presentation_revision(
    *,
    new_id: UUID,
    knowledge_object_id: UUID,
    display_text: str,
    display_label: str | None,
    predecessor: KnowledgePresentationSnapshot | None,
) -> KnowledgePresentationRevisionSpecification:
    normalized_text = _required(display_text, field="display_text")
    normalized_label = _optional(display_label, field="display_label")
    if predecessor is None:
        return KnowledgePresentationRevisionSpecification(
            id=new_id,
            knowledge_object_id=knowledge_object_id,
            version=1,
            display_text=normalized_text,
            display_label=normalized_label,
            supersedes_id=None,
        )
    if predecessor.knowledge_object_id != knowledge_object_id:
        raise DomainValidationError(details={"reason": "presentation_object_mismatch"})
    if new_id == predecessor.id:
        raise DomainValidationError(details={"reason": "presentation_identity_reused"})
    if (
        predecessor.display_text == normalized_text
        and predecessor.display_label == normalized_label
    ):
        raise ResourceConflictError(details={"reason": "presentation_unchanged"})
    return KnowledgePresentationRevisionSpecification(
        id=new_id,
        knowledge_object_id=knowledge_object_id,
        version=predecessor.version + 1,
        display_text=normalized_text,
        display_label=normalized_label,
        supersedes_id=predecessor.id,
    )
