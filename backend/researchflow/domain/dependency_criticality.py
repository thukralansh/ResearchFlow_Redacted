"""Deterministic dependency-scoped criticality for Judgment Requests."""

from dataclasses import dataclass
from typing import Final

from researchflow.domain.blueprint_catalog import DependencyCriticality, DependencyRole

JUDGMENT_REQUEST_POLICY_VERSION: Final = "rf-judgment-request-policy-v1"


@dataclass(frozen=True, slots=True)
class AffectedDependency:
    dependency_type: str
    dependency_id: str
    dependency_role: DependencyRole
    required_for_objective: bool
    correctness_requires_resolution: bool
    material_consequence: bool
    rationale: str


@dataclass(frozen=True, slots=True)
class CriticalityAssessment:
    criticality: DependencyCriticality
    rationale: str


def classify_dependency(value: AffectedDependency) -> CriticalityAssessment:
    """Classify consequence, never global project importance or source authority."""
    if value.required_for_objective and value.correctness_requires_resolution:
        return CriticalityAssessment(
            criticality=DependencyCriticality.BLOCKING,
            rationale=(
                "The identified downstream operation is required by the project objective and "
                "cannot correctly proceed without resolving this interpretation. Unrelated "
                "project work is not blocked."
            ),
        )
    if (
        value.required_for_objective
        or value.correctness_requires_resolution
        or value.material_consequence
    ):
        return CriticalityAssessment(
            criticality=DependencyCriticality.MATERIAL,
            rationale=(
                "The ambiguity can materially affect the project objective or downstream state, "
                "but the supplied dependency context does not prove that work cannot correctly "
                "proceed."
            ),
        )
    return CriticalityAssessment(
        criticality=DependencyCriticality.INFORMATIONAL,
        rationale=(
            "The supplied dependency context identifies provenance or explanatory relevance but "
            "no material downstream consequence."
        ),
    )
