""" exact material mapping review completion gate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID, uuid4

from researchflow.api.schemas.mapping_derivation import (
    MappingDerivationBatch,
    MappingReviewGateResponse,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.mapping_derivation import (
    MAPPING_DERIVATION_TASK_NAME,
    MappingReviewState,
    mapping_review_gate_fingerprint,
    unresolved_material_states,
)
from researchflow.errors import InternalError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    EvidenceLinkData,
    KnowledgeObjectData,
    MethodElementData,
    MethodEvidenceLinkData,
    NewOperationEvent,
    OperationEventData,
    ProjectData,
    RepositoryInspectionRunData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_COMPLETE = "mapping.review_gate.completed"
_NOT_APPLICABLE = "mapping.review_gate.not_applicable"


@dataclass(frozen=True, slots=True)
class CompleteMappingReviewGateCommand:
    project_id: UUID
    workflow_run_id: UUID
    command_id: UUID
    applicable: bool
    derivation_generation_record_id: UUID | None
    rationale: str
    request_id: str | None = None


class MappingReviewGateService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def complete(
        self, command: CompleteMappingReviewGateCommand
    ) -> MappingReviewGateResponse:
        rationale = command.rationale.strip()
        if not rationale:
            raise ResourceConflictError(details={"reason": "gate_rationale_required"})
        async with self._uow_factory(command.project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            workflow = await uow.workflows.get_for_update(command.workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            if not command.applicable:
                return await self._complete_not_applicable(uow, command, rationale)
            if command.derivation_generation_record_id is None:
                raise ResourceConflictError(details={"reason": "derivation_record_required"})
            generation = await uow.generations.get_exact(command.derivation_generation_record_id)
            if (
                generation is None
                or generation.workflow_run_id != command.workflow_run_id
                or generation.task_name != MAPPING_DERIVATION_TASK_NAME
                or generation.status != "succeeded"
            ):
                raise ResourceConflictError(details={"reason": "derivation_record_invalid"})
            event = await uow.events.get_by_idempotency_key(
                "mapping.derivation.succeeded",
                f"mapping-derivation:{generation.id}",
            )
            if event is None:
                raise ResourceConflictError(details={"reason": "derivation_outputs_missing"})
            batch = _batch(generation.raw_structured_output)
            inspection_id = _input_id(event, "repository_inspection_run")
            inspection = await uow.repositories.get_inspection_run_exact(inspection_id)
            if inspection is None:
                raise ResourceConflictError(details={"reason": "inspection_run_missing"})
            states = await _review_states(uow, event, batch)
            fingerprint = mapping_review_gate_fingerprint(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                generation_record_id=generation.id,
                repository_inspection_run_id=inspection.id,
                applicable=True,
                states=states,
                discrepancy_identity=[item.model_dump(mode="json") for item in batch.discrepancies],
            )
            unresolved = unresolved_material_states(states)
            if unresolved:
                await uow.rollback()
                return _response(
                    outcome="blocked",
                    fingerprint=fingerprint,
                    event_id=None,
                    replayed=False,
                    states=states,
                    unresolved=unresolved,
                    partial_coverage=_partial_coverage(inspection),
                )
            existing = await uow.events.get_by_idempotency_key(_COMPLETE, str(command.command_id))
            if existing is not None:
                if existing.input_fingerprint != fingerprint or existing.reason != rationale:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                await uow.rollback()
                return _response(
                    outcome="complete",
                    fingerprint=fingerprint,
                    event_id=existing.id,
                    replayed=True,
                    states=states,
                    unresolved=(),
                    partial_coverage=_partial_coverage(inspection),
                )
            completed = await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_COMPLETE,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=(
                        {"type": "generation_record", "id": str(generation.id)},
                        {"type": "repository_inspection_run", "id": str(inspection.id)},
                        *(state.identity() for state in states),
                    ),
                    output_refs=({"type": "mapping_review_fingerprint", "id": fingerprint},),
                    reason=rationale,
                    idempotency_key=str(command.command_id),
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return _response(
                outcome="complete",
                fingerprint=fingerprint,
                event_id=completed.id,
                replayed=False,
                states=states,
                unresolved=(),
                partial_coverage=_partial_coverage(inspection),
            )

    async def _complete_not_applicable(
        self,
        uow: UnitOfWork,
        command: CompleteMappingReviewGateCommand,
        rationale: str,
    ) -> MappingReviewGateResponse:
        if command.derivation_generation_record_id is not None:
            raise ResourceConflictError(details={"reason": "not_applicable_derivation_forbidden"})
        derivation = await uow.events.get_by_idempotency_key(
            "mapping.derivation.not_applicable",
            f"mapping-derivation-na:{command.workflow_run_id}",
        )
        if derivation is None:
            raise ResourceConflictError(details={"reason": "not_applicable_derivation_missing"})
        fingerprint = mapping_review_gate_fingerprint(
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            generation_record_id=None,
            repository_inspection_run_id=None,
            applicable=False,
            states=(),
            discrepancy_identity=[],
        )
        existing = await uow.events.get_by_idempotency_key(_NOT_APPLICABLE, str(command.command_id))
        if existing is not None:
            if existing.input_fingerprint != fingerprint or existing.reason != rationale:
                raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
            await uow.rollback()
            return MappingReviewGateResponse(
                outcome="not_applicable",
                fingerprint=fingerprint,
                event_id=existing.id,
                replayed=True,
                material_count=0,
                unresolved=[],
                accepted_mapping_ids=[],
                accepted_knowledge_ids=[],
                accepted_method_element_ids=[],
                partial_coverage_statement=None,
            )
        event = await uow.events.append(
            NewOperationEvent(
                id=uuid4(),
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                operation_type=_NOT_APPLICABLE,
                created_by_type="human",
                created_by_id=self._actor.actor_id,
                input_refs=({"type": "operation_event", "id": str(derivation.id)},),
                reason=rationale,
                idempotency_key=str(command.command_id),
                input_fingerprint=fingerprint,
                request_id=command.request_id,
            )
        )
        await uow.commit()
        return MappingReviewGateResponse(
            outcome="not_applicable",
            fingerprint=fingerprint,
            event_id=event.id,
            replayed=False,
            material_count=0,
            unresolved=[],
            accepted_mapping_ids=[],
            accepted_knowledge_ids=[],
            accepted_method_element_ids=[],
            partial_coverage_statement=None,
        )


async def _review_states(
    uow: UnitOfWork,
    event: OperationEventData,
    batch: MappingDerivationBatch,
) -> tuple[MappingReviewState, ...]:
    mapping_material: dict[UUID, bool] = {
        _input_id(reference, "paper_code_mapping"): False
        for reference in event.input_refs
        if reference.get("type") == "paper_code_mapping"
    }
    for reference in event.output_refs:
        raw_mapping_ids = reference.get("source_mapping_ids", ())
        if isinstance(raw_mapping_ids, list):
            for raw_id in raw_mapping_ids:
                identifier = UUID(str(raw_id))
                if identifier not in mapping_material:
                    raise InternalError(details={"reason": "mapping_review_gate_reference_invalid"})
                mapping_material[identifier] = mapping_material[identifier] or bool(
                    reference.get("material")
                )
    for discrepancy in batch.discrepancies:
        for identifier in discrepancy.source_mapping_ids:
            if identifier not in mapping_material:
                raise InternalError(details={"reason": "mapping_review_gate_reference_invalid"})
            mapping_material[identifier] = (
                mapping_material[identifier] or discrepancy.materiality.material
            )

    states: dict[tuple[str, UUID], MappingReviewState] = {}
    for identifier, material in mapping_material.items():
        mapping = await uow.mappings.get_exact_for_update(identifier)
        if mapping is None:
            raise ResourceConflictError(details={"reason": "mapping_review_target_missing"})
        _add_state(
            states,
            MappingReviewState(
                family="paper_code_mapping",
                id=mapping.id,
                lineage_id=mapping.lineage_id,
                version=mapping.version,
                review_status=mapping.review_status,
                lifecycle_status=mapping.lifecycle_status,
                material=material,
            ),
        )
    for reference in event.output_refs:
        family = reference.get("type")
        if family == "proposal_method_model":
            continue
        if family not in {
            "implementation_requirement",
            "ambiguity",
            "method_element_revision",
            "method_element_evidence",
        }:
            raise InternalError(details={"reason": "mapping_review_gate_reference_invalid"})
        identifier = UUID(str(reference["id"]))
        material = bool(reference.get("material"))
        target: KnowledgeObjectData | MethodElementData | None
        if family in {"implementation_requirement", "ambiguity"}:
            target = await uow.knowledge.get_exact_for_update(identifier)
        else:
            target = await uow.method_models.get_element_for_update(identifier)
        if target is None:
            raise ResourceConflictError(details={"reason": "mapping_review_target_missing"})
        _add_state(
            states,
            MappingReviewState(
                family=str(family),
                id=target.id,
                lineage_id=target.lineage_id,
                version=target.version,
                review_status=target.review_status,
                lifecycle_status=target.lifecycle_status,
                material=material and family != "method_element_evidence",
            ),
        )
        raw_evidence_ids = reference.get("evidence_link_ids")
        if not isinstance(raw_evidence_ids, list):
            raise InternalError(details={"reason": "mapping_review_gate_reference_invalid"})
        for raw_id in raw_evidence_ids:
            evidence_id = UUID(str(raw_id))
            evidence = (
                await uow.knowledge.get_evidence_link_for_update(evidence_id)
                if family in {"implementation_requirement", "ambiguity"}
                else await uow.method_models.get_evidence_link_for_update(evidence_id)
            )
            if evidence is None:
                raise ResourceConflictError(details={"reason": "mapping_review_evidence_missing"})
            _add_state(
                states,
                _evidence_state(
                    evidence,
                    family=(
                        "knowledge_evidence"
                        if isinstance(evidence, EvidenceLinkData)
                        else "method_evidence"
                    ),
                    material=material,
                ),
            )
    return tuple(states.values())


def _add_state(
    states: dict[tuple[str, UUID], MappingReviewState], state: MappingReviewState
) -> None:
    key = (state.family, state.id)
    existing = states.get(key)
    if existing is None:
        states[key] = state
    elif existing != state:
        if replace(existing, material=state.material) != state:
            raise InternalError(details={"reason": "mapping_review_state_conflict"})
        states[key] = MappingReviewState(
            family=state.family,
            id=state.id,
            lineage_id=state.lineage_id,
            version=state.version,
            review_status=state.review_status,
            lifecycle_status=state.lifecycle_status,
            material=existing.material or state.material,
        )


def _evidence_state(
    evidence: EvidenceLinkData | MethodEvidenceLinkData,
    *,
    family: str,
    material: bool,
) -> MappingReviewState:
    return MappingReviewState(
        family=family,
        id=evidence.id,
        lineage_id=evidence.lineage_id,
        version=evidence.version,
        review_status=evidence.review_status,
        lifecycle_status=evidence.lifecycle_status,
        material=material,
    )


def _response(
    *,
    outcome: Literal["complete", "blocked", "not_applicable"],
    fingerprint: str,
    event_id: UUID | None,
    replayed: bool,
    states: tuple[MappingReviewState, ...],
    unresolved: tuple[MappingReviewState, ...],
    partial_coverage: str,
) -> MappingReviewGateResponse:
    return MappingReviewGateResponse(
        outcome=outcome,
        fingerprint=fingerprint,
        event_id=event_id,
        replayed=replayed,
        material_count=sum(state.material for state in states),
        unresolved=[state.identity() for state in unresolved],
        accepted_mapping_ids=[
            state.id
            for state in states
            if state.family == "paper_code_mapping"
            and state.review_status == "accepted"
            and state.lifecycle_status == "current"
        ],
        accepted_knowledge_ids=[
            state.id
            for state in states
            if state.family in {"implementation_requirement", "ambiguity"}
            and state.review_status == "accepted"
            and state.lifecycle_status == "current"
        ],
        accepted_method_element_ids=[
            state.id
            for state in states
            if state.family == "method_element_revision"
            and state.review_status == "accepted"
            and state.lifecycle_status == "current"
        ],
        partial_coverage_statement=partial_coverage,
    )


def _partial_coverage(inspection: RepositoryInspectionRunData) -> str:
    paths = inspection.inspected_paths
    coverage = inspection.coverage_status
    return (
        f"Repository evidence is {coverage} and limited to inspection run "
        f"{inspection.id} across {len(paths)} ordered inspected paths. "
        "No missing correspondence is repository-wide proof."
    )


def _input_id(event_or_reference: OperationEventData | dict[str, object], kind: str) -> UUID:
    references = (
        event_or_reference.input_refs
        if isinstance(event_or_reference, OperationEventData)
        else (event_or_reference,)
    )
    matches = [reference for reference in references if reference.get("type") == kind]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise InternalError(details={"reason": "mapping_review_gate_reference_invalid"})
    return UUID(str(matches[0]["id"]))


def _batch(raw: object) -> MappingDerivationBatch:
    try:
        return MappingDerivationBatch.model_validate(raw)
    except ValueError as error:
        raise InternalError(details={"reason": "mapping_derivation_output_invalid"}) from error


def _require_owned(actor: ActorContext, project: ProjectData | None) -> None:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
