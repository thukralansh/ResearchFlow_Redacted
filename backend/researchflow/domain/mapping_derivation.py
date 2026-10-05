"""Deterministic  mapping consequence, materiality, and gate policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

MAPPING_DERIVATION_POLICY_VERSION: Final = "rf-mapping-derivation-v1"
MAPPING_MATERIALITY_POLICY_VERSION: Final = "rf-mapping-materiality-v1"
MAPPING_DISCREPANCY_POLICY_VERSION: Final = "rf-mapping-discrepancy-v1"
MAPPING_REVIEW_GATE_POLICY_VERSION: Final = "rf-mapping-review-gate-v1"
MAPPING_DERIVATION_TASK_NAME: Final = "derive_mapping_knowledge"
MAPPING_DERIVATION_PROMPT_VERSION: Final = "v1"
MAPPING_DERIVATION_SCHEMA_NAME: Final = "mapping_derivation_batch"
MAPPING_DERIVATION_SCHEMA_VERSION: Final = "v1"


class MappingDiscrepancyType(StrEnum):
    CONSISTENT = "consistent"
    QUALIFIED = "qualified"
    IMPLEMENTATION_DETAIL = "implementation_detail"
    PARAMETER_OR_CONFIGURATION_DIFFERENCE = "parameter_or_configuration_difference"
    MISSING_FROM_REPOSITORY = "missing_from_repository"
    CONTRADICTION = "contradiction"
    AMBIGUOUS = "ambiguous"


class MappingMaterialityReason(StrEnum):
    RESEARCH_QUESTION_OR_OBJECTIVE = "research_question_or_objective"
    REQUIRED_METHOD_OR_VALIDITY = "required_method_or_validity"
    BLOCKING_OR_MATERIAL_REQUIREMENT = "blocking_or_material_requirement"
    MATERIAL_AMBIGUITY = "material_ambiguity"
    ACCEPTED_MODEL_SYNTHESIS = "accepted_model_synthesis"
    REPOSITORY_DEPENDENT_ELIGIBILITY = "repository_dependent_eligibility"
    BLUEPRINT_OR_VERIFICATION = "blueprint_or_verification"
    REPOSITORY_DETAIL_ONLY = "repository_detail_only"
    UNCERTAIN_DEFAULTS_MATERIAL = "uncertain_defaults_material"


@dataclass(frozen=True, slots=True)
class MappingReviewState:
    family: str
    id: UUID
    lineage_id: UUID
    version: int
    review_status: str
    lifecycle_status: str
    material: bool

    @property
    def terminal(self) -> bool:
        """Materiality is stable; blocking ends after explicit terminal review."""
        return self.lifecycle_status == "current" and self.review_status in {
            "accepted",
            "rejected",
        }

    def identity(self) -> dict[str, object]:
        return {
            "family": self.family,
            "id": str(self.id),
            "lineage_id": str(self.lineage_id),
            "version": self.version,
            "review_status": self.review_status,
            "lifecycle_status": self.lifecycle_status,
            "material": self.material,
        }


def mapping_review_gate_fingerprint(
    *,
    project_id: UUID,
    workflow_run_id: UUID,
    generation_record_id: UUID | None,
    repository_inspection_run_id: UUID | None,
    applicable: bool,
    states: tuple[MappingReviewState, ...],
    discrepancy_identity: object,
) -> str:
    payload = {
        "policy_version": MAPPING_REVIEW_GATE_POLICY_VERSION,
        "materiality_policy_version": MAPPING_MATERIALITY_POLICY_VERSION,
        "discrepancy_policy_version": MAPPING_DISCREPANCY_POLICY_VERSION,
        "project_id": str(project_id),
        "workflow_run_id": str(workflow_run_id),
        "generation_record_id": (
            None if generation_record_id is None else str(generation_record_id)
        ),
        "repository_inspection_run_id": (
            None if repository_inspection_run_id is None else str(repository_inspection_run_id)
        ),
        "applicable": applicable,
        "states": [
            state.identity()
            for state in sorted(states, key=lambda item: (item.family, str(item.id)))
        ],
        "discrepancies": discrepancy_identity,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def unresolved_material_states(
    states: tuple[MappingReviewState, ...],
) -> tuple[MappingReviewState, ...]:
    return tuple(state for state in states if state.material and not state.terminal)
