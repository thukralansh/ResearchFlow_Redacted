"""Deterministic proposition atomicity and lexical duplicate policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from researchflow.domain.knowledge_catalog import normalize_statement, statement_fingerprint
from researchflow.domain.relationships import (
    RELATIONSHIP_REGISTRY,
    EndpointType,
    RelationshipType,
    StorageStrategy,
)
from researchflow.errors import DomainValidationError

ATOMICITY_POLICY_VERSION: Final = "rf-proposition-atomicity-v1"
LEXICAL_POLICY_VERSION: Final = "rf-proposition-lexical-v1"
GRANULARITY_POLICY_VERSION: Final = "rf-proposition-granularity-v2"
ADMISSION_POLICY_VERSION: Final = "rf-proposition-admission-v1"
QUALITY_POLICY_VERSION: Final = "rf-proposition-quality-v1"
DEFAULT_NEAR_DUPLICATE_THRESHOLD: Final = 0.82
MAX_ATOMIC_STATEMENT_CODEPOINTS: Final = 2_000

_SENTENCE_BOUNDARY: Final = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_INDEPENDENT_CONJUNCTION: Final = re.compile(
    r"\b(?:and|but|whereas|while)\s+(?:the|a|an|this|that|these|those|it|they|we)\s+",
    re.IGNORECASE,
)
_ENUMERATION: Final = re.compile(r"(?:^|\s)(?:\([a-z0-9]\)|[a-z0-9][.)])\s+", re.IGNORECASE)
_TOKEN: Final = re.compile(r"[^\W_]+", re.UNICODE)
_GAP_MARKER: Final = re.compile(
    r"\b(?:unknown|unclear|unresolved|whether|not (?:known|established|reported|specified))\b",
    re.IGNORECASE,
)
_REQUIREMENT_MARKER: Final = re.compile(
    r"\b(?:must|should|shall|need(?:s|ed)? to|require(?:s|d)?)\b", re.IGNORECASE
)
_IMPERATIVE_REQUIREMENT_MARKER: Final = re.compile(
    r"^(?:assess|compare|compute|control|define|document|establish|evaluate|hold|keep|"
    r"measure|preserve|quantify|record|report|retain|test|use|verify)\b",
    re.IGNORECASE,
)
_AMBIGUITY_MARKER: Final = re.compile(
    r"\b(?:ambiguous|ambiguity|conflict(?:ing)?|undefined|unclear|underspecified|"
    r"unspecified|not established|not specified)\b",
    re.IGNORECASE,
)
_STOPWORDS: Final = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "with",
    }
)


class LexicalDuplicateClass(StrEnum):
    EXACT = "exact"
    NEAR = "near"
    DISTINCT = "distinct"


class AtomicityPolicy:
    """Conservative deterministic rejection of obvious compound statements."""

    @staticmethod
    def validate(statement: str) -> str:
        normalized = normalize_statement(statement)
        if not normalized or len(normalized) > MAX_ATOMIC_STATEMENT_CODEPOINTS:
            raise DomainValidationError(details={"reason": "statement_length_invalid"})
        if "\n" in statement or ";" in normalized or _ENUMERATION.search(normalized):
            raise DomainValidationError(details={"reason": "multi_part_statement"})
        if len(_SENTENCE_BOUNDARY.split(normalized)) > 1:
            raise DomainValidationError(details={"reason": "multi_sentence_statement"})
        if _INDEPENDENT_CONJUNCTION.search(normalized):
            raise DomainValidationError(details={"reason": "independent_clause_conjunction"})
        return normalized


class PropositionGranularityPolicy:
    """Versioned subtype checks that reject structurally weak propositions."""

    @staticmethod
    def validate(
        *,
        object_type: str,
        statement: str,
        why_it_matters: str,
        verification_criterion: str | None = None,
        impact_level: str | None = None,
    ) -> str:
        normalized = AtomicityPolicy.validate(statement)
        if not normalize_statement(why_it_matters):
            raise DomainValidationError(details={"reason": "missing_why_it_matters"})
        if object_type == "knowledge_gap" and _GAP_MARKER.search(normalized) is None:
            raise DomainValidationError(details={"reason": "knowledge_gap_not_unknown"})
        if object_type == "implementation_requirement":
            if (
                _REQUIREMENT_MARKER.search(normalized) is None
                and _IMPERATIVE_REQUIREMENT_MARKER.search(normalized) is None
            ):
                raise DomainValidationError(details={"reason": "non_actionable_requirement"})
            criterion = normalize_statement(verification_criterion or "")
            if len(criterion.split()) < 3:
                raise DomainValidationError(details={"reason": "requirement_not_testable"})
        if object_type == "ambiguity":
            if impact_level == "low" or _AMBIGUITY_MARKER.search(normalized) is None:
                raise DomainValidationError(details={"reason": "trivial_ambiguity"})
        return normalized


class PropositionAdmissionPolicy:
    """Fail closed only on objectively decidable proposition invariants."""

    @staticmethod
    def validate(
        *,
        statement: str,
        why_it_matters: str,
        verification_criterion: str | None = None,
        requires_verification_criterion: bool = False,
    ) -> str:
        normalized = normalize_statement(statement)
        if not normalized or len(normalized) > MAX_ATOMIC_STATEMENT_CODEPOINTS:
            raise DomainValidationError(details={"reason": "statement_length_invalid"})
        if not normalize_statement(why_it_matters):
            raise DomainValidationError(details={"reason": "missing_why_it_matters"})
        if requires_verification_criterion and not normalize_statement(
            verification_criterion or ""
        ):
            raise DomainValidationError(details={"reason": "missing_verification_criterion"})
        return normalized


class PropositionQualityPolicy:
    """Return advisory semantic-quality findings without deciding candidate admission."""

    @staticmethod
    def assess(
        *,
        object_type: str,
        statement: str,
        verification_criterion: str | None = None,
        impact_level: str | None = None,
        epistemic_status: str | None = None,
        explicitness: str | None = None,
    ) -> tuple[str, ...]:
        normalized = normalize_statement(statement)
        findings: list[str] = []
        if "\n" in statement or ";" in normalized or _ENUMERATION.search(normalized):
            findings.append("possible_multi_part_statement")
        if len(_SENTENCE_BOUNDARY.split(normalized)) > 1:
            findings.append("possible_multi_sentence_statement")
        if _INDEPENDENT_CONJUNCTION.search(normalized):
            findings.append("possible_independent_clause_conjunction")
        if object_type == "knowledge_gap" and _GAP_MARKER.search(normalized) is None:
            findings.append("knowledge_gap_unknownness_requires_review")
        if object_type == "implementation_requirement":
            if (
                _REQUIREMENT_MARKER.search(normalized) is None
                and _IMPERATIVE_REQUIREMENT_MARKER.search(normalized) is None
            ):
                findings.append("requirement_actionability_requires_review")
            if len(normalize_statement(verification_criterion or "").split()) < 3:
                findings.append("requirement_testability_requires_review")
            if epistemic_status == "explicit" and explicitness == "explicit":
                findings.append("explicit_requirement_authority_requires_review")
        if object_type == "ambiguity" and (
            impact_level == "low" or _AMBIGUITY_MARKER.search(normalized) is None
        ):
            findings.append("ambiguity_materiality_requires_review")
        return tuple(findings)


def source_evidence_relation_is_registered(*, object_type: str, relation: str) -> bool:
    """Return whether SourceChunk evidence may use this relation for the target family."""
    try:
        target_type = EndpointType(object_type)
        relationship_type = RelationshipType(relation)
    except ValueError:
        return False
    return any(
        EndpointType.SOURCE_CHUNK in rule.source_types
        and rule.relationship_type is relationship_type
        and target_type in rule.target_types
        and rule.storage_strategy is StorageStrategy.EVIDENCE_LINK
        for rule in RELATIONSHIP_REGISTRY
    )


@dataclass(frozen=True, slots=True)
class LexicalSimilarityResult:
    classification: LexicalDuplicateClass
    score: float
    policy_version: str = LEXICAL_POLICY_VERSION


def lexical_similarity(
    left: str,
    right: str,
    *,
    threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> LexicalSimilarityResult:
    """Classify exact normalized identity or token-set similarity deterministically."""
    if not 0 < threshold <= 1:
        raise DomainValidationError(details={"field": "threshold"})
    if statement_fingerprint(left) == statement_fingerprint(right):
        return LexicalSimilarityResult(LexicalDuplicateClass.EXACT, 1.0)
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    score = 0.0 if not union else len(left_tokens & right_tokens) / len(union)
    classification = (
        LexicalDuplicateClass.NEAR if score >= threshold else LexicalDuplicateClass.DISTINCT
    )
    return LexicalSimilarityResult(classification, score)


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN.findall(normalize_statement(value).casefold())
        if token not in _STOPWORDS
    )
