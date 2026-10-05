"""Exactly-one evidence-link validation and immutable evidence identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import UUID

from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import EvidenceRelation
from researchflow.domain.provenance import (
    EvidenceProvenance,
    RepositoryComponentEvidenceProvenance,
    evidence_provenance_identity,
    validate_evidence_excerpt,
)
from researchflow.domain.relationships import (
    EVIDENCE_TARGETS,
    EndpointType,
    RelationshipEndpoint,
    RelationshipPolicy,
    RelationshipRequest,
    RelationshipType,
    StorageStrategy,
)
from researchflow.errors import DomainValidationError

EVIDENCE_POLICY_VERSION: Final = "rf-evidence-policy-v1"


@dataclass(frozen=True, slots=True)
class EvidenceTargetSelector:
    project_id: UUID
    target_type: EndpointType
    target_version: int
    knowledge_object_id: UUID | None = None
    method_element_id: UUID | None = None
    current: bool = True

    def target_id(self) -> UUID:
        if (self.knowledge_object_id is None) == (self.method_element_id is None):
            raise DomainValidationError(details={"reason": "evidence_target_not_exactly_one"})
        if self.target_type not in EVIDENCE_TARGETS:
            raise DomainValidationError(details={"reason": "evidence_target_type_invalid"})
        is_method = self.target_type.value.startswith("method_") or self.target_type in {
            EndpointType.EQUATION,
            EndpointType.ALGORITHM_STEP,
            EndpointType.DATA_FLOW,
            EndpointType.VALIDITY_CONDITION,
        }
        if is_method != (self.method_element_id is not None):
            raise DomainValidationError(details={"reason": "evidence_target_family_mismatch"})
        if self.target_version < 1:
            raise DomainValidationError(details={"reason": "evidence_target_version_invalid"})
        if self.method_element_id is not None:
            return self.method_element_id
        if self.knowledge_object_id is None:  # pragma: no cover - exactly-one guard above
            raise AssertionError("validated evidence target must have an identity")
        return self.knowledge_object_id


@dataclass(frozen=True, slots=True)
class EvidenceSourceSelector:
    provenance: EvidenceProvenance
    source_chunk_id: UUID | None = None
    repository_component_id: UUID | None = None

    def source_id(self) -> UUID:
        if (self.source_chunk_id is None) == (self.repository_component_id is None):
            raise DomainValidationError(details={"reason": "evidence_source_not_exactly_one"})
        is_repository = isinstance(self.provenance, RepositoryComponentEvidenceProvenance)
        if is_repository != (self.repository_component_id is not None):
            raise DomainValidationError(details={"reason": "evidence_source_family_mismatch"})
        expected = (
            self.provenance.component_id
            if isinstance(self.provenance, RepositoryComponentEvidenceProvenance)
            else self.provenance.chunk_id
        )
        actual = self.repository_component_id or self.source_chunk_id
        if actual != expected:
            raise DomainValidationError(details={"reason": "evidence_source_identity_mismatch"})
        if actual is None:  # pragma: no cover - guarded by exactly-one validation above
            raise AssertionError("validated evidence source must have an identity")
        return actual


@dataclass(frozen=True, slots=True)
class EvidenceLinkRequest:
    id: UUID
    lineage_id: UUID
    version: int
    target: EvidenceTargetSelector
    source: EvidenceSourceSelector
    relation: EvidenceRelation
    excerpt: str
    confidence: Decimal | None
    review_status: KnowledgeReviewStatus
    lifecycle_status: KnowledgeLifecycleStatus
    material: bool
    materiality_policy_version: str
    objective_context_fingerprint: str


@dataclass(frozen=True, slots=True)
class ValidatedEvidenceLink:
    id: UUID
    lineage_id: UUID
    version: int
    project_id: UUID
    target_id: UUID
    target_type: EndpointType
    target_version: int
    source_id: UUID
    source_type: EndpointType
    relation: EvidenceRelation
    excerpt: str
    confidence: Decimal | None
    review_status: KnowledgeReviewStatus
    lifecycle_status: KnowledgeLifecycleStatus
    material: bool
    provenance: EvidenceProvenance
    relationship_fingerprint: str
    link_fingerprint: str

    @property
    def accepted_current(self) -> bool:
        return (
            self.review_status is KnowledgeReviewStatus.ACCEPTED
            and self.lifecycle_status is KnowledgeLifecycleStatus.CURRENT
            and self.provenance.current
        )


def _link_fingerprint(
    request: EvidenceLinkRequest,
    *,
    target_id: UUID,
    source_id: UUID,
    excerpt: str,
) -> str:
    payload = {
        "policy_version": EVIDENCE_POLICY_VERSION,
        "id": str(request.id),
        "lineage_id": str(request.lineage_id),
        "version": request.version,
        "project_id": str(request.target.project_id),
        "target_id": str(target_id),
        "target_type": request.target.target_type.value,
        "target_version": request.target.target_version,
        "source_id": str(source_id),
        "source": evidence_provenance_identity(request.source.provenance),
        "relation": request.relation.value,
        "excerpt": excerpt,
        "confidence": None if request.confidence is None else str(request.confidence),
        "review_status": request.review_status.value,
        "lifecycle_status": request.lifecycle_status.value,
        "material": request.material,
        "materiality_policy_version": request.materiality_policy_version,
        "objective_context_fingerprint": request.objective_context_fingerprint,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class EvidencePolicy:
    """Validate one exact target, one exact evidence source, and exact provenance."""

    @staticmethod
    def validate(request: EvidenceLinkRequest) -> ValidatedEvidenceLink:
        if request.version < 1:
            raise DomainValidationError(details={"reason": "evidence_link_version_invalid"})
        if request.id == request.lineage_id and request.version != 1:
            raise DomainValidationError(details={"reason": "evidence_lineage_version_invalid"})
        if request.review_status is KnowledgeReviewStatus.ACCEPTED:
            allowed_lifecycle = {
                KnowledgeLifecycleStatus.CURRENT,
                KnowledgeLifecycleStatus.CHALLENGED,
                KnowledgeLifecycleStatus.SUPERSEDED,
            }
        else:
            allowed_lifecycle = {KnowledgeLifecycleStatus.CURRENT}
        if request.lifecycle_status not in allowed_lifecycle:
            raise DomainValidationError(details={"reason": "evidence_review_lifecycle_invalid"})
        if request.confidence is not None and not Decimal("0") <= request.confidence <= Decimal(
            "1"
        ):
            raise DomainValidationError(details={"reason": "evidence_confidence_invalid"})
        if request.target.project_id != request.source.provenance.project_id:
            raise DomainValidationError(details={"reason": "evidence_cross_project"})
        target_id = request.target.target_id()
        source_id = request.source.source_id()
        excerpt = validate_evidence_excerpt(request.source.provenance, request.excerpt)
        source_type = (
            EndpointType.REPOSITORY_COMPONENT
            if isinstance(request.source.provenance, RepositoryComponentEvidenceProvenance)
            else EndpointType.SOURCE_CHUNK
        )
        relationship = RelationshipPolicy.validate(
            RelationshipRequest(
                source=RelationshipEndpoint(
                    id=source_id,
                    project_id=request.target.project_id,
                    endpoint_type=source_type,
                    current=request.source.provenance.current,
                ),
                relationship_type=RelationshipType(request.relation.value),
                target=RelationshipEndpoint(
                    id=target_id,
                    project_id=request.target.project_id,
                    endpoint_type=request.target.target_type,
                    version=request.target.target_version,
                    current=request.target.current,
                ),
                storage_strategy=StorageStrategy.EVIDENCE_LINK,
                review_status=request.review_status,
                material=request.material,
                materiality_policy_version=request.materiality_policy_version,
                objective_context_fingerprint=request.objective_context_fingerprint,
            )
        )
        return ValidatedEvidenceLink(
            id=request.id,
            lineage_id=request.lineage_id,
            version=request.version,
            project_id=request.target.project_id,
            target_id=target_id,
            target_type=request.target.target_type,
            target_version=request.target.target_version,
            source_id=source_id,
            source_type=source_type,
            relation=request.relation,
            excerpt=excerpt,
            confidence=request.confidence,
            review_status=request.review_status,
            lifecycle_status=request.lifecycle_status,
            material=request.material,
            provenance=request.source.provenance,
            relationship_fingerprint=relationship.fingerprint,
            link_fingerprint=_link_fingerprint(
                request,
                target_id=target_id,
                source_id=source_id,
                excerpt=excerpt,
            ),
        )
