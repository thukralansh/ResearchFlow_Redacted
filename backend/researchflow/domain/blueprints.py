"""Deterministic  Blueprint section and provenance policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import UUID

from researchflow.domain.blueprint_catalog import (
    BlueprintEligibilityStatus,
    BlueprintSectionExplicitness,
    BlueprintSectionType,
    BlueprintStatus,
)
from researchflow.errors import DomainValidationError

BLUEPRINT_TASK_NAME: Final = "generate_blueprint"
BLUEPRINT_PROMPT_VERSION: Final = "v1"
BLUEPRINT_SCHEMA_NAME: Final = "implementation_blueprint"
BLUEPRINT_SCHEMA_VERSION: Final = "v1"
BLUEPRINT_SECTION_POLICY_VERSION: Final = "rf-blueprint-sections-v1"
BLUEPRINT_REFERENCE_SCHEMA_POLICY_VERSION: Final = "rf-blueprint-reference-schema-v1"
BLUEPRINT_REVIEW_POLICY_VERSION: Final = "rf-blueprint-review-v1"
BLUEPRINT_REVISION_PROMPT_VERSION: Final = "v1"
BLUEPRINT_REVISION_SCHEMA_VERSION: Final = "v1"
BLUEPRINT_REVISION_TASK_NAME: Final = "revise_blueprint"
BLUEPRINT_RATIONALE_MAX: Final = 4_000

BLUEPRINT_SECTION_ORDER: Final = tuple(BlueprintSectionType)
_MATHEMATICAL_TYPES: Final = frozenset({"equation", "symbol", "validity_condition"})
_ALGORITHMIC_TYPES: Final = frozenset({"algorithm_step", "data_flow"})


@dataclass(frozen=True, slots=True)
class BlueprintReferenceTarget:
    alias: str
    dependency_type: str
    dependency_id: UUID
    semantic_type: str | None = None


def canonical_fingerprint(value: object) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_canonical_json_value,
    )


def _canonical_json_value(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"unsupported canonical Blueprint value: {type(value).__name__}")


def status_for_eligibility(status: str) -> BlueprintStatus:
    value = BlueprintEligibilityStatus(status)
    if value is BlueprintEligibilityStatus.BLOCKED:
        raise DomainValidationError(details={"reason": "blueprint_eligibility_blocked"})
    if value is BlueprintEligibilityStatus.ELIGIBLE_WITH_WARNINGS:
        return BlueprintStatus.PROVISIONAL
    return BlueprintStatus.REVIEW_READY


def aggregate_explicitness(values: tuple[str, ...]) -> BlueprintSectionExplicitness:
    if not values:
        raise DomainValidationError(details={"reason": "blueprint_section_empty"})
    unique = {BlueprintSectionExplicitness(value) for value in values}
    if len(unique) == 1:
        return next(iter(unique))
    return BlueprintSectionExplicitness.MIXED


def validate_item_references(
    *,
    explicitness: str,
    aliases: tuple[str, ...],
    catalog: dict[str, BlueprintReferenceTarget],
    semantic_role: str | None = None,
) -> tuple[BlueprintReferenceTarget, ...]:
    if not aliases:
        raise DomainValidationError(details={"reason": "blueprint_item_reference_missing"})
    try:
        targets = tuple(catalog[alias] for alias in aliases)
    except KeyError as error:
        raise DomainValidationError(
            details={"reason": "blueprint_reference_not_in_context"}
        ) from error
    item_explicitness = BlueprintSectionExplicitness(explicitness)
    if item_explicitness is BlueprintSectionExplicitness.HUMAN_DECIDED and not any(
        item.dependency_type == "human_judgment" for item in targets
    ):
        raise DomainValidationError(details={"reason": "human_decision_reference_required"})
    if semantic_role == "mathematical_specification" and not any(
        item.dependency_type == "method_element" and item.semantic_type in _MATHEMATICAL_TYPES
        for item in targets
    ):
        raise DomainValidationError(details={"reason": "mathematical_method_anchor_required"})
    if semantic_role == "algorithm" and not any(
        item.dependency_type == "method_element" and item.semantic_type in _ALGORITHMIC_TYPES
        for item in targets
    ):
        raise DomainValidationError(details={"reason": "algorithm_method_anchor_required"})
    return targets


def validate_section_summary(
    *, declared: str, item_explicitness: tuple[str, ...]
) -> BlueprintSectionExplicitness:
    aggregate = aggregate_explicitness(item_explicitness)
    if BlueprintSectionExplicitness(declared) is not aggregate:
        raise DomainValidationError(details={"reason": "section_explicitness_mismatch"})
    return aggregate


def validate_confidence(value: Decimal | None) -> None:
    if value is not None and (value < 0 or value > 1):
        raise DomainValidationError(details={"reason": "blueprint_confidence_invalid"})


def validate_review_rationale(value: str) -> str:
    """Return one bounded human-authored rationale without rewriting it."""
    normalized = value.strip()
    if not normalized or len(normalized) > BLUEPRINT_RATIONALE_MAX:
        raise DomainValidationError(details={"reason": "blueprint_rationale_invalid"})
    return normalized


def require_acceptance_status(*, status: str, eligibility_status: str) -> None:
    """Enforce the immutable Blueprint acceptance boundary."""
    if BlueprintStatus(status) is not BlueprintStatus.REVIEW_READY:
        raise DomainValidationError(details={"reason": "blueprint_not_review_ready"})
    if BlueprintEligibilityStatus(eligibility_status) is not BlueprintEligibilityStatus.ELIGIBLE:
        raise DomainValidationError(details={"reason": "blueprint_warnings_unresolved"})


def require_revision_status(status: str) -> None:
    """Allow semantic replacement of a current proposal or accepted Blueprint only."""
    value = BlueprintStatus(status)
    if value not in {
        BlueprintStatus.DRAFT,
        BlueprintStatus.PROVISIONAL,
        BlueprintStatus.REVIEW_READY,
        BlueprintStatus.ACCEPTED,
    }:
        raise DomainValidationError(details={"reason": "blueprint_revision_source_not_current"})
