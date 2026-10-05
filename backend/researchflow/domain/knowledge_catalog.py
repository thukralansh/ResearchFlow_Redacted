"""Stable proposition vocabulary and statement normalization."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum
from typing import Final

STATEMENT_NORMALIZATION_VERSION: Final = "v1"
_ORDINARY_WHITESPACE: Final = re.compile(r"[ \t\n\r\f\v]+")


class KnowledgeObjectType(StrEnum):
    CLAIM = "claim"
    ASSUMPTION = "assumption"
    KNOWLEDGE_GAP = "knowledge_gap"
    IMPLEMENTATION_REQUIREMENT = "implementation_requirement"
    AMBIGUITY = "ambiguity"


class KnowledgeOrigin(StrEnum):
    SOURCE_EXTRACTION = "source_extraction"
    AI_INFERENCE = "ai_inference"
    REPOSITORY_BEHAVIOR = "repository_behavior"
    HUMAN = "human"
    SYSTEM = "system"


class EpistemicStatus(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    SPECULATIVE = "speculative"
    HUMAN_DECIDED = "human_decided"


class GroundingStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    GROUNDED = "grounded"
    PARTIALLY_GROUNDED = "partially_grounded"
    MIXED = "mixed"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


class KnowledgeReviewStatus(StrEnum):
    CANDIDATE = "candidate"
    NEEDS_REVISION = "needs_revision"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class KnowledgeLifecycleStatus(StrEnum):
    CURRENT = "current"
    CHALLENGED = "challenged"
    SUPERSEDED = "superseded"


class ClaimType(StrEnum):
    SCIENTIFIC = "scientific"
    MATHEMATICAL = "mathematical"
    METHODOLOGICAL = "methodological"
    EMPIRICAL = "empirical"
    IMPLEMENTATION = "implementation"
    LIMITATION = "limitation"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    INCONCLUSIVE = "inconclusive"


class AssumptionType(StrEnum):
    THEORETICAL = "theoretical"
    MATHEMATICAL = "mathematical"
    COMPUTATIONAL = "computational"
    DATA = "data"
    EXPERIMENTAL = "experimental"
    IMPLEMENTATION = "implementation"


class ValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    INCONCLUSIVE = "inconclusive"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class KnowledgeGapType(StrEnum):
    CONCEPTUAL = "conceptual"
    MATHEMATICAL = "mathematical"
    COMPUTATIONAL = "computational"
    EVIDENTIARY = "evidentiary"
    LITERATURE = "literature"
    DATA = "data"
    EXPERIMENTAL = "experimental"
    REPOSITORY = "repository"


class PriorityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RequirementCategory(StrEnum):
    MATHEMATICAL = "mathematical"
    DATA = "data"
    PREPROCESSING = "preprocessing"
    ALGORITHM = "algorithm"
    ARCHITECTURE = "architecture"
    TRAINING = "training"
    EVALUATION = "evaluation"
    ENVIRONMENT = "environment"
    COMPUTE = "compute"
    REPRODUCIBILITY = "reproducibility"


class RequirementExplicitness(StrEnum):
    EXPLICIT = "explicit"
    INFERRED_FROM_PAPER = "inferred_from_paper"
    INFERRED_FROM_REPOSITORY = "inferred_from_repository"
    INFERRED_FROM_CONVENTION = "inferred_from_convention"
    HUMAN_DECIDED = "human_decided"
    SPECULATIVE = "speculative"


class SatisfactionStatus(StrEnum):
    UNSATISFIED = "unsatisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    SATISFIED = "satisfied"
    NOT_APPLICABLE = "not_applicable"


class AmbiguityType(StrEnum):
    MISSING_DETAIL = "missing_detail"
    CONFLICTING_SOURCES = "conflicting_sources"
    UNDEFINED_TERMINOLOGY = "undefined_terminology"
    PAPER_CODE_DISCREPANCY = "paper_code_discrepancy"
    MATHEMATICAL = "mathematical"
    ALGORITHMIC = "algorithmic"
    DATA = "data"
    FRAMEWORK = "framework"
    EVALUATION = "evaluation"
    ENVIRONMENT = "environment"


class ImpactLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    JUDGMENT_REQUESTED = "judgment_requested"
    RESOLVED = "resolved"


class GenerationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def normalize_statement(statement: str) -> str:
    """Normalize only presentation-neutral whitespace under the approved v1 policy."""
    normalized_line_endings = statement.replace("\r\n", "\n").replace("\r", "\n")
    composed = unicodedata.normalize("NFC", normalized_line_endings)
    return _ORDINARY_WHITESPACE.sub(" ", composed).strip()


def statement_fingerprint(statement: str) -> str:
    """Hash normalized UTF-8 as a deduplication signal, never semantic proof."""
    normalized = normalize_statement(statement)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
