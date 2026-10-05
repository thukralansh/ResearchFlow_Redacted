"""Transactional human review for immutable paper-code mapping versions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, Protocol
from uuid import UUID, uuid4

from researchflow.domain.actor import ActorContext
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.lifecycle import (
    CandidateCommand,
    CandidateSnapshot,
    VersionSnapshot,
    accept_revision,
    new_version_specification,
)
from researchflow.domain.mapping_lifecycle import (
    MAPPING_REVIEW_POLICY_VERSION,
    MappingLifecyclePolicy,
    MappingSemanticPayload,
)
from researchflow.domain.mappings import validate_mapping_compatibility
from researchflow.domain.method_catalog import FreshnessStatus
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType
from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewPaperCodeMapping,
    OperationEventData,
    PaperCodeMappingData,
    ProjectData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

RATIONALE_MAX: Final = 4_000
_ABSENCE_MARKER: Final = "Bounded-scope limitation:"


class MappingReviewStalenessHook(Protocol):
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


class NoOpMappingReviewStalenessHook:
    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork | None = None,
        project_id: UUID,
        cause_type: str = "paper_code_mapping",
        lineage_id: UUID | None = None,
        before_id: UUID,
        after_id: UUID,
        operation_type: str,
    ) -> None:
        del uow, project_id, cause_type, lineage_id, before_id, after_id, operation_type


@dataclass(frozen=True, slots=True)
class MappingTransitionCommand:
    project_id: UUID
    mapping_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class MappingRevisionCommand:
    project_id: UUID
    mapping_id: UUID
    command_id: UUID
    expected_version: int
    expected_review_status: KnowledgeReviewStatus
    expected_lifecycle_status: KnowledgeLifecycleStatus
    rationale: str
    paper_anchor_type: PaperAnchorType
    anchor_id: UUID
    repository_component_id: UUID | None
    mapping_type: PaperCodeMappingType
    explanation: str
    confidence: Decimal | None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptMappingRevisionCommand:
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
class MappingReviewResult:
    mapping: PaperCodeMappingData
    predecessor: PaperCodeMappingData | None
    event_id: UUID
    replayed: bool


class MappingReviewService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        staleness_hook: MappingReviewStalenessHook | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._staleness_hook = staleness_hook or NoOpMappingReviewStalenessHook()

    async def accept(self, command: MappingTransitionCommand) -> MappingReviewResult:
        return await self._transition(command, CandidateCommand.ACCEPT)

    async def reject(self, command: MappingTransitionCommand) -> MappingReviewResult:
        return await self._transition(command, CandidateCommand.REJECT)

    async def request_revision(self, command: MappingTransitionCommand) -> MappingReviewResult:
        return await self._transition(command, CandidateCommand.REQUEST_REVISION)

    async def challenge(self, command: MappingTransitionCommand) -> MappingReviewResult:
        return await self._transition(command, CandidateCommand.CHALLENGE)

    async def reaffirm(self, command: MappingTransitionCommand) -> MappingReviewResult:
        return await self._transition(command, CandidateCommand.REAFFIRM)

    async def _transition(
        self, command: MappingTransitionCommand, action: CandidateCommand
    ) -> MappingReviewResult:
        rationale = _rationale(command.rationale)
        operation_type = f"paper_code_mapping.{action.value}"
        fingerprint = _fingerprint(
            {
                "policy_version": MAPPING_REVIEW_POLICY_VERSION,
                "mapping_id": str(command.mapping_id),
                "expected_version": command.expected_version,
                "expected_review_status": command.expected_review_status.value,
                "expected_lifecycle_status": command.expected_lifecycle_status.value,
                "rationale": rationale,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._replay(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            current = await uow.mappings.get_exact_for_update(command.mapping_id)
            _require_expected(current, command)
            assert current is not None
            await _validate_persisted_endpoints(uow, current)
            transition = MappingLifecyclePolicy.transition(
                _snapshot(current), action, command_id=command.command_id
            )
            updated = await uow.mappings.update_review_lifecycle(
                current.id,
                expected_version=current.version,
                expected_review_status=current.review_status,
                expected_lifecycle_status=current.lifecycle_status,
                review_status=transition.next_review_status.value,
                lifecycle_status=transition.next_lifecycle_status.value,
            )
            await self._changed(uow, current, updated, operation_type)
            event = await self._event(
                uow,
                operation_type,
                command.command_id,
                fingerprint,
                rationale,
                (current,),
                (updated,),
                command.request_id,
            )
            await uow.commit()
            return MappingReviewResult(updated, current, event.id, False)

    async def create_semantic_revision(
        self, command: MappingRevisionCommand
    ) -> MappingReviewResult:
        rationale = _rationale(command.rationale)
        proposed = MappingSemanticPayload(
            command.paper_anchor_type,
            command.anchor_id,
            command.repository_component_id,
            command.mapping_type,
            command.explanation,
            command.confidence,
        ).validated()
        operation_type = "paper_code_mapping.semantic_revision"
        fingerprint = _fingerprint(
            {
                "policy_version": MAPPING_REVIEW_POLICY_VERSION,
                "mapping_id": str(command.mapping_id),
                "expected_version": command.expected_version,
                "expected_review_status": command.expected_review_status.value,
                "expected_lifecycle_status": command.expected_lifecycle_status.value,
                "rationale": rationale,
                "semantic_payload": _payload_identity(proposed),
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._replay(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            current = await uow.mappings.get_exact_for_update(command.mapping_id)
            _require_expected(current, command)
            assert current is not None
            proposed = MappingLifecyclePolicy.require_semantic_revision(
                _semantic_payload(current), proposed
            )
            await _validate_revision_endpoints(uow, current, proposed)
            lineage = await uow.mappings.list_lineage_for_update(current.lineage_id)
            next_version = max(item.version for item in lineage) + 1
            specification = new_version_specification(
                VersionSnapshot(
                    current.id, current.project_id, current.lineage_id, current.version
                ),
                new_id=uuid4(),
                project_id=current.project_id,
                lineage_id=current.lineage_id,
                version=next_version,
                latest_lineage_version=next_version - 1,
            )
            fields = _anchor_fields(proposed.paper_anchor_type, proposed.anchor_id)
            revision = await uow.mappings.add(
                NewPaperCodeMapping(
                    id=specification.id,
                    project_id=current.project_id,
                    repository_id=current.repository_id,
                    repository_snapshot_id=current.repository_snapshot_id,
                    repository_inspection_run_id=current.repository_inspection_run_id,
                    lineage_id=current.lineage_id,
                    version=specification.version,
                    paper_anchor_type=proposed.paper_anchor_type.value,
                    source_chunk_id=fields["source_chunk_id"],
                    knowledge_object_id=fields["knowledge_object_id"],
                    method_model_id=fields["method_model_id"],
                    method_component_id=fields["method_component_id"],
                    method_equation_id=fields["method_equation_id"],
                    method_symbol_id=fields["method_symbol_id"],
                    method_algorithm_step_id=fields["method_algorithm_step_id"],
                    method_data_flow_id=fields["method_data_flow_id"],
                    method_validity_condition_id=fields["method_validity_condition_id"],
                    repository_component_id=proposed.repository_component_id,
                    mapping_type=proposed.mapping_type.value,
                    explanation=proposed.explanation,
                    confidence=proposed.confidence,
                    generation_record_id=None,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    derived_from_id=specification.derived_from_id,
                )
            )
            await self._changed(uow, current, revision, operation_type)
            event = await self._event(
                uow,
                operation_type,
                command.command_id,
                fingerprint,
                rationale,
                (current,),
                (revision,),
                command.request_id,
            )
            await uow.commit()
            return MappingReviewResult(revision, current, event.id, False)

    async def accept_revision(self, command: AcceptMappingRevisionCommand) -> MappingReviewResult:
        rationale = _rationale(command.rationale)
        operation_type = "paper_code_mapping.accept_revision"
        fingerprint = _fingerprint(
            {
                "policy_version": MAPPING_REVIEW_POLICY_VERSION,
                "predecessor_id": str(command.predecessor_id),
                "replacement_id": str(command.replacement_id),
                "expected_predecessor_version": command.expected_predecessor_version,
                "expected_predecessor_lifecycle_status": (
                    command.expected_predecessor_lifecycle_status.value
                ),
                "expected_replacement_version": command.expected_replacement_version,
                "rationale": rationale,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            self._require_owned(await uow.projects.get())
            replay = await self._replay(uow, operation_type, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            predecessor = await uow.mappings.get_exact_for_update(command.predecessor_id)
            replacement = await uow.mappings.get_exact_for_update(command.replacement_id)
            if predecessor is None or replacement is None:
                raise ResourceNotFoundError(details={"resource": "paper_code_mapping"})
            if (
                predecessor.version != command.expected_predecessor_version
                or predecessor.review_status != KnowledgeReviewStatus.ACCEPTED.value
                or predecessor.lifecycle_status
                != command.expected_predecessor_lifecycle_status.value
                or replacement.version != command.expected_replacement_version
                or replacement.review_status != KnowledgeReviewStatus.CANDIDATE.value
                or replacement.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
            ):
                raise ResourceConflictError(details={"reason": "stale_paper_code_mapping"})
            lineage = await uow.mappings.list_lineage_for_update(predecessor.lineage_id)
            await _validate_persisted_endpoints(uow, replacement)
            plan = accept_revision(
                _snapshot(predecessor),
                _snapshot(replacement),
                command_id=command.command_id,
                lineage=tuple(_snapshot(item) for item in lineage),
            )
            prior = await uow.mappings.update_review_lifecycle(
                predecessor.id,
                expected_version=predecessor.version,
                expected_review_status=predecessor.review_status,
                expected_lifecycle_status=predecessor.lifecycle_status,
                review_status=KnowledgeReviewStatus.ACCEPTED.value,
                lifecycle_status=plan.previous_next_lifecycle_status.value,
            )
            accepted = await uow.mappings.update_review_lifecycle(
                replacement.id,
                expected_version=replacement.version,
                expected_review_status=replacement.review_status,
                expected_lifecycle_status=replacement.lifecycle_status,
                review_status=plan.replacement_next_review_status.value,
                lifecycle_status=plan.replacement_next_lifecycle_status.value,
                supersedes_id=plan.replacement_supersedes_id,
            )
            await self._changed(uow, predecessor, accepted, operation_type)
            event = await self._event(
                uow,
                operation_type,
                command.command_id,
                fingerprint,
                rationale,
                (predecessor, replacement),
                (prior, accepted),
                command.request_id,
            )
            await uow.commit()
            return MappingReviewResult(accepted, prior, event.id, False)

    async def _replay(
        self, uow: UnitOfWork, operation_type: str, command_id: UUID, fingerprint: str
    ) -> MappingReviewResult | None:
        event = await uow.events.get_by_idempotency_key(operation_type, str(command_id))
        if event is None:
            return None
        if event.input_fingerprint != fingerprint:
            raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
        mapping_id = _output_id(event)
        mapping = await uow.mappings.get_exact(mapping_id)
        if mapping is None:
            raise InternalError(details={"reason": "review_replay_target_missing"})
        predecessor_ref = _predecessor_reference(event)
        predecessor = None
        if predecessor_ref is not None:
            predecessor_id = UUID(str(predecessor_ref["id"]))
            persisted_predecessor = await uow.mappings.get_exact(predecessor_id)
            if persisted_predecessor is None:
                raise InternalError(details={"reason": "review_replay_target_missing"})
            predecessor = _mapping_at_reference(persisted_predecessor, predecessor_ref)
        return MappingReviewResult(_event_mapping(event, mapping), predecessor, event.id, True)

    async def _event(
        self,
        uow: UnitOfWork,
        operation_type: str,
        command_id: UUID,
        fingerprint: str,
        rationale: str,
        before: tuple[PaperCodeMappingData, ...],
        after: tuple[PaperCodeMappingData, ...],
        request_id: str | None,
    ) -> OperationEventData:
        return await uow.events.append(
            NewOperationEvent(
                id=uuid4(),
                project_id=uow.mappings.project_id,
                workflow_run_id=None,
                operation_type=operation_type,
                created_by_type="human",
                created_by_id=self._actor.actor_id,
                input_refs=tuple(_reference(item) for item in before),
                output_refs=tuple(_reference(item) for item in after),
                reason=rationale,
                idempotency_key=str(command_id),
                input_fingerprint=fingerprint,
                request_id=request_id,
            )
        )

    async def _changed(
        self,
        uow: UnitOfWork,
        before: PaperCodeMappingData,
        after: PaperCodeMappingData,
        operation_type: str,
    ) -> None:
        await self._staleness_hook.artifact_changed(
            uow=uow,
            project_id=before.project_id,
            cause_type="paper_code_mapping",
            before_id=before.id,
            after_id=after.id,
            operation_type=operation_type,
        )

    def _require_owned(self, project: ProjectData | None) -> None:
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})


def _require_expected(
    current: PaperCodeMappingData | None,
    command: MappingTransitionCommand | MappingRevisionCommand,
) -> None:
    if current is None:
        raise ResourceNotFoundError(details={"resource": "paper_code_mapping"})
    if (
        current.version != command.expected_version
        or current.review_status != command.expected_review_status.value
        or current.lifecycle_status != command.expected_lifecycle_status.value
    ):
        raise ResourceConflictError(details={"reason": "stale_paper_code_mapping"})


async def _validate_persisted_endpoints(uow: UnitOfWork, mapping: PaperCodeMappingData) -> None:
    await _validate_revision_endpoints(uow, mapping, _semantic_payload(mapping))


async def _validate_revision_endpoints(
    uow: UnitOfWork,
    current: PaperCodeMappingData,
    proposed: MappingSemanticPayload,
) -> None:
    inspection = await uow.repositories.get_inspection_run_exact(
        current.repository_inspection_run_id
    )
    if (
        inspection is None
        or inspection.status != "succeeded"
        or inspection.repository_snapshot_id != current.repository_snapshot_id
    ):
        raise ResourceConflictError(details={"reason": "mapping_inspection_not_current"})
    components = await uow.repositories.list_inspection_components(inspection.id)
    if proposed.repository_component_id is not None and not any(
        item.id == proposed.repository_component_id for item in components
    ):
        raise DomainValidationError(details={"reason": "mapping_component_invalid"})
    knowledge_type: str | None = None
    symbol_type: str | None = None
    anchor_exists = False
    if proposed.paper_anchor_type is PaperAnchorType.SOURCE_CHUNK:
        anchor_exists = await uow.sources.get_chunk_exact(proposed.anchor_id) is not None
    elif proposed.paper_anchor_type is PaperAnchorType.KNOWLEDGE_OBJECT:
        knowledge = await uow.knowledge.get_exact(proposed.anchor_id)
        anchor_exists = knowledge is not None
        knowledge_type = None if knowledge is None else knowledge.object_type
    elif proposed.paper_anchor_type is PaperAnchorType.METHOD_MODEL:
        anchor_exists = await uow.method_models.get_model_exact(proposed.anchor_id) is not None
    else:
        element = await uow.method_models.get_element_exact(proposed.anchor_id)
        expected_element_type = {
            PaperAnchorType.METHOD_COMPONENT: "component",
            PaperAnchorType.EQUATION: "equation",
            PaperAnchorType.METHOD_SYMBOL: "symbol",
            PaperAnchorType.ALGORITHM_STEP: "algorithm_step",
            PaperAnchorType.DATA_FLOW: "data_flow",
            PaperAnchorType.VALIDITY_CONDITION: "validity_condition",
        }[proposed.paper_anchor_type]
        anchor_exists = element is not None and element.element_type == expected_element_type
        if anchor_exists and proposed.paper_anchor_type is PaperAnchorType.METHOD_SYMBOL:
            payload = await uow.method_models.get_element_payload_exact(proposed.anchor_id)
            raw = None if payload is None else payload.get("symbol_type")
            symbol_type = raw if isinstance(raw, str) else None
    if not anchor_exists:
        raise DomainValidationError(details={"reason": "mapping_anchor_invalid"})
    validate_mapping_compatibility(
        anchor_type=proposed.paper_anchor_type,
        mapping_type=proposed.mapping_type,
        knowledge_object_type=knowledge_type,
        method_symbol_type=symbol_type,
    )
    if proposed.mapping_type is PaperCodeMappingType.ABSENT_FROM_REPO:
        prefix, marker, limitation = proposed.explanation.partition(_ABSENCE_MARKER)
        if not prefix.strip() or not marker or not limitation.strip():
            raise DomainValidationError(details={"reason": "mapping_absence_scope_invalid"})


def _semantic_payload(mapping: PaperCodeMappingData) -> MappingSemanticPayload:
    return MappingSemanticPayload(
        PaperAnchorType(mapping.paper_anchor_type),
        _anchor_id(mapping),
        mapping.repository_component_id,
        PaperCodeMappingType(mapping.mapping_type),
        mapping.explanation,
        mapping.confidence,
    )


def _anchor_id(mapping: PaperCodeMappingData) -> UUID:
    value = {
        PaperAnchorType.SOURCE_CHUNK: mapping.source_chunk_id,
        PaperAnchorType.KNOWLEDGE_OBJECT: mapping.knowledge_object_id,
        PaperAnchorType.METHOD_MODEL: mapping.method_model_id,
        PaperAnchorType.METHOD_COMPONENT: mapping.method_component_id,
        PaperAnchorType.EQUATION: mapping.method_equation_id,
        PaperAnchorType.METHOD_SYMBOL: mapping.method_symbol_id,
        PaperAnchorType.ALGORITHM_STEP: mapping.method_algorithm_step_id,
        PaperAnchorType.DATA_FLOW: mapping.method_data_flow_id,
        PaperAnchorType.VALIDITY_CONDITION: mapping.method_validity_condition_id,
    }[PaperAnchorType(mapping.paper_anchor_type)]
    if value is None:
        raise InternalError(details={"reason": "mapping_anchor_missing"})
    return value


def _anchor_fields(anchor_type: PaperAnchorType, anchor_id: UUID) -> dict[str, UUID | None]:
    names = {
        PaperAnchorType.SOURCE_CHUNK: "source_chunk_id",
        PaperAnchorType.KNOWLEDGE_OBJECT: "knowledge_object_id",
        PaperAnchorType.METHOD_MODEL: "method_model_id",
        PaperAnchorType.METHOD_COMPONENT: "method_component_id",
        PaperAnchorType.EQUATION: "method_equation_id",
        PaperAnchorType.METHOD_SYMBOL: "method_symbol_id",
        PaperAnchorType.ALGORITHM_STEP: "method_algorithm_step_id",
        PaperAnchorType.DATA_FLOW: "method_data_flow_id",
        PaperAnchorType.VALIDITY_CONDITION: "method_validity_condition_id",
    }
    fields: dict[str, UUID | None] = {name: None for name in names.values()}
    fields[names[anchor_type]] = anchor_id
    return fields


def _snapshot(mapping: PaperCodeMappingData) -> CandidateSnapshot:
    return CandidateSnapshot(
        id=mapping.id,
        project_id=mapping.project_id,
        lineage_id=mapping.lineage_id,
        version=mapping.version,
        review_status=KnowledgeReviewStatus(mapping.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(mapping.lifecycle_status),
        freshness_status=FreshnessStatus(mapping.freshness_status),
        derived_from_id=mapping.derived_from_id,
    )


def _reference(mapping: PaperCodeMappingData) -> dict[str, object]:
    return {
        "type": "paper_code_mapping",
        "id": str(mapping.id),
        "lineage_id": str(mapping.lineage_id),
        "version": mapping.version,
        "review_status": mapping.review_status,
        "lifecycle_status": mapping.lifecycle_status,
        "freshness_status": mapping.freshness_status,
    }


def _output_id(event: OperationEventData) -> UUID:
    for reference in reversed(event.output_refs):
        value = reference.get("id")
        if reference.get("type") == "paper_code_mapping" and isinstance(value, str):
            return UUID(value)
    raise InternalError(details={"reason": "review_replay_output_missing"})


def _event_mapping(
    event: OperationEventData, persisted: PaperCodeMappingData
) -> PaperCodeMappingData:
    for reference in reversed(event.output_refs):
        if reference.get("type") != "paper_code_mapping" or reference.get("id") != str(
            persisted.id
        ):
            continue
        review = reference.get("review_status")
        lifecycle = reference.get("lifecycle_status")
        if isinstance(review, str) and isinstance(lifecycle, str):
            return replace(persisted, review_status=review, lifecycle_status=lifecycle)
    raise InternalError(details={"reason": "review_replay_output_missing"})


def _predecessor_reference(event: OperationEventData) -> dict[str, object] | None:
    output_mappings = [
        reference
        for reference in event.output_refs
        if reference.get("type") == "paper_code_mapping"
    ]
    if len(output_mappings) > 1:
        return output_mappings[-2]
    for reference in reversed(event.input_refs):
        if reference.get("type") == "paper_code_mapping":
            return reference
    return None


def _mapping_at_reference(
    persisted: PaperCodeMappingData, reference: dict[str, object]
) -> PaperCodeMappingData:
    review = reference.get("review_status")
    lifecycle = reference.get("lifecycle_status")
    freshness = reference.get("freshness_status")
    if (
        not isinstance(review, str)
        or not isinstance(lifecycle, str)
        or not isinstance(freshness, str)
    ):
        raise InternalError(details={"reason": "review_replay_output_missing"})
    return replace(
        persisted,
        review_status=review,
        lifecycle_status=lifecycle,
        freshness_status=freshness,
    )


def _payload_identity(payload: MappingSemanticPayload) -> dict[str, object]:
    return {
        "paper_anchor_type": payload.paper_anchor_type.value,
        "anchor_id": str(payload.anchor_id),
        "repository_component_id": (
            None
            if payload.repository_component_id is None
            else str(payload.repository_component_id)
        ),
        "mapping_type": payload.mapping_type.value,
        "explanation": payload.explanation,
        "confidence": None
        if payload.confidence is None
        else format(payload.confidence.normalize(), "f"),
    }


def _rationale(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > RATIONALE_MAX:
        raise DomainValidationError(details={"field": "rationale", "reason": "invalid"})
    return normalized


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
