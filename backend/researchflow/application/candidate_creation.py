"""Channel-neutral candidate creation without extraction workflow coupling."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.application.grounding import assess_persisted_evidence
from researchflow.domain.actor import ActorContext
from researchflow.domain.copilot_actions import (
    EvidenceCandidatePayload,
    KnowledgeCandidatePayload,
    ProposedEvidence,
)
from researchflow.domain.evidence import (
    EvidenceLinkRequest,
    EvidencePolicy,
    EvidenceSourceSelector,
    EvidenceTargetSelector,
)
from researchflow.domain.evidence_anchors import resolve_source_evidence_anchor
from researchflow.domain.knowledge_catalog import (
    STATEMENT_NORMALIZATION_VERSION,
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
    statement_fingerprint,
)
from researchflow.domain.provenance import (
    RepositoryComponentEvidenceProvenance,
    SourceChunkEvidenceProvenance,
)
from researchflow.domain.relationships import EndpointType
from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    EvidenceLinkData,
    GroundingAssessmentData,
    KnowledgeObjectData,
    NewEvidenceLink,
    NewGroundingAssessment,
    NewKnowledgeCandidate,
    NewOperationEvent,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

CANDIDATE_CREATION_POLICY_VERSION = "rf-channel-neutral-candidate-v1"
_EVIDENCE_MATERIALITY_VERSION = "rf-copilot-evidence-materiality-v1"
_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/copilot-candidates")


@dataclass(frozen=True, slots=True)
class CandidateCreationResult:
    knowledge_object: KnowledgeObjectData
    evidence: tuple[EvidenceLinkData, ...]
    grounding: GroundingAssessmentData
    event_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class EvidenceCandidateResult:
    evidence: EvidenceLinkData
    knowledge_object: KnowledgeObjectData
    grounding: GroundingAssessmentData
    event_id: UUID
    replayed: bool


class CandidateCreationService:
    """Persist only reviewable candidate state with exact existing provenance."""

    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def create_knowledge_candidate(
        self,
        *,
        project_id: UUID,
        generation_record_id: UUID,
        command_id: UUID,
        payload: KnowledgeCandidatePayload,
        request_id: str | None = None,
    ) -> CandidateCreationResult:
        fingerprint = _fingerprint(
            {"policy": CANDIDATE_CREATION_POLICY_VERSION, **payload.model_dump(mode="json")}
        )
        event_key = f"copilot-knowledge-candidate:{command_id}"
        candidate_id = uuid5(_NAMESPACE, f"knowledge:{command_id}")
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            replay = await uow.events.get_by_idempotency_key(
                "copilot.knowledge_candidate.created", event_key
            )
            if replay is not None:
                if replay.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                result = await self._knowledge_replay(uow, candidate_id, replay.id)
                await uow.rollback()
                return result
            generation = await uow.generations.get_exact(generation_record_id)
            if generation is None or generation.status != "succeeded":
                raise ResourceConflictError(details={"reason": "candidate_generation_invalid"})
            normalized_hash = statement_fingerprint(payload.statement)
            active = await uow.knowledge.list_by_normalized_identity(
                object_type=payload.object_type.value,
                statement_normalization_version=STATEMENT_NORMALIZATION_VERSION,
                normalized_statement_hash=normalized_hash,
            )
            if any(
                item.lifecycle_status == KnowledgeLifecycleStatus.CURRENT.value
                and item.review_status != KnowledgeReviewStatus.REJECTED.value
                for item in active
            ):
                raise ResourceConflictError(details={"reason": "candidate_duplicate_current"})
            subtype, interpretations = _subtype(
                payload.subtype.model_dump(mode="json", exclude_none=False)
            )
            candidate = await uow.knowledge.add_candidate(
                NewKnowledgeCandidate(
                    id=candidate_id,
                    project_id=project_id,
                    lineage_id=candidate_id,
                    version=1,
                    object_type=payload.object_type.value,
                    statement=payload.statement,
                    statement_normalization_version=STATEMENT_NORMALIZATION_VERSION,
                    normalized_statement_hash=normalized_hash,
                    origin=payload.origin.value,
                    epistemic_status=payload.epistemic_status.value,
                    confidence=payload.confidence,
                    subtype_payload=subtype,
                    ambiguity_interpretations=interpretations,
                    generation_record_id=generation_record_id,
                    created_by_type="ai",
                )
            )
            links = tuple(
                [
                    await self._add_evidence(
                        uow,
                        project_id=project_id,
                        target=candidate,
                        generation_record_id=generation_record_id,
                        command_id=command_id,
                        sequence=index,
                        evidence=item,
                    )
                    for index, item in enumerate(payload.evidence)
                ]
            )
            candidate, grounding = await self._ground(uow, candidate)
            output_refs: list[dict[str, object]] = [
                {"type": "knowledge_object", "id": str(candidate.id)}
            ]
            output_refs.extend({"type": "evidence_link", "id": str(item.id)} for item in links)
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"event:{command_id}"),
                    project_id=project_id,
                    workflow_run_id=None,
                    operation_type="copilot.knowledge_candidate.created",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"type": "generation_record", "id": str(generation_record_id)},),
                    output_refs=tuple(output_refs),
                    reason="Human explicitly saved a model-proposed candidate for review.",
                    idempotency_key=event_key,
                    input_fingerprint=fingerprint,
                    request_id=request_id,
                )
            )
            await uow.commit()
            return CandidateCreationResult(candidate, links, grounding, event.id, False)

    async def create_evidence_candidate(
        self,
        *,
        project_id: UUID,
        generation_record_id: UUID,
        command_id: UUID,
        payload: EvidenceCandidatePayload,
        request_id: str | None = None,
    ) -> EvidenceCandidateResult:
        fingerprint = _fingerprint(
            {"policy": CANDIDATE_CREATION_POLICY_VERSION, **payload.model_dump(mode="json")}
        )
        event_key = f"copilot-evidence-candidate:{command_id}"
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            replay = await uow.events.get_by_idempotency_key(
                "copilot.evidence_candidate.created", event_key
            )
            if replay is not None:
                if replay.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                evidence_id = _reference(replay.output_refs, "evidence_link")
                link = await uow.knowledge.get_evidence_link_exact(evidence_id)
                target = await uow.knowledge.get_exact(payload.knowledge_object_id)
                if link is None or target is None or target.grounding_assessment_id is None:
                    raise ResourceConflictError(details={"reason": "candidate_replay_incomplete"})
                grounding = await uow.knowledge.get_grounding_assessment(
                    target.grounding_assessment_id
                )
                if grounding is None:
                    raise ResourceConflictError(details={"reason": "candidate_replay_incomplete"})
                await uow.rollback()
                return EvidenceCandidateResult(link, target, grounding, replay.id, True)
            generation = await uow.generations.get_exact(generation_record_id)
            target = await uow.knowledge.get_exact(payload.knowledge_object_id)
            if generation is None or generation.status != "succeeded":
                raise ResourceConflictError(details={"reason": "candidate_generation_invalid"})
            if (
                target is None
                or target.version != payload.expected_version
                or target.review_status != payload.expected_review_status.value
                or target.lifecycle_status != payload.expected_lifecycle_status.value
            ):
                raise ResourceConflictError(details={"reason": "candidate_target_state_changed"})
            link = await self._add_evidence(
                uow,
                project_id=project_id,
                target=target,
                generation_record_id=generation_record_id,
                command_id=command_id,
                sequence=0,
                evidence=payload.evidence,
            )
            target, grounding = await self._ground(uow, target)
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"evidence-event:{command_id}"),
                    project_id=project_id,
                    workflow_run_id=None,
                    operation_type="copilot.evidence_candidate.created",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=(
                        {"type": "generation_record", "id": str(generation_record_id)},
                        {"type": "knowledge_object", "id": str(target.id)},
                    ),
                    output_refs=({"type": "evidence_link", "id": str(link.id)},),
                    reason="Human explicitly saved model-proposed evidence for independent review.",
                    idempotency_key=event_key,
                    input_fingerprint=fingerprint,
                    request_id=request_id,
                )
            )
            await uow.commit()
            return EvidenceCandidateResult(link, target, grounding, event.id, False)

    async def _add_evidence(
        self,
        uow: UnitOfWork,
        *,
        project_id: UUID,
        target: KnowledgeObjectData,
        generation_record_id: UUID,
        command_id: UUID,
        sequence: int,
        evidence: ProposedEvidence,
    ) -> EvidenceLinkData:
        link_id = uuid5(_NAMESPACE, f"evidence:{command_id}:{sequence}")
        source_chunk_id: UUID | None = None
        repository_component_id: UUID | None = None
        start: int | None = None
        end: int | None = None
        anchor_hash: str | None = None
        anchor_policy: str | None = None
        if evidence.source_kind == "source_chunk":
            chunk = await uow.sources.get_chunk_exact(evidence.source_id)
            run = (
                None
                if chunk is None
                else await uow.sources.get_processing_run_exact(chunk.processing_run_id)
            )
            source = None if run is None else await uow.sources.get_exact(run.source_id)
            if chunk is None or run is None or source is None or source.project_id != project_id:
                raise ResourceNotFoundError(details={"resource": "source_chunk"})
            anchor = resolve_source_evidence_anchor(
                chunk_text=chunk.chunk_text,
                chunk_start_offset=chunk.start_offset,
                proposed_excerpt=evidence.excerpt,
            )
            provenance: SourceChunkEvidenceProvenance | RepositoryComponentEvidenceProvenance
            provenance = SourceChunkEvidenceProvenance(
                project_id=project_id,
                source_id=source.id,
                processing_run_id=run.id,
                source_hash=run.source_hash,
                parser_name=run.parser_name,
                parser_version=run.parser_version,
                chunker_name=run.chunker_name,
                chunker_version=run.chunker_version,
                normalization_version=run.normalization_version,
                chunk_id=chunk.id,
                chunk_content_hash=chunk.content_hash,
                chunk_text=chunk.chunk_text,
                current=run.status == "succeeded",
            )
            source_chunk_id = chunk.id
            excerpt = anchor.excerpt
            start, end, anchor_hash, anchor_policy = (
                anchor.start_offset,
                anchor.end_offset,
                anchor.excerpt_hash,
                anchor.policy_version,
            )
        elif evidence.source_kind == "repository_component":
            component = await uow.repositories.get_component_exact(evidence.source_id)
            snapshot = (
                None
                if component is None
                else await uow.repositories.get_snapshot_exact(component.repository_snapshot_id)
            )
            repository = (
                None
                if snapshot is None
                else await uow.repositories.get_exact(snapshot.repository_id)
            )
            if (
                component is None
                or snapshot is None
                or repository is None
                or repository.project_id != project_id
                or component.repository_inspection_run_id is None
            ):
                raise ResourceNotFoundError(details={"resource": "repository_component"})
            provenance = RepositoryComponentEvidenceProvenance(
                project_id=project_id,
                repository_id=repository.id,
                repository_snapshot_id=snapshot.id,
                commit_sha=snapshot.commit_sha,
                inspection_run_id=component.repository_inspection_run_id,
                component_id=component.id,
                path=component.path,
                content_hash=component.content_hash,
                content_excerpt=component.content_excerpt,
                current=snapshot.lifecycle_status == "current",
            )
            repository_component_id = component.id
            excerpt = evidence.excerpt
        else:
            raise DomainValidationError(details={"reason": "copilot_evidence_kind_invalid"})
        validated = EvidencePolicy.validate(
            EvidenceLinkRequest(
                id=link_id,
                lineage_id=link_id,
                version=1,
                target=EvidenceTargetSelector(
                    project_id=project_id,
                    target_type=EndpointType(target.object_type),
                    target_version=target.version,
                    knowledge_object_id=target.id,
                ),
                source=EvidenceSourceSelector(
                    provenance=provenance,
                    source_chunk_id=source_chunk_id,
                    repository_component_id=repository_component_id,
                ),
                relation=evidence.relation,
                excerpt=excerpt,
                confidence=evidence.confidence,
                review_status=KnowledgeReviewStatus.CANDIDATE,
                lifecycle_status=KnowledgeLifecycleStatus.CURRENT,
                material=True,
                materiality_policy_version=_EVIDENCE_MATERIALITY_VERSION,
                objective_context_fingerprint=_fingerprint({"project_id": str(project_id)}),
            )
        )
        return await uow.knowledge.add_evidence_link(
            NewEvidenceLink(
                id=validated.id,
                project_id=project_id,
                lineage_id=validated.lineage_id,
                version=1,
                knowledge_object_id=target.id,
                source_chunk_id=source_chunk_id,
                repository_component_id=repository_component_id,
                evidence_relation=validated.relation.value,
                evidence_excerpt=validated.excerpt,
                confidence=validated.confidence,
                review_status=KnowledgeReviewStatus.CANDIDATE.value,
                lifecycle_status=KnowledgeLifecycleStatus.CURRENT.value,
                generation_record_id=generation_record_id,
                created_by_type="ai",
                source_anchor_start_offset=start,
                source_anchor_end_offset=end,
                source_anchor_hash=anchor_hash,
                source_anchor_policy_version=anchor_policy,
            )
        )

    async def _ground(
        self, uow: UnitOfWork, target: KnowledgeObjectData
    ) -> tuple[KnowledgeObjectData, GroundingAssessmentData]:
        grounding = assess_persisted_evidence(await uow.knowledge.list_evidence_links(target.id))
        assessment = await uow.knowledge.add_grounding_assessment(
            NewGroundingAssessment(
                id=uuid5(_NAMESPACE, f"grounding:{target.id}:{grounding.evidence_set_fingerprint}"),
                project_id=target.project_id,
                knowledge_object_id=target.id,
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
        updated = await uow.knowledge.set_grounding_cache(
            knowledge_object_id=target.id,
            grounding_assessment_id=assessment.id,
            grounding_status=assessment.status,
        )
        return updated, assessment

    async def _knowledge_replay(
        self, uow: UnitOfWork, candidate_id: UUID, event_id: UUID
    ) -> CandidateCreationResult:
        target = await uow.knowledge.get_exact(candidate_id)
        if target is None or target.grounding_assessment_id is None:
            raise ResourceConflictError(details={"reason": "candidate_replay_incomplete"})
        grounding = await uow.knowledge.get_grounding_assessment(target.grounding_assessment_id)
        if grounding is None:
            raise ResourceConflictError(details={"reason": "candidate_replay_incomplete"})
        return CandidateCreationResult(
            target,
            await uow.knowledge.list_evidence_links(target.id),
            grounding,
            event_id,
            True,
        )

    async def _owned(self, uow: UnitOfWork) -> None:
        project = await uow.projects.get()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})


def _subtype(value: dict[str, object]) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    raw = dict(value)
    interpretations_raw = raw.pop("interpretations", ())
    if not isinstance(interpretations_raw, (tuple, list)):
        raise DomainValidationError(details={"reason": "ambiguity_interpretations_invalid"})
    interpretations = tuple(
        {"id": uuid5(_NAMESPACE, f"interpretation:{_fingerprint(item)}"), **_plain(item)}
        for item in interpretations_raw
        if isinstance(item, dict)
    )
    if len(interpretations) != len(interpretations_raw):
        raise DomainValidationError(details={"reason": "ambiguity_interpretations_invalid"})
    return _plain(raw), interpretations


def _plain(value: dict[str, object]) -> dict[str, object]:
    return {str(key): item.value if isinstance(item, Enum) else item for key, item in value.items()}


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _reference(values: tuple[dict[str, object], ...], kind: str) -> UUID:
    for item in values:
        if item.get("type") == kind:
            return UUID(str(item["id"]))
    raise ResourceConflictError(details={"reason": "candidate_replay_incomplete"})
