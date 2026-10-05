""" typed Blueprint generation from one exact eligible assessment."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel

from researchflow.api.schemas.blueprints import (
    BlueprintContentItem,
    BlueprintGenerationResult,
    BlueprintSectionProposal,
    LimitationsSection,
    ObjectiveSection,
    RepositoryCorrespondenceSection,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationOutcome,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprint_catalog import DependencyCriticality, DependencyRole
from researchflow.domain.blueprints import (
    BLUEPRINT_PROMPT_VERSION,
    BLUEPRINT_REFERENCE_SCHEMA_POLICY_VERSION,
    BLUEPRINT_SCHEMA_NAME,
    BLUEPRINT_SCHEMA_VERSION,
    BLUEPRINT_SECTION_POLICY_VERSION,
    BLUEPRINT_TASK_NAME,
    BlueprintReferenceTarget,
    canonical_fingerprint,
    canonical_json,
    status_for_eligibility,
    validate_confidence,
    validate_item_references,
    validate_section_summary,
)
from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import (
    JsonValue,
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    BlueprintEligibilityAssessmentData,
    BlueprintEligibilityItemData,
    BlueprintSectionData,
    HumanJudgmentData,
    ImplementationBlueprintData,
    KnowledgeObjectData,
    MentalModelData,
    MentalModelMemberData,
    MethodModelData,
    MethodModelMemberData,
    NewBlueprintSection,
    NewImplementationBlueprint,
    NewOperationEvent,
    OperationEventData,
    PaperCodeMappingData,
    ProjectData,
    ResearchQuestionData,
    WorkflowRunData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_IDENTITY_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/blueprint")
_OPERATION_TYPE = "blueprint.generated"


class BlueprintGenerationService(Protocol):
    async def generate_structured[OutputT: BaseModel](
        self, command: GenerateStructuredCommand[OutputT]
    ) -> StructuredGenerationOutcome[OutputT]: ...


@dataclass(frozen=True, slots=True)
class GenerateBlueprintCommand:
    project_id: UUID
    workflow_run_id: UUID
    eligibility_assessment_id: UUID
    model_policy: StructuredGenerationModelPolicy
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class BlueprintDetail:
    blueprint: ImplementationBlueprintData
    sections: tuple[BlueprintSectionData, ...]
    dependencies: tuple[ArtifactDependencyData, ...]
    history: tuple[OperationEventData, ...]


@dataclass(frozen=True, slots=True)
class BlueprintGenerationView(BlueprintDetail):
    event: OperationEventData
    replayed: bool


@dataclass(frozen=True, slots=True)
class _BlueprintContext:
    project: ProjectData
    workflow: WorkflowRunData
    assessment: BlueprintEligibilityAssessmentData
    eligibility_items: tuple[BlueprintEligibilityItemData, ...]
    question: ResearchQuestionData
    method: MethodModelData
    method_members: tuple[MethodModelMemberData, ...]
    mental: MentalModelData
    mental_members: tuple[MentalModelMemberData, ...]
    knowledge: tuple[KnowledgeObjectData, ...]
    judgments: tuple[HumanJudgmentData, ...]
    mappings: tuple[PaperCodeMappingData, ...]
    references: dict[str, BlueprintReferenceTarget]
    payload: dict[str, object]
    fingerprint: str


class BlueprintService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: BlueprintGenerationService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def generate(self, command: GenerateBlueprintCommand) -> BlueprintGenerationView:
        async with self._uow_factory(command.project_id) as uow:
            existing = await uow.blueprints.get_by_eligibility(command.eligibility_assessment_id)
            if existing is not None:
                result = await _generation_view(uow, existing, replayed=True)
                await uow.rollback()
                return result
            context = await _load_context(uow, self._actor, command, lock=False)
            await uow.rollback()

        provider_schema = _provider_schema(tuple(context.references))
        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=BLUEPRINT_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=BLUEPRINT_PROMPT_VERSION,
                schema_name=BLUEPRINT_SCHEMA_NAME,
                schema_version=BLUEPRINT_SCHEMA_VERSION,
                output_type=BlueprintGenerationResult,
                messages=(
                    StructuredGenerationMessage(
                        role="user",
                        content=canonical_json(context.payload),
                    ),
                ),
                model_policy=command.model_policy,
                provider_json_schema=provider_schema,
                schema_variant_policy_version=BLUEPRINT_REFERENCE_SCHEMA_POLICY_VERSION,
            )
        )
        normalized_sections, section_targets = _validate_output(context, outcome.value)

        async with self._uow_factory(command.project_id) as uow:
            current = await _load_context(uow, self._actor, command, lock=True)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(
                    details={"reason": "blueprint_generation_stale_context"}
                )
            replay = await uow.blueprints.get_by_eligibility(command.eligibility_assessment_id)
            if replay is not None:
                result = await _generation_view(uow, replay, replayed=True)
                await uow.rollback()
                return result

            prior = await uow.blueprints.get_current_for_update()
            blueprint_id = uuid5(_IDENTITY_NAMESPACE, f"blueprint:{outcome.generation_record.id}")
            if prior is None:
                lineage_id = uuid5(_IDENTITY_NAMESPACE, f"lineage:{command.project_id}")
                version = 1
                supersedes_id = None
            else:
                lineage_id = prior.lineage_id
                version = prior.version + 1
                supersedes_id = prior.id
                await uow.blueprints.mark_superseded(prior.id, expected_status=prior.status)
            blueprint = await uow.blueprints.add(
                NewImplementationBlueprint(
                    id=blueprint_id,
                    project_id=command.project_id,
                    lineage_id=lineage_id,
                    version=version,
                    eligibility_assessment_id=current.assessment.id,
                    eligibility_status=current.assessment.status,
                    research_question_id=current.assessment.research_question_id,
                    method_model_id=current.assessment.method_model_id,
                    method_model_assessment_id=current.assessment.method_model_assessment_id,
                    mental_model_id=current.assessment.mental_model_id,
                    mental_model_assessment_id=current.assessment.mental_model_assessment_id,
                    paper_repository_relationship_id=(
                        current.assessment.paper_repository_relationship_id
                    ),
                    repository_snapshot_id=current.assessment.repository_snapshot_id,
                    status=status_for_eligibility(current.assessment.status).value,
                    confidence=outcome.value.confidence,
                    generation_record_id=outcome.generation_record.id,
                    supersedes_id=supersedes_id,
                )
            )
            sections: list[BlueprintSectionData] = []
            for sequence, (proposal, structured_content) in enumerate(
                zip(outcome.value.sections, normalized_sections, strict=True), 1
            ):
                sections.append(
                    await uow.blueprints.add_section(
                        NewBlueprintSection(
                            id=uuid5(
                                _IDENTITY_NAMESPACE,
                                f"section:{blueprint.id}:{proposal.section_type}",
                            ),
                            project_id=command.project_id,
                            blueprint_id=blueprint.id,
                            sequence_index=sequence,
                            section_type=proposal.section_type,
                            content_schema_version=BLUEPRINT_SCHEMA_VERSION,
                            structured_content=structured_content,
                            explicitness=proposal.explicitness.value,
                            confidence=proposal.confidence,
                        )
                    )
                )
            dependencies = await _persist_dependencies(
                uow=uow,
                context=current,
                blueprint=blueprint,
                sections=tuple(sections),
                section_targets=section_targets,
            )
            output_ref_values: list[dict[str, object]] = [
                {
                    "type": "implementation_blueprint",
                    "id": str(blueprint.id),
                    "lineage_id": str(blueprint.lineage_id),
                    "version": blueprint.version,
                    "status": blueprint.status,
                }
            ]
            output_ref_values.extend(
                {"type": "blueprint_section", "id": str(item.id)} for item in sections
            )
            output_refs = tuple(output_ref_values)
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=current.workflow.id,
                    operation_type=_OPERATION_TYPE,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=(
                        {
                            "type": "blueprint_eligibility_assessment",
                            "id": str(current.assessment.id),
                        },
                        {
                            "type": "generation_record",
                            "id": str(outcome.generation_record.id),
                        },
                    ),
                    output_refs=output_refs,
                    reason="Generated a typed Blueprint from one exact eligible canonical state.",
                    idempotency_key=f"blueprint:{blueprint.id}",
                    input_fingerprint=current.fingerprint,
                    request_id=command.request_id,
                )
            )
            history = await uow.events.list_for_knowledge_lineage(blueprint.lineage_id)
            await uow.commit()
            return BlueprintGenerationView(
                blueprint=blueprint,
                sections=tuple(sections),
                dependencies=dependencies,
                history=history,
                event=event,
                replayed=outcome.replayed,
            )


class BlueprintQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def detail(self, project_id: UUID, blueprint_id: UUID) -> BlueprintDetail:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            blueprint = await uow.blueprints.get_exact(blueprint_id)
            if blueprint is None:
                raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
            result = await _detail(uow, blueprint)
            await uow.rollback()
            return result

    async def list(self, project_id: UUID) -> tuple[BlueprintDetail, ...]:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            values = tuple(
                [await _detail(uow, item) for item in await uow.blueprints.list_history()]
            )
            await uow.rollback()
            return values


async def _load_context(
    uow: UnitOfWork,
    actor: ActorContext,
    command: GenerateBlueprintCommand,
    *,
    lock: bool,
) -> _BlueprintContext:
    project = await (uow.projects.get_for_update() if lock else uow.projects.get())
    _require_owned(actor, project)
    workflow = await (
        uow.workflows.get_for_update(command.workflow_run_id)
        if lock
        else uow.workflows.get_exact(command.workflow_run_id)
    )
    assessment = await (
        uow.blueprints.get_eligibility_for_update(command.eligibility_assessment_id)
        if lock
        else uow.blueprints.get_eligibility_exact(command.eligibility_assessment_id)
    )
    if project is None or workflow is None or assessment is None:
        raise ResourceNotFoundError(details={"resource": "blueprint_generation_input"})
    if workflow.primary_research_question_id != assessment.research_question_id:
        raise ResourceConflictError(details={"reason": "blueprint_workflow_question_mismatch"})
    status_for_eligibility(assessment.status)
    if await uow.dependencies.list_unresolved_invalidations(
        "blueprint_eligibility_assessment", assessment.id
    ):
        raise ResourceConflictError(details={"reason": "blueprint_eligibility_stale"})

    question = await (
        uow.research_questions.get_exact_for_update(assessment.research_question_id)
        if lock
        else uow.research_questions.get_exact(assessment.research_question_id)
    )
    method = await (
        uow.method_models.get_model_for_update(assessment.method_model_id)
        if lock
        else uow.method_models.get_model_exact(assessment.method_model_id)
    )
    mental = await (
        uow.mental_models.get_exact_for_update(assessment.mental_model_id)
        if lock
        else uow.mental_models.get_exact(assessment.mental_model_id)
    )
    method_assessment = await uow.method_models.get_assessment_exact(
        assessment.method_model_assessment_id
    )
    mental_assessment = await uow.mental_models.get_assessment_exact(
        assessment.mental_model_assessment_id
    )
    if question is None or method is None or mental is None:
        raise ResourceConflictError(details={"reason": "blueprint_canonical_input_missing"})
    current_method = await uow.method_models.get_accepted_current_model(question.id)
    current_mental = await uow.mental_models.get_current()
    if (
        question.id != project.primary_research_question_id
        or question.lifecycle_status != "current"
        or current_method is None
        or current_method.id != method.id
        or method.review_status != "accepted"
        or method.lifecycle_status != "current"
        or method.freshness_status != "current"
        or method.current_assessment_id != assessment.method_model_assessment_id
        or method_assessment is None
        or method_assessment.outcome == "insufficient"
        or current_mental is None
        or current_mental.id != mental.id
        or mental.status != "current"
        or mental.research_question_id != question.id
        or mental.method_model_id != method.id
        or mental_assessment is None
        or mental_assessment.research_question_id != question.id
        or mental_assessment.method_model_id != method.id
        or mental_assessment.method_model_assessment_id != method_assessment.id
        or (
            mental_assessment.assessed_model_id != mental.id
            and mental_assessment.resulting_model_id != mental.id
        )
    ):
        raise ResourceConflictError(details={"reason": "blueprint_canonical_input_stale"})

    items = await uow.blueprints.list_eligibility_items(assessment.id)
    if any(item.criticality == "blocking" and item.outcome != "satisfied" for item in items):
        raise ResourceConflictError(details={"reason": "blueprint_eligibility_blocked"})
    assessment_edges = await uow.dependencies.list_dependencies(
        "blueprint_eligibility_assessment", assessment.id
    )
    item_targets = {
        (item.expected_dependency_type, item.target_id)
        for item in items
        if item.target_id is not None
    }
    edge_targets = {(item.dependency_type, item.dependency_id) for item in assessment_edges}
    if item_targets != edge_targets:
        raise ResourceConflictError(details={"reason": "eligibility_dependency_mismatch"})

    method_members = await uow.method_models.list_members(method.id)
    if not method_members or any(
        item.element.review_status != "accepted" or item.element.lifecycle_status != "current"
        for item in method_members
    ):
        raise ResourceConflictError(details={"reason": "blueprint_method_members_stale"})
    mental_members = await uow.mental_models.list_members(mental.id)
    accepted_knowledge = {item.id: item for item in await uow.knowledge.list_accepted_current()}
    item_knowledge_ids = {
        item.target_id
        for item in items
        if item.expected_dependency_type == "knowledge_object" and item.target_id is not None
    }
    mental_knowledge_ids = {
        item.knowledge_object_id for item in mental_members if item.knowledge_object_id is not None
    }
    selected_knowledge_ids = item_knowledge_ids | mental_knowledge_ids
    if not selected_knowledge_ids.issubset(accepted_knowledge):
        raise ResourceConflictError(details={"reason": "blueprint_knowledge_input_stale"})
    knowledge = tuple(
        accepted_knowledge[item_id] for item_id in sorted(selected_knowledge_ids, key=str)
    )

    current_judgments = {item.id: item for item in await uow.judgments.list_current()}
    judgment_ids = {
        item.target_id
        for item in items
        if item.expected_dependency_type == "human_judgment" and item.target_id is not None
    } | {item.human_judgment_id for item in mental_members if item.human_judgment_id is not None}
    if not judgment_ids.issubset(current_judgments):
        raise ResourceConflictError(details={"reason": "blueprint_judgment_input_stale"})
    judgments = tuple(current_judgments[item_id] for item_id in sorted(judgment_ids, key=str))

    current_mappings = {item.id: item for item in await uow.mappings.list_accepted_current()}
    mapping_ids = {
        item.target_id
        for item in items
        if item.expected_dependency_type == "paper_code_mapping" and item.target_id is not None
    }
    if not mapping_ids.issubset(current_mappings):
        raise ResourceConflictError(details={"reason": "blueprint_mapping_input_stale"})
    mappings = tuple(current_mappings[item_id] for item_id in sorted(mapping_ids, key=str))

    references, payload = await _context_payload(
        uow=uow,
        project=project,
        question=question,
        assessment=assessment,
        items=items,
        method=method,
        method_members=method_members,
        mental=mental,
        mental_members=mental_members,
        knowledge=knowledge,
        judgments=judgments,
        mappings=mappings,
    )
    fingerprint = canonical_fingerprint(payload)
    return _BlueprintContext(
        project=project,
        workflow=workflow,
        assessment=assessment,
        eligibility_items=items,
        question=question,
        method=method,
        method_members=method_members,
        mental=mental,
        mental_members=mental_members,
        knowledge=knowledge,
        judgments=judgments,
        mappings=mappings,
        references=references,
        payload=payload,
        fingerprint=fingerprint,
    )


async def _context_payload(
    *,
    uow: UnitOfWork,
    project: ProjectData,
    question: ResearchQuestionData,
    assessment: BlueprintEligibilityAssessmentData,
    items: tuple[BlueprintEligibilityItemData, ...],
    method: MethodModelData,
    method_members: tuple[MethodModelMemberData, ...],
    mental: MentalModelData,
    mental_members: tuple[MentalModelMemberData, ...],
    knowledge: tuple[KnowledgeObjectData, ...],
    judgments: tuple[HumanJudgmentData, ...],
    mappings: tuple[PaperCodeMappingData, ...],
) -> tuple[dict[str, BlueprintReferenceTarget], dict[str, object]]:
    raw: dict[tuple[str, UUID], tuple[str | None, dict[str, object]]] = {}

    def add(
        dependency_type: str,
        dependency_id: UUID,
        *,
        semantic_type: str | None,
        content: dict[str, object],
    ) -> None:
        raw[(dependency_type, dependency_id)] = (semantic_type, content)

    add(
        "blueprint_eligibility_assessment",
        assessment.id,
        semantic_type="eligibility_assessment",
        content={
            "status": assessment.status,
            "mapping_coverage": assessment.mapping_coverage,
            "rule_version": assessment.eligibility_rule_version,
        },
    )
    add(
        "research_question",
        question.id,
        semantic_type="research_question",
        content={
            "version": question.version,
            "question": question.question_text,
            "scope": question.scope_text,
        },
    )
    add(
        "method_model",
        method.id,
        semantic_type="method_model",
        content={"version": method.version, "name": method.name, "objective": method.objective},
    )
    add(
        "method_model_assessment",
        assessment.method_model_assessment_id,
        semantic_type="method_model_assessment",
        content={"status": "current"},
    )
    add(
        "mental_model",
        mental.id,
        semantic_type="mental_model",
        content={"version": mental.version, "title": mental.title},
    )
    add(
        "mental_model_assessment",
        assessment.mental_model_assessment_id,
        semantic_type="mental_model_assessment",
        content={"status": "current"},
    )
    for member in method_members:
        add(
            "method_element",
            member.method_element_id,
            semantic_type=member.element.element_type,
            content={
                "element_type": member.element.element_type,
                "epistemic_status": member.element.epistemic_status,
                "grounding_status": member.element.grounding_status,
                "review_section": member.review_section,
                "member_role": member.member_role,
                "required": member.required,
                "semantic_content": member.subtype_payload,
            },
        )
    for knowledge_item in knowledge:
        knowledge_payload = await uow.knowledge.get_payload(knowledge_item.id)
        add(
            "knowledge_object",
            knowledge_item.id,
            semantic_type=knowledge_item.object_type,
            content={
                "object_type": knowledge_item.object_type,
                "statement": knowledge_item.statement,
                "epistemic_status": knowledge_item.epistemic_status,
                "grounding_status": knowledge_item.grounding_status,
                "semantic_content": (
                    {} if knowledge_payload is None else knowledge_payload.subtype_payload
                ),
            },
        )
    for judgment in judgments:
        add(
            "human_judgment",
            judgment.id,
            semantic_type="human_judgment",
            content={
                "version": judgment.version,
                "selected_option_id": (
                    None
                    if judgment.selected_option_id is None
                    else str(judgment.selected_option_id)
                ),
                "custom_decision": judgment.custom_decision,
                "rationale": judgment.rationale,
            },
        )
    for mapping in mappings:
        add(
            "paper_code_mapping",
            mapping.id,
            semantic_type=mapping.mapping_type,
            content={
                "mapping_type": mapping.mapping_type,
                "explanation": mapping.explanation,
                "repository_component_id": (
                    None
                    if mapping.repository_component_id is None
                    else str(mapping.repository_component_id)
                ),
            },
        )
    for eligibility_item in items:
        if (
            eligibility_item.target_id is not None
            and (
                eligibility_item.expected_dependency_type,
                eligibility_item.target_id,
            )
            not in raw
        ):
            add(
                eligibility_item.expected_dependency_type,
                eligibility_item.target_id,
                semantic_type=eligibility_item.reason_code,
                content={"eligibility_reason": eligibility_item.explanation},
            )

    references: dict[str, BlueprintReferenceTarget] = {}
    available: list[dict[str, object]] = []
    for index, ((dependency_type, dependency_id), (semantic_type, content)) in enumerate(
        sorted(raw.items(), key=lambda value: (value[0][0], str(value[0][1]))), 1
    ):
        alias = f"bp{index:04d}"
        references[alias] = BlueprintReferenceTarget(
            alias=alias,
            dependency_type=dependency_type,
            dependency_id=dependency_id,
            semantic_type=semantic_type,
        )
        available.append(
            {
                "ref": alias,
                "dependency_type": dependency_type,
                "semantic_type": semantic_type,
                "content": content,
            }
        )
    eligibility_items = []
    for eligibility_item in items:
        alias = next(
            (
                candidate.alias
                for candidate in references.values()
                if candidate.dependency_type == eligibility_item.expected_dependency_type
                and candidate.dependency_id == eligibility_item.target_id
            ),
            next(
                candidate.alias
                for candidate in references.values()
                if candidate.dependency_type == "blueprint_eligibility_assessment"
            ),
        )
        eligibility_items.append(
            {
                "sequence_index": eligibility_item.sequence_index,
                "reference": alias,
                "criticality": eligibility_item.criticality,
                "outcome": eligibility_item.outcome,
                "reason_code": eligibility_item.reason_code,
                "explanation": eligibility_item.explanation,
            }
        )
    payload: dict[str, object] = {
        "section_policy_version": BLUEPRINT_SECTION_POLICY_VERSION,
        "project": {
            "title": project.title,
            "objective": project.description,
            "target_outcome": project.target_outcome,
            "research_depth": project.reproduction_depth,
        },
        "research_question": {
            "question": question.question_text,
            "scope": question.scope_text,
        },
        "eligibility": {
            "status": assessment.status,
            "mapping_coverage": assessment.mapping_coverage,
            "items": eligibility_items,
        },
        "mental_model": {
            "title": mental.title,
            "members": [
                {
                    "role": item.role,
                    "inclusion_rationale": item.inclusion_rationale,
                    "target_type": (
                        "knowledge_object"
                        if item.knowledge_object_id is not None
                        else "human_judgment"
                        if item.human_judgment_id is not None
                        else "evidence_link"
                    ),
                    "target_id": str(
                        item.knowledge_object_id or item.human_judgment_id or item.evidence_link_id
                    ),
                }
                for item in mental_members
            ],
        },
        "available_references": available,
        "constraints": {
            "no_raw_source_reconstruction": True,
            "all_items_require_supplied_references": True,
            "repository_applicable": bool(assessment.mapping_coverage.get("applicable")),
        },
    }
    return references, payload


def _provider_schema(aliases: tuple[str, ...]) -> dict[str, JsonValue]:
    schema = cast(
        dict[str, JsonValue], copy.deepcopy(BlueprintGenerationResult.model_json_schema())
    )
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise DomainValidationError(details={"reason": "blueprint_schema_defs_missing"})
    reference = definitions.get("BlueprintReference")
    if not isinstance(reference, dict):
        raise DomainValidationError(details={"reason": "blueprint_reference_schema_missing"})
    properties = reference.get("properties")
    if not isinstance(properties, dict):
        raise DomainValidationError(details={"reason": "blueprint_reference_schema_missing"})
    ref_schema = properties.get("ref")
    if not isinstance(ref_schema, dict):
        raise DomainValidationError(details={"reason": "blueprint_reference_schema_missing"})
    ref_schema["enum"] = list(aliases)
    return schema


def _validate_output(
    context: _BlueprintContext,
    output: BlueprintGenerationResult,
) -> tuple[tuple[dict[str, object], ...], tuple[tuple[BlueprintReferenceTarget, ...], ...]]:
    validate_confidence(output.confidence)
    repository_applicable = bool(context.assessment.mapping_coverage.get("applicable"))
    warning_aliases = {
        _reference_alias(context, item.expected_dependency_type, item.target_id)
        for item in context.eligibility_items
        if item.criticality == "material" and item.outcome != "satisfied"
    }
    normalized: list[dict[str, object]] = []
    section_targets: list[tuple[BlueprintReferenceTarget, ...]] = []
    for section in output.sections:
        if isinstance(section, ObjectiveSection):
            if (
                section.target_outcome != context.project.target_outcome
                or section.research_depth != context.project.reproduction_depth
            ):
                raise DomainValidationError(details={"reason": "blueprint_objective_input_changed"})
        if isinstance(section, RepositoryCorrespondenceSection):
            expected = "applicable" if repository_applicable else "not_applicable"
            if section.applicability != expected:
                raise DomainValidationError(
                    details={"reason": "blueprint_repository_applicability_mismatch"}
                )
        content_items = _section_items(section)
        targets: list[BlueprintReferenceTarget] = []
        explicitness_values: list[str] = []
        for role, item in content_items:
            explicitness_values.append(item.explicitness.value)
            targets.extend(
                validate_item_references(
                    explicitness=item.explicitness.value,
                    aliases=tuple(reference.ref for reference in item.references),
                    catalog=context.references,
                    semantic_role=role,
                )
            )
        validate_section_summary(
            declared=section.explicitness.value,
            item_explicitness=tuple(explicitness_values),
        )
        if isinstance(section, LimitationsSection):
            supplied = {
                reference.ref
                for item in section.eligibility_warnings
                for reference in item.references
            }
            if warning_aliases != supplied:
                raise DomainValidationError(
                    details={"reason": "blueprint_eligibility_warning_coverage_mismatch"}
                )
        content = section.model_dump(mode="json")
        normalized.append(_resolve_aliases(content, context.references))
        section_targets.append(
            tuple(
                sorted(
                    set(targets),
                    key=lambda item: (item.dependency_type, str(item.dependency_id)),
                )
            )
        )
    return tuple(normalized), tuple(section_targets)


def _section_items(
    section: BlueprintSectionProposal,
) -> tuple[tuple[str | None, BlueprintContentItem], ...]:
    values: list[tuple[str | None, BlueprintContentItem]] = []
    for field_name in type(section).model_fields:
        value = getattr(section, field_name)
        if isinstance(value, BlueprintContentItem):
            values.append((field_name, value))
        elif isinstance(value, list):
            values.extend(
                (field_name, item) for item in value if isinstance(item, BlueprintContentItem)
            )
    return tuple(values)


def _reference_alias(
    context: _BlueprintContext, dependency_type: str, dependency_id: UUID | None
) -> str:
    if dependency_id is None:
        dependency_type = "blueprint_eligibility_assessment"
        dependency_id = context.assessment.id
    for alias, target in context.references.items():
        if target.dependency_type == dependency_type and target.dependency_id == dependency_id:
            return alias
    raise ResourceConflictError(details={"reason": "eligibility_reference_missing"})


def _resolve_aliases(value: object, catalog: dict[str, BlueprintReferenceTarget]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"ref"} and isinstance(value["ref"], str):
            try:
                target = catalog[value["ref"]]
            except KeyError as error:
                raise DomainValidationError(
                    details={"reason": "blueprint_reference_not_in_context"}
                ) from error
            return {
                "dependency_type": target.dependency_type,
                "dependency_id": str(target.dependency_id),
            }
        return {key: _resolve_aliases(item, catalog) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_aliases(item, catalog) for item in value]
    return value


async def _persist_dependencies(
    *,
    uow: UnitOfWork,
    context: _BlueprintContext,
    blueprint: ImplementationBlueprintData,
    sections: tuple[BlueprintSectionData, ...],
    section_targets: tuple[tuple[BlueprintReferenceTarget, ...], ...],
) -> tuple[ArtifactDependencyData, ...]:
    criticalities = {
        (item.expected_dependency_type, item.target_id): item.criticality
        for item in context.eligibility_items
        if item.target_id is not None
    }
    base = BlueprintReferenceTarget(
        alias="eligibility",
        dependency_type="blueprint_eligibility_assessment",
        dependency_id=context.assessment.id,
        semantic_type="eligibility_assessment",
    )
    blueprint_targets = {base, *(item for values in section_targets for item in values)}
    persisted: list[ArtifactDependencyData] = []
    blueprint_chain_types = {
        "research_question",
        "method_model",
        "method_model_assessment",
        "mental_model",
        "mental_model_assessment",
    }
    for artifact_type, artifact_id, targets in (
        ("implementation_blueprint", blueprint.id, blueprint_targets),
        *(
            ("blueprint_section", section.id, {base, *targets})
            for section, targets in zip(sections, section_targets, strict=True)
        ),
    ):
        for target in sorted(
            targets, key=lambda item: (item.dependency_type, str(item.dependency_id))
        ):
            role = (
                DependencyRole.VALIDATION_TARGET
                if target.dependency_type == "blueprint_eligibility_assessment"
                else DependencyRole.PROVENANCE_TRACE
                if target.dependency_type in {"evidence_link", "repository_component"}
                or (
                    artifact_type == "implementation_blueprint"
                    and target.dependency_type not in blueprint_chain_types
                )
                else DependencyRole.DERIVATION_INPUT
            )
            criticality = criticalities.get(
                (target.dependency_type, target.dependency_id),
                "blocking"
                if target.dependency_type
                in {
                    "blueprint_eligibility_assessment",
                    "research_question",
                    "method_model",
                    "method_model_assessment",
                    "mental_model",
                    "mental_model_assessment",
                }
                else "material",
            )
            identity = (
                f"{artifact_type}:{artifact_id}:{target.dependency_type}:"
                f"{target.dependency_id}:{role.value}"
            )
            persisted.append(
                await uow.dependencies.append_dependency(
                    ArtifactDependencyData(
                        id=uuid5(_IDENTITY_NAMESPACE, f"dependency:{identity}"),
                        project_id=context.project.id,
                        artifact_type=artifact_type,
                        artifact_id=artifact_id,
                        dependency_type=target.dependency_type,
                        dependency_id=target.dependency_id,
                        dependency_role=role.value,
                        criticality=DependencyCriticality(criticality).value,
                    )
                )
            )
    return tuple(persisted)


async def _detail(uow: UnitOfWork, blueprint: ImplementationBlueprintData) -> BlueprintDetail:
    return BlueprintDetail(
        blueprint=blueprint,
        sections=await uow.blueprints.list_sections(blueprint.id),
        dependencies=await uow.dependencies.list_dependencies(
            "implementation_blueprint", blueprint.id
        ),
        history=await uow.events.list_for_knowledge_lineage(blueprint.lineage_id),
    )


async def _generation_view(
    uow: UnitOfWork,
    blueprint: ImplementationBlueprintData,
    *,
    replayed: bool,
) -> BlueprintGenerationView:
    event = await uow.events.get_by_idempotency_key(_OPERATION_TYPE, f"blueprint:{blueprint.id}")
    if event is None:
        raise ResourceConflictError(details={"reason": "blueprint_generation_event_missing"})
    detail = await _detail(uow, blueprint)
    return BlueprintGenerationView(
        blueprint=detail.blueprint,
        sections=detail.sections,
        dependencies=detail.dependencies,
        history=detail.history,
        event=event,
        replayed=replayed,
    )


def _require_owned(actor: ActorContext, project: ProjectData | None) -> None:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
