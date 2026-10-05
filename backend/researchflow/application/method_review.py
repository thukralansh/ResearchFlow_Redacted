"""Grouped MethodElement and MethodModel review application boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, Protocol
from uuid import UUID, uuid4

from researchflow.application.method_grounding import (
    InitialMethodMember,
    assess_initial_method_model,
    assess_method_evidence,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.lifecycle import (
    CandidateCommand,
    CandidateLifecyclePolicy,
    CandidateSnapshot,
)
from researchflow.domain.method_catalog import (
    FreshnessStatus,
    MethodComponentType,
    MethodElementType,
    MethodEpistemicStatus,
    MethodReviewSection,
    MethodSymbolType,
    SectionReviewOutcome,
)
from researchflow.domain.method_review import (
    MethodMemberSnapshot,
    MethodReviewPolicy,
    SectionReviewSnapshot,
    member_set_fingerprint,
    method_composition_fingerprint,
)
from researchflow.domain.method_validation import (
    classify_method_membership,
    method_element_endpoint_type,
    validate_symbol_uniqueness,
)
from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.repositories import (
    MethodElementData,
    MethodEvidenceLinkData,
    MethodModelAssessmentData,
    MethodModelData,
    MethodModelMemberData,
    MethodModelSectionReviewData,
    NewMethodElementCandidate,
    NewMethodEvidenceLink,
    NewMethodGroundingAssessment,
    NewMethodModelAssessment,
    NewMethodModelCandidate,
    NewMethodModelMember,
    NewMethodModelSectionReview,
    NewMethodObjectLink,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

METHOD_REVIEW_POLICY_VERSION: Final = "rf-method-review-v1"
RATIONALE_MAX: Final = 4_000


class MethodReviewStalenessHook(Protocol):
    """ seam; dependency traversal remains deferred to ."""

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


class NoOpMethodReviewStalenessHook:
    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork | None = None,
        project_id: UUID,
        cause_type: str = "method_element",
        lineage_id: UUID | None = None,
        before_id: UUID,
        after_id: UUID,
        operation_type: str,
    ) -> None:
        del uow, project_id, cause_type, lineage_id, before_id, after_id, operation_type


@dataclass(frozen=True, slots=True)
class MethodElementTransitionCommand:
    project_id: UUID
    method_model_id: UUID
    element_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class MethodElementRevisionCommand:
    project_id: UUID
    method_model_id: UUID
    element_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    element_type: MethodElementType
    epistemic_status: MethodEpistemicStatus
    confidence: Decimal | None
    subtype_payload: dict[str, object]
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class MethodEvidenceReviewCommand:
    project_id: UUID
    method_model_id: UUID
    element_id: UUID
    evidence_link_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExcludeMethodElementCommand:
    project_id: UUID
    method_model_id: UUID
    element_id: UUID
    command_id: UUID
    expected_model_version: int
    expected_composition_fingerprint: str
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewMethodSectionCommand:
    project_id: UUID
    method_model_id: UUID
    review_section: MethodReviewSection
    command_id: UUID
    displayed_member_ids: tuple[UUID, ...]
    displayed_member_set_fingerprint: str
    outcome: SectionReviewOutcome
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptMethodModelCommand:
    project_id: UUID
    method_model_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    expected_composition_fingerprint: str
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class MethodReviewView:
    model: MethodModelData
    members: tuple[MethodModelMemberData, ...]
    section_reviews: tuple[MethodModelSectionReviewData, ...]
    assessment: MethodModelAssessmentData
    section_fingerprints: dict[str, str]
    history: tuple[OperationEventData, ...]
    aggregate_ready: bool
    blocking_reason: str | None


@dataclass(frozen=True, slots=True)
class MethodElementCommandResult:
    element: MethodElementData
    model: MethodModelData
    assessment: MethodModelAssessmentData
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class MethodEvidenceCommandResult:
    evidence: MethodEvidenceLinkData
    element: MethodElementData
    model: MethodModelData
    assessment: MethodModelAssessmentData
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class MethodCompositionCommandResult:
    model: MethodModelData
    replacement_element: MethodElementData | None
    assessment: MethodModelAssessmentData
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class MethodSectionCommandResult:
    model: MethodModelData
    section_review: MethodModelSectionReviewData
    accepted_element_ids: tuple[UUID, ...]
    assessment: MethodModelAssessmentData
    aggregate_ready: bool
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class MethodModelCommandResult:
    model: MethodModelData
    assessment: MethodModelAssessmentData
    event_id: UUID
    replayed: bool


class MethodReviewService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        staleness_hook: MethodReviewStalenessHook | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._staleness_hook = staleness_hook or NoOpMethodReviewStalenessHook()

    async def detail(self, project_id: UUID, method_model_id: UUID) -> MethodReviewView:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            model = await self._model(uow, method_model_id)
            detailed_members: list[MethodModelMemberData] = []
            for item in await uow.method_models.list_members(model.id):
                detailed_members.append(
                    replace(
                        item,
                        evidence=await uow.method_models.list_evidence_links(item.element.id),
                        object_links=await uow.method_models.list_object_links((item.element.id,)),
                    )
                )
            members = tuple(detailed_members)
            reviews = await uow.method_models.list_current_section_reviews(model.id)
            assessment = await self._assessment(uow, model)
            ready, reason = _readiness(model, members, reviews, assessment)
            section_fingerprints = {
                section.value: member_set_fingerprint(
                    tuple(
                        _snapshot(item) for item in members if item.review_section == section.value
                    )
                )
                for section in MethodReviewSection
            }
            history = await uow.events.list_for_knowledge_lineage(model.lineage_id)
            await uow.rollback()
            return MethodReviewView(
                model,
                members,
                reviews,
                assessment,
                section_fingerprints,
                history,
                ready,
                reason,
            )

    async def accepted_current(
        self, project_id: UUID, research_question_id: UUID
    ) -> MethodModelData | None:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            model = await uow.method_models.get_accepted_current_model(research_question_id)
            await uow.rollback()
            return model

    async def list_models(
        self, project_id: UUID, research_question_id: UUID
    ) -> tuple[MethodModelData, ...]:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            models = await uow.method_models.list_models_for_question(research_question_id)
            await uow.rollback()
            return models

    async def accept_element(
        self, command: MethodElementTransitionCommand
    ) -> MethodElementCommandResult:
        return await self._transition_element(command, CandidateCommand.ACCEPT)

    async def reject_element(
        self, command: MethodElementTransitionCommand
    ) -> MethodElementCommandResult:
        return await self._transition_element(command, CandidateCommand.REJECT)

    async def request_element_revision(
        self, command: MethodElementTransitionCommand
    ) -> MethodElementCommandResult:
        return await self._transition_element(command, CandidateCommand.REQUEST_REVISION)

    async def challenge_element(
        self, command: MethodElementTransitionCommand
    ) -> MethodElementCommandResult:
        return await self._transition_element(command, CandidateCommand.CHALLENGE)

    async def reaffirm_element(
        self, command: MethodElementTransitionCommand
    ) -> MethodElementCommandResult:
        return await self._transition_element(command, CandidateCommand.REAFFIRM)

    async def accept_evidence(
        self, command: MethodEvidenceReviewCommand
    ) -> MethodEvidenceCommandResult:
        return await self._transition_evidence(command, CandidateCommand.ACCEPT)

    async def reject_evidence(
        self, command: MethodEvidenceReviewCommand
    ) -> MethodEvidenceCommandResult:
        return await self._transition_evidence(command, CandidateCommand.REJECT)

    async def request_evidence_revision(
        self, command: MethodEvidenceReviewCommand
    ) -> MethodEvidenceCommandResult:
        return await self._transition_evidence(command, CandidateCommand.REQUEST_REVISION)

    async def challenge_evidence(
        self, command: MethodEvidenceReviewCommand
    ) -> MethodEvidenceCommandResult:
        return await self._transition_evidence(command, CandidateCommand.CHALLENGE)

    async def reaffirm_evidence(
        self, command: MethodEvidenceReviewCommand
    ) -> MethodEvidenceCommandResult:
        return await self._transition_evidence(command, CandidateCommand.REAFFIRM)

    async def _transition_evidence(
        self, command: MethodEvidenceReviewCommand, action: CandidateCommand
    ) -> MethodEvidenceCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = f"method_evidence.{action.value}"
        identity = {
            "policy_version": METHOD_REVIEW_POLICY_VERSION,
            "method_model_id": str(command.method_model_id),
            "element_id": str(command.element_id),
            "evidence_link_id": str(command.evidence_link_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._evidence_replay(
                uow, operation_type, command.command_id, fingerprint
            )
            if replay is not None:
                return replay
            model = await self._locked_model(uow, command.method_model_id)
            members = await uow.method_models.list_members_for_update(model.id)
            member = _member(members, command.element_id)
            link = await uow.method_models.get_evidence_link_for_update(command.evidence_link_id)
            if link is None or link.method_element_id != member.method_element_id:
                raise ResourceNotFoundError(details={"resource": "method_evidence"})
            if (
                link.version != command.expected_version
                or link.review_status != command.expected_review_status.value
                or link.lifecycle_status != command.expected_lifecycle_status.value
            ):
                raise ResourceConflictError(details={"reason": "stale_method_evidence"})
            transition = CandidateLifecyclePolicy.transition(
                CandidateSnapshot(
                    id=link.id,
                    project_id=link.project_id,
                    lineage_id=link.lineage_id,
                    version=link.version,
                    review_status=KnowledgeReviewStatus(link.review_status),
                    lifecycle_status=KnowledgeLifecycleStatus(link.lifecycle_status),
                ),
                action,
                command_id=command.command_id,
            )
            updated_link = await uow.method_models.update_evidence_review_lifecycle(
                link.id,
                expected_version=link.version,
                expected_review_status=link.review_status,
                expected_lifecycle_status=link.lifecycle_status,
                review_status=transition.next_review_status.value,
                lifecycle_status=transition.next_lifecycle_status.value,
            )
            updated_element = await self._refresh_element_grounding(uow, member)
            refreshed_members = tuple(
                replace(item, element=updated_element)
                if item.method_element_id == updated_element.id
                else item
                for item in members
            )
            assessment = await self._refresh_assessment(uow, model, refreshed_members)
            event = await self._append_event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(_evidence_ref(link), _element_ref(member.element), _model_ref(model)),
                outputs=(
                    _evidence_ref(updated_link),
                    _element_ref(updated_element),
                    _model_ref(model),
                    _assessment_ref(assessment),
                ),
            )
            await uow.commit()
            return MethodEvidenceCommandResult(
                updated_link, updated_element, model, assessment, event.id, False
            )

    async def _transition_element(
        self,
        command: MethodElementTransitionCommand,
        action: CandidateCommand,
    ) -> MethodElementCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = f"method_element.{action.value}"
        identity = {
            "policy_version": METHOD_REVIEW_POLICY_VERSION,
            "method_model_id": str(command.method_model_id),
            "element_id": str(command.element_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._element_replay(
                uow, operation_type, command.command_id, fingerprint, command.method_model_id
            )
            if replay is not None:
                return replay
            model = await self._locked_model(uow, command.method_model_id)
            members = await uow.method_models.list_members_for_update(model.id)
            member = _member(members, command.element_id)
            current = await uow.method_models.get_element_for_update(member.method_element_id)
            if current is None:
                raise ResourceNotFoundError(details={"resource": "method_element"})
            _expected_element(current, command)
            transition = CandidateLifecyclePolicy.transition(
                _element_snapshot(current), action, command_id=command.command_id
            )
            updated = (
                await self._accept_element(uow, current)
                if action is CandidateCommand.ACCEPT
                else await uow.method_models.update_element_review_lifecycle(
                    current.id,
                    expected_version=current.version,
                    expected_review_status=current.review_status,
                    expected_lifecycle_status=current.lifecycle_status,
                    review_status=transition.next_review_status.value,
                    lifecycle_status=transition.next_lifecycle_status.value,
                )
            )
            updated_members = tuple(
                replace(item, element=updated) if item.method_element_id == updated.id else item
                for item in members
            )
            assessment = await self._refresh_assessment(uow, model, updated_members)
            event = await self._append_event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(_element_ref(current), _model_ref(model)),
                outputs=(
                    _element_ref(updated),
                    _model_ref(model),
                    _assessment_ref(assessment),
                ),
            )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="method_element",
                before_id=current.id,
                after_id=updated.id,
                operation_type=operation_type,
            )
            await uow.commit()
            return MethodElementCommandResult(updated, model, assessment, event.id, False)

    async def exclude_element(
        self, command: ExcludeMethodElementCommand
    ) -> MethodCompositionCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = "method_model.exclude_element"
        identity = {
            "policy_version": METHOD_REVIEW_POLICY_VERSION,
            "method_model_id": str(command.method_model_id),
            "element_id": str(command.element_id),
            "expected_model_version": command.expected_model_version,
            "expected_composition_fingerprint": command.expected_composition_fingerprint,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._composition_replay(
                uow, operation_type, command.command_id, fingerprint
            )
            if replay is not None:
                return replay
            model = await self._locked_model(uow, command.method_model_id)
            _expected_model_composition(
                model, command.expected_model_version, command.expected_composition_fingerprint
            )
            members = await uow.method_models.list_members_for_update(model.id)
            _member(members, command.element_id)
            next_members = tuple(
                item for item in members if item.method_element_id != command.element_id
            )
            revised = await self._append_model_revision(uow, model, next_members)
            assessment = await self._refresh_assessment(uow, revised, next_members)
            event = await self._append_event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(
                    _model_ref(model),
                    {"type": "method_element", "id": str(command.element_id)},
                ),
                outputs=(_model_ref(revised), _assessment_ref(assessment)),
            )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="method_model",
                before_id=model.id,
                after_id=revised.id,
                operation_type=operation_type,
            )
            await uow.commit()
            return MethodCompositionCommandResult(revised, None, assessment, event.id, False)

    async def revise_element(
        self, command: MethodElementRevisionCommand
    ) -> MethodCompositionCommandResult:
        rationale = _rationale(command.rationale)
        payload = _validated_subtype(command.element_type, command.subtype_payload)
        operation_type = "method_element.semantic_revision"
        identity = {
            "policy_version": METHOD_REVIEW_POLICY_VERSION,
            "method_model_id": str(command.method_model_id),
            "element_id": str(command.element_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "element_type": command.element_type.value,
            "epistemic_status": command.epistemic_status.value,
            "confidence": None if command.confidence is None else str(command.confidence),
            "subtype_payload": payload,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._composition_replay(
                uow, operation_type, command.command_id, fingerprint
            )
            if replay is not None:
                return replay
            model = await self._locked_model(uow, command.method_model_id)
            members = await uow.method_models.list_members_for_update(model.id)
            old_member = _member(members, command.element_id)
            current = await uow.method_models.get_element_for_update(command.element_id)
            if current is None:
                raise ResourceNotFoundError(details={"resource": "method_element"})
            _expected_element(current, command)
            if current.element_type != command.element_type.value:
                raise DomainValidationError(
                    details={"reason": "method_element_type_change_forbidden"}
                )
            if command.element_type is MethodElementType.DATA_FLOW:
                source_id = payload["source_element_id"]
                target_id = payload["target_element_id"]
                if not isinstance(source_id, UUID) or not isinstance(target_id, UUID):
                    raise DomainValidationError(details={"reason": "data_flow_endpoint_unresolved"})
                endpoints = (source_id, target_id)
                member_by_id = {item.method_element_id: item for item in members}
                if any(item not in member_by_id for item in endpoints):
                    raise DomainValidationError(details={"reason": "data_flow_endpoint_unresolved"})
                allowed = {
                    MethodElementType.COMPONENT.value,
                    MethodElementType.SYMBOL.value,
                    MethodElementType.ALGORITHM_STEP.value,
                }
                if any(
                    member_by_id[item].element.element_type not in allowed for item in endpoints
                ):
                    raise DomainValidationError(
                        details={"reason": "data_flow_endpoint_type_invalid"}
                    )
            latest = await uow.method_models.latest_element_lineage_for_update(current.lineage_id)
            if latest is None or latest.id != current.id:
                raise ResourceConflictError(
                    details={"reason": "method_element_revision_not_latest"}
                )
            replacement_id = uuid4()
            replacement = await uow.method_models.add_candidate_element(
                NewMethodElementCandidate(
                    id=replacement_id,
                    project_id=command.project_id,
                    lineage_id=current.lineage_id,
                    version=current.version + 1,
                    element_type=command.element_type.value,
                    epistemic_status=command.epistemic_status.value,
                    confidence=command.confidence,
                    subtype_payload=payload,
                    generation_record_id=None,
                    derived_from_id=current.id,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                )
            )
            for link in await uow.method_models.list_evidence_links(current.id):
                link_id = uuid4()
                await uow.method_models.add_evidence_link(
                    NewMethodEvidenceLink(
                        id=link_id,
                        project_id=command.project_id,
                        lineage_id=link_id,
                        version=1,
                        method_element_id=replacement.id,
                        source_chunk_id=link.source_chunk_id,
                        repository_component_id=link.repository_component_id,
                        evidence_relation=link.evidence_relation,
                        evidence_excerpt=link.evidence_excerpt,
                        confidence=link.confidence,
                        review_status=KnowledgeReviewStatus.CANDIDATE.value,
                        lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
                        generation_record_id=None,
                        created_by_type="human",
                        created_by_id=self._actor.actor_id,
                    )
                )
            for object_link in await uow.method_models.list_object_links((current.id,)):
                link_id = uuid4()
                await uow.method_models.add_object_link(
                    NewMethodObjectLink(
                        id=link_id,
                        project_id=command.project_id,
                        lineage_id=link_id,
                        version=1,
                        source_method_element_id=replacement.id,
                        relationship_type=object_link.relationship_type,
                        target_method_element_id=object_link.target_method_element_id,
                        rationale=object_link.rationale,
                        confidence=object_link.confidence,
                        generation_record_id=None,
                        review_status=KnowledgeReviewStatus.CANDIDATE.value,
                        lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
                        created_by_type="human",
                        created_by_id=self._actor.actor_id,
                    )
                )
            component_type = (
                MethodComponentType(str(payload["component_type"]))
                if command.element_type is MethodElementType.COMPONENT
                else None
            )
            classification = classify_method_membership(
                element_type=command.element_type, component_type=component_type
            )
            raw_sequence_index = payload.get("sequence_index")
            sequence_index = (
                raw_sequence_index
                if command.element_type is MethodElementType.ALGORITHM_STEP
                else old_member.sequence_index
            )
            if sequence_index is not None and not isinstance(sequence_index, int):
                raise DomainValidationError(details={"reason": "sequence_index_invalid"})
            revised_members = tuple(
                replace(
                    item,
                    method_element_id=replacement.id,
                    review_section=classification.review_section.value,
                    member_role=classification.member_role.value,
                    sequence_index=sequence_index,
                    element=replacement,
                    subtype_payload=payload,
                )
                if item.method_element_id == current.id
                else item
                for item in members
            )
            symbols = tuple(
                (
                    str(item.method_element_id),
                    str(item.subtype_payload["symbol"]),
                    str(item.subtype_payload["canonical_name"]),
                )
                for item in revised_members
                if item.element.element_type == MethodElementType.SYMBOL.value
            )
            validate_symbol_uniqueness(symbols)
            revised_model = await self._append_model_revision(uow, model, revised_members)
            assessment = await self._refresh_assessment(uow, revised_model, revised_members)
            event = await self._append_event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(_element_ref(current), _model_ref(model)),
                outputs=(
                    _element_ref(replacement),
                    _model_ref(revised_model),
                    _assessment_ref(assessment),
                ),
            )
            await self._staleness_hook.artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="method_element",
                before_id=current.id,
                after_id=replacement.id,
                operation_type=operation_type,
            )
            await uow.commit()
            return MethodCompositionCommandResult(
                revised_model, replacement, assessment, event.id, False
            )

    async def review_section(
        self, command: ReviewMethodSectionCommand
    ) -> MethodSectionCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = f"method_model.section.{command.outcome.value}"
        identity = {
            "policy_version": METHOD_REVIEW_POLICY_VERSION,
            "method_model_id": str(command.method_model_id),
            "review_section": command.review_section.value,
            "displayed_member_ids": [str(item) for item in command.displayed_member_ids],
            "displayed_member_set_fingerprint": command.displayed_member_set_fingerprint,
            "outcome": command.outcome.value,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._section_replay(
                uow, operation_type, command.command_id, fingerprint
            )
            if replay is not None:
                return replay
            model = await self._locked_model(uow, command.method_model_id)
            if model.review_status != KnowledgeReviewStatus.CANDIDATE.value:
                raise ResourceConflictError(details={"reason": "method_model_not_candidate"})
            members = await uow.method_models.list_members_for_update(model.id)
            displayed = tuple(
                _snapshot(item)
                for identifier in command.displayed_member_ids
                for item in members
                if item.method_element_id == identifier
            )
            if len(displayed) != len(command.displayed_member_ids):
                raise ResourceConflictError(details={"reason": "section_display_incomplete"})
            all_snapshots = tuple(_snapshot(item) for item in members)
            if member_set_fingerprint(displayed) != command.displayed_member_set_fingerprint:
                raise ResourceConflictError(details={"reason": "section_fingerprint_stale"})
            specification = MethodReviewPolicy.prepare_section_review(
                review_section=command.review_section,
                composition_members=all_snapshots,
                displayed_members=displayed,
                outcome=command.outcome,
                rationale=rationale,
            )
            prior = await uow.method_models.get_current_section_review_for_update(
                model.id, command.review_section.value
            )
            if prior is not None:
                raise ResourceConflictError(details={"reason": "section_already_reviewed"})
            accepted: list[UUID] = []
            accepted_links: list[UUID] = []
            refreshed_members = list(members)
            for element_id in specification.member_ids_to_accept:
                current = next(
                    item.element for item in members if item.method_element_id == element_id
                )
                updated = await self._accept_element(uow, current)
                refreshed_members = [
                    replace(item, element=updated) if item.method_element_id == updated.id else item
                    for item in refreshed_members
                ]
                accepted.append(updated.id)
            section_element_ids = tuple(
                item.method_element_id
                for item in refreshed_members
                if item.review_section == command.review_section.value
            )
            for link in await uow.method_models.list_object_links(section_element_ids):
                if link.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value:
                    raise ResourceConflictError(
                        details={"reason": "section_relationship_not_current"}
                    )
                if link.review_status == KnowledgeReviewStatus.NEEDS_REVISION.value:
                    raise ResourceConflictError(
                        details={"reason": "section_relationship_unresolved"}
                    )
                if link.review_status == KnowledgeReviewStatus.CANDIDATE.value:
                    updated_link = await uow.method_models.update_object_link_review_lifecycle(
                        link.id,
                        expected_version=link.version,
                        expected_review_status=link.review_status,
                        expected_lifecycle_status=link.lifecycle_status,
                        review_status=KnowledgeReviewStatus.ACCEPTED.value,
                        lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
                    )
                    accepted_links.append(updated_link.id)
            assessment = await self._refresh_assessment(uow, model, tuple(refreshed_members))
            review_id = uuid4()
            event_id = uuid4()
            event = await self._append_event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                event_id=event_id,
                inputs=(_model_ref(model),),
                outputs=(
                    {"type": "method_model_section_review", "id": str(review_id)},
                    _model_ref(model),
                    _assessment_ref(assessment),
                    {"type": "accepted_method_elements", "ids": [str(item) for item in accepted]},
                    {
                        "type": "accepted_method_object_links",
                        "ids": [str(item) for item in accepted_links],
                    },
                ),
            )
            review = await uow.method_models.add_section_review(
                NewMethodModelSectionReview(
                    id=review_id,
                    method_model_id=model.id,
                    review_section=command.review_section.value,
                    version=1,
                    outcome=specification.outcome.value,
                    member_set_fingerprint=specification.member_set_fingerprint,
                    reviewer_id=self._actor.actor_id,
                    rationale=rationale,
                    operation_event_id=event.id,
                    supersedes_id=None,
                )
            )
            reviews = (*await uow.method_models.list_current_section_reviews(model.id),)
            ready, _ = _readiness(model, tuple(refreshed_members), reviews, assessment)
            await uow.commit()
            return MethodSectionCommandResult(
                model, review, tuple(accepted), assessment, ready, event.id, False
            )

    async def accept_model(self, command: AcceptMethodModelCommand) -> MethodModelCommandResult:
        rationale = _rationale(command.rationale)
        operation_type = "method_model.accept"
        identity = {
            "policy_version": METHOD_REVIEW_POLICY_VERSION,
            "method_model_id": str(command.method_model_id),
            "expected_version": command.expected_version,
            "expected_review_status": command.expected_review_status.value,
            "expected_lifecycle_status": command.expected_lifecycle_status.value,
            "expected_composition_fingerprint": command.expected_composition_fingerprint,
            "rationale": rationale,
        }
        fingerprint = _fingerprint(identity)
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._model_replay(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                return replay
            model = await self._locked_model(uow, command.method_model_id)
            _expected_model(
                model,
                command.expected_version,
                command.expected_review_status,
                command.expected_lifecycle_status,
                command.expected_composition_fingerprint,
            )
            members = await uow.method_models.list_members_for_update(model.id)
            reviews = await uow.method_models.list_current_section_reviews(
                model.id, for_update=True
            )
            MethodReviewPolicy.validate_model_acceptance(
                composition_members=tuple(_snapshot(item) for item in members),
                current_section_reviews=tuple(_review_snapshot(item) for item in reviews),
            )
            assessment = await self._refresh_assessment(uow, model, members)
            if assessment.outcome == "insufficient":
                raise ResourceConflictError(
                    details={"reason": "method_model_assessment_insufficient"}
                )
            existing = await uow.method_models.get_accepted_current_model_for_update(
                model.research_question_id
            )
            supersedes_id: UUID | None = None
            if existing is not None and existing.id != model.id:
                if existing.lineage_id != model.lineage_id or model.derived_from_id is None:
                    raise ResourceConflictError(details={"reason": "accepted_method_model_exists"})
                await uow.method_models.update_model_review_lifecycle(
                    existing.id,
                    expected_version=existing.version,
                    expected_review_status=existing.review_status,
                    expected_lifecycle_status=existing.lifecycle_status,
                    review_status=KnowledgeReviewStatus.ACCEPTED.value,
                    lifecycle_status=KnowledgeLifecycleStatus.SUPERSEDED.value,
                )
                supersedes_id = existing.id
            updated = await uow.method_models.update_model_review_lifecycle(
                model.id,
                expected_version=model.version,
                expected_review_status=model.review_status,
                expected_lifecycle_status=model.lifecycle_status,
                review_status=KnowledgeReviewStatus.ACCEPTED.value,
                lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
                supersedes_id=supersedes_id,
            )
            event = await self._append_event(
                uow,
                operation_type=operation_type,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(_model_ref(model), *(_section_ref(item) for item in reviews)),
                outputs=(_model_ref(updated), _assessment_ref(assessment)),
            )
            if existing is not None and existing.id != updated.id:
                await self._staleness_hook.artifact_changed(
                    uow=uow,
                    project_id=command.project_id,
                    cause_type="method_model",
                    before_id=existing.id,
                    after_id=updated.id,
                    operation_type="method_model.replaced",
                )
            await uow.commit()
            return MethodModelCommandResult(updated, assessment, event.id, False)

    async def _append_model_revision(
        self,
        uow: UnitOfWork,
        predecessor: MethodModelData,
        members: tuple[MethodModelMemberData, ...],
    ) -> MethodModelData:
        latest = await uow.method_models.latest_model_lineage_for_update(predecessor.lineage_id)
        if latest is None or latest.id != predecessor.id:
            raise ResourceConflictError(details={"reason": "method_model_revision_not_latest"})
        model_id = uuid4()
        snapshots = tuple(_snapshot(item) for item in members)
        revised = await uow.method_models.add_candidate_model(
            NewMethodModelCandidate(
                id=model_id,
                project_id=predecessor.project_id,
                research_question_id=predecessor.research_question_id,
                lineage_id=predecessor.lineage_id,
                version=predecessor.version + 1,
                name=predecessor.name,
                objective=predecessor.objective,
                confidence=None,
                composition_fingerprint=method_composition_fingerprint(snapshots),
                generation_record_id=None,
                derived_from_id=predecessor.id,
                created_by_type="human",
                created_by_id=self._actor.actor_id,
            )
        )
        for item in members:
            await uow.method_models.add_member(
                NewMethodModelMember(
                    id=uuid4(),
                    method_model_id=revised.id,
                    method_element_id=item.method_element_id,
                    review_section=item.review_section,
                    member_role=item.member_role,
                    sequence_index=item.sequence_index,
                    required=item.required,
                    inclusion_rationale=item.inclusion_rationale,
                )
            )
        return revised

    async def _accept_element(
        self, uow: UnitOfWork, element: MethodElementData
    ) -> MethodElementData:
        predecessor = await uow.method_models.get_accepted_element_lineage_for_update(
            element.lineage_id
        )
        if predecessor is None or predecessor.id == element.id:
            return await uow.method_models.update_element_review_lifecycle(
                element.id,
                expected_version=element.version,
                expected_review_status=element.review_status,
                expected_lifecycle_status=element.lifecycle_status,
                review_status=KnowledgeReviewStatus.ACCEPTED.value,
                lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
            )
        if element.derived_from_id is None:
            raise ResourceConflictError(
                details={"reason": "method_element_revision_lineage_invalid"}
            )
        await uow.method_models.update_element_review_lifecycle(
            predecessor.id,
            expected_version=predecessor.version,
            expected_review_status=predecessor.review_status,
            expected_lifecycle_status=predecessor.lifecycle_status,
            review_status=KnowledgeReviewStatus.ACCEPTED.value,
            lifecycle_status=KnowledgeLifecycleStatus.SUPERSEDED.value,
        )
        return await uow.method_models.update_element_review_lifecycle(
            element.id,
            expected_version=element.version,
            expected_review_status=element.review_status,
            expected_lifecycle_status=element.lifecycle_status,
            review_status=KnowledgeReviewStatus.ACCEPTED.value,
            lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
            supersedes_id=predecessor.id,
        )

    async def _refresh_assessment(
        self,
        uow: UnitOfWork,
        model: MethodModelData,
        members: tuple[MethodModelMemberData, ...],
    ) -> MethodModelAssessmentData:
        assessed_members: list[InitialMethodMember] = []
        for member in members:
            payload = member.subtype_payload
            symbol_type = (
                MethodSymbolType(str(payload["symbol_type"]))
                if member.element.element_type == MethodElementType.SYMBOL.value
                else None
            )
            endpoint = method_element_endpoint_type(
                element_type=MethodElementType(member.element.element_type),
                symbol_type=symbol_type,
            )
            links = await uow.method_models.list_evidence_links(member.element.id)
            grounding = assess_method_evidence(
                project_id=model.project_id,
                element_id=member.element.id,
                element_version=member.element.version,
                endpoint_type=endpoint,
                links=links,
            )
            assessed_members.append(
                InitialMethodMember(
                    element_id=member.element.id,
                    element_version=member.element.version,
                    required=member.required,
                    provenance_complete=bool(links),
                    endpoint_type=endpoint,
                    grounding=grounding,
                )
            )
        composition = method_composition_fingerprint(tuple(_snapshot(item) for item in members))
        specification = assess_initial_method_model(
            method_model_id=model.id,
            composition_fingerprint=composition,
            objective_context_fingerprint=_fingerprint(
                {
                    "research_question_id": str(model.research_question_id),
                    "objective": model.objective,
                }
            ),
            members=tuple(assessed_members),
        )
        persisted = await uow.method_models.add_model_assessment(
            NewMethodModelAssessment(
                id=uuid4(),
                method_model_id=model.id,
                composition_fingerprint=specification.composition_fingerprint,
                member_grounding_fingerprint=specification.member_grounding_fingerprint,
                source_evidence_fingerprint=specification.source_evidence_fingerprint,
                repository_evidence_fingerprint=specification.repository_evidence_fingerprint,
                unsupported_required_count=specification.unsupported_required_count,
                mixed_evidence_count=specification.mixed_evidence_count,
                contradicted_count=specification.contradicted_count,
                missing_provenance_count=specification.missing_provenance_count,
                unresolved_scientific_ambiguity_count=specification.unresolved_scientific_ambiguity_count,
                outcome=specification.outcome.value,
                rationale=specification.rationale,
                generation_record_id=None,
            )
        )
        await uow.method_models.set_current_assessment(
            method_model_id=model.id, assessment_id=persisted.id
        )
        return persisted

    async def _refresh_element_grounding(
        self, uow: UnitOfWork, member: MethodModelMemberData
    ) -> MethodElementData:
        symbol_type = (
            MethodSymbolType(str(member.subtype_payload["symbol_type"]))
            if member.element.element_type == MethodElementType.SYMBOL.value
            else None
        )
        endpoint = method_element_endpoint_type(
            element_type=MethodElementType(member.element.element_type),
            symbol_type=symbol_type,
        )
        links = await uow.method_models.list_evidence_links(member.element.id)
        grounding = assess_method_evidence(
            project_id=member.element.project_id,
            element_id=member.element.id,
            element_version=member.element.version,
            endpoint_type=endpoint,
            links=links,
        )
        grounding_id = uuid4()
        await uow.method_models.add_grounding_assessment(
            NewMethodGroundingAssessment(
                id=grounding_id,
                project_id=member.element.project_id,
                method_element_id=member.element.id,
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
        return await uow.method_models.set_element_grounding_cache(
            element_id=member.element.id,
            assessment_id=grounding_id,
            grounding_status=grounding.status.value,
        )

    async def _assessment(
        self, uow: UnitOfWork, model: MethodModelData
    ) -> MethodModelAssessmentData:
        if model.current_assessment_id is None:
            raise InternalError(details={"reason": "method_model_assessment_missing"})
        assessment = await uow.method_models.get_assessment_exact(model.current_assessment_id)
        if assessment is None:
            raise InternalError(details={"reason": "method_model_assessment_missing"})
        return assessment

    async def _locked_model(self, uow: UnitOfWork, model_id: UUID) -> MethodModelData:
        model = await uow.method_models.get_model_for_update(model_id)
        if model is None:
            raise ResourceNotFoundError(details={"resource": "method_model"})
        return model

    async def _model(self, uow: UnitOfWork, model_id: UUID) -> MethodModelData:
        model = await uow.method_models.get_model_exact(model_id)
        if model is None:
            raise ResourceNotFoundError(details={"resource": "method_model"})
        return model

    async def _append_event(
        self,
        uow: UnitOfWork,
        *,
        operation_type: str,
        command_id: UUID,
        fingerprint: str,
        rationale: str,
        request_id: str | None,
        inputs: tuple[dict[str, object], ...],
        outputs: tuple[dict[str, object], ...],
        event_id: UUID | None = None,
    ) -> OperationEventData:
        return await uow.events.append(
            NewOperationEvent(
                id=event_id or uuid4(),
                project_id=uow.project_id,
                workflow_run_id=None,
                operation_type=operation_type,
                created_by_type="human",
                created_by_id=self._actor.actor_id,
                input_refs=inputs,
                output_refs=outputs,
                reason=rationale,
                idempotency_key=str(command_id),
                input_fingerprint=fingerprint,
                request_id=request_id,
            )
        )

    async def _event(
        self, uow: UnitOfWork, operation_type: str, command_id: UUID, fingerprint: str
    ) -> OperationEventData | None:
        event = await uow.events.get_by_idempotency_key(operation_type, str(command_id))
        if event is not None and event.input_fingerprint != fingerprint:
            raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
        return event

    async def _element_replay(
        self,
        uow: UnitOfWork,
        operation_type: str,
        command_id: UUID,
        fingerprint: str,
        model_id: UUID,
    ) -> MethodElementCommandResult | None:
        event = await self._event(uow, operation_type, command_id, fingerprint)
        if event is None:
            return None
        element = await uow.method_models.get_element_exact(_output_id(event, "method_element"))
        model = await uow.method_models.get_model_exact(model_id)
        assessment = await uow.method_models.get_assessment_exact(
            _output_id(event, "method_model_assessment")
        )
        if element is None or model is None or assessment is None:
            raise InternalError(details={"reason": "method_review_replay_target_missing"})
        await uow.rollback()
        return MethodElementCommandResult(element, model, assessment, event.id, True)

    async def _composition_replay(
        self, uow: UnitOfWork, operation_type: str, command_id: UUID, fingerprint: str
    ) -> MethodCompositionCommandResult | None:
        event = await self._event(uow, operation_type, command_id, fingerprint)
        if event is None:
            return None
        model = await uow.method_models.get_model_exact(_output_id(event, "method_model"))
        assessment = await uow.method_models.get_assessment_exact(
            _output_id(event, "method_model_assessment")
        )
        element_id = _optional_output_id(event, "method_element")
        element = (
            None if element_id is None else await uow.method_models.get_element_exact(element_id)
        )
        if model is None or assessment is None or (element_id is not None and element is None):
            raise InternalError(details={"reason": "method_review_replay_target_missing"})
        await uow.rollback()
        return MethodCompositionCommandResult(model, element, assessment, event.id, True)

    async def _evidence_replay(
        self, uow: UnitOfWork, operation_type: str, command_id: UUID, fingerprint: str
    ) -> MethodEvidenceCommandResult | None:
        event = await self._event(uow, operation_type, command_id, fingerprint)
        if event is None:
            return None
        model = await uow.method_models.get_model_exact(_output_id(event, "method_model"))
        element = await uow.method_models.get_element_exact(_output_id(event, "method_element"))
        assessment = await uow.method_models.get_assessment_exact(
            _output_id(event, "method_model_assessment")
        )
        evidence_id = _output_id(event, "method_evidence")
        element_links = (
            () if element is None else await uow.method_models.list_evidence_links(element.id)
        )
        evidence = next((item for item in element_links if item.id == evidence_id), None)
        if model is None or element is None or assessment is None or evidence is None:
            raise InternalError(details={"reason": "method_review_replay_target_missing"})
        await uow.rollback()
        return MethodEvidenceCommandResult(evidence, element, model, assessment, event.id, True)

    async def _section_replay(
        self, uow: UnitOfWork, operation_type: str, command_id: UUID, fingerprint: str
    ) -> MethodSectionCommandResult | None:
        event = await self._event(uow, operation_type, command_id, fingerprint)
        if event is None:
            return None
        review_id = _output_id(event, "method_model_section_review")
        model = await uow.method_models.get_model_exact(_output_id(event, "method_model"))
        assessment = await uow.method_models.get_assessment_exact(
            _output_id(event, "method_model_assessment")
        )
        reviews = await uow.method_models.list_current_section_reviews(
            _output_id(event, "method_model")
        )
        review = next((item for item in reviews if item.id == review_id), None)
        accepted_ids = _output_ids(event, "accepted_method_elements")
        if model is None or assessment is None or review is None:
            raise InternalError(details={"reason": "method_review_replay_target_missing"})
        members = await uow.method_models.list_members(model.id)
        ready, _ = _readiness(model, members, reviews, assessment)
        await uow.rollback()
        return MethodSectionCommandResult(
            model, review, accepted_ids, assessment, ready, event.id, True
        )

    async def _model_replay(
        self, uow: UnitOfWork, operation_type: str, command_id: UUID, fingerprint: str
    ) -> MethodModelCommandResult | None:
        event = await self._event(uow, operation_type, command_id, fingerprint)
        if event is None:
            return None
        model = await uow.method_models.get_model_exact(_output_id(event, "method_model"))
        assessment = await uow.method_models.get_assessment_exact(
            _output_id(event, "method_model_assessment")
        )
        if model is None or assessment is None:
            raise InternalError(details={"reason": "method_review_replay_target_missing"})
        await uow.rollback()
        return MethodModelCommandResult(model, assessment, event.id, True)

    def _require_owned(self, project: ProjectData | None) -> None:
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})


def _rationale(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > RATIONALE_MAX:
        raise DomainValidationError(details={"field": "rationale", "reason": "invalid"})
    return normalized


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _member(members: tuple[MethodModelMemberData, ...], element_id: UUID) -> MethodModelMemberData:
    found = next((item for item in members if item.method_element_id == element_id), None)
    if found is None:
        raise ResourceNotFoundError(details={"resource": "method_model_member"})
    return found


def _snapshot(member: MethodModelMemberData) -> MethodMemberSnapshot:
    element = member.element
    return MethodMemberSnapshot(
        method_element_id=element.id,
        element_lineage_id=element.lineage_id,
        element_version=element.version,
        review_section=MethodReviewSection(member.review_section),
        member_role=classify_method_membership(
            element_type=MethodElementType(element.element_type),
            component_type=(
                MethodComponentType(str(member.subtype_payload["component_type"]))
                if element.element_type == MethodElementType.COMPONENT.value
                else None
            ),
        ).member_role,
        sequence_index=member.sequence_index,
        required=member.required,
        inclusion_rationale=member.inclusion_rationale,
        review_status=KnowledgeReviewStatus(element.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(element.lifecycle_status),
        freshness_status=FreshnessStatus.CURRENT,
    ).validated()


def _review_snapshot(review: MethodModelSectionReviewData) -> SectionReviewSnapshot:
    return SectionReviewSnapshot(
        review_section=MethodReviewSection(review.review_section),
        outcome=SectionReviewOutcome(review.outcome),
        member_set_fingerprint=review.member_set_fingerprint,
        rationale=review.rationale,
    )


def _element_snapshot(element: MethodElementData) -> CandidateSnapshot:
    return CandidateSnapshot(
        id=element.id,
        project_id=element.project_id,
        lineage_id=element.lineage_id,
        version=element.version,
        review_status=KnowledgeReviewStatus(element.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(element.lifecycle_status),
    )


def _expected_element(
    element: MethodElementData,
    command: MethodElementTransitionCommand | MethodElementRevisionCommand,
) -> None:
    if (
        element.version != command.expected_version
        or element.review_status != command.expected_review_status.value
        or element.lifecycle_status != command.expected_lifecycle_status.value
    ):
        raise ResourceConflictError(details={"reason": "stale_method_element"})


def _expected_model_composition(model: MethodModelData, version: int, fingerprint: str) -> None:
    if model.version != version or model.composition_fingerprint != fingerprint:
        raise ResourceConflictError(details={"reason": "stale_method_model"})


def _expected_model(
    model: MethodModelData,
    version: int,
    review_status: KnowledgeReviewStatus,
    lifecycle_status: KnowledgeLifecycleStatus,
    fingerprint: str,
) -> None:
    _expected_model_composition(model, version, fingerprint)
    if (
        model.review_status != review_status.value
        or model.lifecycle_status != lifecycle_status.value
    ):
        raise ResourceConflictError(details={"reason": "stale_method_model"})


def _readiness(
    model: MethodModelData,
    members: tuple[MethodModelMemberData, ...],
    reviews: tuple[MethodModelSectionReviewData, ...],
    assessment: MethodModelAssessmentData,
) -> tuple[bool, str | None]:
    if model.review_status == KnowledgeReviewStatus.ACCEPTED.value:
        return True, None
    if assessment.outcome == "insufficient":
        return False, "method_model_assessment_insufficient"
    try:
        MethodReviewPolicy.validate_model_acceptance(
            composition_members=tuple(_snapshot(item) for item in members),
            current_section_reviews=tuple(_review_snapshot(item) for item in reviews),
        )
    except (DomainValidationError, ResourceConflictError) as error:
        reason = error.public_details.get("reason", "method_model_not_ready")
        return False, str(reason)
    return True, None


def _validated_subtype(
    element_type: MethodElementType, payload: dict[str, object]
) -> dict[str, object]:
    required: dict[MethodElementType, set[str]] = {
        MethodElementType.COMPONENT: {"component_type", "name", "description"},
        MethodElementType.EQUATION: {
            "label",
            "latex_expression",
            "normalized_expression",
            "explanation",
            "equation_type",
        },
        MethodElementType.SYMBOL: {
            "symbol_type",
            "symbol",
            "canonical_name",
            "definition",
            "units",
            "domain_or_data_type",
            "constraints",
            "role",
            "variable_role",
            "parameter_role",
            "value_or_range",
        },
        MethodElementType.ALGORITHM_STEP: {
            "sequence_index",
            "title",
            "action",
            "conditions",
        },
        MethodElementType.DATA_FLOW: {
            "source_element_id",
            "target_element_id",
            "transformation",
            "data_type_or_shape",
        },
        MethodElementType.VALIDITY_CONDITION: {
            "statement",
            "condition_type",
            "consequence_if_violated",
        },
    }
    expected = required[element_type]
    if set(payload) != expected:
        raise DomainValidationError(details={"reason": "method_element_payload_shape_invalid"})
    for key, value in payload.items():
        if isinstance(value, str) and not value.strip():
            raise DomainValidationError(details={"field": key, "reason": "blank"})
    return dict(payload)


def _model_ref(model: MethodModelData) -> dict[str, object]:
    return {
        "type": "method_model",
        "id": str(model.id),
        "lineage_id": str(model.lineage_id),
        "version": model.version,
        "review_status": model.review_status,
        "lifecycle_status": model.lifecycle_status,
        "composition_fingerprint": model.composition_fingerprint,
    }


def _element_ref(element: MethodElementData) -> dict[str, object]:
    return {
        "type": "method_element",
        "id": str(element.id),
        "lineage_id": str(element.lineage_id),
        "version": element.version,
        "review_status": element.review_status,
        "lifecycle_status": element.lifecycle_status,
    }


def _evidence_ref(evidence: MethodEvidenceLinkData) -> dict[str, object]:
    return {
        "type": "method_evidence",
        "id": str(evidence.id),
        "lineage_id": str(evidence.lineage_id),
        "version": evidence.version,
        "method_element_id": str(evidence.method_element_id),
        "review_status": evidence.review_status,
        "lifecycle_status": evidence.lifecycle_status,
    }


def _assessment_ref(assessment: MethodModelAssessmentData) -> dict[str, object]:
    return {
        "type": "method_model_assessment",
        "id": str(assessment.id),
        "outcome": assessment.outcome,
    }


def _section_ref(review: MethodModelSectionReviewData) -> dict[str, object]:
    return {
        "type": "method_model_section_review",
        "id": str(review.id),
        "review_section": review.review_section,
        "version": review.version,
        "member_set_fingerprint": review.member_set_fingerprint,
    }


def _output_id(event: OperationEventData, artifact_type: str) -> UUID:
    identifier = _optional_output_id(event, artifact_type)
    if identifier is None:
        raise InternalError(details={"reason": "method_review_replay_output_missing"})
    return identifier


def _optional_output_id(event: OperationEventData, artifact_type: str) -> UUID | None:
    for reference in reversed(event.output_refs):
        value = reference.get("id")
        if reference.get("type") == artifact_type and isinstance(value, str):
            return UUID(value)
    return None


def _output_ids(event: OperationEventData, artifact_type: str) -> tuple[UUID, ...]:
    for reference in reversed(event.output_refs):
        values = reference.get("ids")
        if reference.get("type") == artifact_type and isinstance(values, list):
            return tuple(UUID(value) for value in values if isinstance(value, str))
    return ()
