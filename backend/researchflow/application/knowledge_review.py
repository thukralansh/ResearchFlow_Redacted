"""Immutable proposition review commands and accepted-only lifecycle boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Final, Protocol
from uuid import UUID, uuid4

from researchflow.application.grounding import assess_persisted_evidence
from researchflow.domain.actor import ActorContext
from researchflow.domain.knowledge import (
    AmbiguityPayload,
    KnowledgeObjectPayload,
    KnowledgePresentationSnapshot,
    knowledge_presentation_revision,
)
from researchflow.domain.knowledge_catalog import (
    STATEMENT_NORMALIZATION_VERSION,
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
    statement_fingerprint,
)
from researchflow.domain.lifecycle import (
    CandidateCommand,
    CandidateLifecyclePolicy,
    CandidateSnapshot,
    CanonicalArtifactFamily,
    SemanticEditPolicy,
    VersionSnapshot,
    accept_revision,
    new_version_specification,
)
from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.repositories import (
    EvidenceLinkData,
    GroundingAssessmentData,
    KnowledgeObjectData,
    KnowledgeObjectPayloadData,
    KnowledgePresentationData,
    NewEvidenceLink,
    NewGroundingAssessment,
    NewKnowledgePresentation,
    NewKnowledgeRevision,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

RATIONALE_MAX: Final = 4_000
REVIEW_POLICY_VERSION: Final = "rf-knowledge-review-v1"
EVIDENCE_REPROPOSAL_POLICY_VERSION: Final = "rf-knowledge-evidence-reproposal-v1"
_REPROPOSABLE_EVIDENCE_REVIEW_STATUSES: Final = frozenset(
    {
        KnowledgeReviewStatus.CANDIDATE.value,
        KnowledgeReviewStatus.NEEDS_REVISION.value,
        KnowledgeReviewStatus.ACCEPTED.value,
    }
)


class DirectStalenessHook(Protocol):
    """ seam; full dependency propagation is deferred to ."""

    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork,
        project_id: UUID,
        cause_type: str,
        before_id: UUID,
        after_id: UUID,
        operation_type: str,
    ) -> None: ...


class NoOpDirectStalenessHook:
    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork | None = None,
        project_id: UUID,
        cause_type: str = "knowledge_object",
        lineage_id: UUID | None = None,
        before_id: UUID,
        after_id: UUID,
        operation_type: str,
    ) -> None:
        del uow, project_id, cause_type, lineage_id, before_id, after_id, operation_type


@dataclass(frozen=True, slots=True)
class ReviewTransitionCommand:
    project_id: UUID
    object_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticRevisionCommand:
    project_id: UUID
    object_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    payload: KnowledgeObjectPayload
    reproposed_evidence_link_ids: tuple[UUID, ...] = ()
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PresentationRevisionCommand:
    project_id: UUID
    object_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    display_text: str
    display_label: str | None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptRevisionCommand:
    project_id: UUID
    predecessor_id: UUID
    replacement_id: UUID
    command_id: UUID
    expected_predecessor_version: int
    expected_predecessor_lifecycle_status: KnowledgeLifecycleStatus
    expected_replacement_version: int
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceReviewCommand:
    project_id: UUID
    knowledge_object_id: UUID
    evidence_link_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewCommandResult:
    object: KnowledgeObjectData
    predecessor: KnowledgeObjectData | None
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class PresentationCommandResult:
    object: KnowledgeObjectData
    presentation: KnowledgePresentationData
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class EvidenceReviewResult:
    evidence: EvidenceLinkData
    object: KnowledgeObjectData
    grounding: GroundingAssessmentData
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class KnowledgeReviewDetail:
    object: KnowledgeObjectData
    payload: KnowledgeObjectPayloadData
    evidence: tuple[EvidenceLinkData, ...]
    presentations: tuple[KnowledgePresentationData, ...]
    lineage: tuple[KnowledgeObjectData, ...]
    history: tuple[OperationEventData, ...]


class KnowledgeReviewService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        staleness_hook: DirectStalenessHook | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._staleness_hook = staleness_hook or NoOpDirectStalenessHook()

    async def list_items(
        self,
        project_id: UUID,
        *,
        review_status: KnowledgeReviewStatus | None = None,
        object_type: str | None = None,
    ) -> tuple[KnowledgeObjectData, ...]:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            values = await uow.knowledge.list_review_items(
                review_status=None if review_status is None else review_status.value,
                object_type=object_type,
            )
            await uow.rollback()
            return values

    async def list_accepted_current(self, project_id: UUID) -> tuple[KnowledgeObjectData, ...]:
        """Return the server-authoritative accepted-only synthesis view."""
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            values = await uow.knowledge.list_accepted_current()
            await uow.rollback()
            return values

    async def detail(self, project_id: UUID, object_id: UUID) -> KnowledgeReviewDetail:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            item = await uow.knowledge.get_exact(object_id)
            if item is None:
                raise ResourceNotFoundError(details={"resource": "knowledge_object"})
            payload = await uow.knowledge.get_payload(object_id)
            if payload is None:
                raise InternalError(details={"reason": "knowledge_subtype_missing"})
            detail = KnowledgeReviewDetail(
                object=item,
                payload=payload,
                evidence=await uow.knowledge.list_evidence_links(object_id),
                presentations=await uow.knowledge.list_presentations(object_id),
                lineage=await uow.knowledge.list_lineage(item.lineage_id),
                history=await uow.events.list_for_knowledge_lineage(item.lineage_id),
            )
            await uow.rollback()
            return detail

    async def accept(self, command: ReviewTransitionCommand) -> ReviewCommandResult:
        return await self._transition(command, CandidateCommand.ACCEPT)

    async def reject(self, command: ReviewTransitionCommand) -> ReviewCommandResult:
        return await self._transition(command, CandidateCommand.REJECT)

    async def request_revision(self, command: ReviewTransitionCommand) -> ReviewCommandResult:
        return await self._transition(command, CandidateCommand.REQUEST_REVISION)

    async def challenge(self, command: ReviewTransitionCommand) -> ReviewCommandResult:
        return await self._transition(command, CandidateCommand.CHALLENGE)

    async def reaffirm(self, command: ReviewTransitionCommand) -> ReviewCommandResult:
        return await self._transition(command, CandidateCommand.REAFFIRM)

    async def accept_evidence(self, command: EvidenceReviewCommand) -> EvidenceReviewResult:
        return await self._evidence_transition(command, CandidateCommand.ACCEPT)

    async def reject_evidence(self, command: EvidenceReviewCommand) -> EvidenceReviewResult:
        return await self._evidence_transition(command, CandidateCommand.REJECT)

    async def request_evidence_revision(
        self, command: EvidenceReviewCommand
    ) -> EvidenceReviewResult:
        return await self._evidence_transition(command, CandidateCommand.REQUEST_REVISION)

    async def challenge_evidence(self, command: EvidenceReviewCommand) -> EvidenceReviewResult:
        return await self._evidence_transition(command, CandidateCommand.CHALLENGE)

    async def reaffirm_evidence(self, command: EvidenceReviewCommand) -> EvidenceReviewResult:
        return await self._evidence_transition(command, CandidateCommand.REAFFIRM)

    async def _evidence_transition(
        self,
        command: EvidenceReviewCommand,
        action: CandidateCommand,
    ) -> EvidenceReviewResult:
        rationale = _rationale(command.rationale)
        operation_type = f"evidence_link.{action.value}"
        identity = {
            "policy_version": REVIEW_POLICY_VERSION,
            "knowledge_object_id": str(command.knowledge_object_id),
            "evidence_link_id": str(command.evidence_link_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            existing_event = await uow.events.get_by_idempotency_key(
                operation_type, str(command.command_id)
            )
            if existing_event is not None:
                if existing_event.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                link = await uow.knowledge.get_evidence_link_for_update(command.evidence_link_id)
                target = await uow.knowledge.get_exact(command.knowledge_object_id)
                if link is None or target is None:
                    raise InternalError(details={"reason": "review_replay_target_missing"})
                assessment = await uow.knowledge.get_grounding_assessment(
                    _output_id(existing_event, "grounding_assessment")
                )
                if assessment is None:
                    raise InternalError(details={"reason": "review_replay_target_missing"})
                await uow.rollback()
                return EvidenceReviewResult(
                    _event_evidence(existing_event, link),
                    _event_object(existing_event, target),
                    assessment,
                    existing_event.id,
                    True,
                )
            target = await uow.knowledge.get_exact_for_update(command.knowledge_object_id)
            link = await uow.knowledge.get_evidence_link_for_update(command.evidence_link_id)
            if target is None or link is None or link.knowledge_object_id != target.id:
                raise ResourceNotFoundError(details={"resource": "evidence_link"})
            if (
                link.version != command.expected_version
                or link.review_status != command.expected_review_status.value
                or link.lifecycle_status != command.expected_lifecycle_status.value
            ):
                raise ResourceConflictError(details={"reason": "stale_evidence_link"})
            transition = CandidateLifecyclePolicy.transition(
                _evidence_snapshot(link), action, command_id=command.command_id
            )
            updated = await uow.knowledge.update_evidence_review_lifecycle(
                link.id,
                expected_version=link.version,
                expected_review_status=link.review_status,
                expected_lifecycle_status=link.lifecycle_status,
                review_status=transition.next_review_status.value,
                lifecycle_status=transition.next_lifecycle_status.value,
            )
            specification = assess_persisted_evidence(
                await uow.knowledge.list_evidence_links(target.id)
            )
            assessment = await uow.knowledge.add_grounding_assessment(
                NewGroundingAssessment(
                    id=uuid4(),
                    project_id=target.project_id,
                    knowledge_object_id=target.id,
                    evidence_set_fingerprint=specification.evidence_set_fingerprint,
                    status=specification.status.value,
                    supporting_count=specification.supporting_count,
                    contradicting_count=specification.contradicting_count,
                    qualifying_count=specification.qualifying_count,
                    rationale=specification.rationale,
                    validator_name=specification.validator_name,
                    validator_version=specification.validator_version,
                )
            )
            grounded_target = await uow.knowledge.set_grounding_cache(
                knowledge_object_id=target.id,
                grounding_assessment_id=assessment.id,
                grounding_status=assessment.status,
            )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="evidence_link",
                before_id=link.id,
                after_id=link.id,
                operation_type=operation_type,
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=None,
                    operation_type=operation_type,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=(_reference(target), _evidence_reference(link)),
                    output_refs=(
                        _reference(grounded_target),
                        _evidence_reference(updated),
                        {
                            "type": "grounding_assessment",
                            "id": str(assessment.id),
                            "status": assessment.status,
                            "lineage_id": str(target.lineage_id),
                        },
                    ),
                    reason=rationale,
                    idempotency_key=str(command.command_id),
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return EvidenceReviewResult(updated, grounded_target, assessment, event.id, False)

    async def _transition(
        self,
        command: ReviewTransitionCommand,
        action: CandidateCommand,
    ) -> ReviewCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = f"knowledge_object.{action.value}"
        identity = {
            "policy_version": REVIEW_POLICY_VERSION,
            "object_id": str(command.object_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._replay_review(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            if action is CandidateCommand.ACCEPT:
                candidate = await uow.knowledge.get_exact(command.object_id)
                if candidate is None:
                    raise ResourceNotFoundError(details={"resource": "knowledge_object"})
                lineage = await uow.knowledge.list_lineage_for_update(candidate.lineage_id)
                current = next((item for item in lineage if item.id == candidate.id), None)
            else:
                current = await uow.knowledge.get_exact_for_update(command.object_id)
            self._require_expected(current, command)
            assert current is not None
            if action is CandidateCommand.ACCEPT:
                if any(
                    item.id != current.id
                    and item.review_status == KnowledgeReviewStatus.ACCEPTED.value
                    and item.lifecycle_status
                    in {
                        KnowledgeLifecycleStatus.CURRENT.value,
                        KnowledgeLifecycleStatus.CHALLENGED.value,
                    }
                    for item in lineage
                ):
                    raise ResourceConflictError(
                        details={"reason": "accepted_predecessor_requires_revision_acceptance"}
                    )
            transition = CandidateLifecyclePolicy.transition(
                _snapshot(current), action, command_id=command.command_id
            )
            updated = await uow.knowledge.update_review_lifecycle(
                current.id,
                expected_version=current.version,
                expected_review_status=current.review_status,
                expected_lifecycle_status=current.lifecycle_status,
                review_status=transition.next_review_status.value,
                lifecycle_status=transition.next_lifecycle_status.value,
            )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="knowledge_object",
                before_id=current.id,
                after_id=updated.id,
                operation_type=operation_type,
            )
            event = await self._event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                before=(current,),
                after=(updated,),
                request_id=command.request_id,
            )
            await uow.commit()
            return ReviewCommandResult(updated, current, event.id, False)

    async def create_semantic_revision(
        self, command: SemanticRevisionCommand
    ) -> ReviewCommandResult:
        rationale = _rationale(command.rationale)
        payload = command.payload.validated()
        payload_identity, subtype_payload, interpretations = _payload_parts(payload)
        operation_type = "knowledge_object.semantic_revision"
        if len(set(command.reproposed_evidence_link_ids)) != len(
            command.reproposed_evidence_link_ids
        ):
            raise DomainValidationError(details={"reason": "evidence_reproposal_duplicate"})
        identity = {
            "policy_version": REVIEW_POLICY_VERSION,
            "object_id": str(command.object_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "rationale": rationale,
            "payload": payload_identity,
        }
        if command.reproposed_evidence_link_ids:
            identity = {
                **identity,
                "evidence_reproposal_policy_version": EVIDENCE_REPROPOSAL_POLICY_VERSION,
                "reproposed_evidence_link_ids": [
                    str(item) for item in command.reproposed_evidence_link_ids
                ],
            }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._replay_review(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            current = await uow.knowledge.get_exact_for_update(command.object_id)
            self._require_expected(current, command)
            assert current is not None
            if payload.object_type.value != current.object_type:
                raise DomainValidationError(details={"reason": "knowledge_subtype_mismatch"})
            existing_payload = await uow.knowledge.get_payload(current.id)
            if existing_payload is None:
                raise InternalError(details={"reason": "knowledge_subtype_missing"})
            SemanticEditPolicy.require_semantic_revision(
                SemanticEditPolicy.classify(
                    family=CanonicalArtifactFamily.KNOWLEDGE_OBJECT,
                    current_payload=_semantic_identity(current, existing_payload),
                    proposed_payload=payload_identity,
                )
            )
            lineage = await uow.knowledge.list_lineage_for_update(current.lineage_id)
            predecessor_evidence = (
                await uow.knowledge.list_evidence_links(current.id)
                if command.reproposed_evidence_link_ids
                else ()
            )
            predecessor_evidence_by_id = {item.id: item for item in predecessor_evidence}
            reproposed_sources: list[EvidenceLinkData] = []
            for evidence_link_id in command.reproposed_evidence_link_ids:
                source_link = predecessor_evidence_by_id.get(evidence_link_id)
                if source_link is None:
                    raise ResourceNotFoundError(details={"resource": "evidence_link"})
                if (
                    source_link.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
                    or source_link.review_status not in _REPROPOSABLE_EVIDENCE_REVIEW_STATUSES
                ):
                    raise ResourceConflictError(
                        details={"reason": "evidence_reproposal_source_ineligible"}
                    )
                reproposed_sources.append(source_link)
            next_version = max(item.version for item in lineage) + 1
            revision_id = uuid4()
            specification = new_version_specification(
                VersionSnapshot(
                    id=current.id,
                    project_id=current.project_id,
                    lineage_id=current.lineage_id,
                    version=current.version,
                ),
                new_id=revision_id,
                project_id=current.project_id,
                lineage_id=current.lineage_id,
                version=next_version,
                latest_lineage_version=next_version - 1,
            )
            revision = await uow.knowledge.add_revision(
                NewKnowledgeRevision(
                    id=specification.id,
                    project_id=current.project_id,
                    lineage_id=current.lineage_id,
                    version=specification.version,
                    object_type=payload.object_type.value,
                    statement=payload.statement,
                    statement_normalization_version=STATEMENT_NORMALIZATION_VERSION,
                    normalized_statement_hash=statement_fingerprint(payload.statement),
                    origin=payload.origin.value,
                    epistemic_status=payload.epistemic_status.value,
                    confidence=payload.confidence,
                    subtype_payload=subtype_payload,
                    ambiguity_interpretations=interpretations,
                    derived_from_id=current.id,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                )
            )
            reproposed_links: list[EvidenceLinkData] = []
            for source_link in reproposed_sources:
                link_id = uuid4()
                reproposed_links.append(
                    await uow.knowledge.add_evidence_link(
                        NewEvidenceLink(
                            id=link_id,
                            project_id=current.project_id,
                            lineage_id=link_id,
                            version=1,
                            knowledge_object_id=revision.id,
                            source_chunk_id=source_link.source_chunk_id,
                            repository_component_id=source_link.repository_component_id,
                            evidence_relation=source_link.evidence_relation,
                            evidence_excerpt=source_link.evidence_excerpt,
                            confidence=source_link.confidence,
                            review_status=KnowledgeReviewStatus.CANDIDATE.value,
                            lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
                            generation_record_id=None,
                            created_by_type="human",
                            created_by_id=self._actor.actor_id,
                            source_anchor_start_offset=source_link.source_anchor_start_offset,
                            source_anchor_end_offset=source_link.source_anchor_end_offset,
                            source_anchor_hash=source_link.source_anchor_hash,
                            source_anchor_policy_version=source_link.source_anchor_policy_version,
                        )
                    )
                )
            assessment: GroundingAssessmentData | None = None
            if reproposed_links:
                grounding = assess_persisted_evidence(tuple(reproposed_links))
                assessment = await uow.knowledge.add_grounding_assessment(
                    NewGroundingAssessment(
                        id=uuid4(),
                        project_id=current.project_id,
                        knowledge_object_id=revision.id,
                        evidence_set_fingerprint=grounding.evidence_set_fingerprint,
                        status=grounding.status.value,
                        supporting_count=grounding.supporting_count,
                        contradicting_count=grounding.contradicting_count,
                        qualifying_count=grounding.qualifying_count,
                        rationale=grounding.rationale,
                        validator_name=grounding.validator_name,
                        validator_version=grounding.validator_version,
                    )
                )
                revision = await uow.knowledge.set_grounding_cache(
                    knowledge_object_id=revision.id,
                    grounding_assessment_id=assessment.id,
                    grounding_status=assessment.status,
                )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="knowledge_object",
                before_id=current.id,
                after_id=revision.id,
                operation_type=operation_type,
            )
            event = await self._event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                before=(current,),
                after=(revision,),
                extra_inputs=tuple(_evidence_reference(item) for item in reproposed_sources),
                extra_outputs=tuple(
                    {
                        **_evidence_reference(item),
                        "reproposed_from_id": str(source.id),
                    }
                    for item, source in zip(reproposed_links, reproposed_sources, strict=True)
                )
                + (() if assessment is None else (_grounding_reference(assessment),)),
                request_id=command.request_id,
            )
            await uow.commit()
            return ReviewCommandResult(revision, current, event.id, False)

    async def create_presentation_revision(
        self, command: PresentationRevisionCommand
    ) -> PresentationCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = "knowledge_object.presentation_revision"
        identity = {
            "policy_version": REVIEW_POLICY_VERSION,
            "object_id": str(command.object_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "rationale": rationale,
            "display_text": command.display_text,
            "display_label": command.display_label,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            existing_event = await uow.events.get_by_idempotency_key(
                operation_type, str(command.command_id)
            )
            if existing_event is not None:
                if existing_event.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                presentation_id = _output_id(existing_event, "knowledge_presentation")
                presentations = await uow.knowledge.list_presentations(command.object_id)
                presentation = next(
                    (item for item in presentations if item.id == presentation_id), None
                )
                item = await uow.knowledge.get_exact(command.object_id)
                if item is None or presentation is None:
                    raise InternalError(details={"reason": "review_replay_target_missing"})
                await uow.rollback()
                return PresentationCommandResult(
                    _event_object(existing_event, item),
                    presentation,
                    existing_event.id,
                    True,
                )
            current = await uow.knowledge.get_exact_for_update(command.object_id)
            self._require_expected(current, command)
            assert current is not None
            previous = await uow.knowledge.get_current_presentation_for_update(current.id)
            specification = knowledge_presentation_revision(
                new_id=uuid4(),
                knowledge_object_id=current.id,
                display_text=command.display_text,
                display_label=command.display_label,
                predecessor=(
                    None
                    if previous is None
                    else KnowledgePresentationSnapshot(
                        id=previous.id,
                        knowledge_object_id=previous.knowledge_object_id,
                        version=previous.version,
                        display_text=previous.display_text,
                        display_label=previous.display_label,
                    )
                ),
            )
            presentation = await uow.knowledge.add_presentation(
                NewKnowledgePresentation(
                    id=specification.id,
                    knowledge_object_id=current.id,
                    version=specification.version,
                    display_text=specification.display_text,
                    display_label=specification.display_label,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    supersedes_id=specification.supersedes_id,
                )
            )
            event = await self._event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                before=(current,),
                after=(current,),
                extra_outputs=(
                    {
                        "type": "knowledge_presentation",
                        "id": str(presentation.id),
                        "version": presentation.version,
                        "lineage_id": str(current.lineage_id),
                    },
                ),
                request_id=command.request_id,
            )
            await uow.commit()
            return PresentationCommandResult(current, presentation, event.id, False)

    async def accept_revision(self, command: AcceptRevisionCommand) -> ReviewCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = "knowledge_object.accept_revision"
        identity = {
            "policy_version": REVIEW_POLICY_VERSION,
            "predecessor_id": str(command.predecessor_id),
            "replacement_id": str(command.replacement_id),
            "expected_predecessor_version": command.expected_predecessor_version,
            "expected_predecessor_lifecycle_status": command.expected_predecessor_lifecycle_status.value,
            "expected_replacement_version": command.expected_replacement_version,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._replay_review(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            predecessor = await uow.knowledge.get_exact_for_update(command.predecessor_id)
            replacement = await uow.knowledge.get_exact_for_update(command.replacement_id)
            if predecessor is None or replacement is None:
                raise ResourceNotFoundError(details={"resource": "knowledge_object"})
            if (
                predecessor.version != command.expected_predecessor_version
                or predecessor.review_status != KnowledgeReviewStatus.ACCEPTED.value
                or predecessor.lifecycle_status
                != command.expected_predecessor_lifecycle_status.value
                or replacement.version != command.expected_replacement_version
                or replacement.review_status != KnowledgeReviewStatus.CANDIDATE.value
                or replacement.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
            ):
                raise ResourceConflictError(details={"reason": "stale_knowledge_object"})
            lineage = await uow.knowledge.list_lineage_for_update(predecessor.lineage_id)
            plan = accept_revision(
                _snapshot(predecessor),
                _snapshot(replacement),
                command_id=command.command_id,
                lineage=tuple(_snapshot(item) for item in lineage),
            )
            prior = await uow.knowledge.update_review_lifecycle(
                predecessor.id,
                expected_version=predecessor.version,
                expected_review_status=predecessor.review_status,
                expected_lifecycle_status=predecessor.lifecycle_status,
                review_status=KnowledgeReviewStatus.ACCEPTED.value,
                lifecycle_status=plan.previous_next_lifecycle_status.value,
            )
            accepted = await uow.knowledge.update_review_lifecycle(
                replacement.id,
                expected_version=replacement.version,
                expected_review_status=replacement.review_status,
                expected_lifecycle_status=replacement.lifecycle_status,
                review_status=plan.replacement_next_review_status.value,
                lifecycle_status=plan.replacement_next_lifecycle_status.value,
                supersedes_id=plan.replacement_supersedes_id,
            )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="knowledge_object",
                before_id=predecessor.id,
                after_id=accepted.id,
                operation_type=operation_type,
            )
            event = await self._event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                before=(predecessor, replacement),
                after=(prior, accepted),
                request_id=command.request_id,
            )
            await uow.commit()
            return ReviewCommandResult(accepted, prior, event.id, False)

    async def _replay_review(
        self,
        uow: UnitOfWork,
        operation_type: str,
        command_id: UUID,
        fingerprint: str,
    ) -> ReviewCommandResult | None:
        event = await uow.events.get_by_idempotency_key(operation_type, str(command_id))
        if event is None:
            return None
        if event.input_fingerprint != fingerprint:
            raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
        object_id = _output_id(event, "knowledge_object")
        item = await uow.knowledge.get_exact(object_id)
        if item is None:
            raise InternalError(details={"reason": "review_replay_target_missing"})
        return ReviewCommandResult(_event_object(event, item), None, event.id, True)

    async def _event(
        self,
        uow: UnitOfWork,
        *,
        operation_type: str,
        command_id: UUID,
        fingerprint: str,
        rationale: str,
        before: tuple[KnowledgeObjectData, ...],
        after: tuple[KnowledgeObjectData, ...],
        request_id: str | None,
        extra_inputs: tuple[dict[str, object], ...] = (),
        extra_outputs: tuple[dict[str, object], ...] = (),
    ) -> OperationEventData:
        return await uow.events.append(
            NewOperationEvent(
                id=uuid4(),
                project_id=uow.knowledge.project_id,
                workflow_run_id=None,
                operation_type=operation_type,
                created_by_type="human",
                created_by_id=self._actor.actor_id,
                input_refs=tuple(_reference(item) for item in before) + extra_inputs,
                output_refs=tuple(_reference(item) for item in after) + extra_outputs,
                reason=rationale,
                idempotency_key=str(command_id),
                input_fingerprint=fingerprint,
                request_id=request_id,
            )
        )

    def _require_owned(self, project: ProjectData | None) -> None:
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})

    @staticmethod
    def _require_expected(
        current: KnowledgeObjectData | None,
        command: ReviewTransitionCommand | SemanticRevisionCommand | PresentationRevisionCommand,
    ) -> None:
        if current is None:
            raise ResourceNotFoundError(details={"resource": "knowledge_object"})
        if (
            current.version != command.expected_version
            or current.review_status != command.expected_review_status.value
            or current.lifecycle_status != command.expected_lifecycle_status.value
        ):
            raise ResourceConflictError(details={"reason": "stale_knowledge_object"})


def _rationale(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > RATIONALE_MAX:
        raise DomainValidationError(details={"field": "rationale", "reason": "invalid"})
    return normalized


def _snapshot(value: KnowledgeObjectData) -> CandidateSnapshot:
    return CandidateSnapshot(
        id=value.id,
        project_id=value.project_id,
        lineage_id=value.lineage_id,
        version=value.version,
        review_status=KnowledgeReviewStatus(value.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(value.lifecycle_status),
        derived_from_id=value.derived_from_id,
    )


def _evidence_snapshot(value: EvidenceLinkData) -> CandidateSnapshot:
    return CandidateSnapshot(
        id=value.id,
        project_id=value.project_id,
        lineage_id=value.lineage_id,
        version=value.version,
        review_status=KnowledgeReviewStatus(value.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(value.lifecycle_status),
    )


def _reference(value: KnowledgeObjectData) -> dict[str, object]:
    return {
        "type": "knowledge_object",
        "id": str(value.id),
        "lineage_id": str(value.lineage_id),
        "version": value.version,
        "review_status": value.review_status,
        "lifecycle_status": value.lifecycle_status,
    }


def _evidence_reference(value: EvidenceLinkData) -> dict[str, object]:
    return {
        "type": "evidence_link",
        "id": str(value.id),
        "lineage_id": str(value.lineage_id),
        "version": value.version,
        "review_status": value.review_status,
        "lifecycle_status": value.lifecycle_status,
    }


def _grounding_reference(value: GroundingAssessmentData) -> dict[str, object]:
    return {
        "type": "grounding_assessment",
        "id": str(value.id),
        "knowledge_object_id": str(value.knowledge_object_id),
        "status": value.status,
        "evidence_set_fingerprint": value.evidence_set_fingerprint,
    }


def _output_id(event: OperationEventData, artifact_type: str) -> UUID:
    for reference in reversed(event.output_refs):
        reference_id = reference.get("id")
        if reference.get("type") == artifact_type and isinstance(reference_id, str):
            return UUID(reference_id)
    raise InternalError(details={"reason": "review_replay_output_missing"})


def _event_object(event: OperationEventData, persisted: KnowledgeObjectData) -> KnowledgeObjectData:
    for reference in reversed(event.output_refs):
        if reference.get("type") != "knowledge_object" or reference.get("id") != str(persisted.id):
            continue
        review_status = reference.get("review_status")
        lifecycle_status = reference.get("lifecycle_status")
        if isinstance(review_status, str) and isinstance(lifecycle_status, str):
            return replace(
                persisted,
                review_status=review_status,
                lifecycle_status=lifecycle_status,
            )
    raise InternalError(details={"reason": "review_replay_output_missing"})


def _event_evidence(event: OperationEventData, persisted: EvidenceLinkData) -> EvidenceLinkData:
    for reference in reversed(event.output_refs):
        if reference.get("type") != "evidence_link" or reference.get("id") != str(persisted.id):
            continue
        review_status = reference.get("review_status")
        lifecycle_status = reference.get("lifecycle_status")
        if isinstance(review_status, str) and isinstance(lifecycle_status, str):
            return replace(
                persisted,
                review_status=review_status,
                lifecycle_status=lifecycle_status,
            )
    raise InternalError(details={"reason": "review_replay_output_missing"})


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _payload_parts(
    payload: KnowledgeObjectPayload,
) -> tuple[dict[str, object], dict[str, object], tuple[dict[str, object], ...]]:
    raw = asdict(payload.subtype)
    interpretations: tuple[dict[str, object], ...] = ()
    if isinstance(payload.subtype, AmbiguityPayload):
        raw_interpretations = raw.pop("interpretations")
        assert isinstance(raw_interpretations, tuple)
        interpretations = tuple({"id": uuid4(), **_plain(item)} for item in raw_interpretations)
    subtype = _plain(raw)
    identity: dict[str, object] = {
        "object_type": payload.object_type.value,
        "statement": payload.statement,
        "origin": payload.origin.value,
        "epistemic_status": payload.epistemic_status.value,
        "confidence": _decimal_identity(payload.confidence),
        "subtype": subtype,
        "interpretations": [
            {key: value for key, value in item.items() if key != "id"} for item in interpretations
        ],
    }
    return identity, subtype, interpretations


def _semantic_identity(
    item: KnowledgeObjectData, payload: KnowledgeObjectPayloadData
) -> dict[str, object]:
    return {
        "object_type": item.object_type,
        "statement": item.statement,
        "origin": item.origin,
        "epistemic_status": item.epistemic_status,
        "confidence": _decimal_identity(item.confidence),
        "subtype": payload.subtype_payload,
        "interpretations": list(payload.ambiguity_interpretations),
    }


def _plain(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return {
        str(key): (item.value if hasattr(item, "value") else item) for key, item in value.items()
    }


def _decimal_identity(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")
