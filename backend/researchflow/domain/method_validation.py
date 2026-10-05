"""Deterministic validation policy for source-derived MethodModel candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from researchflow.domain.method_catalog import (
    MethodComponentType,
    MethodElementType,
    MethodMemberRole,
    MethodReviewSection,
    MethodSymbolType,
)
from researchflow.domain.relationships import (
    RELATIONSHIP_REGISTRY,
    EndpointType,
    RelationshipType,
    StorageStrategy,
)
from researchflow.errors import DomainValidationError

METHOD_EXTRACTION_VALIDATION_VERSION: Final = "rf-method-extraction-validation-v1"
METHOD_REQUIRED_POLICY_VERSION: Final = "rf-method-required-v1"
METHOD_SECTION_POLICY_VERSION: Final = "rf-method-section-v1"


@dataclass(frozen=True, slots=True)
class MethodMembershipClassification:
    review_section: MethodReviewSection
    member_role: MethodMemberRole
    required: bool = True


def classify_method_membership(
    *,
    element_type: MethodElementType,
    component_type: MethodComponentType | None = None,
) -> MethodMembershipClassification:
    """Classify first-slice candidates without model-selected section semantics."""
    if element_type is MethodElementType.COMPONENT:
        if component_type is None:
            raise DomainValidationError(details={"reason": "component_type_missing"})
        role = (
            MethodMemberRole.OBJECTIVE_CONTEXT
            if component_type is MethodComponentType.OBJECTIVE_CONTEXT
            else MethodMemberRole.MECHANISM
        )
        return MethodMembershipClassification(MethodReviewSection.OBJECTIVE_MECHANISM, role)
    if component_type is not None:
        raise DomainValidationError(details={"reason": "component_type_not_applicable"})
    mapping = {
        MethodElementType.EQUATION: (
            MethodReviewSection.EQUATIONS_SYMBOLS,
            MethodMemberRole.EQUATION,
        ),
        MethodElementType.SYMBOL: (
            MethodReviewSection.EQUATIONS_SYMBOLS,
            MethodMemberRole.SYMBOL_DEFINITION,
        ),
        MethodElementType.ALGORITHM_STEP: (
            MethodReviewSection.ALGORITHM_DATA_FLOW,
            MethodMemberRole.PROCEDURE,
        ),
        MethodElementType.DATA_FLOW: (
            MethodReviewSection.ALGORITHM_DATA_FLOW,
            MethodMemberRole.DATA_FLOW,
        ),
        MethodElementType.VALIDITY_CONDITION: (
            MethodReviewSection.VALIDITY_CONDITIONS,
            MethodMemberRole.VALIDITY_CONDITION,
        ),
    }
    try:
        section, role = mapping[element_type]
    except KeyError as error:
        raise DomainValidationError(details={"reason": "method_element_type_invalid"}) from error
    return MethodMembershipClassification(section, role)


def method_element_endpoint_type(
    *, element_type: MethodElementType, symbol_type: MethodSymbolType | None = None
) -> EndpointType:
    if element_type is MethodElementType.SYMBOL:
        if symbol_type is None:
            raise DomainValidationError(details={"reason": "symbol_type_missing"})
        return (
            EndpointType.METHOD_SYMBOL_VARIABLE
            if symbol_type is MethodSymbolType.VARIABLE
            else EndpointType.METHOD_SYMBOL_PARAMETER
        )
    if symbol_type is not None:
        raise DomainValidationError(details={"reason": "symbol_type_not_applicable"})
    return {
        MethodElementType.COMPONENT: EndpointType.METHOD_COMPONENT,
        MethodElementType.EQUATION: EndpointType.EQUATION,
        MethodElementType.ALGORITHM_STEP: EndpointType.ALGORITHM_STEP,
        MethodElementType.DATA_FLOW: EndpointType.DATA_FLOW,
        MethodElementType.VALIDITY_CONDITION: EndpointType.VALIDITY_CONDITION,
    }[element_type]


def source_method_evidence_relation_is_registered(
    *, element_type: MethodElementType, symbol_type: MethodSymbolType | None, relation: str
) -> bool:
    try:
        relationship_type = RelationshipType(relation)
    except ValueError:
        return False
    target = method_element_endpoint_type(element_type=element_type, symbol_type=symbol_type)
    return any(
        EndpointType.SOURCE_CHUNK in rule.source_types
        and rule.relationship_type is relationship_type
        and target in rule.target_types
        and rule.storage_strategy is StorageStrategy.EVIDENCE_LINK
        for rule in RELATIONSHIP_REGISTRY
    )


def validate_equation_expression(expression: str) -> None:
    """Reject prose-only values without attempting to rewrite mathematical meaning."""
    mathematical_markers = ("=", "\\", "^", "_", "*", "/", "∂", "∇", "Σ", "∫", "≤", "≥")
    if not any(marker in expression for marker in mathematical_markers):
        raise DomainValidationError(details={"reason": "equation_expression_not_structured"})


def validate_symbol_uniqueness(
    symbols: tuple[tuple[str, str, str], ...],
) -> None:
    """Require one client identity for each case-sensitive notation and meaning."""
    notation_seen: dict[str, str] = {}
    meaning_seen: dict[str, str] = {}
    for client_id, symbol, canonical_name in symbols:
        existing_notation = notation_seen.get(symbol)
        if existing_notation is not None and existing_notation != client_id:
            raise DomainValidationError(details={"reason": "duplicate_symbol_notation"})
        meaning_key = " ".join(canonical_name.split()).casefold()
        existing_meaning = meaning_seen.get(meaning_key)
        if existing_meaning is not None and existing_meaning != client_id:
            raise DomainValidationError(details={"reason": "duplicate_symbol_meaning"})
        notation_seen[symbol] = client_id
        meaning_seen[meaning_key] = client_id


def validate_data_flow_graph(edges: tuple[tuple[str, str], ...], *, allow_cycles: bool) -> None:
    if allow_cycles:
        return
    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise DomainValidationError(details={"reason": "data_flow_cycle_forbidden"})
        if node in visited:
            return
        visiting.add(node)
        for target in adjacency[node]:
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in tuple(adjacency):
        visit(node)
