"""Stable mapping, Mental Model, and Judgment persistence vocabulary."""

from enum import StrEnum


class PaperAnchorType(StrEnum):
    SOURCE_CHUNK = "source_chunk"
    KNOWLEDGE_OBJECT = "knowledge_object"
    METHOD_MODEL = "method_model"
    METHOD_COMPONENT = "method_component"
    EQUATION = "equation"
    METHOD_SYMBOL = "method_symbol"
    ALGORITHM_STEP = "algorithm_step"
    DATA_FLOW = "data_flow"
    VALIDITY_CONDITION = "validity_condition"


class PaperCodeMappingType(StrEnum):
    IMPLEMENTS = "implements"
    CONFIGURES = "configures"
    CONSUMES = "consumes"
    PRODUCES = "produces"
    TESTS = "tests"
    EVALUATES = "evaluates"
    APPROXIMATES = "approximates"
    CONTRADICTS = "contradicts"
    UNDOCUMENTED_IN_PAPER = "undocumented_in_paper"
    ABSENT_FROM_REPO = "absent_from_repo"


class MentalModelStatus(StrEnum):
    DRAFT = "draft"
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"


class MentalModelMemberRole(StrEnum):
    CORE_CLAIM = "core_claim"
    MECHANISM = "mechanism"
    DEFINITION = "definition"
    ASSUMPTION = "assumption"
    LIMITATION = "limitation"
    VALIDITY_CONDITION = "validity_condition"
    KNOWLEDGE_GAP = "knowledge_gap"
    HUMAN_JUDGMENT = "human_judgment"
    IMPLEMENTATION_CONSEQUENCE = "implementation_consequence"
    DECISIVE_SUPPORTING_EVIDENCE = "decisive_supporting_evidence"
    CENTRAL_CONTRADICTING_EVIDENCE = "central_contradicting_evidence"


class MentalModelRenderingType(StrEnum):
    NARRATIVE = "narrative"


class MentalModelAssessmentResult(StrEnum):
    NO_CHANGE = "no_change"
    NEW_VERSION_CREATED = "new_version_created"


class JudgmentRequestStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
