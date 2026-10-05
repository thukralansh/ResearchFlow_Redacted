"""Immutable paper-code mapping lifecycle and semantic-version policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from researchflow.domain.lifecycle import (
    CandidateCommand,
    CandidateLifecyclePolicy,
    CandidateSnapshot,
    CandidateTransitionResult,
    CanonicalArtifactFamily,
    SemanticEditPolicy,
)
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType
from researchflow.errors import DomainValidationError

MAPPING_REVIEW_POLICY_VERSION = "rf-mapping-review-v1"
MAPPING_EXPLANATION_MAX = 4_000


@dataclass(frozen=True, slots=True)
class MappingSemanticPayload:
    paper_anchor_type: PaperAnchorType
    anchor_id: UUID
    repository_component_id: UUID | None
    mapping_type: PaperCodeMappingType
    explanation: str
    confidence: Decimal | None

    def validated(self) -> MappingSemanticPayload:
        explanation = self.explanation.strip()
        if not explanation or len(explanation) > MAPPING_EXPLANATION_MAX:
            raise DomainValidationError(details={"field": "explanation", "reason": "invalid"})
        if self.confidence is not None and not Decimal("0") <= self.confidence <= Decimal("1"):
            raise DomainValidationError(
                details={"field": "confidence", "reason": "outside_unit_interval"}
            )
        absent = self.mapping_type is PaperCodeMappingType.ABSENT_FROM_REPO
        if absent != (self.repository_component_id is None):
            raise DomainValidationError(details={"reason": "mapping_component_shape_invalid"})
        return MappingSemanticPayload(
            paper_anchor_type=self.paper_anchor_type,
            anchor_id=self.anchor_id,
            repository_component_id=self.repository_component_id,
            mapping_type=self.mapping_type,
            explanation=explanation,
            confidence=self.confidence,
        )


class MappingLifecyclePolicy:
    """Mapping-specific facade over the frozen candidate lifecycle policy."""

    @staticmethod
    def transition(
        snapshot: CandidateSnapshot,
        command: CandidateCommand,
        *,
        command_id: UUID,
    ) -> CandidateTransitionResult:
        return CandidateLifecyclePolicy.transition(snapshot, command, command_id=command_id)

    @staticmethod
    def require_semantic_revision(
        current: MappingSemanticPayload,
        proposed: MappingSemanticPayload,
    ) -> MappingSemanticPayload:
        validated = proposed.validated()
        SemanticEditPolicy.require_semantic_revision(
            SemanticEditPolicy.classify(
                family=CanonicalArtifactFamily.PAPER_CODE_MAPPING,
                current_payload=current.validated(),
                proposed_payload=validated,
            )
        )
        return validated
