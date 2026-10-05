"""Evidence-derived grounding and transparent MethodModel assessment policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from researchflow.domain.evidence import ValidatedEvidenceLink
from researchflow.domain.method_catalog import (
    EvidenceRelation,
    GroundingAssessmentStatus,
    MethodAssessmentOutcome,
)
from researchflow.domain.provenance import RepositoryComponentEvidenceProvenance
from researchflow.domain.relationships import EndpointType
from researchflow.errors import DomainValidationError

GROUNDING_POLICY_VERSION: Final = "rf-grounding-policy-v1"
METHOD_ASSESSMENT_POLICY_VERSION: Final = "rf-method-assessment-policy-v1"


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GroundingPolicyConfiguration:
    version: str = GROUNDING_POLICY_VERSION
    accepted_current_only: bool = True
    qualification_requires_support: bool = True
    any_support_and_contradiction_is_mixed: bool = True
    weighted_voting_enabled: bool = False

    def identity(self) -> dict[str, object]:
        if (
            not self.version.strip()
            or not self.accepted_current_only
            or not self.qualification_requires_support
            or not self.any_support_and_contradiction_is_mixed
            or self.weighted_voting_enabled
        ):
            raise DomainValidationError(details={"reason": "grounding_policy_invalid"})
        return {
            "version": self.version,
            "accepted_current_only": self.accepted_current_only,
            "qualification_requires_support": self.qualification_requires_support,
            "any_support_and_contradiction_is_mixed": (self.any_support_and_contradiction_is_mixed),
            "weighted_voting_enabled": self.weighted_voting_enabled,
        }


DEFAULT_GROUNDING_POLICY: Final = GroundingPolicyConfiguration()


@dataclass(frozen=True, slots=True)
class GroundingTarget:
    project_id: UUID
    target_id: UUID
    target_type: EndpointType
    target_version: int


@dataclass(frozen=True, slots=True)
class GroundingAssessmentSpecification:
    project_id: UUID
    target_id: UUID
    target_type: EndpointType
    target_version: int
    evidence_set_fingerprint: str
    source_evidence_fingerprint: str
    repository_evidence_fingerprint: str | None
    status: GroundingAssessmentStatus
    supporting_count: int
    contradicting_count: int
    qualifying_count: int
    rationale: str
    validator_name: str
    validator_version: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class GroundingCacheSnapshot:
    target_id: UUID
    target_version: int
    evidence_set_fingerprint: str
    validator_name: str
    validator_version: str
    policy_version: str


def _evidence_identity(links: tuple[ValidatedEvidenceLink, ...]) -> list[dict[str, str]]:
    return [
        {
            "link_id": str(link.id),
            "link_fingerprint": link.link_fingerprint,
            "review_status": link.review_status.value,
            "lifecycle_status": link.lifecycle_status.value,
            "source_current": str(link.provenance.current).lower(),
        }
        for link in sorted(links, key=lambda item: str(item.id))
    ]


class GroundingPolicy:
    """Derive status from the complete supplied validated evidence-link set."""

    @staticmethod
    def assess(
        *,
        target: GroundingTarget,
        evidence_links: tuple[ValidatedEvidenceLink, ...],
        validator_name: str,
        validator_version: str,
        configuration: GroundingPolicyConfiguration = DEFAULT_GROUNDING_POLICY,
    ) -> GroundingAssessmentSpecification:
        if target.target_version < 1:
            raise DomainValidationError(details={"reason": "grounding_target_version_invalid"})
        name = validator_name.strip()
        version = validator_version.strip()
        if not name or not version:
            raise DomainValidationError(details={"reason": "grounding_validator_identity_missing"})
        policy_identity = configuration.identity()
        link_ids = tuple(link.id for link in evidence_links)
        if len(link_ids) != len(set(link_ids)):
            raise DomainValidationError(details={"reason": "grounding_duplicate_evidence_link"})
        for link in evidence_links:
            if (
                link.project_id != target.project_id
                or link.target_id != target.target_id
                or link.target_type is not target.target_type
                or link.target_version != target.target_version
            ):
                raise DomainValidationError(
                    details={"reason": "grounding_evidence_target_mismatch"}
                )

        complete_identity = {
            "policy": policy_identity,
            "target_id": str(target.target_id),
            "target_type": target.target_type.value,
            "target_version": target.target_version,
            "validator_name": name,
            "validator_version": version,
            "evidence_links": _evidence_identity(evidence_links),
        }
        evidence_set_fingerprint = _fingerprint(complete_identity)
        accepted = tuple(link for link in evidence_links if link.accepted_current)
        supporting = sum(link.relation is EvidenceRelation.SUPPORTS for link in accepted)
        contradicting = sum(link.relation is EvidenceRelation.CONTRADICTS for link in accepted)
        qualifying = sum(link.relation is EvidenceRelation.QUALIFIES for link in accepted)

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

        source_links = tuple(
            link
            for link in accepted
            if not isinstance(link.provenance, RepositoryComponentEvidenceProvenance)
        )
        repository_links = tuple(
            link
            for link in accepted
            if isinstance(link.provenance, RepositoryComponentEvidenceProvenance)
        )
        return GroundingAssessmentSpecification(
            project_id=target.project_id,
            target_id=target.target_id,
            target_type=target.target_type,
            target_version=target.target_version,
            evidence_set_fingerprint=evidence_set_fingerprint,
            source_evidence_fingerprint=_fingerprint(_evidence_identity(source_links)),
            repository_evidence_fingerprint=(
                _fingerprint(_evidence_identity(repository_links)) if repository_links else None
            ),
            status=status,
            supporting_count=supporting,
            contradicting_count=contradicting,
            qualifying_count=qualifying,
            rationale=rationale,
            validator_name=name,
            validator_version=version,
            policy_version=configuration.version,
        )

    @staticmethod
    def cache_is_current(
        cache: GroundingCacheSnapshot | None,
        assessment: GroundingAssessmentSpecification,
    ) -> bool:
        return cache == GroundingCacheSnapshot(
            target_id=assessment.target_id,
            target_version=assessment.target_version,
            evidence_set_fingerprint=assessment.evidence_set_fingerprint,
            validator_name=assessment.validator_name,
            validator_version=assessment.validator_version,
            policy_version=assessment.policy_version,
        )


@dataclass(frozen=True, slots=True)
class MethodAssessmentPolicyConfiguration:
    version: str = METHOD_ASSESSMENT_POLICY_VERSION
    unsupported_required_tolerance: int = 0
    contradicted_required_tolerance: int = 0
    missing_required_provenance_tolerance: int = 0
    weighted_evidence_voting_enabled: bool = False

    def identity(self) -> dict[str, object]:
        if (
            not self.version.strip()
            or self.unsupported_required_tolerance != 0
            or self.contradicted_required_tolerance != 0
            or self.missing_required_provenance_tolerance != 0
            or self.weighted_evidence_voting_enabled
        ):
            raise DomainValidationError(details={"reason": "method_assessment_policy_invalid"})
        return {
            "version": self.version,
            "unsupported_required_tolerance": self.unsupported_required_tolerance,
            "contradicted_required_tolerance": self.contradicted_required_tolerance,
            "missing_required_provenance_tolerance": (self.missing_required_provenance_tolerance),
            "weighted_evidence_voting_enabled": self.weighted_evidence_voting_enabled,
        }


DEFAULT_METHOD_ASSESSMENT_POLICY: Final = MethodAssessmentPolicyConfiguration()


@dataclass(frozen=True, slots=True)
class MethodMemberGrounding:
    method_element_id: UUID
    element_version: int
    required: bool
    material: bool
    current: bool
    provenance_required: bool
    provenance_complete: bool
    applicability_policy_version: str
    objective_context_fingerprint: str
    assessment: GroundingAssessmentSpecification


@dataclass(frozen=True, slots=True)
class ScientificAmbiguityState:
    ambiguity_id: UUID
    current: bool
    material: bool
    resolved: bool
    applicability_policy_version: str
    objective_context_fingerprint: str


@dataclass(frozen=True, slots=True)
class MethodModelAssessmentSpecification:
    method_model_id: UUID
    composition_fingerprint: str
    member_grounding_fingerprint: str
    source_evidence_fingerprint: str
    repository_evidence_fingerprint: str | None
    unsupported_required_count: int
    mixed_evidence_count: int
    contradicted_count: int
    missing_provenance_count: int
    unresolved_scientific_ambiguity_count: int
    outcome: MethodAssessmentOutcome
    rationale: str
    policy_version: str


def _validate_sha256(value: str, *, reason: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise DomainValidationError(details={"reason": reason})


class MethodModelAssessmentPolicy:
    """Aggregate exact member grounding without manufacturing an opaque score."""

    @staticmethod
    def assess(
        *,
        method_model_id: UUID,
        composition_fingerprint: str,
        members: tuple[MethodMemberGrounding, ...],
        ambiguities: tuple[ScientificAmbiguityState, ...],
        configuration: MethodAssessmentPolicyConfiguration = DEFAULT_METHOD_ASSESSMENT_POLICY,
    ) -> MethodModelAssessmentSpecification:
        _validate_sha256(composition_fingerprint, reason="composition_fingerprint_invalid")
        policy_identity = configuration.identity()
        member_ids = tuple(member.method_element_id for member in members)
        if len(member_ids) != len(set(member_ids)):
            raise DomainValidationError(details={"reason": "method_assessment_duplicate_member"})
        ambiguity_ids = tuple(ambiguity.ambiguity_id for ambiguity in ambiguities)
        if len(ambiguity_ids) != len(set(ambiguity_ids)):
            raise DomainValidationError(details={"reason": "method_assessment_duplicate_ambiguity"})

        for member in members:
            if (
                member.element_version < 1
                or member.assessment.target_id != member.method_element_id
            ):
                raise DomainValidationError(details={"reason": "method_member_grounding_mismatch"})
            _validate_sha256(
                member.objective_context_fingerprint,
                reason="member_objective_context_fingerprint_invalid",
            )
            if not member.applicability_policy_version.strip():
                raise DomainValidationError(
                    details={"reason": "member_applicability_policy_missing"}
                )
        for ambiguity in ambiguities:
            _validate_sha256(
                ambiguity.objective_context_fingerprint,
                reason="ambiguity_objective_context_fingerprint_invalid",
            )
            if not ambiguity.applicability_policy_version.strip():
                raise DomainValidationError(
                    details={"reason": "ambiguity_applicability_policy_missing"}
                )

        current_members = tuple(member for member in members if member.current)
        unsupported_required = sum(
            member.required and member.assessment.status is GroundingAssessmentStatus.UNSUPPORTED
            for member in current_members
        )
        required_contradicted = sum(
            member.required and member.assessment.status is GroundingAssessmentStatus.CONTRADICTED
            for member in current_members
        )
        required_missing_provenance = sum(
            member.required and member.provenance_required and not member.provenance_complete
            for member in current_members
        )
        partially_grounded_required = sum(
            member.required
            and member.assessment.status is GroundingAssessmentStatus.PARTIALLY_GROUNDED
            for member in current_members
        )
        mixed = sum(
            member.assessment.status is GroundingAssessmentStatus.MIXED
            for member in current_members
        )
        contradicted = sum(
            member.assessment.status is GroundingAssessmentStatus.CONTRADICTED
            for member in current_members
        )
        missing_provenance = sum(
            member.provenance_required and not member.provenance_complete
            for member in current_members
        )
        unresolved_ambiguities = sum(
            ambiguity.current and ambiguity.material and not ambiguity.resolved
            for ambiguity in ambiguities
        )

        if unsupported_required or required_contradicted or required_missing_provenance:
            outcome = MethodAssessmentOutcome.INSUFFICIENT
        elif partially_grounded_required or mixed or unresolved_ambiguities:
            outcome = MethodAssessmentOutcome.SUFFICIENT_WITH_WARNINGS
        else:
            outcome = MethodAssessmentOutcome.SUFFICIENT

        member_payload = [
            {
                "method_element_id": str(member.method_element_id),
                "element_version": member.element_version,
                "required": member.required,
                "material": member.material,
                "current": member.current,
                "provenance_required": member.provenance_required,
                "provenance_complete": member.provenance_complete,
                "applicability_policy_version": member.applicability_policy_version,
                "objective_context_fingerprint": member.objective_context_fingerprint,
                "grounding_fingerprint": member.assessment.evidence_set_fingerprint,
                "grounding_status": member.assessment.status.value,
            }
            for member in sorted(members, key=lambda item: str(item.method_element_id))
        ]
        ambiguity_payload = [
            {
                "ambiguity_id": str(ambiguity.ambiguity_id),
                "current": ambiguity.current,
                "material": ambiguity.material,
                "resolved": ambiguity.resolved,
                "applicability_policy_version": ambiguity.applicability_policy_version,
                "objective_context_fingerprint": ambiguity.objective_context_fingerprint,
            }
            for ambiguity in sorted(ambiguities, key=lambda item: str(item.ambiguity_id))
        ]
        member_grounding_fingerprint = _fingerprint(
            {"policy": policy_identity, "members": member_payload, "ambiguities": ambiguity_payload}
        )
        source_fingerprints = sorted(
            member.assessment.source_evidence_fingerprint for member in current_members
        )
        repository_fingerprints = sorted(
            fingerprint
            for member in current_members
            if (fingerprint := member.assessment.repository_evidence_fingerprint) is not None
        )
        return MethodModelAssessmentSpecification(
            method_model_id=method_model_id,
            composition_fingerprint=composition_fingerprint,
            member_grounding_fingerprint=member_grounding_fingerprint,
            source_evidence_fingerprint=_fingerprint(source_fingerprints),
            repository_evidence_fingerprint=(
                _fingerprint(repository_fingerprints) if repository_fingerprints else None
            ),
            unsupported_required_count=unsupported_required,
            mixed_evidence_count=mixed,
            contradicted_count=contradicted,
            missing_provenance_count=missing_provenance,
            unresolved_scientific_ambiguity_count=unresolved_ambiguities,
            outcome=outcome,
            rationale=(
                f"{outcome.value}: required unsupported={unsupported_required}, "
                f"required contradicted={required_contradicted}, "
                f"required partially grounded={partially_grounded_required}, "
                f"mixed current={mixed}, missing provenance={missing_provenance}, "
                f"unresolved material ambiguities={unresolved_ambiguities}."
            ),
            policy_version=configuration.version,
        )
