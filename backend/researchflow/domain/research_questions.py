"""Research Question validation and inquiry-transition policy."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from researchflow.domain.lifecycle import (
    CanonicalArtifactFamily,
    EditClassification,
    SemanticEditPolicy,
    VersionSnapshot,
    new_version_specification,
)
from researchflow.errors import DomainValidationError, ResourceConflictError

QUESTION_TEXT_MAX: Final = 8_000
QUESTION_SCOPE_MAX: Final = 8_000


class QuestionType(StrEnum):
    PRIMARY = "primary"
    SUBQUESTION = "subquestion"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


class InquiryStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_ANSWERED = "partially_answered"
    ANSWERED = "answered"


class QuestionPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class QuestionLifecycleStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class InquiryTransitionKind(StrEnum):
    STATUS_CHANGED = "status_changed"
    REOPENED = "reopened"


@dataclass(frozen=True, slots=True)
class QuestionDefinition:
    question_text: str
    scope_text: str | None

    @classmethod
    def validate(cls, *, question_text: str, scope_text: str | None) -> "QuestionDefinition":
        normalized_question = question_text.strip()
        if not normalized_question or len(normalized_question) > QUESTION_TEXT_MAX:
            raise DomainValidationError(
                details={"field": "question_text", "reason": "invalid_length"}
            )

        normalized_scope = None if scope_text is None else scope_text.strip() or None
        if normalized_scope is not None and len(normalized_scope) > QUESTION_SCOPE_MAX:
            raise DomainValidationError(details={"field": "scope_text", "reason": "invalid_length"})
        return cls(question_text=normalized_question, scope_text=normalized_scope)


@dataclass(frozen=True, slots=True)
class InquiryTransitionResult:
    prior_status: InquiryStatus
    next_status: InquiryStatus
    transition_kind: InquiryTransitionKind


@dataclass(frozen=True, slots=True)
class QuestionSnapshot:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    definition: QuestionDefinition
    question_type: QuestionType
    inquiry_status: InquiryStatus
    lifecycle_status: QuestionLifecycleStatus
    parent_question_id: UUID | None
    priority: QuestionPriority

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError(details={"reason": "version_invalid"})


@dataclass(frozen=True, slots=True)
class QuestionRefinementSpecification:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    definition: QuestionDefinition
    question_type: QuestionType
    inquiry_status: InquiryStatus
    lifecycle_status: QuestionLifecycleStatus
    parent_question_id: UUID | None
    priority: QuestionPriority
    derived_from_id: UUID
    supersedes_id: UUID
    predecessor_next_lifecycle_status: QuestionLifecycleStatus


def validate_status_change(*, current: InquiryStatus, target: InquiryStatus) -> None:
    if current is target:
        raise ResourceConflictError(details={"reason": "inquiry_status_unchanged"})
    if current is InquiryStatus.ANSWERED:
        raise ResourceConflictError(details={"reason": "explicit_reopen_required"})


def validate_reopen(*, current: InquiryStatus, target: InquiryStatus) -> None:
    if current is not InquiryStatus.ANSWERED:
        raise ResourceConflictError(details={"reason": "question_not_answered"})
    if target not in {InquiryStatus.OPEN, InquiryStatus.PARTIALLY_ANSWERED}:
        raise DomainValidationError(details={"field": "target_status", "reason": "invalid_reopen"})


class InquiryLifecyclePolicy:
    @staticmethod
    def change_status(*, current: InquiryStatus, target: InquiryStatus) -> InquiryTransitionResult:
        validate_status_change(current=current, target=target)
        return InquiryTransitionResult(
            prior_status=current,
            next_status=target,
            transition_kind=InquiryTransitionKind.STATUS_CHANGED,
        )

    @staticmethod
    def reopen(*, current: InquiryStatus, target: InquiryStatus) -> InquiryTransitionResult:
        validate_reopen(current=current, target=target)
        return InquiryTransitionResult(
            prior_status=current,
            next_status=target,
            transition_kind=InquiryTransitionKind.REOPENED,
        )


def refine_question(
    current: QuestionSnapshot,
    *,
    new_id: UUID,
    project_id: UUID,
    lineage_id: UUID,
    version: int,
    question_text: str,
    scope_text: str | None,
) -> QuestionRefinementSpecification:
    if current.lifecycle_status is not QuestionLifecycleStatus.CURRENT:
        raise ResourceConflictError(details={"reason": "current_question_required"})
    proposed = QuestionDefinition.validate(
        question_text=question_text,
        scope_text=scope_text,
    )
    classification = SemanticEditPolicy.classify(
        family=CanonicalArtifactFamily.RESEARCH_QUESTION,
        current_payload=current.definition,
        proposed_payload=proposed,
    )
    SemanticEditPolicy.require_semantic_revision(classification)
    assert classification is EditClassification.SEMANTIC
    identity = new_version_specification(
        VersionSnapshot(
            id=current.id,
            project_id=current.project_id,
            lineage_id=current.lineage_id,
            version=current.version,
        ),
        new_id=new_id,
        project_id=project_id,
        lineage_id=lineage_id,
        version=version,
    )
    return QuestionRefinementSpecification(
        id=identity.id,
        project_id=identity.project_id,
        lineage_id=identity.lineage_id,
        version=identity.version,
        definition=proposed,
        question_type=current.question_type,
        inquiry_status=current.inquiry_status,
        lifecycle_status=QuestionLifecycleStatus.CURRENT,
        parent_question_id=current.parent_question_id,
        priority=current.priority,
        derived_from_id=current.id,
        supersedes_id=current.id,
        predecessor_next_lifecycle_status=QuestionLifecycleStatus.SUPERSEDED,
    )
