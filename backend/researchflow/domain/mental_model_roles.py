"""Versioned compatibility policy for structured Mental Model membership."""

from __future__ import annotations

from typing import Final

from researchflow.domain.knowledge_catalog import KnowledgeObjectType
from researchflow.domain.model_catalog import MentalModelMemberRole
from researchflow.errors import DomainValidationError

MENTAL_MODEL_ROLE_POLICY_VERSION: Final = "rf-mental-model-role-v1"

_KNOWLEDGE_ROLES: Final[dict[KnowledgeObjectType, frozenset[MentalModelMemberRole]]] = {
    KnowledgeObjectType.CLAIM: frozenset(
        {
            MentalModelMemberRole.CORE_CLAIM,
            MentalModelMemberRole.MECHANISM,
            MentalModelMemberRole.DEFINITION,
            MentalModelMemberRole.LIMITATION,
            MentalModelMemberRole.VALIDITY_CONDITION,
        }
    ),
    KnowledgeObjectType.ASSUMPTION: frozenset({MentalModelMemberRole.ASSUMPTION}),
    KnowledgeObjectType.KNOWLEDGE_GAP: frozenset({MentalModelMemberRole.KNOWLEDGE_GAP}),
    KnowledgeObjectType.IMPLEMENTATION_REQUIREMENT: frozenset(
        {MentalModelMemberRole.IMPLEMENTATION_CONSEQUENCE}
    ),
    # An Ambiguity is unresolved interpretation/conflict, not missing knowledge.
    KnowledgeObjectType.AMBIGUITY: frozenset({MentalModelMemberRole.LIMITATION}),
}

_EXCEPTIONAL_EVIDENCE_ROLES: Final = frozenset(
    {
        MentalModelMemberRole.DECISIVE_SUPPORTING_EVIDENCE,
        MentalModelMemberRole.CENTRAL_CONTRADICTING_EVIDENCE,
    }
)


def validate_knowledge_member_role(
    *, object_type: KnowledgeObjectType, role: MentalModelMemberRole
) -> None:
    """Reject a role that changes the meaning of a typed KnowledgeObject."""
    if role not in _KNOWLEDGE_ROLES[object_type]:
        raise DomainValidationError(details={"reason": "mental_model_role_incompatible"})


def validate_judgment_member_role(role: MentalModelMemberRole) -> None:
    if role is not MentalModelMemberRole.HUMAN_JUDGMENT:
        raise DomainValidationError(details={"reason": "mental_model_role_incompatible"})


def validate_evidence_member_role(role: MentalModelMemberRole) -> None:
    if role not in _EXCEPTIONAL_EVIDENCE_ROLES:
        raise DomainValidationError(details={"reason": "mental_model_evidence_role_invalid"})


def compatible_roles(object_type: KnowledgeObjectType) -> frozenset[MentalModelMemberRole]:
    """Expose the immutable first-slice matrix for contracts and tests."""
    return _KNOWLEDGE_ROLES[object_type]
