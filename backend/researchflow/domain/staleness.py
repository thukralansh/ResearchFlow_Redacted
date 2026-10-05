"""Deterministic first-slice direct staleness policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from researchflow.domain.blueprint_catalog import InvalidationEffect
from researchflow.errors import DomainValidationError

DIRECT_STALENESS_POLICY_VERSION: Final = "rf-direct-staleness-v1"
INVALIDATABLE_ARTIFACT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "method_model",
        "paper_code_mapping",
        "mental_model",
        "blueprint_eligibility_assessment",
        "implementation_blueprint",
        "blueprint_section",
        "implementation_step",
        "verification_check",
    }
)


class StalenessReason(StrEnum):
    RESEARCH_QUESTION_REVISED = "staleness.research_question_revised"
    RESEARCH_QUESTION_REOPENED = "staleness.research_question_reopened"
    QUESTION_SOURCE_REVISED = "staleness.question_source_revised"
    REPOSITORY_RELATIONSHIP_REVISED = "staleness.repository_relationship_revised"
    SOURCE_PROCESSING_CHANGED = "staleness.source_processing_changed"
    SOURCE_CONTENT_CHANGED = "staleness.source_content_changed"
    REPOSITORY_SNAPSHOT_REPLACED = "staleness.repository_snapshot_replaced"
    KNOWLEDGE_SUPERSEDED = "staleness.knowledge_superseded"
    KNOWLEDGE_CHALLENGED = "staleness.knowledge_challenged"
    REQUIREMENT_WITHDRAWN = "staleness.requirement_withdrawn"
    METHOD_ELEMENT_SUPERSEDED = "staleness.method_element_superseded"
    METHOD_ELEMENT_CHALLENGED = "staleness.method_element_challenged"
    METHOD_MODEL_REPLACED = "staleness.method_model_replaced"
    MAPPING_REVISED = "staleness.mapping_revised"
    MAPPING_REJECTED = "staleness.mapping_rejected"
    HUMAN_JUDGMENT_SUPERSEDED = "staleness.human_judgment_superseded"
    AMBIGUITY_RESOLVED_BY_JUDGMENT = "staleness.ambiguity_resolved_by_judgment"
    MENTAL_MODEL_REPLACED = "staleness.mental_model_replaced"
    IMPLEMENTATION_STEP_REVISED = "staleness.implementation_step_revised"
    MATERIAL_RELATIONSHIP_REVISED = "staleness.material_relationship_revised"
    REQUIRED_DEPENDENCY_MISSING = "staleness.required_dependency_missing"
    INTEGRITY_FAILURE = "staleness.integrity_failure"


@dataclass(frozen=True, slots=True)
class DirectStalenessRule:
    cause_type: str
    reason: StalenessReason
    effect: InvalidationEffect = InvalidationEffect.STALE
    material_only: bool = False


_RULES: Mapping[str, DirectStalenessRule] = MappingProxyType(
    {
        "research_question.refined": DirectStalenessRule(
            "research_question", StalenessReason.RESEARCH_QUESTION_REVISED
        ),
        "research_question.reopened": DirectStalenessRule(
            "research_question", StalenessReason.RESEARCH_QUESTION_REOPENED
        ),
        "source.primary_paper.replaced": DirectStalenessRule(
            "research_question_source", StalenessReason.SOURCE_CONTENT_CHANGED
        ),
        "source.question_relationship.revised": DirectStalenessRule(
            "research_question_source", StalenessReason.QUESTION_SOURCE_REVISED
        ),
        "source.processing.changed": DirectStalenessRule(
            "source_processing_run", StalenessReason.SOURCE_PROCESSING_CHANGED
        ),
        "repository.relationship.revised": DirectStalenessRule(
            "paper_repository_relationship",
            StalenessReason.REPOSITORY_RELATIONSHIP_REVISED,
        ),
        "repository.snapshot.replaced": DirectStalenessRule(
            "repository_snapshot", StalenessReason.REPOSITORY_SNAPSHOT_REPLACED
        ),
        "knowledge_object.accept_revision": DirectStalenessRule(
            "knowledge_object", StalenessReason.KNOWLEDGE_SUPERSEDED
        ),
        "knowledge_object.challenge": DirectStalenessRule(
            "knowledge_object", StalenessReason.KNOWLEDGE_CHALLENGED
        ),
        "knowledge.requirement_withdrawn": DirectStalenessRule(
            "knowledge_object", StalenessReason.REQUIREMENT_WITHDRAWN
        ),
        "method_element.accept_revision": DirectStalenessRule(
            "method_element", StalenessReason.METHOD_ELEMENT_SUPERSEDED
        ),
        "method_element.challenge": DirectStalenessRule(
            "method_element", StalenessReason.METHOD_ELEMENT_CHALLENGED
        ),
        "method_model.replaced": DirectStalenessRule(
            "method_model", StalenessReason.METHOD_MODEL_REPLACED
        ),
        "paper_code_mapping.accept_revision": DirectStalenessRule(
            "paper_code_mapping", StalenessReason.MAPPING_REVISED
        ),
        "paper_code_mapping.challenge": DirectStalenessRule(
            "paper_code_mapping", StalenessReason.MAPPING_REJECTED
        ),
        "human_judgment.revised": DirectStalenessRule(
            "human_judgment", StalenessReason.HUMAN_JUDGMENT_SUPERSEDED
        ),
        "human_judgment.ambiguity_resolved": DirectStalenessRule(
            "knowledge_object", StalenessReason.AMBIGUITY_RESOLVED_BY_JUDGMENT
        ),
        "mental_model.replaced": DirectStalenessRule(
            "mental_model", StalenessReason.MENTAL_MODEL_REPLACED
        ),
        "implementation_step.revised": DirectStalenessRule(
            "implementation_step", StalenessReason.IMPLEMENTATION_STEP_REVISED
        ),
        "material_relationship.revised": DirectStalenessRule(
            "evidence_link", StalenessReason.MATERIAL_RELATIONSHIP_REVISED, material_only=True
        ),
        "evidence_link.challenge": DirectStalenessRule(
            "evidence_link", StalenessReason.MATERIAL_RELATIONSHIP_REVISED, material_only=True
        ),
        "dependency.required_missing": DirectStalenessRule(
            "knowledge_object",
            StalenessReason.REQUIRED_DEPENDENCY_MISSING,
            InvalidationEffect.BLOCKED,
        ),
        "dependency.integrity_failure": DirectStalenessRule(
            "knowledge_object",
            StalenessReason.INTEGRITY_FAILURE,
            InvalidationEffect.INVALID,
        ),
    }
)

_NO_EFFECT_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "knowledge.reject",
        "knowledge.presentation_revised",
        "knowledge_object.accept",
        "knowledge_object.reject",
        "knowledge_object.request_revision",
        "knowledge_object.reaffirm",
        "knowledge_object.semantic_revision",
        "knowledge_object.presentation_revision",
        "evidence_link.accept",
        "evidence_link.reject",
        "evidence_link.request_revision",
        "evidence_link.reaffirm",
        "mapping.candidate_rejected",
        "paper_code_mapping.accept",
        "paper_code_mapping.reject",
        "paper_code_mapping.request_revision",
        "paper_code_mapping.reaffirm",
        "paper_code_mapping.semantic_revision",
        "method_element.accept",
        "method_element.reject",
        "method_element.request_revision",
        "method_element.reaffirm",
        "method_element.semantic_revision",
        "method_evidence.accept",
        "method_evidence.reject",
        "method_evidence.request_revision",
        "method_evidence.challenge",
        "method_evidence.reaffirm",
        "method_model.exclude_element",
        "method_model.section.accepted",
        "method_model.section.not_applicable",
        "method_model.reassessment.no_change",
        "mental_model.reassessment.no_change",
        "source.processing.same_hash_unchanged",
    }
)

_PRECEDENCE: Final[Mapping[str, int]] = MappingProxyType(
    {"current": 0, "stale": 1, "blocked": 2, "invalid": 3}
)


def rule_for(operation_type: str) -> DirectStalenessRule | None:
    if operation_type in _NO_EFFECT_OPERATIONS:
        return None
    try:
        return _RULES[operation_type]
    except KeyError as error:
        raise DomainValidationError(details={"reason": "unknown_staleness_rule"}) from error


def strongest_effect(effects: tuple[str, ...]) -> str:
    return max(("current", *effects), key=lambda value: _PRECEDENCE[value])


def reason_text(reason: StalenessReason) -> str:
    return reason.value.removeprefix("staleness.").replace("_", " ").capitalize() + "."


def registered_rules() -> Mapping[str, DirectStalenessRule]:
    return _RULES
