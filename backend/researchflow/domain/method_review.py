"""Pure grouped MethodModel review and aggregate-acceptance policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from uuid import UUID

from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import (
    FreshnessStatus,
    MethodMemberRole,
    MethodReviewSection,
    SectionReviewOutcome,
)
from researchflow.errors import DomainValidationError, ResourceConflictError

_ROLES_BY_SECTION: dict[MethodReviewSection, frozenset[MethodMemberRole]] = {
    MethodReviewSection.OBJECTIVE_MECHANISM: frozenset(
        {MethodMemberRole.OBJECTIVE_CONTEXT, MethodMemberRole.MECHANISM}
    ),
    MethodReviewSection.EQUATIONS_SYMBOLS: frozenset(
        {MethodMemberRole.EQUATION, MethodMemberRole.SYMBOL_DEFINITION}
    ),
    MethodReviewSection.ALGORITHM_DATA_FLOW: frozenset(
        {MethodMemberRole.PROCEDURE, MethodMemberRole.DATA_FLOW}
    ),
    MethodReviewSection.VALIDITY_CONDITIONS: frozenset({MethodMemberRole.VALIDITY_CONDITION}),
}


def _required(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(details={"field": field, "reason": "blank"})
    return normalized


@dataclass(frozen=True, slots=True)
class MethodMemberSnapshot:
    method_element_id: UUID
    element_lineage_id: UUID
    element_version: int
    review_section: MethodReviewSection
    member_role: MethodMemberRole
    sequence_index: int | None
    required: bool
    inclusion_rationale: str
    review_status: KnowledgeReviewStatus
    lifecycle_status: KnowledgeLifecycleStatus
    freshness_status: FreshnessStatus = FreshnessStatus.CURRENT

    def validated(self) -> MethodMemberSnapshot:
        if self.element_version < 1:
            raise DomainValidationError(details={"reason": "element_version_invalid"})
        if self.sequence_index is not None and self.sequence_index < 0:
            raise DomainValidationError(details={"reason": "sequence_index_negative"})
        if self.member_role not in _ROLES_BY_SECTION[self.review_section]:
            raise DomainValidationError(details={"reason": "member_role_section_mismatch"})
        return replace(
            self,
            inclusion_rationale=_required(
                self.inclusion_rationale,
                field="inclusion_rationale",
            ),
        )


@dataclass(frozen=True, slots=True)
class MethodModelSemanticPayload:
    name: str
    objective: str
    members: tuple[MethodMemberSnapshot, ...]

    def validated(self) -> MethodModelSemanticPayload:
        members = _validate_members(self.members)
        return replace(
            self,
            name=_required(self.name, field="name"),
            objective=_required(self.objective, field="objective"),
            members=members,
        )


@dataclass(frozen=True, slots=True)
class SectionAcceptanceSpecification:
    review_section: MethodReviewSection
    outcome: SectionReviewOutcome
    member_set_fingerprint: str
    member_ids_to_accept: tuple[UUID, ...]
    rationale: str | None


@dataclass(frozen=True, slots=True)
class SectionReviewSnapshot:
    review_section: MethodReviewSection
    outcome: SectionReviewOutcome
    member_set_fingerprint: str
    rationale: str | None


@dataclass(frozen=True, slots=True)
class MethodModelAcceptanceResult:
    composition_fingerprint: str
    section_fingerprints: tuple[tuple[MethodReviewSection, str], ...]
    accepted: bool = True


def _validate_members(
    members: tuple[MethodMemberSnapshot, ...],
) -> tuple[MethodMemberSnapshot, ...]:
    validated = tuple(member.validated() for member in members)
    ids = tuple(member.method_element_id for member in validated)
    if len(ids) != len(set(ids)):
        raise DomainValidationError(details={"reason": "duplicate_method_member"})
    sequence_slots = tuple(
        (member.review_section, member.sequence_index)
        for member in validated
        if member.sequence_index is not None
    )
    if len(sequence_slots) != len(set(sequence_slots)):
        raise DomainValidationError(details={"reason": "duplicate_section_sequence"})
    return validated


def _fingerprint_payload(member: MethodMemberSnapshot) -> dict[str, object]:
    return {
        "element_id": str(member.method_element_id),
        "element_lineage_id": str(member.element_lineage_id),
        "element_version": member.element_version,
        "review_section": member.review_section.value,
        "member_role": member.member_role.value,
        "sequence_index": member.sequence_index,
        "required": member.required,
        "inclusion_rationale": member.inclusion_rationale,
    }


def _member_sort_key(member: MethodMemberSnapshot) -> tuple[str, bool, int, str]:
    return (
        member.review_section.value,
        member.sequence_index is None,
        -1 if member.sequence_index is None else member.sequence_index,
        str(member.method_element_id),
    )


def member_set_fingerprint(members: tuple[MethodMemberSnapshot, ...]) -> str:
    validated = _validate_members(members)
    payload = [_fingerprint_payload(member) for member in sorted(validated, key=_member_sort_key)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def method_composition_fingerprint(members: tuple[MethodMemberSnapshot, ...]) -> str:
    return member_set_fingerprint(members)


class MethodReviewPolicy:
    @staticmethod
    def prepare_section_review(
        *,
        review_section: MethodReviewSection,
        composition_members: tuple[MethodMemberSnapshot, ...],
        displayed_members: tuple[MethodMemberSnapshot, ...],
        outcome: SectionReviewOutcome,
        rationale: str | None,
    ) -> SectionAcceptanceSpecification:
        all_members = _validate_members(composition_members)
        displayed = _validate_members(displayed_members)
        expected = tuple(
            member for member in all_members if member.review_section is review_section
        )
        if any(member.review_section is not review_section for member in displayed):
            raise DomainValidationError(details={"reason": "displayed_section_mismatch"})
        if member_set_fingerprint(expected) != member_set_fingerprint(displayed):
            raise ResourceConflictError(details={"reason": "section_display_incomplete"})

        normalized_rationale = None if rationale is None else rationale.strip() or None
        if outcome is SectionReviewOutcome.NOT_APPLICABLE:
            if expected:
                raise ResourceConflictError(details={"reason": "section_members_not_resolved"})
            if normalized_rationale is None:
                raise DomainValidationError(
                    details={"field": "rationale", "reason": "not_applicable_requires_rationale"}
                )
            return SectionAcceptanceSpecification(
                review_section=review_section,
                outcome=outcome,
                member_set_fingerprint=member_set_fingerprint(expected),
                member_ids_to_accept=(),
                rationale=normalized_rationale,
            )

        if not expected:
            raise ResourceConflictError(details={"reason": "accepted_section_empty"})
        for member in expected:
            if member.lifecycle_status is not KnowledgeLifecycleStatus.CURRENT:
                raise ResourceConflictError(details={"reason": "section_member_not_current"})
            if member.freshness_status is not FreshnessStatus.CURRENT:
                raise ResourceConflictError(details={"reason": "section_member_not_fresh"})
            if member.review_status not in {
                KnowledgeReviewStatus.CANDIDATE,
                KnowledgeReviewStatus.ACCEPTED,
            }:
                raise ResourceConflictError(details={"reason": "section_member_unresolved"})
        return SectionAcceptanceSpecification(
            review_section=review_section,
            outcome=outcome,
            member_set_fingerprint=member_set_fingerprint(expected),
            member_ids_to_accept=tuple(
                member.method_element_id
                for member in expected
                if member.review_status is KnowledgeReviewStatus.CANDIDATE
            ),
            rationale=normalized_rationale,
        )

    @staticmethod
    def validate_model_acceptance(
        *,
        composition_members: tuple[MethodMemberSnapshot, ...],
        current_section_reviews: tuple[SectionReviewSnapshot, ...],
    ) -> MethodModelAcceptanceResult:
        members = _validate_members(composition_members)
        reviews = {review.review_section: review for review in current_section_reviews}
        if len(reviews) != len(current_section_reviews):
            raise DomainValidationError(details={"reason": "duplicate_section_review"})
        if set(reviews) != set(MethodReviewSection):
            raise ResourceConflictError(details={"reason": "required_section_review_missing"})

        section_fingerprints: list[tuple[MethodReviewSection, str]] = []
        for section in MethodReviewSection:
            section_members = tuple(
                member for member in members if member.review_section is section
            )
            review = reviews[section]
            fingerprint = member_set_fingerprint(section_members)
            if review.member_set_fingerprint != fingerprint:
                raise ResourceConflictError(details={"reason": "section_fingerprint_stale"})
            if review.outcome is SectionReviewOutcome.NOT_APPLICABLE:
                if section_members or review.rationale is None or not review.rationale.strip():
                    raise ResourceConflictError(
                        details={"reason": "section_not_applicable_invalid"}
                    )
            else:
                if not section_members:
                    raise ResourceConflictError(details={"reason": "accepted_section_empty"})
                if any(
                    member.review_status is not KnowledgeReviewStatus.ACCEPTED
                    or member.lifecycle_status is not KnowledgeLifecycleStatus.CURRENT
                    or member.freshness_status is not FreshnessStatus.CURRENT
                    for member in section_members
                ):
                    raise ResourceConflictError(
                        details={"reason": "model_member_not_accepted_current"}
                    )
            section_fingerprints.append((section, fingerprint))

        return MethodModelAcceptanceResult(
            composition_fingerprint=method_composition_fingerprint(members),
            section_fingerprints=tuple(section_fingerprints),
        )
