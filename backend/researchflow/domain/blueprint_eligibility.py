"""Deterministic, applicability-aware Blueprint eligibility policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from researchflow.domain.blueprint_catalog import (
    BlueprintEligibilityStatus,
    DependencyCriticality,
    EligibilityItemOutcome,
)

BLUEPRINT_ELIGIBILITY_RULE_VERSION: Final = "rf-blueprint-eligibility-v1"


class EligibilityReason(StrEnum):
    QUESTION_CURRENT = "question.current"
    PRIMARY_PAPER_CURRENT = "source.primary_paper_current"
    PRIMARY_PAPER_PROCESSED = "source.primary_paper_processed"
    METHOD_MODEL_CURRENT = "method_model.accepted_current"
    METHOD_SECTIONS_REVIEWED = "method_model.sections_reviewed"
    METHOD_ASSESSMENT_CURRENT = "method_model.assessment_current"
    METHOD_ASSESSMENT_WARNINGS = "method_model.assessment_warnings"
    MENTAL_MODEL_CURRENT = "mental_model.current"
    MENTAL_ASSESSMENT_CURRENT = "mental_model.assessment_current"
    REPOSITORY_NOT_APPLICABLE = "repository.not_applicable"
    REPOSITORY_CURRENT = "repository.current"
    MAPPING_REVIEW_COMPLETE = "mapping.review_complete"
    MAPPING_COVERAGE_PARTIAL = "mapping.coverage_partial"
    REQUIREMENT_CURRENT = "knowledge.requirement_current"
    AMBIGUITY_UNRESOLVED = "knowledge.ambiguity_unresolved"
    JUDGMENT_OPEN = "judgment.request_open"
    JUDGMENT_CURRENT = "judgment.current"


@dataclass(frozen=True, slots=True)
class EligibilityFinding:
    expected_dependency_type: str
    target_id: UUID | None
    criticality: DependencyCriticality
    outcome: EligibilityItemOutcome
    reason_code: EligibilityReason
    explanation: str


@dataclass(frozen=True, slots=True)
class EligibilityEvaluation:
    status: BlueprintEligibilityStatus
    findings: tuple[EligibilityFinding, ...]


def evaluate_eligibility(findings: tuple[EligibilityFinding, ...]) -> EligibilityEvaluation:
    """Reduce exact typed findings without confidence arithmetic or model judgment."""
    if any(
        item.criticality is DependencyCriticality.BLOCKING
        and item.outcome
        in {EligibilityItemOutcome.UNSATISFIED, EligibilityItemOutcome.INDETERMINATE}
        for item in findings
    ):
        status = BlueprintEligibilityStatus.BLOCKED
    elif any(
        item.criticality is DependencyCriticality.MATERIAL
        and item.outcome
        in {EligibilityItemOutcome.UNSATISFIED, EligibilityItemOutcome.INDETERMINATE}
        for item in findings
    ):
        status = BlueprintEligibilityStatus.ELIGIBLE_WITH_WARNINGS
    else:
        status = BlueprintEligibilityStatus.ELIGIBLE
    return EligibilityEvaluation(status=status, findings=findings)


def permits_blueprint_generation(status: BlueprintEligibilityStatus) -> bool:
    """Expose the deterministic workflow routing contract without generating anything."""
    return status is not BlueprintEligibilityStatus.BLOCKED


def eligibility_input_fingerprint(payload: dict[str, object]) -> str:
    """Hash the complete deterministic input identity under the immutable v1 rule."""
    encoded = json.dumps(
        {"rule_version": BLUEPRINT_ELIGIBILITY_RULE_VERSION, **payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
