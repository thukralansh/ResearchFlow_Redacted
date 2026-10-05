"""Persistence-facing grounding over the complete reviewed evidence set."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from researchflow.domain.grounding import DEFAULT_GROUNDING_POLICY
from researchflow.domain.method_catalog import GroundingAssessmentStatus
from researchflow.ports.repositories import EvidenceLinkData

GROUNDING_VALIDATOR_NAME: Final = "researchflow-grounding-policy"
GROUNDING_VALIDATOR_VERSION: Final = "rf-grounding-policy-v1"


@dataclass(frozen=True, slots=True)
class PersistedGroundingSpecification:
    evidence_set_fingerprint: str
    status: GroundingAssessmentStatus
    supporting_count: int
    contradicting_count: int
    qualifying_count: int
    rationale: str
    validator_name: str = GROUNDING_VALIDATOR_NAME
    validator_version: str = GROUNDING_VALIDATOR_VERSION


def assess_persisted_evidence(
    evidence_links: tuple[EvidenceLinkData, ...],
) -> PersistedGroundingSpecification:
    """Apply S16 accepted/current aggregation without trusting candidate links."""
    policy = DEFAULT_GROUNDING_POLICY.identity()
    complete = sorted(
        (
            {
                "id": str(link.id),
                "lineage_id": str(link.lineage_id),
                "version": link.version,
                "source_chunk_id": (
                    None if link.source_chunk_id is None else str(link.source_chunk_id)
                ),
                "repository_component_id": (
                    None
                    if link.repository_component_id is None
                    else str(link.repository_component_id)
                ),
                "relation": link.evidence_relation,
                "excerpt": link.evidence_excerpt,
                "confidence": None if link.confidence is None else str(link.confidence),
                "review_status": link.review_status,
                "lifecycle_status": link.lifecycle_status,
            }
            for link in evidence_links
        ),
        key=lambda item: str(item["id"]),
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {"policy": policy, "evidence_links": complete},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    accepted = tuple(
        link
        for link in evidence_links
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
    return PersistedGroundingSpecification(
        evidence_set_fingerprint=fingerprint,
        status=status,
        supporting_count=supporting,
        contradicting_count=contradicting,
        qualifying_count=qualifying,
        rationale=rationale,
    )
