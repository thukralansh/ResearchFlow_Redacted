"""Stable Blueprint, eligibility, dependency, and verification vocabulary."""

from enum import StrEnum
from typing import Final

BLUEPRINT_SECTION_CONTENT_MAX_BYTES: Final = 64 * 1024
ELIGIBILITY_MAPPING_COVERAGE_MAX_BYTES: Final = 32 * 1024
VERIFICATION_LIKELY_CAUSES_MAX_BYTES: Final = 16 * 1024


class BlueprintEligibilityStatus(StrEnum):
    BLOCKED = "blocked"
    ELIGIBLE_WITH_WARNINGS = "eligible_with_warnings"
    ELIGIBLE = "eligible"


class DependencyCriticality(StrEnum):
    BLOCKING = "blocking"
    MATERIAL = "material"
    INFORMATIONAL = "informational"


class EligibilityItemOutcome(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    INDETERMINATE = "indeterminate"
    NOT_APPLICABLE = "not_applicable"


class DependencyRole(StrEnum):
    DERIVATION_INPUT = "derivation_input"
    PROVENANCE_TRACE = "provenance_trace"
    VALIDATION_TARGET = "validation_target"


class DependencyTargetType(StrEnum):
    RESEARCH_QUESTION = "research_question"
    RESEARCH_QUESTION_SOURCE = "research_question_source"
    SOURCE_PROCESSING_RUN = "source_processing_run"
    PAPER_REPOSITORY_RELATIONSHIP = "paper_repository_relationship"
    REPOSITORY_SNAPSHOT = "repository_snapshot"
    REPOSITORY_COMPONENT = "repository_component"
    KNOWLEDGE_OBJECT = "knowledge_object"
    EVIDENCE_LINK = "evidence_link"
    GROUNDING_ASSESSMENT = "grounding_assessment"
    METHOD_MODEL = "method_model"
    METHOD_MODEL_ASSESSMENT = "method_model_assessment"
    METHOD_ELEMENT = "method_element"
    PAPER_CODE_MAPPING = "paper_code_mapping"
    MENTAL_MODEL = "mental_model"
    MENTAL_MODEL_ASSESSMENT = "mental_model_assessment"
    HUMAN_JUDGMENT = "human_judgment"
    BLUEPRINT_ELIGIBILITY_ASSESSMENT = "blueprint_eligibility_assessment"
    IMPLEMENTATION_BLUEPRINT = "implementation_blueprint"
    BLUEPRINT_SECTION = "blueprint_section"
    IMPLEMENTATION_STEP = "implementation_step"
    VERIFICATION_CHECK = "verification_check"


class BlueprintStatus(StrEnum):
    DRAFT = "draft"
    PROVISIONAL = "provisional"
    REVIEW_READY = "review_ready"
    ACCEPTED = "accepted"
    STALE = "stale"
    SUPERSEDED = "superseded"


class BlueprintSectionType(StrEnum):
    OBJECTIVE_AND_REPRODUCTION_TARGET = "objective_and_reproduction_target"
    CONCEPTUAL_ARCHITECTURE = "conceptual_architecture"
    DATA_AND_PREPROCESSING = "data_and_preprocessing"
    MODEL_AND_ALGORITHM = "model_and_algorithm"
    TRAINING = "training"
    EVALUATION = "evaluation"
    ENVIRONMENT_AND_DEPENDENCIES = "environment_and_dependencies"
    REPOSITORY_CORRESPONDENCE = "repository_correspondence"
    AMBIGUITY_AND_DECISION_LEDGER = "ambiguity_and_decision_ledger"
    LIMITATIONS_AND_OPEN_DECISIONS = "limitations_and_open_decisions"


class BlueprintSectionExplicitness(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    HUMAN_DECIDED = "human_decided"
    MIXED = "mixed"


class VerificationResultStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class InvalidationEffect(StrEnum):
    STALE = "stale"
    BLOCKED = "blocked"
    INVALID = "invalid"
    SUPERSEDED = "superseded"
