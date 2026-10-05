""" derivation of reviewable project knowledge from bounded mapping evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.api.schemas.mapping_derivation import (
    DerivedItemResponse,
    MappingDerivationBatch,
    MappingDerivationResponse,
    MappingDerivedAmbiguity,
    MappingDerivedItem,
    MappingDerivedMethodEvidence,
    MappingDerivedMethodRevision,
    MappingDerivedRequirement,
    MappingDiscrepancy,
    RepositoryEvidenceReference,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.application.grounding import assess_persisted_evidence
from researchflow.application.mapping_generation import mapping_view
from researchflow.application.method_grounding import assess_method_evidence
from researchflow.domain.actor import ActorContext
from researchflow.domain.evidence import (
    EvidenceLinkRequest,
    EvidencePolicy,
    EvidenceSourceSelector,
    EvidenceTargetSelector,
    ValidatedEvidenceLink,
)
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
    statement_fingerprint,
)
from researchflow.domain.mapping_derivation import (
    MAPPING_DERIVATION_POLICY_VERSION,
    MAPPING_DERIVATION_PROMPT_VERSION,
    MAPPING_DERIVATION_SCHEMA_NAME,
    MAPPING_DERIVATION_SCHEMA_VERSION,
    MAPPING_DERIVATION_TASK_NAME,
    MAPPING_DISCREPANCY_POLICY_VERSION,
    MAPPING_MATERIALITY_POLICY_VERSION,
)
from researchflow.domain.method_catalog import (
    EvidenceRelation,
    MethodElementType,
    MethodEpistemicStatus,
    MethodMemberRole,
    MethodReviewSection,
    MethodSymbolType,
)
from researchflow.domain.method_review import MethodMemberSnapshot, method_composition_fingerprint
from researchflow.domain.method_validation import method_element_endpoint_type
from researchflow.domain.provenance import RepositoryComponentEvidenceProvenance
from researchflow.domain.relationships import RELATIONSHIP_POLICY_VERSION, EndpointType
from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.providers import (
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    GenerationRecordData,
    KnowledgeObjectData,
    MethodElementData,
    MethodModelData,
    MethodModelMemberData,
    NewEvidenceLink,
    NewGroundingAssessment,
    NewKnowledgeCandidate,
    NewMethodElementCandidate,
    NewMethodEvidenceLink,
    NewMethodGroundingAssessment,
    NewMethodModelCandidate,
    NewMethodModelMember,
    NewOperationEvent,
    OperationEventData,
    PaperCodeMappingData,
    ProjectData,
    RepositoryComponentData,
    RepositoryInspectionRunData,
    RepositorySnapshotData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "https://researchflow.local/ids/mapping-derivation")
_SUCCEEDED: Final = "mapping.derivation.succeeded"
_NOT_APPLICABLE: Final = "mapping.derivation.not_applicable"
_EVIDENCE_MATERIALITY_VERSION: Final = MAPPING_MATERIALITY_POLICY_VERSION
_STATEMENT_NORMALIZATION_VERSION: Final = "v1"


@dataclass(frozen=True, slots=True)
class DeriveMappingKnowledgeCommand:
    project_id: UUID
    workflow_run_id: UUID
    applicable: bool
    applicability_rationale: str
    repository_inspection_run_id: UUID | None
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class _DerivationContext:
    project: ProjectData
    question_id: UUID
    question_text: str
    question_scope: str
    method_model: MethodModelData
    method_members: tuple[MethodModelMemberData, ...]
    mental_model_id: UUID
    mental_model_fingerprint: str
    inspection: RepositoryInspectionRunData
    snapshot: RepositorySnapshotData
    authority_level: str
    mappings: tuple[PaperCodeMappingData, ...]
    components: tuple[RepositoryComponentData, ...]
    payload: dict[str, object]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _PersistedItem:
    client_id: str
    kind: str
    target_id: UUID
    material: bool
    materiality_reason: str
    source_mapping_ids: tuple[UUID, ...]
    evidence_link_ids: tuple[UUID, ...]

    def response(self) -> DerivedItemResponse:
        return DerivedItemResponse(
            client_id=self.client_id,
            kind=self.kind,
            target_id=self.target_id,
            material=self.material,
            materiality_reason=self.materiality_reason,
            source_mapping_ids=list(self.source_mapping_ids),
            evidence_link_ids=list(self.evidence_link_ids),
        )

    def event_ref(self) -> dict[str, object]:
        return {
            "type": self.kind,
            "id": str(self.target_id),
            "client_id": self.client_id,
            "material": self.material,
            "materiality_reason": self.materiality_reason,
            "source_mapping_ids": [str(item) for item in self.source_mapping_ids],
            "evidence_link_ids": [str(item) for item in self.evidence_link_ids],
        }


class MappingDerivationService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: StructuredGenerationService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def derive(self, command: DeriveMappingKnowledgeCommand) -> MappingDerivationResponse:
        rationale = command.applicability_rationale.strip()
        if not rationale:
            raise DomainValidationError(details={"reason": "applicability_rationale_required"})
        if not command.applicable:
            return await self._not_applicable(command, rationale)
        if command.repository_inspection_run_id is None:
            raise DomainValidationError(details={"reason": "inspection_run_required"})

        async with self._uow_factory(command.project_id) as uow:
            context = await _load_context(uow, self._actor, command)
            await uow.rollback()
        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=MAPPING_DERIVATION_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=MAPPING_DERIVATION_PROMPT_VERSION,
                schema_name=MAPPING_DERIVATION_SCHEMA_NAME,
                schema_version=MAPPING_DERIVATION_SCHEMA_VERSION,
                output_type=MappingDerivationBatch,
                messages=(
                    StructuredGenerationMessage(
                        role="user",
                        content=json.dumps(context.payload, sort_keys=True, separators=(",", ":")),
                    ),
                ),
                model_policy=command.model_policy,
                explicit_rerun=command.explicit_rerun,
            )
        )
        async with self._uow_factory(command.project_id) as uow:
            replay = await uow.events.get_by_idempotency_key(
                _SUCCEEDED, f"mapping-derivation:{outcome.generation_record.id}"
            )
            if replay is not None:
                response = await _replay_response(uow, outcome.generation_record, replay)
                await uow.rollback()
                return response
            current = await _load_context(uow, self._actor, command)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "mapping_derivation_stale_context"})
            items, proposal_model_id = await _persist_batch(
                uow=uow,
                command=command,
                context=current,
                generation=outcome.generation_record,
                batch=outcome.value,
            )
            mapping_refs: tuple[dict[str, object], ...] = tuple(
                {"type": "paper_code_mapping", "id": str(mapping.id)}
                for mapping in current.mappings
            )
            input_refs: tuple[dict[str, object], ...] = (
                {"type": "generation_record", "id": str(outcome.generation_record.id)},
                {"type": "repository_inspection_run", "id": str(current.inspection.id)},
                *mapping_refs,
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=_id(outcome.generation_record.id, "event"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_SUCCEEDED,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=input_refs,
                    output_refs=(
                        *(item.event_ref() for item in items),
                        *(
                            ()
                            if proposal_model_id is None
                            else (
                                {
                                    "type": "proposal_method_model",
                                    "id": str(proposal_model_id),
                                },
                            )
                        ),
                    ),
                    reason="Persisted reviewable mapping-derived consequences; no output was accepted.",
                    idempotency_key=f"mapping-derivation:{outcome.generation_record.id}",
                    input_fingerprint=current.fingerprint,
                    request_id=command.request_id,
                )
            )
            del event
            await uow.commit()
        return MappingDerivationResponse(
            outcome="applicable",
            generation_record_id=outcome.generation_record.id,
            proposal_method_model_id=proposal_model_id,
            replayed=outcome.replayed,
            derived_items=[item.response() for item in items],
            discrepancies=outcome.value.discrepancies,
            diagnostics=[item.model_dump(mode="json") for item in outcome.value.diagnostics],
        )

    async def _not_applicable(
        self, command: DeriveMappingKnowledgeCommand, rationale: str
    ) -> MappingDerivationResponse:
        if command.repository_inspection_run_id is not None:
            raise DomainValidationError(details={"reason": "not_applicable_inspection_forbidden"})
        fingerprint = _fingerprint(
            {
                "policy_version": MAPPING_DERIVATION_POLICY_VERSION,
                "project_id": str(command.project_id),
                "workflow_run_id": str(command.workflow_run_id),
                "rationale": rationale,
                "applicable": False,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            if await uow.workflows.get_exact(command.workflow_run_id) is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            existing = await uow.events.get_by_idempotency_key(
                _NOT_APPLICABLE, f"mapping-derivation-na:{command.workflow_run_id}"
            )
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                await uow.rollback()
                return MappingDerivationResponse(
                    outcome="not_applicable",
                    generation_record_id=None,
                    proposal_method_model_id=None,
                    replayed=True,
                    derived_items=[],
                    discrepancies=[],
                    diagnostics=[{"reason": rationale}],
                )
            await uow.events.append(
                NewOperationEvent(
                    id=_id(command.workflow_run_id, "not-applicable"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_NOT_APPLICABLE,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    reason=rationale,
                    idempotency_key=f"mapping-derivation-na:{command.workflow_run_id}",
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        return MappingDerivationResponse(
            outcome="not_applicable",
            generation_record_id=None,
            proposal_method_model_id=None,
            replayed=False,
            derived_items=[],
            discrepancies=[],
            diagnostics=[{"reason": rationale}],
        )


class MappingDerivationQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def detail(
        self, project_id: UUID, generation_record_id: UUID
    ) -> MappingDerivationResponse:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            generation = await uow.generations.get_exact(generation_record_id)
            if generation is None or generation.task_name != MAPPING_DERIVATION_TASK_NAME:
                raise ResourceNotFoundError(details={"resource": "mapping_derivation"})
            event = await uow.events.get_by_idempotency_key(
                _SUCCEEDED, f"mapping-derivation:{generation.id}"
            )
            if event is None:
                raise ResourceNotFoundError(details={"resource": "mapping_derivation"})
            response = await _replay_response(uow, generation, event)
            await uow.rollback()
            return response


async def _load_context(
    uow: UnitOfWork,
    actor: ActorContext,
    command: DeriveMappingKnowledgeCommand,
) -> _DerivationContext:
    project = await uow.projects.get()
    _require_owned(actor, project)
    workflow = await uow.workflows.get_exact(command.workflow_run_id)
    question = await uow.research_questions.get_current_primary()
    inspection = (
        None
        if command.repository_inspection_run_id is None
        else await uow.repositories.get_inspection_run_exact(command.repository_inspection_run_id)
    )
    if project is None or workflow is None or question is None:
        raise ResourceConflictError(details={"reason": "mapping_derivation_context_missing"})
    if workflow.primary_research_question_id != question.id:
        raise ResourceConflictError(details={"reason": "mapping_derivation_question_stale"})
    if inspection is None or inspection.status != "succeeded":
        raise ResourceConflictError(details={"reason": "successful_inspection_required"})
    snapshot = await uow.repositories.get_snapshot_exact(inspection.repository_snapshot_id)
    relationship = await uow.repositories.get_accepted_current_relationship()
    method = await uow.method_models.get_accepted_current_model(question.id)
    mental = await uow.mental_models.get_current()
    if (
        snapshot is None
        or snapshot.lifecycle_status != "current"
        or relationship is None
        or relationship.repository_snapshot_id != snapshot.id
        or method is None
        or method.freshness_status != "current"
        or mental is None
        or mental.method_model_id != method.id
    ):
        raise ResourceConflictError(details={"reason": "mapping_derivation_context_stale"})
    members = await uow.method_models.list_members(method.id)
    mappings = tuple(
        item
        for item in await uow.mappings.list_all()
        if item.repository_inspection_run_id == inspection.id
        and item.lifecycle_status == "current"
        and item.freshness_status == "current"
        and item.review_status in {"candidate", "needs_revision", "accepted"}
    )
    components = await uow.repositories.list_inspection_components(inspection.id)
    mapping_views = [(await mapping_view(uow, item)).model_dump(mode="json") for item in mappings]
    knowledge = await uow.knowledge.list_accepted_current()
    payload: dict[str, object] = {
        "policy": {
            "derivation": MAPPING_DERIVATION_POLICY_VERSION,
            "materiality": MAPPING_MATERIALITY_POLICY_VERSION,
            "discrepancy": MAPPING_DISCREPANCY_POLICY_VERSION,
            "relationship": RELATIONSHIP_POLICY_VERSION,
        },
        "project": {
            "id": str(project.id),
            "research_question_id": str(question.id),
            "research_question": question.question_text,
            "objective": " ".join(
                value for value in (project.target_outcome, project.description) if value
            ),
            "scope": question.scope_text,
            "research_reproduction_depth": project.reproduction_depth,
        },
        "accepted_state": {
            "method_model": {
                "id": str(method.id),
                "version": method.version,
                "composition_fingerprint": method.composition_fingerprint,
                "members": [
                    {
                        "id": str(member.element.id),
                        "lineage_id": str(member.element.lineage_id),
                        "version": member.element.version,
                        "element_type": member.element.element_type,
                        "required": member.required,
                        "review_section": member.review_section,
                        "member_role": member.member_role,
                        "subtype_payload": _json_value(member.subtype_payload),
                    }
                    for member in members
                    if member.element.review_status == "accepted"
                    and member.element.lifecycle_status == "current"
                ],
            },
            "mental_model": {
                "id": str(mental.id),
                "composition_fingerprint": mental.composition_fingerprint,
            },
            "knowledge": [
                {
                    "id": str(item.id),
                    "version": item.version,
                    "object_type": item.object_type,
                    "statement": item.statement,
                }
                for item in knowledge
            ],
        },
        "repository": {
            "snapshot_id": str(snapshot.id),
            "commit_sha": snapshot.commit_sha,
            "inspection_run_id": str(inspection.id),
            "authority_level": relationship.authority_level,
            "coverage_status": inspection.coverage_status,
            "inspected_paths": list(inspection.inspected_paths),
            "configured_limits": inspection.configured_limits,
            "components": [
                {
                    "id": str(item.id),
                    "path": item.path,
                    "component_type": item.component_type,
                    "symbol": item.symbol,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "content_hash": item.content_hash,
                    "content_excerpt": item.content_excerpt,
                }
                for item in components
            ],
        },
        "mappings": mapping_views,
    }
    fingerprint = _fingerprint(payload)
    return _DerivationContext(
        project=project,
        question_id=question.id,
        question_text=question.question_text,
        question_scope=question.scope_text or "",
        method_model=method,
        method_members=members,
        mental_model_id=mental.id,
        mental_model_fingerprint=mental.composition_fingerprint,
        inspection=inspection,
        snapshot=snapshot,
        authority_level=relationship.authority_level,
        mappings=mappings,
        components=components,
        payload=payload,
        fingerprint=fingerprint,
    )


async def _persist_batch(
    *,
    uow: UnitOfWork,
    command: DeriveMappingKnowledgeCommand,
    context: _DerivationContext,
    generation: GenerationRecordData,
    batch: MappingDerivationBatch,
) -> tuple[tuple[_PersistedItem, ...], UUID | None]:
    mapping_by_id = {item.id: item for item in context.mappings}
    component_by_id = {item.id: item for item in context.components}
    member_by_element = {item.element.id: item for item in context.method_members}
    seen_method_revision: set[UUID] = set()
    validated_discrepancies: list[MappingDiscrepancy] = []
    for discrepancy in batch.discrepancies:
        mappings = _resolve_mappings(discrepancy.source_mapping_ids, mapping_by_id)
        if discrepancy.discrepancy_type == "missing_from_repository":
            if not any(item.mapping_type == "absent_from_repo" for item in mappings):
                raise DomainValidationError(details={"reason": "bounded_absence_mapping_required"})
            _require_bounded_limitation(discrepancy.bounded_limitation, context.inspection)
        for evidence in discrepancy.repository_evidence:
            _validate_repository_reference(evidence, component_by_id)
        validated_discrepancies.append(discrepancy)
    for discrepancy in validated_discrepancies:
        if discrepancy.materiality.material and not any(
            item.materiality.material
            and set(item.source_mapping_ids) & set(discrepancy.source_mapping_ids)
            for item in batch.derived_items
        ):
            raise DomainValidationError(
                details={"reason": "material_discrepancy_consequence_required"}
            )

    persisted: list[_PersistedItem] = []
    method_replacements: dict[UUID, MethodElementData] = {}
    for item in batch.derived_items:
        mappings = _resolve_mappings(item.source_mapping_ids, mapping_by_id)
        del mappings
        for evidence in item.evidence:
            _validate_repository_reference(evidence, component_by_id)
        if isinstance(item, MappingDerivedRequirement | MappingDerivedAmbiguity):
            persisted.append(
                await _persist_knowledge_item(
                    uow=uow,
                    command=command,
                    context=context,
                    generation=generation,
                    item=item,
                    component_by_id=component_by_id,
                )
            )
        elif isinstance(item, MappingDerivedMethodEvidence):
            member = member_by_element.get(item.target_method_element_id)
            if member is None or not _accepted_current_element(member.element):
                raise DomainValidationError(details={"reason": "method_target_invalid"})
            evidence_ids = await _persist_method_evidence(
                uow=uow,
                command=command,
                context=context,
                generation=generation,
                item=item,
                element=member.element,
                component_by_id=component_by_id,
            )
            persisted.append(_persisted_item(item, member.element.id, evidence_ids))
        elif isinstance(item, MappingDerivedMethodRevision):
            if item.target_method_element_id in seen_method_revision:
                raise DomainValidationError(details={"reason": "method_revision_target_duplicate"})
            seen_method_revision.add(item.target_method_element_id)
            member = member_by_element.get(item.target_method_element_id)
            if member is None or not _accepted_current_element(member.element):
                raise DomainValidationError(details={"reason": "method_target_invalid"})
            if item.payload.element_type.value != member.element.element_type:
                raise DomainValidationError(
                    details={"reason": "method_element_type_change_forbidden"}
                )
            replacement = await _persist_method_revision(
                uow=uow,
                command=command,
                context=context,
                generation=generation,
                item=item,
                member=member,
            )
            method_replacements[member.element.id] = replacement[0]
            persisted.append(_persisted_item(item, replacement[0].id, replacement[1]))
        else:  # pragma: no cover - discriminated union is exhaustive
            raise InternalError(details={"reason": "mapping_derivation_kind_invalid"})

    proposal_model_id = await _persist_proposal_method_model(
        uow=uow,
        context=context,
        generation=generation,
        replacements=method_replacements,
    )
    # The exact discrepancy ledger remains in the immutable GenerationRecord. Event
    # references bind its generation and every persisted canonical candidate.
    del validated_discrepancies
    return tuple(persisted), proposal_model_id


async def _persist_knowledge_item(
    *,
    uow: UnitOfWork,
    command: DeriveMappingKnowledgeCommand,
    context: _DerivationContext,
    generation: GenerationRecordData,
    item: MappingDerivedRequirement | MappingDerivedAmbiguity,
    component_by_id: dict[UUID, RepositoryComponentData],
) -> _PersistedItem:
    object_type = item.kind
    normalized_hash = statement_fingerprint(item.statement)
    active = await uow.knowledge.list_active_dedup_candidates(object_type=object_type)
    target = next(
        (
            candidate
            for candidate in active
            if candidate.statement_normalization_version == _STATEMENT_NORMALIZATION_VERSION
            and candidate.normalized_statement_hash == normalized_hash
        ),
        None,
    )
    if target is None:
        object_id = _id(generation.id, f"knowledge:{item.client_id}")
        if isinstance(item, MappingDerivedRequirement):
            subtype: dict[str, object] = {
                "category": item.category,
                "explicitness": "inferred_from_repository",
                "priority": item.priority,
                "satisfaction_status": "unsatisfied",
            }
            interpretations: tuple[dict[str, object], ...] = ()
        else:
            subtype = {
                "ambiguity_type": item.ambiguity_type.value,
                "impact_level": item.impact_level.value,
                "resolution_status": "unresolved",
            }
            interpretations = tuple(
                {
                    "id": _id(object_id, f"interpretation:{interpretation.rank}"),
                    "title": interpretation.title,
                    "description": interpretation.description,
                    "implementation_consequences": interpretation.implementation_consequences,
                    "rank": interpretation.rank,
                }
                for interpretation in item.interpretations
            )
        target = await uow.knowledge.add_candidate(
            NewKnowledgeCandidate(
                id=object_id,
                project_id=command.project_id,
                lineage_id=object_id,
                version=1,
                object_type=object_type,
                statement=item.statement,
                statement_normalization_version=_STATEMENT_NORMALIZATION_VERSION,
                normalized_statement_hash=normalized_hash,
                origin="ai_inference",
                epistemic_status="inferred",
                confidence=item.confidence,
                subtype_payload=subtype,
                ambiguity_interpretations=interpretations,
                generation_record_id=generation.id,
            )
        )
    evidence_ids = await _persist_knowledge_evidence(
        uow=uow,
        command=command,
        context=context,
        generation=generation,
        item=item,
        target_id=target.id,
        target_version=target.version,
        component_by_id=component_by_id,
    )
    links = await uow.knowledge.list_evidence_links(target.id)
    grounding = assess_persisted_evidence(links)
    assessment_id = _id(
        generation.id, f"knowledge-grounding:{target.id}:{grounding.evidence_set_fingerprint}"
    )
    await uow.knowledge.add_grounding_assessment(
        NewGroundingAssessment(
            id=assessment_id,
            project_id=command.project_id,
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
    await uow.knowledge.set_grounding_cache(
        knowledge_object_id=target.id,
        grounding_assessment_id=assessment_id,
        grounding_status=grounding.status.value,
    )
    return _persisted_item(item, target.id, evidence_ids)


async def _persist_knowledge_evidence(
    *,
    uow: UnitOfWork,
    command: DeriveMappingKnowledgeCommand,
    context: _DerivationContext,
    generation: GenerationRecordData,
    item: MappingDerivedRequirement | MappingDerivedAmbiguity,
    target_id: UUID,
    target_version: int,
    component_by_id: dict[UUID, RepositoryComponentData],
) -> tuple[UUID, ...]:
    target_type = (
        EndpointType.REQUIREMENT
        if isinstance(item, MappingDerivedRequirement)
        else EndpointType.AMBIGUITY
    )
    result: list[UUID] = []
    for index, reference in enumerate(item.evidence):
        component = component_by_id[reference.repository_component_id]
        existing = await uow.knowledge.find_evidence_link(
            knowledge_object_id=target_id,
            source_chunk_id=None,
            repository_component_id=component.id,
            evidence_relation=reference.relation,
            evidence_excerpt=reference.excerpt,
        )
        if existing is not None:
            result.append(existing.id)
            continue
        validated = _validated_repository_evidence(
            command=command,
            context=context,
            generation=generation,
            item_client_id=item.client_id,
            evidence_index=index,
            target_id=target_id,
            target_version=target_version,
            target_type=target_type,
            component=component,
            reference=reference,
            material=item.materiality.material,
        )
        link = await uow.knowledge.add_evidence_link(
            NewEvidenceLink(
                id=validated.id,
                project_id=command.project_id,
                lineage_id=validated.lineage_id,
                version=validated.version,
                knowledge_object_id=target_id,
                source_chunk_id=None,
                repository_component_id=component.id,
                evidence_relation=validated.relation.value,
                evidence_excerpt=validated.excerpt,
                confidence=validated.confidence,
                review_status=validated.review_status.value,
                lifecycle_status=validated.lifecycle_status.value,
                generation_record_id=generation.id,
            )
        )
        result.append(link.id)
    return tuple(result)


async def _persist_method_evidence(
    *,
    uow: UnitOfWork,
    command: DeriveMappingKnowledgeCommand,
    context: _DerivationContext,
    generation: GenerationRecordData,
    item: MappingDerivedMethodEvidence | MappingDerivedMethodRevision,
    element: MethodElementData,
    component_by_id: dict[UUID, RepositoryComponentData],
) -> tuple[UUID, ...]:
    payload = await uow.method_models.get_element_payload_exact(element.id)
    if payload is None:
        raise ResourceConflictError(details={"reason": "method_element_payload_missing"})
    symbol_type = payload.get("symbol_type") if element.element_type == "symbol" else None
    endpoint = method_element_endpoint_type(
        element_type=MethodElementType(element.element_type),
        symbol_type=None if symbol_type is None else MethodSymbolType(str(symbol_type)),
    )
    current_links = list(await uow.method_models.list_evidence_links(element.id))
    result: list[UUID] = []
    for index, reference in enumerate(item.evidence):
        component = component_by_id[reference.repository_component_id]
        duplicate = next(
            (
                link
                for link in current_links
                if link.repository_component_id == component.id
                and link.evidence_relation == reference.relation
                and link.evidence_excerpt == reference.excerpt
                and link.lifecycle_status == "current"
                and link.review_status in {"candidate", "needs_revision", "accepted"}
            ),
            None,
        )
        if duplicate is not None:
            result.append(duplicate.id)
            continue
        validated = _validated_repository_evidence(
            command=command,
            context=context,
            generation=generation,
            item_client_id=item.client_id,
            evidence_index=index,
            target_id=element.id,
            target_version=element.version,
            target_type=endpoint,
            component=component,
            reference=reference,
            material=item.materiality.material,
        )
        link = await uow.method_models.add_evidence_link(
            NewMethodEvidenceLink(
                id=validated.id,
                project_id=command.project_id,
                lineage_id=validated.lineage_id,
                version=validated.version,
                method_element_id=element.id,
                source_chunk_id=None,
                repository_component_id=component.id,
                evidence_relation=validated.relation.value,
                evidence_excerpt=validated.excerpt,
                confidence=validated.confidence,
                review_status=validated.review_status.value,
                lifecycle_status=validated.lifecycle_status.value,
                generation_record_id=generation.id,
            )
        )
        current_links.append(link)
        result.append(link.id)
    grounding = assess_method_evidence(
        project_id=command.project_id,
        element_id=element.id,
        element_version=element.version,
        endpoint_type=endpoint,
        links=tuple(current_links),
    )
    grounding_id = _id(
        generation.id, f"method-grounding:{element.id}:{grounding.evidence_set_fingerprint}"
    )
    await uow.method_models.add_grounding_assessment(
        NewMethodGroundingAssessment(
            id=grounding_id,
            project_id=command.project_id,
            method_element_id=element.id,
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
    await uow.method_models.set_element_grounding_cache(
        element_id=element.id,
        assessment_id=grounding_id,
        grounding_status=grounding.status.value,
    )
    return tuple(result)


async def _persist_method_revision(
    *,
    uow: UnitOfWork,
    command: DeriveMappingKnowledgeCommand,
    context: _DerivationContext,
    generation: GenerationRecordData,
    item: MappingDerivedMethodRevision,
    member: MethodModelMemberData,
) -> tuple[MethodElementData, tuple[UUID, ...]]:
    latest = await uow.method_models.latest_element_lineage_for_update(member.element.lineage_id)
    if latest is None:
        raise ResourceConflictError(details={"reason": "method_element_lineage_missing"})
    element_id = _id(generation.id, f"method-revision:{item.client_id}")
    replacement = await uow.method_models.add_candidate_element(
        NewMethodElementCandidate(
            id=element_id,
            project_id=command.project_id,
            lineage_id=member.element.lineage_id,
            version=latest.version + 1,
            element_type=member.element.element_type,
            epistemic_status=MethodEpistemicStatus.REPOSITORY_OBSERVED.value,
            confidence=item.confidence,
            subtype_payload=item.payload.model_dump(mode="python", exclude={"element_type"}),
            generation_record_id=generation.id,
            derived_from_id=latest.id,
        )
    )
    evidence_ids = await _persist_method_evidence(
        uow=uow,
        command=command,
        context=context,
        generation=generation,
        item=item,
        element=replacement,
        component_by_id={value.id: value for value in context.components},
    )
    return replacement, evidence_ids


async def _persist_proposal_method_model(
    *,
    uow: UnitOfWork,
    context: _DerivationContext,
    generation: GenerationRecordData,
    replacements: dict[UUID, MethodElementData],
) -> UUID | None:
    if not replacements:
        return None
    latest = await uow.method_models.latest_model_lineage_for_update(
        context.method_model.lineage_id
    )
    if latest is None:
        raise ResourceConflictError(details={"reason": "method_model_lineage_missing"})
    revised_member_list: list[MethodModelMemberData] = []
    for member in context.method_members:
        replacement = replacements.get(member.element.id)
        if replacement is None:
            revised_member_list.append(member)
            continue
        revised_member_list.append(
            replace(
                member,
                method_element_id=replacement.id,
                element=replacement,
                subtype_payload=(
                    await uow.method_models.get_element_payload_exact(replacement.id) or {}
                ),
            )
        )
    revised_members = tuple(revised_member_list)
    snapshots = tuple(_member_snapshot(item) for item in revised_members)
    model_id = _id(generation.id, "proposal-method-model")
    model = await uow.method_models.add_candidate_model(
        NewMethodModelCandidate(
            id=model_id,
            project_id=context.project.id,
            research_question_id=context.question_id,
            lineage_id=context.method_model.lineage_id,
            version=latest.version + 1,
            name=context.method_model.name,
            objective=context.method_model.objective,
            confidence=None,
            composition_fingerprint=method_composition_fingerprint(snapshots),
            generation_record_id=generation.id,
            derived_from_id=context.method_model.id,
        )
    )
    for index, member in enumerate(revised_members):
        await uow.method_models.add_member(
            NewMethodModelMember(
                id=_id(generation.id, f"proposal-member:{index}:{member.method_element_id}"),
                method_model_id=model.id,
                method_element_id=member.method_element_id,
                review_section=member.review_section,
                member_role=member.member_role,
                sequence_index=member.sequence_index,
                required=member.required,
                inclusion_rationale=member.inclusion_rationale,
            )
        )
    return model.id


def _member_snapshot(member: MethodModelMemberData) -> MethodMemberSnapshot:
    return MethodMemberSnapshot(
        method_element_id=member.element.id,
        element_lineage_id=member.element.lineage_id,
        element_version=member.element.version,
        review_section=MethodReviewSection(member.review_section),
        member_role=MethodMemberRole(member.member_role),
        sequence_index=member.sequence_index,
        required=member.required,
        inclusion_rationale=member.inclusion_rationale,
        review_status=KnowledgeReviewStatus(member.element.review_status),
        lifecycle_status=KnowledgeLifecycleStatus(member.element.lifecycle_status),
    )


def _validated_repository_evidence(
    *,
    command: DeriveMappingKnowledgeCommand,
    context: _DerivationContext,
    generation: GenerationRecordData,
    item_client_id: str,
    evidence_index: int,
    target_id: UUID,
    target_version: int,
    target_type: EndpointType,
    component: RepositoryComponentData,
    reference: RepositoryEvidenceReference,
    material: bool,
) -> ValidatedEvidenceLink:
    link_id = _id(
        generation.id,
        f"evidence:{item_client_id}:{evidence_index}:{target_id}:{component.id}",
    )
    return EvidencePolicy.validate(
        EvidenceLinkRequest(
            id=link_id,
            lineage_id=link_id,
            version=1,
            target=EvidenceTargetSelector(
                project_id=command.project_id,
                target_type=target_type,
                target_version=target_version,
                knowledge_object_id=(
                    target_id
                    if target_type
                    in {
                        EndpointType.REQUIREMENT,
                        EndpointType.AMBIGUITY,
                    }
                    else None
                ),
                method_element_id=(
                    None
                    if target_type
                    in {
                        EndpointType.REQUIREMENT,
                        EndpointType.AMBIGUITY,
                    }
                    else target_id
                ),
            ),
            source=EvidenceSourceSelector(
                provenance=RepositoryComponentEvidenceProvenance(
                    project_id=command.project_id,
                    repository_id=context.snapshot.repository_id,
                    repository_snapshot_id=context.snapshot.id,
                    commit_sha=context.snapshot.commit_sha,
                    inspection_run_id=context.inspection.id,
                    component_id=component.id,
                    path=component.path,
                    content_hash=component.content_hash,
                    content_excerpt=component.content_excerpt,
                    current=True,
                ),
                repository_component_id=component.id,
            ),
            relation=EvidenceRelation(reference.relation),
            excerpt=reference.excerpt,
            confidence=reference.confidence,
            review_status=KnowledgeReviewStatus.CANDIDATE,
            lifecycle_status=KnowledgeLifecycleStatus.CURRENT,
            material=material,
            materiality_policy_version=_EVIDENCE_MATERIALITY_VERSION,
            objective_context_fingerprint=context.fingerprint,
        )
    )


def _resolve_mappings(
    identifiers: list[UUID],
    mapping_by_id: dict[UUID, PaperCodeMappingData],
) -> tuple[PaperCodeMappingData, ...]:
    if len(identifiers) != len(set(identifiers)):
        raise DomainValidationError(details={"reason": "source_mapping_duplicate"})
    result = tuple(mapping_by_id.get(identifier) for identifier in identifiers)
    if any(item is None for item in result):
        raise DomainValidationError(details={"reason": "source_mapping_invalid"})
    return tuple(item for item in result if item is not None)


def _validate_repository_reference(
    reference: RepositoryEvidenceReference,
    components: dict[UUID, RepositoryComponentData],
) -> None:
    component = components.get(reference.repository_component_id)
    if component is None or reference.excerpt not in component.content_excerpt:
        raise DomainValidationError(details={"reason": "repository_evidence_invalid"})


def _require_bounded_limitation(
    limitation: str | None, inspection: RepositoryInspectionRunData
) -> None:
    if limitation is None or not limitation.strip() or not inspection.inspected_paths:
        raise DomainValidationError(details={"reason": "bounded_absence_scope_invalid"})


def _accepted_current_element(element: MethodElementData) -> bool:
    return (
        element.review_status == "accepted"
        and element.lifecycle_status == "current"
        and element.grounding_status != "invalid"
    )


def _persisted_item(
    item: MappingDerivedItem,
    target_id: UUID,
    evidence_ids: tuple[UUID, ...],
) -> _PersistedItem:
    return _PersistedItem(
        client_id=item.client_id,
        kind=item.kind,
        target_id=target_id,
        material=item.materiality.material,
        materiality_reason=item.materiality.reason_code,
        source_mapping_ids=tuple(item.source_mapping_ids),
        evidence_link_ids=evidence_ids,
    )


async def _replay_response(
    uow: UnitOfWork,
    generation: GenerationRecordData,
    event: OperationEventData,
) -> MappingDerivationResponse:
    try:
        batch = MappingDerivationBatch.model_validate(generation.raw_structured_output)
    except ValueError as error:
        raise InternalError(
            details={"reason": "mapping_derivation_replay_output_invalid"}
        ) from error
    items: list[DerivedItemResponse] = []
    proposal_model_id: UUID | None = None
    for reference in event.output_refs:
        reference_type = reference.get("type")
        raw_id = reference.get("id")
        if not isinstance(raw_id, str):
            raise InternalError(details={"reason": "mapping_derivation_replay_ref_invalid"})
        identifier = UUID(raw_id)
        if reference_type == "proposal_method_model":
            if await uow.method_models.get_model_exact(identifier) is None:
                raise InternalError(details={"reason": "mapping_derivation_replay_target_missing"})
            proposal_model_id = identifier
            continue
        target: KnowledgeObjectData | MethodElementData | None
        if reference_type in {"implementation_requirement", "ambiguity"}:
            target = await uow.knowledge.get_exact(identifier)
        elif reference_type in {"method_element_revision", "method_element_evidence"}:
            target = await uow.method_models.get_element_exact(identifier)
        else:
            raise InternalError(details={"reason": "mapping_derivation_replay_ref_invalid"})
        if target is None:
            raise InternalError(details={"reason": "mapping_derivation_replay_target_missing"})
        mapping_ids = reference.get("source_mapping_ids")
        evidence_ids = reference.get("evidence_link_ids")
        if not isinstance(mapping_ids, list) or not isinstance(evidence_ids, list):
            raise InternalError(details={"reason": "mapping_derivation_replay_ref_invalid"})
        items.append(
            DerivedItemResponse(
                client_id=str(reference.get("client_id")),
                kind=str(reference_type),
                target_id=identifier,
                material=bool(reference.get("material")),
                materiality_reason=str(reference.get("materiality_reason")),
                source_mapping_ids=[UUID(str(item)) for item in mapping_ids],
                evidence_link_ids=[UUID(str(item)) for item in evidence_ids],
            )
        )
    return MappingDerivationResponse(
        outcome="applicable",
        generation_record_id=generation.id,
        proposal_method_model_id=proposal_model_id,
        replayed=True,
        derived_items=items,
        discrepancies=batch.discrepancies,
        diagnostics=[item.model_dump(mode="json") for item in batch.diagnostics],
    )


def _id(parent: UUID, value: str) -> UUID:
    return uuid5(_NAMESPACE, f"{parent}:{value}")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _require_owned(actor: ActorContext, project: ProjectData | None) -> None:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
