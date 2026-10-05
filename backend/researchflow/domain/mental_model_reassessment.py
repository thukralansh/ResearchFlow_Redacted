"""Deterministic  Mental Model reassessment identity and change policy."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

MENTAL_MODEL_REASSESSMENT_POLICY_VERSION: Final = "rf-mental-model-reassessment-v1"
MENTAL_MODEL_REASSESSMENT_TASK_NAME: Final = "reassess_mental_model"
MENTAL_MODEL_REASSESSMENT_PROMPT_VERSION: Final = "v1"
MENTAL_MODEL_REASSESSMENT_SCHEMA_NAME: Final = "mental_model_reassessment"
MENTAL_MODEL_REASSESSMENT_SCHEMA_VERSION: Final = "v1"
MENTAL_MODEL_REASSESSMENT_RATIONALE_MAX: Final = 4_000


@dataclass(frozen=True, slots=True)
class MentalModelMemberSemantic:
    target_type: str
    target_id: object
    role: str
    inclusion_rationale: str
    confidence: Decimal | None


def reassessment_fingerprint(payload: object) -> str:
    """Return the exact deterministic identity of a reassessment input."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def member_semantic_identity(member: MentalModelMemberSemantic) -> tuple[object, ...]:
    """Return canonical member semantics independent of row/model identity."""
    return (
        member.target_type,
        member.target_id,
        member.role,
        member.inclusion_rationale.strip(),
        _decimal_identity(member.confidence),
    )


def semantic_composition_changed(
    *,
    prior_method_model_id: object,
    resulting_method_model_id: object,
    prior_members: tuple[MentalModelMemberSemantic, ...],
    proposed_members: tuple[MentalModelMemberSemantic, ...],
) -> bool:
    """Apply the approved material-change rubric to canonical structure only."""
    if prior_method_model_id != resulting_method_model_id:
        return True
    return sorted(map(member_semantic_identity, prior_members), key=str) != sorted(
        map(member_semantic_identity, proposed_members), key=str
    )


def _decimal_identity(value: Decimal | None) -> str | None:
    return None if value is None else str(value.normalize())
