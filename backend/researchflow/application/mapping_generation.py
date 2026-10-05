""" provenance-aware candidate mapping generation and reconstruction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from researchflow.api.schemas.mappings import (
    MappingAnchorResponse,
    MappingComponentResponse,
    MappingGenerationResponse,
    MappingScopeResponse,
    PaperCodeMappingBatch,
    PaperCodeMappingResponse,
    ProposedPaperCodeMapping,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.mappings import (
    MAPPING_POLICY_VERSION,
    MAPPING_PROMPT_VERSION,
    MAPPING_SCHEMA_NAME,
    MAPPING_SCHEMA_VERSION,
    MAPPING_TASK_NAME,
    MappingIdentity,
    canonical_fingerprint,
    validate_mapping_compatibility,
)
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType
from researchflow.domain.repository_ranking import (
    BYTE_BUDGET_POLICY_VERSION,
    GuidanceInput,
    InspectionLimits,
    RankingWeights,
    RepositoryGuidance,
    expected_roles_for_element,
)
from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import (
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewPaperCodeMapping,
    PaperCodeMappingData,
    ProjectData,
    RepositoryComponentData,
    RepositoryInspectionRunData,
    SourceChunkData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_SUCCEEDED = "mapping.generation.succeeded"
_NOT_APPLICABLE = "mapping.generation.not_applicable"


@dataclass(frozen=True, slots=True)
class GenerateMappingsCommand:
    project_id: UUID
    workflow_run_id: UUID
    applicable: bool
    applicability_rationale: str
    repository_inspection_run_id: UUID | None
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Anchor:
    anchor_type: PaperAnchorType
    id: UUID
    text: str
    source_location: dict[str, object] | None = None
    knowledge_object_type: str | None = None
    method_symbol_type: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "anchor_type": self.anchor_type.value,
            "anchor_id": str(self.id),
            "text": self.text,
            "source_location": self.source_location,
            "knowledge_object_type": self.knowledge_object_type,
            "method_symbol_type": self.method_symbol_type,
        }


@dataclass(frozen=True, slots=True)
class _Context:
    inspection: RepositoryInspectionRunData
    repository_id: UUID
    relationship_id: UUID
    question_id: UUID
    method_model_id: UUID
    mental_model_id: UUID
    anchors: tuple[_Anchor, ...]
    components: tuple[RepositoryComponentData, ...]
    payload: dict[str, object]
    fingerprint: str


class MappingGenerationService:
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

    async def generate(self, command: GenerateMappingsCommand) -> MappingGenerationResponse:
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
                task_name=MAPPING_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=MAPPING_PROMPT_VERSION,
                schema_name=MAPPING_SCHEMA_NAME,
                schema_version=MAPPING_SCHEMA_VERSION,
                output_type=PaperCodeMappingBatch,
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
        if not outcome.value.mappings:
            raise ResourceConflictError(details={"reason": "mapping_generation_empty"})

        async with self._uow_factory(command.project_id) as uow:
            current = await _load_context(uow, self._actor, command)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "mapping_generation_stale_context"})
            anchors = {(item.anchor_type, item.id): item for item in current.anchors}
            components = {item.id: item for item in current.components}
            seen_client: set[str] = set()
            seen_identity: set[str] = set()
            persisted: list[PaperCodeMappingData] = []
            diagnostics = [item.model_dump(mode="json") for item in outcome.value.diagnostics]
            for proposal in outcome.value.mappings:
                if proposal.client_id in seen_client:
                    raise DomainValidationError(details={"reason": "mapping_client_id_duplicate"})
                seen_client.add(proposal.client_id)
                anchor = anchors.get((proposal.paper_anchor_type, proposal.anchor_id))
                if anchor is None or proposal.anchor_evidence not in anchor.text:
                    raise DomainValidationError(details={"reason": "mapping_anchor_invalid"})
                component = (
                    None
                    if proposal.repository_component_id is None
                    else components.get(proposal.repository_component_id)
                )
                if proposal.mapping_type is not PaperCodeMappingType.ABSENT_FROM_REPO:
                    if (
                        component is None
                        or proposal.repository_evidence is None
                        or proposal.repository_evidence not in component.content_excerpt
                    ):
                        raise DomainValidationError(details={"reason": "mapping_component_invalid"})
                else:
                    _validate_scope(proposal, current.inspection)
                validate_mapping_compatibility(
                    anchor_type=anchor.anchor_type,
                    mapping_type=proposal.mapping_type,
                    knowledge_object_type=anchor.knowledge_object_type,
                    method_symbol_type=anchor.method_symbol_type,
                )
                identity = MappingIdentity(
                    current.inspection.id,
                    anchor.anchor_type,
                    anchor.id,
                    None if component is None else component.id,
                    proposal.mapping_type,
                )
                identity_fp = identity.fingerprint()
                if identity_fp in seen_identity:
                    diagnostics.append(
                        {"client_id": proposal.client_id, "reason": "batch_exact_duplicate"}
                    )
                    continue
                seen_identity.add(identity_fp)
                existing = await uow.mappings.find_candidate_duplicate(
                    repository_inspection_run_id=current.inspection.id,
                    paper_anchor_type=anchor.anchor_type.value,
                    anchor_id=anchor.id,
                    repository_component_id=None if component is None else component.id,
                    mapping_type=proposal.mapping_type.value,
                )
                if existing is not None:
                    diagnostics.append(
                        {
                            "client_id": proposal.client_id,
                            "reason": "existing_exact_duplicate",
                            "existing_mapping_id": str(existing.id),
                        }
                    )
                    persisted.append(existing)
                    continue
                persisted.append(
                    await uow.mappings.add(
                        _new_mapping(
                            command,
                            current,
                            proposal,
                            anchor,
                            None if component is None else component.id,
                            outcome.generation_record.id,
                            identity.deterministic_id(),
                        )
                    )
                )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_SUCCEEDED,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=(
                        {"repository_inspection_run_id": str(current.inspection.id)},
                        {"generation_record_id": str(outcome.generation_record.id)},
                    ),
                    output_refs=tuple(
                        {"paper_code_mapping_id": str(item.id)} for item in persisted
                    ),
                    reason="Generated reviewable bounded source-to-code mapping candidates.",
                    idempotency_key=f"mapping-generation:{outcome.generation_record.id}",
                    input_fingerprint=current.fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        views = await self._views(command.project_id, tuple(persisted))
        return MappingGenerationResponse(
            outcome="applicable",
            generation_record_id=outcome.generation_record.id,
            replayed=outcome.replayed,
            mappings=list(views),
            diagnostics=diagnostics,
        )

    async def _not_applicable(
        self, command: GenerateMappingsCommand, rationale: str
    ) -> MappingGenerationResponse:
        if command.repository_inspection_run_id is not None:
            raise DomainValidationError(details={"reason": "not_applicable_inspection_forbidden"})
        fingerprint = canonical_fingerprint(
            {
                "project_id": str(command.project_id),
                "workflow_run_id": str(command.workflow_run_id),
                "rationale": rationale,
                "policy_version": MAPPING_POLICY_VERSION,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            workflow = await uow.workflows.get_exact(command.workflow_run_id)
            _require_owned(self._actor, project)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_NOT_APPLICABLE,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    reason=rationale,
                    idempotency_key=f"mapping-not-applicable:{command.workflow_run_id}",
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        return MappingGenerationResponse(
            outcome="not_applicable",
            generation_record_id=None,
            replayed=False,
            mappings=[],
            diagnostics=[{"reason": rationale}],
        )

    async def _views(
        self, project_id: UUID, mappings: tuple[PaperCodeMappingData, ...]
    ) -> tuple[PaperCodeMappingResponse, ...]:
        async with self._uow_factory(project_id) as uow:
            result = tuple([await _mapping_view(uow, item) for item in mappings])
            await uow.rollback()
            return result


class MappingQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def list(self, project_id: UUID) -> tuple[PaperCodeMappingResponse, ...]:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            result = tuple(
                [await _mapping_view(uow, item) for item in await uow.mappings.list_all()]
            )
            await uow.rollback()
            return result

    async def detail(self, project_id: UUID, mapping_id: UUID) -> PaperCodeMappingResponse:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            mapping = await uow.mappings.get_exact(mapping_id)
            if mapping is None:
                raise ResourceNotFoundError(details={"resource": "paper_code_mapping"})
            result = await _mapping_view(uow, mapping)
            await uow.rollback()
            return result


async def _load_context(
    uow: UnitOfWork, actor: ActorContext, command: GenerateMappingsCommand
) -> _Context:
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
        raise ResourceConflictError(details={"reason": "mapping_project_context_missing"})
    if workflow.primary_research_question_id != question.id:
        raise ResourceConflictError(details={"reason": "mapping_workflow_question_stale"})
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
        or method.current_assessment_id is None
        or mental is None
        or mental.method_model_id != method.id
    ):
        raise ResourceConflictError(details={"reason": "mapping_accepted_context_stale"})
    members = await uow.method_models.list_members(method.id)
    knowledge = await uow.knowledge.list_accepted_current()
    mental_members = await uow.mental_models.list_members(mental.id)
    discovery = await uow.repositories.get_discovery_run_exact(inspection.discovery_run_id)
    if discovery is None or discovery.tree_hash is None:
        raise ResourceConflictError(details={"reason": "mapping_discovery_missing"})
    guidance_inputs: list[GuidanceInput] = [
        GuidanceInput(
            "method_model",
            str(method.id),
            f"{method.name} {method.objective}",
            ("model_definition",),
        )
    ]
    guidance_inputs.extend(
        GuidanceInput("knowledge_object", str(item.id), item.statement)
        for item in knowledge
        if item.object_type in {"claim", "assumption", "implementation_requirement"}
    )
    guidance_inputs.extend(
        GuidanceInput(
            "method_element",
            str(member.element.id),
            " ".join(_flatten_text(member.subtype_payload)),
            expected_roles_for_element(member.element.element_type),
        )
        for member in members
        if member.element.review_status == "accepted"
        and member.element.lifecycle_status == "current"
    )
    guidance_inputs.extend(
        GuidanceInput(
            "mental_model_member",
            str(member.id),
            f"{member.role} {member.inclusion_rationale}",
        )
        for member in mental_members
    )
    guidance = RepositoryGuidance(
        project_id=str(project.id),
        research_question_id=str(question.id),
        question_text=question.question_text,
        objective=" ".join(
            value for value in (project.target_outcome, project.description) if value
        ),
        scope=question.scope_text,
        research_depth=project.reproduction_depth,
        method_model_id=str(method.id),
        method_model_fingerprint=method.composition_fingerprint,
        mental_model_id=str(mental.id),
        mental_model_fingerprint=mental.composition_fingerprint,
        relationship_id=str(relationship.id),
        relationship_type=relationship.relationship_type,
        authority_level=relationship.authority_level,
        discovery_run_id=str(discovery.id),
        discovery_tree_hash=discovery.tree_hash,
        accepted_inputs=tuple(guidance_inputs),
    )
    try:
        limits = InspectionLimits(
            candidate_limit=_configured_int(inspection, "candidate_limit"),
            inspection_limit=_configured_int(inspection, "inspection_limit"),
            max_file_bytes=_configured_int(inspection, "max_file_bytes"),
            aggregate_bytes=_configured_int(inspection, "aggregate_bytes"),
        )
        raw_weights = inspection.configured_limits["weights"]
        if not isinstance(raw_weights, dict):
            raise TypeError
        weights = RankingWeights(
            guidance_term=Decimal(str(raw_weights["guidance_term"])),
            expected_role=Decimal(str(raw_weights["expected_role"])),
            stage_a_reference=Decimal(str(raw_weights["stage_a_reference"])),
            implementation_source=Decimal(str(raw_weights["implementation_source"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ResourceConflictError(
            details={"reason": "mapping_inspection_policy_missing"}
        ) from error
    expected_guidance_fingerprint = guidance.fingerprint(
        ranking_version=inspection.ranking_version,
        analysis_version=inspection.analysis_version,
        byte_budget_policy_version=str(
            inspection.configured_limits.get(
                "byte_budget_policy_version", BYTE_BUDGET_POLICY_VERSION
            )
        ),
        weights=weights,
        limits=limits,
    )
    if expected_guidance_fingerprint != inspection.guidance_fingerprint:
        raise ResourceConflictError(details={"reason": "mapping_inspection_stale"})
    anchors: list[_Anchor] = [
        _Anchor(PaperAnchorType.METHOD_MODEL, method.id, f"{method.name}\n{method.objective}")
    ]
    chunks: dict[UUID, _Anchor] = {}
    for item in knowledge:
        anchors.append(
            _Anchor(
                PaperAnchorType.KNOWLEDGE_OBJECT,
                item.id,
                item.statement,
                knowledge_object_type=item.object_type,
            )
        )
        for knowledge_evidence in await uow.knowledge.list_evidence_links(item.id):
            if (
                knowledge_evidence.review_status == "accepted"
                and knowledge_evidence.lifecycle_status == "current"
                and knowledge_evidence.source_chunk_id is not None
            ):
                chunk = await uow.sources.get_chunk_exact(knowledge_evidence.source_chunk_id)
                if chunk is not None:
                    chunks[chunk.id] = _chunk_anchor(chunk)
    for member in members:
        if (
            member.element.review_status != "accepted"
            or member.element.lifecycle_status != "current"
        ):
            continue
        anchor_type = _element_anchor_type(member.element.element_type)
        text = " ".join(_flatten_text(member.subtype_payload))
        anchors.append(
            _Anchor(
                anchor_type,
                member.element.id,
                text,
                method_symbol_type=(
                    str(member.subtype_payload.get("symbol_type"))
                    if member.element.element_type == "symbol"
                    else None
                ),
            )
        )
        for evidence in member.evidence:
            if (
                evidence.review_status == "accepted"
                and evidence.lifecycle_status == "current"
                and evidence.source_chunk_id is not None
            ):
                chunk = await uow.sources.get_chunk_exact(evidence.source_chunk_id)
                if chunk is not None:
                    chunks[chunk.id] = _chunk_anchor(chunk)
    anchors.extend(chunks.values())
    anchors.sort(key=lambda item: (item.anchor_type.value, str(item.id)))
    components = await uow.repositories.list_inspection_components(inspection.id)
    if not components:
        raise ResourceConflictError(details={"reason": "inspection_components_required"})
    payload: dict[str, object] = {
        "policy_version": MAPPING_POLICY_VERSION,
        "project": {
            "id": str(project.id),
            "research_question_id": str(question.id),
            "research_question": question.question_text,
            "scope": question.scope_text,
            "objective": project.target_outcome,
            "description": project.description,
            "research_depth": project.reproduction_depth,
        },
        "accepted_state": {
            "method_model_id": str(method.id),
            "method_model_fingerprint": method.composition_fingerprint,
            "mental_model_id": str(mental.id),
            "mental_model_fingerprint": mental.composition_fingerprint,
        },
        "repository": {
            "relationship_id": str(relationship.id),
            "relationship_type": relationship.relationship_type,
            "authority_level": relationship.authority_level,
            "snapshot_id": str(snapshot.id),
            "commit_sha": snapshot.commit_sha,
            "inspection_run_id": str(inspection.id),
            "inspection_guidance_fingerprint": inspection.guidance_fingerprint,
            "coverage_status": inspection.coverage_status,
            "configured_limits": inspection.configured_limits,
            "configured_limits_fingerprint": canonical_fingerprint(inspection.configured_limits),
            "inspected_paths": list(inspection.inspected_paths),
        },
        "anchors": [item.payload() for item in anchors],
        "components": [
            {
                "id": str(item.id),
                "path": item.path,
                "symbol": item.symbol,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "excerpt": item.content_excerpt,
                "content_hash": item.content_hash,
            }
            for item in components
        ],
    }
    return _Context(
        inspection,
        snapshot.repository_id,
        relationship.id,
        question.id,
        method.id,
        mental.id,
        tuple(anchors),
        components,
        payload,
        canonical_fingerprint(payload),
    )


def _validate_scope(
    proposal: ProposedPaperCodeMapping, inspection: RepositoryInspectionRunData
) -> None:
    scope = proposal.searched_scope
    if scope is None or (
        scope.inspection_run_id != inspection.id
        or tuple(scope.inspected_paths) != inspection.inspected_paths
        or scope.coverage_status != inspection.coverage_status
        or scope.configured_limits_fingerprint
        != canonical_fingerprint(inspection.configured_limits)
    ):
        raise DomainValidationError(details={"reason": "mapping_absence_scope_invalid"})


def _configured_int(inspection: RepositoryInspectionRunData, key: str) -> int:
    value = inspection.configured_limits[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(key)
    return value


def _new_mapping(
    command: GenerateMappingsCommand,
    context: _Context,
    proposal: ProposedPaperCodeMapping,
    anchor: _Anchor,
    component_id: UUID | None,
    generation_record_id: UUID,
    mapping_id: UUID,
) -> NewPaperCodeMapping:
    anchor_fields: dict[str, UUID | None] = {
        name: None
        for name in (
            "source_chunk_id",
            "knowledge_object_id",
            "method_model_id",
            "method_component_id",
            "method_equation_id",
            "method_symbol_id",
            "method_algorithm_step_id",
            "method_data_flow_id",
            "method_validity_condition_id",
        )
    }
    anchor_fields[_anchor_column(anchor.anchor_type)] = anchor.id
    explanation = proposal.explanation
    if proposal.mapping_type is PaperCodeMappingType.ABSENT_FROM_REPO:
        scope = proposal.searched_scope
        assert scope is not None
        explanation = f"{explanation} Bounded-scope limitation: {scope.limitation}"
    return NewPaperCodeMapping(
        id=mapping_id,
        project_id=command.project_id,
        repository_id=context.repository_id,
        repository_snapshot_id=context.inspection.repository_snapshot_id,
        repository_inspection_run_id=context.inspection.id,
        lineage_id=mapping_id,
        version=1,
        paper_anchor_type=anchor.anchor_type.value,
        source_chunk_id=anchor_fields["source_chunk_id"],
        knowledge_object_id=anchor_fields["knowledge_object_id"],
        method_model_id=anchor_fields["method_model_id"],
        method_component_id=anchor_fields["method_component_id"],
        method_equation_id=anchor_fields["method_equation_id"],
        method_symbol_id=anchor_fields["method_symbol_id"],
        method_algorithm_step_id=anchor_fields["method_algorithm_step_id"],
        method_data_flow_id=anchor_fields["method_data_flow_id"],
        method_validity_condition_id=anchor_fields["method_validity_condition_id"],
        repository_component_id=component_id,
        mapping_type=proposal.mapping_type.value,
        explanation=explanation,
        confidence=proposal.confidence,
        generation_record_id=generation_record_id,
        created_by_type="ai",
        created_by_id=None,
    )


async def mapping_view(uow: UnitOfWork, mapping: PaperCodeMappingData) -> PaperCodeMappingResponse:
    anchor = await _resolve_mapping_anchor(uow, mapping)
    component = None
    components = await uow.repositories.list_inspection_components(
        mapping.repository_inspection_run_id
    )
    if mapping.repository_component_id is not None:
        item = next(
            (value for value in components if value.id == mapping.repository_component_id), None
        )
        if item is None:
            raise ResourceConflictError(details={"reason": "mapping_component_missing"})
        component = MappingComponentResponse(
            id=item.id,
            path=item.path,
            symbol=item.symbol,
            line_start=item.line_start,
            line_end=item.line_end,
            excerpt=item.content_excerpt,
            content_hash=item.content_hash,
        )
    scope = None
    if mapping.mapping_type == PaperCodeMappingType.ABSENT_FROM_REPO.value:
        run = await uow.repositories.get_inspection_run_exact(mapping.repository_inspection_run_id)
        if run is None:
            raise ResourceConflictError(details={"reason": "mapping_inspection_missing"})
        limitation = mapping.explanation.split("Bounded-scope limitation:", 1)[-1].strip()
        scope = MappingScopeResponse(
            inspection_run_id=run.id,
            inspected_paths=list(run.inspected_paths),
            coverage_status=run.coverage_status,
            configured_limits=run.configured_limits,
            limitation=limitation,
        )
    return PaperCodeMappingResponse(
        id=mapping.id,
        lineage_id=mapping.lineage_id,
        version=mapping.version,
        mapping_type=mapping.mapping_type,
        explanation=mapping.explanation,
        confidence=mapping.confidence,
        review_status=mapping.review_status,
        lifecycle_status=mapping.lifecycle_status,
        freshness_status=mapping.freshness_status,
        generation_record_id=mapping.generation_record_id,
        anchor=anchor,
        component=component,
        searched_scope=scope,
    )


# Backward-compatible private seam retained for the frozen  policy tests.
_mapping_view = mapping_view


async def _resolve_mapping_anchor(
    uow: UnitOfWork, mapping: PaperCodeMappingData
) -> MappingAnchorResponse:
    anchor_id = _mapping_anchor_id(mapping)
    anchor_type = PaperAnchorType(mapping.paper_anchor_type)
    if anchor_type is PaperAnchorType.SOURCE_CHUNK:
        chunk = await uow.sources.get_chunk_exact(anchor_id)
        if chunk is None:
            raise ResourceConflictError(details={"reason": "mapping_anchor_missing"})
        return MappingAnchorResponse(
            anchor_type=anchor_type.value,
            anchor_id=anchor_id,
            text=chunk.chunk_text,
            source_location=_chunk_anchor(chunk).source_location,
        )
    if anchor_type is PaperAnchorType.KNOWLEDGE_OBJECT:
        item = await uow.knowledge.get_exact(anchor_id)
        if item is None:
            raise ResourceConflictError(details={"reason": "mapping_anchor_missing"})
        return MappingAnchorResponse(
            anchor_type=anchor_type.value, anchor_id=anchor_id, text=item.statement
        )
    if anchor_type is PaperAnchorType.METHOD_MODEL:
        method_model = await uow.method_models.get_model_exact(anchor_id)
        if method_model is None:
            raise ResourceConflictError(details={"reason": "mapping_anchor_missing"})
        return MappingAnchorResponse(
            anchor_type=anchor_type.value,
            anchor_id=anchor_id,
            text=f"{method_model.name}\n{method_model.objective}",
        )
    payload = await uow.method_models.get_element_payload_exact(anchor_id)
    if payload is None:
        raise ResourceConflictError(details={"reason": "mapping_anchor_missing"})
    text = " ".join(_flatten_text(payload))
    return MappingAnchorResponse(anchor_type=anchor_type.value, anchor_id=anchor_id, text=text)


def _chunk_anchor(chunk: SourceChunkData) -> _Anchor:
    return _Anchor(
        PaperAnchorType.SOURCE_CHUNK,
        chunk.id,
        chunk.chunk_text,
        source_location={
            "processing_run_id": str(chunk.processing_run_id),
            "page_number": chunk.page_number,
            "chunk_index": chunk.chunk_index,
            "start_offset": chunk.start_offset,
            "end_offset": chunk.end_offset,
            "content_hash": chunk.content_hash,
        },
    )


def _element_anchor_type(element_type: str) -> PaperAnchorType:
    return {
        "component": PaperAnchorType.METHOD_COMPONENT,
        "equation": PaperAnchorType.EQUATION,
        "symbol": PaperAnchorType.METHOD_SYMBOL,
        "algorithm_step": PaperAnchorType.ALGORITHM_STEP,
        "data_flow": PaperAnchorType.DATA_FLOW,
        "validity_condition": PaperAnchorType.VALIDITY_CONDITION,
    }[element_type]


def _anchor_column(anchor_type: PaperAnchorType) -> str:
    return {
        PaperAnchorType.SOURCE_CHUNK: "source_chunk_id",
        PaperAnchorType.KNOWLEDGE_OBJECT: "knowledge_object_id",
        PaperAnchorType.METHOD_MODEL: "method_model_id",
        PaperAnchorType.METHOD_COMPONENT: "method_component_id",
        PaperAnchorType.EQUATION: "method_equation_id",
        PaperAnchorType.METHOD_SYMBOL: "method_symbol_id",
        PaperAnchorType.ALGORITHM_STEP: "method_algorithm_step_id",
        PaperAnchorType.DATA_FLOW: "method_data_flow_id",
        PaperAnchorType.VALIDITY_CONDITION: "method_validity_condition_id",
    }[anchor_type]


def _mapping_anchor_id(mapping: PaperCodeMappingData) -> UUID:
    values = {
        "source_chunk_id": mapping.source_chunk_id,
        "knowledge_object_id": mapping.knowledge_object_id,
        "method_model_id": mapping.method_model_id,
        "method_component_id": mapping.method_component_id,
        "method_equation_id": mapping.method_equation_id,
        "method_symbol_id": mapping.method_symbol_id,
        "method_algorithm_step_id": mapping.method_algorithm_step_id,
        "method_data_flow_id": mapping.method_data_flow_id,
        "method_validity_condition_id": mapping.method_validity_condition_id,
    }
    value = values[_anchor_column(PaperAnchorType(mapping.paper_anchor_type))]
    if value is None:
        raise ResourceConflictError(details={"reason": "mapping_anchor_missing"})
    return value


def _flatten_text(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        return tuple(part for key in sorted(value) for part in _flatten_text(value[key]))
    if isinstance(value, (list, tuple)):
        return tuple(part for item in value for part in _flatten_text(item))
    return (str(value),)


def _require_owned(actor: ActorContext, project: ProjectData | None) -> None:
    if project is None:
        raise ResourceNotFoundError(details={"resource": "project"})
    if project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
