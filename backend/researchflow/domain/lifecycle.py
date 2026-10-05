"""Framework-independent lifecycle, edit, and immutable-version policies."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import FreshnessStatus
from researchflow.errors import DomainValidationError, ResourceConflictError


class CandidateCommand(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUEST_REVISION = "request_revision"
    CHALLENGE = "challenge"
    REAFFIRM = "reaffirm"
    WITHDRAW = "withdraw"


class TransitionDisposition(StrEnum):
    APPLIED = "applied"
    NO_OP = "no_op"


class LifecycleReasonCode(StrEnum):
    CANDIDATE_ACCEPTED = "candidate_accepted"
    CANDIDATE_REJECTED = "candidate_rejected"
    REVISION_REQUESTED = "revision_requested"
    ACCEPTED_OBJECT_CHALLENGED = "accepted_object_challenged"
    CHALLENGED_OBJECT_REAFFIRMED = "challenged_object_reaffirmed"
    ACCEPTED_OBJECT_WITHDRAWN = "accepted_object_withdrawn"
    EXACT_COMMAND_REPLAY = "exact_command_replay"
    REVISION_ACCEPTED = "revision_accepted"


class CanonicalArtifactFamily(StrEnum):
    RESEARCH_QUESTION = "research_question"
    KNOWLEDGE_OBJECT = "knowledge_object"
    METHOD_ELEMENT = "method_element"
    METHOD_MODEL = "method_model"
    PAPER_CODE_MAPPING = "paper_code_mapping"


class EditClassification(StrEnum):
    UNCHANGED = "unchanged"
    SEMANTIC = "semantic"
    PRESENTATION_ONLY = "presentation_only"
    EVIDENCE_ONLY = "evidence_only"


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    review_status: KnowledgeReviewStatus
    lifecycle_status: KnowledgeLifecycleStatus
    freshness_status: FreshnessStatus | None = None
    derived_from_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError(details={"reason": "version_invalid"})
        _validate_review_lifecycle_pair(self.review_status, self.lifecycle_status)


@dataclass(frozen=True, slots=True)
class AppliedCommandReceipt:
    command_id: UUID
    target_id: UUID
    target_version: int
    command: CandidateCommand


@dataclass(frozen=True, slots=True)
class CandidateTransitionResult:
    command_id: UUID
    target_id: UUID
    target_version: int
    command: CandidateCommand
    disposition: TransitionDisposition
    reason_code: LifecycleReasonCode
    prior_review_status: KnowledgeReviewStatus
    prior_lifecycle_status: KnowledgeLifecycleStatus
    next_review_status: KnowledgeReviewStatus
    next_lifecycle_status: KnowledgeLifecycleStatus


@dataclass(frozen=True, slots=True)
class VersionSnapshot:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError(details={"reason": "version_invalid"})


@dataclass(frozen=True, slots=True)
class NewVersionSpecification:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    derived_from_id: UUID


@dataclass(frozen=True, slots=True)
class RevisionAcceptanceResult:
    command_id: UUID
    disposition: TransitionDisposition
    reason_code: LifecycleReasonCode
    previous_id: UUID
    previous_version: int
    previous_next_lifecycle_status: KnowledgeLifecycleStatus
    replacement_id: UUID
    replacement_version: int
    replacement_next_review_status: KnowledgeReviewStatus
    replacement_next_lifecycle_status: KnowledgeLifecycleStatus
    replacement_supersedes_id: UUID


def _validate_review_lifecycle_pair(
    review_status: KnowledgeReviewStatus,
    lifecycle_status: KnowledgeLifecycleStatus,
) -> None:
    if review_status is KnowledgeReviewStatus.ACCEPTED:
        if lifecycle_status not in {
            KnowledgeLifecycleStatus.CURRENT,
            KnowledgeLifecycleStatus.CHALLENGED,
            KnowledgeLifecycleStatus.SUPERSEDED,
        }:
            raise DomainValidationError(details={"reason": "review_lifecycle_pair_invalid"})
        return
    if lifecycle_status is not KnowledgeLifecycleStatus.CURRENT:
        raise DomainValidationError(details={"reason": "review_lifecycle_pair_invalid"})


def is_accepted_current(snapshot: CandidateSnapshot) -> bool:
    """Return the accepted-only synthesis predicate; challenged is excluded."""
    return (
        snapshot.review_status is KnowledgeReviewStatus.ACCEPTED
        and snapshot.lifecycle_status is KnowledgeLifecycleStatus.CURRENT
        and snapshot.freshness_status in {None, FreshnessStatus.CURRENT}
    )


def accepted_current(
    snapshots: Iterable[CandidateSnapshot],
) -> tuple[CandidateSnapshot, ...]:
    return tuple(snapshot for snapshot in snapshots if is_accepted_current(snapshot))


class CandidateLifecyclePolicy:
    """Validate review/lifecycle transitions without persistence side effects."""

    _TRANSITIONS: ClassVar[
        dict[
            tuple[KnowledgeReviewStatus, KnowledgeLifecycleStatus, CandidateCommand],
            tuple[KnowledgeReviewStatus, KnowledgeLifecycleStatus, LifecycleReasonCode],
        ]
    ] = {
        (
            KnowledgeReviewStatus.CANDIDATE,
            KnowledgeLifecycleStatus.CURRENT,
            CandidateCommand.ACCEPT,
        ): (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CURRENT,
            LifecycleReasonCode.CANDIDATE_ACCEPTED,
        ),
        (
            KnowledgeReviewStatus.CANDIDATE,
            KnowledgeLifecycleStatus.CURRENT,
            CandidateCommand.REJECT,
        ): (
            KnowledgeReviewStatus.REJECTED,
            KnowledgeLifecycleStatus.CURRENT,
            LifecycleReasonCode.CANDIDATE_REJECTED,
        ),
        (
            KnowledgeReviewStatus.NEEDS_REVISION,
            KnowledgeLifecycleStatus.CURRENT,
            CandidateCommand.REJECT,
        ): (
            KnowledgeReviewStatus.REJECTED,
            KnowledgeLifecycleStatus.CURRENT,
            LifecycleReasonCode.CANDIDATE_REJECTED,
        ),
        (
            KnowledgeReviewStatus.CANDIDATE,
            KnowledgeLifecycleStatus.CURRENT,
            CandidateCommand.REQUEST_REVISION,
        ): (
            KnowledgeReviewStatus.NEEDS_REVISION,
            KnowledgeLifecycleStatus.CURRENT,
            LifecycleReasonCode.REVISION_REQUESTED,
        ),
        (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CURRENT,
            CandidateCommand.CHALLENGE,
        ): (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CHALLENGED,
            LifecycleReasonCode.ACCEPTED_OBJECT_CHALLENGED,
        ),
        (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CHALLENGED,
            CandidateCommand.REAFFIRM,
        ): (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CURRENT,
            LifecycleReasonCode.CHALLENGED_OBJECT_REAFFIRMED,
        ),
        (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CURRENT,
            CandidateCommand.WITHDRAW,
        ): (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.SUPERSEDED,
            LifecycleReasonCode.ACCEPTED_OBJECT_WITHDRAWN,
        ),
        (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.CHALLENGED,
            CandidateCommand.WITHDRAW,
        ): (
            KnowledgeReviewStatus.ACCEPTED,
            KnowledgeLifecycleStatus.SUPERSEDED,
            LifecycleReasonCode.ACCEPTED_OBJECT_WITHDRAWN,
        ),
    }

    @classmethod
    def transition(
        cls,
        snapshot: CandidateSnapshot,
        command: CandidateCommand,
        *,
        command_id: UUID,
        replay: AppliedCommandReceipt | None = None,
    ) -> CandidateTransitionResult:
        if replay is not None:
            if replay != AppliedCommandReceipt(
                command_id=command_id,
                target_id=snapshot.id,
                target_version=snapshot.version,
                command=command,
            ):
                raise ResourceConflictError(details={"reason": "command_replay_conflict"})
            return CandidateTransitionResult(
                command_id=command_id,
                target_id=snapshot.id,
                target_version=snapshot.version,
                command=command,
                disposition=TransitionDisposition.NO_OP,
                reason_code=LifecycleReasonCode.EXACT_COMMAND_REPLAY,
                prior_review_status=snapshot.review_status,
                prior_lifecycle_status=snapshot.lifecycle_status,
                next_review_status=snapshot.review_status,
                next_lifecycle_status=snapshot.lifecycle_status,
            )

        if command is CandidateCommand.ACCEPT and snapshot.freshness_status not in {
            None,
            FreshnessStatus.CURRENT,
        }:
            raise ResourceConflictError(details={"reason": "stale_candidate_not_acceptable"})

        target = cls._TRANSITIONS.get((snapshot.review_status, snapshot.lifecycle_status, command))
        if target is None:
            raise ResourceConflictError(details={"reason": "candidate_transition_invalid"})
        next_review, next_lifecycle, reason = target
        return CandidateTransitionResult(
            command_id=command_id,
            target_id=snapshot.id,
            target_version=snapshot.version,
            command=command,
            disposition=TransitionDisposition.APPLIED,
            reason_code=reason,
            prior_review_status=snapshot.review_status,
            prior_lifecycle_status=snapshot.lifecycle_status,
            next_review_status=next_review,
            next_lifecycle_status=next_lifecycle,
        )


def new_version_specification(
    predecessor: VersionSnapshot,
    *,
    new_id: UUID,
    project_id: UUID,
    lineage_id: UUID,
    version: int,
    latest_lineage_version: int | None = None,
) -> NewVersionSpecification:
    if new_id == predecessor.id:
        raise DomainValidationError(details={"reason": "version_identity_reused"})
    if project_id != predecessor.project_id:
        raise DomainValidationError(details={"reason": "version_project_mismatch"})
    if lineage_id != predecessor.lineage_id:
        raise DomainValidationError(details={"reason": "version_lineage_mismatch"})
    latest_version = (
        predecessor.version if latest_lineage_version is None else latest_lineage_version
    )
    if latest_version < predecessor.version:
        raise DomainValidationError(details={"reason": "lineage_version_invalid"})
    if version != latest_version + 1:
        raise DomainValidationError(details={"reason": "version_gap"})
    return NewVersionSpecification(
        id=new_id,
        project_id=project_id,
        lineage_id=lineage_id,
        version=version,
        derived_from_id=predecessor.id,
    )


def accept_revision(
    current: CandidateSnapshot,
    replacement: CandidateSnapshot,
    *,
    command_id: UUID,
    lineage: Iterable[CandidateSnapshot],
) -> RevisionAcceptanceResult:
    if current.project_id != replacement.project_id:
        raise DomainValidationError(details={"reason": "revision_project_mismatch"})
    if current.lineage_id != replacement.lineage_id:
        raise DomainValidationError(details={"reason": "revision_lineage_mismatch"})
    if replacement.version <= current.version:
        raise DomainValidationError(details={"reason": "revision_version_order_invalid"})
    if (
        current.review_status is not KnowledgeReviewStatus.ACCEPTED
        or current.lifecycle_status is not KnowledgeLifecycleStatus.CURRENT
    ):
        raise ResourceConflictError(details={"reason": "current_accepted_version_required"})
    if (
        replacement.review_status is not KnowledgeReviewStatus.CANDIDATE
        or replacement.lifecycle_status is not KnowledgeLifecycleStatus.CURRENT
    ):
        raise ResourceConflictError(details={"reason": "replacement_candidate_invalid"})
    _require_derivation_ancestry(current, replacement, lineage)
    if replacement.freshness_status not in {None, FreshnessStatus.CURRENT}:
        raise ResourceConflictError(details={"reason": "stale_candidate_not_acceptable"})
    return RevisionAcceptanceResult(
        command_id=command_id,
        disposition=TransitionDisposition.APPLIED,
        reason_code=LifecycleReasonCode.REVISION_ACCEPTED,
        previous_id=current.id,
        previous_version=current.version,
        previous_next_lifecycle_status=KnowledgeLifecycleStatus.SUPERSEDED,
        replacement_id=replacement.id,
        replacement_version=replacement.version,
        replacement_next_review_status=KnowledgeReviewStatus.ACCEPTED,
        replacement_next_lifecycle_status=KnowledgeLifecycleStatus.CURRENT,
        replacement_supersedes_id=current.id,
    )


def _require_derivation_ancestry(
    current: CandidateSnapshot,
    replacement: CandidateSnapshot,
    lineage: Iterable[CandidateSnapshot],
) -> None:
    by_id: dict[UUID, CandidateSnapshot] = {}
    for snapshot in lineage:
        if snapshot.project_id != current.project_id:
            raise DomainValidationError(details={"reason": "revision_project_mismatch"})
        if snapshot.lineage_id != current.lineage_id:
            raise DomainValidationError(details={"reason": "revision_lineage_mismatch"})
        if snapshot.id in by_id:
            raise DomainValidationError(details={"reason": "revision_lineage_duplicate"})
        by_id[snapshot.id] = snapshot
    if current.id not in by_id or replacement.id not in by_id:
        raise ResourceConflictError(details={"reason": "revision_ancestry_incomplete"})

    seen: set[UUID] = set()
    cursor = replacement
    while cursor.id != current.id:
        if cursor.id in seen:
            raise DomainValidationError(details={"reason": "revision_ancestry_cycle"})
        seen.add(cursor.id)
        if cursor.derived_from_id is None:
            raise ResourceConflictError(details={"reason": "replacement_ancestry_invalid"})
        parent = by_id.get(cursor.derived_from_id)
        if parent is None:
            raise ResourceConflictError(details={"reason": "revision_ancestry_incomplete"})
        if parent.version >= cursor.version:
            raise DomainValidationError(details={"reason": "revision_ancestry_order_invalid"})
        cursor = parent


class SemanticEditPolicy:
    """Classify exact canonical changes; uncertainty defaults to semantic."""

    @staticmethod
    def classify(
        *,
        family: CanonicalArtifactFamily,
        current_payload: object,
        proposed_payload: object,
        presentation_changed: bool = False,
        evidence_changed: bool = False,
    ) -> EditClassification:
        if current_payload != proposed_payload:
            return EditClassification.SEMANTIC
        if presentation_changed and evidence_changed:
            raise DomainValidationError(details={"reason": "edit_kind_ambiguous"})
        if presentation_changed:
            if family is not CanonicalArtifactFamily.KNOWLEDGE_OBJECT:
                raise DomainValidationError(
                    details={"reason": "persisted_presentation_not_supported"}
                )
            return EditClassification.PRESENTATION_ONLY
        if evidence_changed:
            return EditClassification.EVIDENCE_ONLY
        return EditClassification.UNCHANGED

    @staticmethod
    def require_semantic_revision(classification: EditClassification) -> None:
        if classification is not EditClassification.SEMANTIC:
            raise ResourceConflictError(details={"reason": "semantic_payload_unchanged"})
