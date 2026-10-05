"""Stable structured-method, evidence, and relationship vocabulary."""

from enum import StrEnum
from typing import Final

PARAMETER_VALUE_MAX_BYTES: Final = 4 * 1024
DATA_SHAPE_MAX_BYTES: Final = 8 * 1024
DATA_SHAPE_MAX_DIMENSIONS: Final = 16


class FreshnessStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    BLOCKED = "blocked"
    INVALID = "invalid"


class MethodElementType(StrEnum):
    COMPONENT = "component"
    EQUATION = "equation"
    SYMBOL = "symbol"
    ALGORITHM_STEP = "algorithm_step"
    DATA_FLOW = "data_flow"
    VALIDITY_CONDITION = "validity_condition"


class MethodEpistemicStatus(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    IMPLEMENTATION_CONVENTION = "implementation_convention"
    REPOSITORY_OBSERVED = "repository_observed"
    HUMAN_DECIDED = "human_decided"


class MethodReviewSection(StrEnum):
    OBJECTIVE_MECHANISM = "objective_mechanism"
    EQUATIONS_SYMBOLS = "equations_symbols"
    ALGORITHM_DATA_FLOW = "algorithm_data_flow"
    VALIDITY_CONDITIONS = "validity_conditions"


class MethodMemberRole(StrEnum):
    OBJECTIVE_CONTEXT = "objective_context"
    MECHANISM = "mechanism"
    EQUATION = "equation"
    SYMBOL_DEFINITION = "symbol_definition"
    PROCEDURE = "procedure"
    DATA_FLOW = "data_flow"
    VALIDITY_CONDITION = "validity_condition"


class SectionReviewOutcome(StrEnum):
    ACCEPTED = "accepted"
    NOT_APPLICABLE = "not_applicable"


class MethodRenderingType(StrEnum):
    NARRATIVE = "narrative"


class MethodAssessmentOutcome(StrEnum):
    SUFFICIENT = "sufficient"
    SUFFICIENT_WITH_WARNINGS = "sufficient_with_warnings"
    INSUFFICIENT = "insufficient"


class MethodComponentType(StrEnum):
    OBJECTIVE_CONTEXT = "objective_context"
    MECHANISM = "mechanism"
    TRANSFORMATION = "transformation"
    MODEL = "model"
    LOSS = "loss"
    PREPROCESSING = "preprocessing"
    TRAINING = "training"
    EVALUATION = "evaluation"
    INTERFACE = "interface"


class MethodEquationType(StrEnum):
    DEFINITION = "definition"
    OBJECTIVE = "objective"
    GOVERNING = "governing"
    TRANSFORMATION = "transformation"
    CONSTRAINT = "constraint"
    UPDATE_RULE = "update_rule"
    LOSS = "loss"
    METRIC = "metric"


class MethodSymbolType(StrEnum):
    VARIABLE = "variable"
    PARAMETER = "parameter"


class VariableRole(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    INTERMEDIATE = "intermediate"
    TARGET = "target"
    OBSERVED = "observed"
    LATENT = "latent"
    STATE = "state"


class ParameterRole(StrEnum):
    TRAINABLE = "trainable"
    FIXED = "fixed"
    HYPERPARAMETER = "hyperparameter"
    PHYSICAL = "physical"
    NUMERICAL = "numerical"
    CONFIGURATION = "configuration"


class ValidityConditionType(StrEnum):
    ASSUMPTION = "assumption"
    PRECONDITION = "precondition"
    BOUNDARY_CONDITION = "boundary_condition"
    INVARIANT = "invariant"
    LIMITATION = "limitation"
    FAILURE_CONDITION = "failure_condition"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"


class GroundingAssessmentStatus(StrEnum):
    GROUNDED = "grounded"
    PARTIALLY_GROUNDED = "partially_grounded"
    MIXED = "mixed"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


class ObjectLinkRelationship(StrEnum):
    SUPPORTED_BY = "supported_by"
    DEPENDS_ON = "depends_on"
    USES = "uses"
    CONCERNS = "concerns"
    AFFECTS = "affects"
    DERIVED_FROM = "derived_from"
    QUALIFIES = "qualifies"
    REVEALS = "reveals"
    SATISFIES = "satisfies"
    VALIDATES = "validates"
