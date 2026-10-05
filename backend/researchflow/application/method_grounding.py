"""Persistence-facing grounding and initial MethodModel assessment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from researchflow.domain.grounding import (
    DEFAULT_GROUNDING_POLICY,
    GroundingAssessmentSpecification,
    MethodMemberGrounding,
    MethodModelAssessmentPolicy,
    MethodModelAssessmentSpecification,
)
from researchflow.domain.method_catalog import GroundingAssessmentStatus
from researchflow.domain.relationships import EndpointType
from researchflow.ports.repositories import MethodEvidenceLinkData

METHOD_GROUNDING_VALIDATOR_NAME: Final = "researchflow-grounding-policy"
METHOD_GROUNDING_VALIDATOR_VERSION: Final = "rf-grounding-policy-v1"
METHOD_APPLICABILITY_POLICY_VERSION: Final = "rf-method-required-v1"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assess_method_evidence(
    *,
    project_id: UUID,
    element_id: UUID,
    element_version: int = 1,
    endpoint_type: EndpointType,
    links: tuple[MethodEvidenceLinkData, ...],
) -> GroundingAssessmentSpecification:
    complete = [
        {
            "id": str(link.id),
            "lineage_id": str(link.lineage_id),
            "version": link.version,
            "source_chunk_id": (
                None if link.source_chunk_id is None else str(link.source_chunk_id)
            ),
            "repository_component_id": (
                None if link.repository_component_id is None else str(link.repository_component_id)
            ),
            "relation": link.evidence_relation,
            "excerpt": link.evidence_excerpt,
            "confidence": None if link.confidence is None else str(link.confidence),
            "review_status": link.review_status,
            "lifecycle_status": link.lifecycle_status,
        }
        for link in sorted(links, key=lambda item: str(item.id))
    ]
    policy = DEFAULT_GROUNDING_POLICY.identity()
    evidence_set_fingerprint = _fingerprint({"policy": policy, "links": complete})
    accepted = tuple(
        link
        for link in links
        if link.review_status == "accepted" and link.lifecycle_status == "current"
    )
    supporting = sum(link.evidence_relation == "supports" for link in accepted)
    contradicting = sum(link.evidence_relation == "contradicts" for link in accepted)
    qualifying = sum(link.evidence_relation == "qualifies" for link in accepted)
    if supporting and contradicting:
        status = GroundingAssessmentStatus.MIXED
        rationale = "Accepted current evidence contains both support and contradiction."
    elif contradicting:
        status = GroundingAssessmentStatus.CONTRADICTED
        rationale = "Accepted current evidence contradicts the target without support."
    elif supporting and qualifying:
        status = GroundingAssessmentStatus.PARTIALLY_GROUNDED
        rationale = "Accepted current support is materially qualified."
    elif supporting:
        status = GroundingAssessmentStatus.GROUNDED
        rationale = "Accepted current evidence supports the target without qualification."
    else:
        status = GroundingAssessmentStatus.UNSUPPORTED
        rationale = (
            "Qualification alone does not establish affirmative support."
            if qualifying
            else "No accepted current evidence supports the target."
        )
    accepted_complete = [
        item
        for item in complete
        if item["review_status"] == "accepted" and item["lifecycle_status"] == "current"
    ]
    source_fingerprint = _fingerprint(
        [item for item in accepted_complete if item["source_chunk_id"] is not None]
    )
    repository_items = [
        item for item in accepted_complete if item["repository_component_id"] is not None
    ]
    return GroundingAssessmentSpecification(
        project_id=project_id,
        target_id=element_id,
        target_type=endpoint_type,
        target_version=element_version,
        evidence_set_fingerprint=evidence_set_fingerprint,
        source_evidence_fingerprint=source_fingerprint,
        repository_evidence_fingerprint=(
            None if not repository_items else _fingerprint(repository_items)
        ),
        status=status,
        supporting_count=supporting,
        contradicting_count=contradicting,
        qualifying_count=qualifying,
        rationale=rationale,
        validator_name=METHOD_GROUNDING_VALIDATOR_NAME,
        validator_version=METHOD_GROUNDING_VALIDATOR_VERSION,
        policy_version=DEFAULT_GROUNDING_POLICY.version,
    )


@dataclass(frozen=True, slots=True)
class InitialMethodMember:
    element_id: UUID
    required: bool
    provenance_complete: bool
    endpoint_type: EndpointType
    grounding: GroundingAssessmentSpecification
    element_version: int = 1


def assess_initial_method_model(
    *,
    method_model_id: UUID,
    composition_fingerprint: str,
    objective_context_fingerprint: str,
    members: tuple[InitialMethodMember, ...],
) -> MethodModelAssessmentSpecification:
    return MethodModelAssessmentPolicy.assess(
        method_model_id=method_model_id,
        composition_fingerprint=composition_fingerprint,
        members=tuple(
            MethodMemberGrounding(
                method_element_id=member.element_id,
                element_version=member.element_version,
                required=member.required,
                material=member.required,
                current=True,
                provenance_required=True,
                provenance_complete=member.provenance_complete,
                applicability_policy_version=METHOD_APPLICABILITY_POLICY_VERSION,
                objective_context_fingerprint=objective_context_fingerprint,
                assessment=member.grounding,
            )
            for member in members
        ),
        ambiguities=(),
    )
