"""Deterministic canonical identity and validation for Mental Models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import UUID

from researchflow.domain.knowledge_catalog import KnowledgeObjectType
from researchflow.domain.mental_model_roles import (
    MENTAL_MODEL_ROLE_POLICY_VERSION,
    validate_evidence_member_role,
    validate_judgment_member_role,
    validate_knowledge_member_role,
)
from researchflow.domain.model_catalog import MentalModelMemberRole
from researchflow.errors import DomainValidationError

MENTAL_MODEL_COMPOSITION_POLICY_VERSION: Final = "rf-mental-model-composition-v1"


@dataclass(frozen=True, slots=True)
class MentalModelMemberProposal:
    target_type: str
    target_id: UUID
    role: MentalModelMemberRole
    inclusion_rationale: str
    confidence: Decimal | None


def validate_member_proposal(
    proposal: MentalModelMemberProposal,
    *,
    knowledge_type: KnowledgeObjectType | None = None,
) -> None:
    if not proposal.inclusion_rationale.strip():
        raise DomainValidationError(details={"reason": "mental_model_rationale_blank"})
    if proposal.confidence is not None and not Decimal("0") <= proposal.confidence <= Decimal("1"):
        raise DomainValidationError(details={"reason": "mental_model_confidence_invalid"})
    if proposal.target_type == "knowledge_object":
        if knowledge_type is None:
            raise DomainValidationError(details={"reason": "mental_model_target_type_missing"})
        validate_knowledge_member_role(object_type=knowledge_type, role=proposal.role)
    elif proposal.target_type == "human_judgment":
        validate_judgment_member_role(proposal.role)
    elif proposal.target_type == "evidence_link":
        validate_evidence_member_role(proposal.role)
    else:
        raise DomainValidationError(details={"reason": "mental_model_target_type_invalid"})


def composition_fingerprint(
    *,
    research_question_id: UUID,
    method_model_id: UUID,
    accepted_input_fingerprint: str,
    members: tuple[MentalModelMemberProposal, ...],
) -> str:
    """Hash structured identity independent of provider output ordering."""
    if not members:
        raise DomainValidationError(details={"reason": "mental_model_members_empty"})
    identities = [(item.target_type, str(item.target_id)) for item in members]
    if len(identities) != len(set(identities)):
        raise DomainValidationError(details={"reason": "mental_model_member_duplicate"})
    payload = {
        "accepted_input_fingerprint": accepted_input_fingerprint,
        "composition_policy_version": MENTAL_MODEL_COMPOSITION_POLICY_VERSION,
        "member_role_policy_version": MENTAL_MODEL_ROLE_POLICY_VERSION,
        "members": sorted(
            (
                {
                    "confidence": None if item.confidence is None else str(item.confidence),
                    "inclusion_rationale": item.inclusion_rationale.strip(),
                    "role": item.role.value,
                    "target_id": str(item.target_id),
                    "target_type": item.target_type,
                }
                for item in members
            ),
            key=lambda item: (str(item["target_type"]), str(item["target_id"])),
        ),
        "method_model_id": str(method_model_id),
        "research_question_id": str(research_question_id),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()
