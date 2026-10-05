"""Generate and revise ordered, provenance-bound implementation steps."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from researchflow.api.schemas.verification import StepsAndChecksGenerationResult
from researchflow.application.blueprints import (
    GenerateBlueprintCommand,
    _load_context,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationOutcome,
)
from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprint_catalog import DependencyCriticality, DependencyRole
from researchflow.domain.implementation import (
    IMPLEMENTATION_POLICY_VERSION,
    IMPLEMENTATION_PROMPT_VERSION,
    IMPLEMENTATION_SCHEMA_NAME,
    IMPLEMENTATION_SCHEMA_VERSION,
    IMPLEMENTATION_TASK_NAME,
    canonical_fingerprint,
    validate_human_text,
    validate_ordered_graph,
    validate_requirement_coverage,
)
from researchflow.errors import (
    DomainValidationError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.providers import (
    JsonValue,
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    BlueprintSectionData,
    ImplementationBlueprintData,
    ImplementationStepData,
    NewImplementationStep,
    NewOperationEvent,
    NewVerificationCheck,
    OperationEventData,
    VerificationCheckData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/")
_GENERATED = "implementation_plan.generated"
_REVISED = "implementation_step.revised"
_ALLOWED_BLUEPRINT_STATUSES = {"provisional", "review_ready", "accepted"}


class PlanGenerationService(Protocol):
    async def generate_structured[OutputT: BaseModel](
        self, command: GenerateStructuredCommand[OutputT]
    ) -> StructuredGenerationOutcome[OutputT]: ...


@dataclass(frozen=True, slots=True)
class GenerateImplementationPlanCommand:
    project_id: UUID
    workflow_run_id: UUID
    blueprint_id: UUID
    model_policy: StructuredGenerationModelPolicy
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviseImplementationStepCommand:
    project_id: UUID
    step_id: UUID
    command_id: UUID
    expected_version: int
    title: str
    description: str
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ImplementationPlan:
    blueprint_id: UUID
    steps: tuple[ImplementationStepData, ...]
    checks: tuple[VerificationCheckData, ...]
    dependencies: tuple[ArtifactDependencyData, ...]
    event: OperationEventData | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class StepRevisionResult:
    step: ImplementationStepData
    invalidated_checks: tuple[UUID, ...]
    event: OperationEventData
    replayed: bool


@dataclass(frozen=True, slots=True)
class _PlanContext:
    blueprint: ImplementationBlueprintData
    sections: tuple[BlueprintSectionData, ...]
    reference_catalog: dict[str, tuple[str, UUID, str | None]]
    section_catalog: dict[str, BlueprintSectionData]
    warning_catalog: dict[str, tuple[str, UUID | None]]
    required_requirement_ids: frozenset[UUID]
    payload: dict[str, object]
    fingerprint: str


class ImplementationService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: PlanGenerationService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def generate(self, command: GenerateImplementationPlanCommand) -> ImplementationPlan:
        async with self._uow_factory(command.project_id) as uow:
            existing = await uow.blueprints.list_steps(command.blueprint_id)
            if existing:
                result = await _plan_view(uow, command.blueprint_id, replayed=True)
                await uow.rollback()
                return result
            context = await _load_plan_context(uow, self._actor, command, lock=False)
            await uow.rollback()

        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=command.workflow_run_id,
                task_name=IMPLEMENTATION_TASK_NAME,
                input_fingerprint=context.fingerprint,
                prompt_version=IMPLEMENTATION_PROMPT_VERSION,
                schema_name=IMPLEMENTATION_SCHEMA_NAME,
                schema_version=IMPLEMENTATION_SCHEMA_VERSION,
                output_type=StepsAndChecksGenerationResult,
                messages=(
                    StructuredGenerationMessage(
                        role="user", content=_canonical_json(context.payload)
                    ),
                ),
                model_policy=command.model_policy,
                provider_json_schema=_provider_schema(context),
                schema_variant_policy_version=IMPLEMENTATION_POLICY_VERSION,
            )
        )
        _validate_output(context, outcome.value)

        async with self._uow_factory(command.project_id) as uow:
            refreshed = await _load_plan_context(uow, self._actor, command, lock=True)
            if refreshed.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "implementation_plan_stale_context"})
            if await uow.blueprints.list_steps(command.blueprint_id):
                result = await _plan_view(uow, command.blueprint_id, replayed=True)
                await uow.rollback()
                return result
            steps: list[ImplementationStepData] = []
            step_by_client: dict[str, ImplementationStepData] = {}
            dependencies: list[ArtifactDependencyData] = []
            for step_proposal in outcome.value.steps:
                step_id = uuid5(
                    _NAMESPACE,
                    f"step:{outcome.generation_record.id}:{step_proposal.client_id}",
                )
                step = await uow.blueprints.add_step(
                    NewImplementationStep(
                        id=step_id,
                        project_id=command.project_id,
                        blueprint_id=refreshed.blueprint.id,
                        lineage_id=uuid5(_NAMESPACE, f"step-lineage:{step_id}"),
                        version=1,
                        sequence_index=step_proposal.sequence_index,
                        title=step_proposal.title,
                        description=step_proposal.description,
                        generation_record_id=outcome.generation_record.id,
                    )
                )
                steps.append(step)
                step_by_client[step_proposal.client_id] = step
                targets = _step_targets(refreshed, step_proposal)
                targets.extend(
                    ("implementation_step", step_by_client[item].id, "derivation_input", "blocking")
                    for item in step_proposal.depends_on
                )
                dependencies.extend(
                    await _persist_edges(
                        uow, step.project_id, "implementation_step", step.id, targets
                    )
                )

            checks: list[VerificationCheckData] = []
            for check_proposal in outcome.value.checks:
                step = step_by_client[check_proposal.step_client_id]
                section = refreshed.section_catalog[check_proposal.section_ref.ref]
                check_id = uuid5(
                    _NAMESPACE,
                    f"check:{outcome.generation_record.id}:{check_proposal.client_id}",
                )
                check = await uow.blueprints.add_check(
                    NewVerificationCheck(
                        id=check_id,
                        project_id=command.project_id,
                        blueprint_id=refreshed.blueprint.id,
                        blueprint_section_id=section.id,
                        implementation_step_id=step.id,
                        lineage_id=uuid5(_NAMESPACE, f"check-lineage:{check_id}"),
                        version=1,
                        sequence_index=check_proposal.sequence_index,
                        condition=check_proposal.condition,
                        test_type=check_proposal.test_type.value,
                        expected_result=check_proposal.expected_result,
                        failure_meaning=check_proposal.failure_meaning,
                        likely_causes=tuple(check_proposal.likely_causes),
                        generation_record_id=outcome.generation_record.id,
                    )
                )
                checks.append(check)
                dependencies.extend(
                    await _persist_edges(
                        uow,
                        check.project_id,
                        "verification_check",
                        check.id,
                        _check_targets(refreshed, check_proposal, step, section),
                    )
                )
            output_refs: list[dict[str, object]] = [
                {"type": "implementation_step", "id": str(item.id)} for item in steps
            ]
            output_refs.extend(
                {"type": "verification_check", "id": str(item.id)} for item in checks
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"event:{outcome.generation_record.id}"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=_GENERATED,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=(
                        {"type": "implementation_blueprint", "id": str(refreshed.blueprint.id)},
                        {"type": "generation_record", "id": str(outcome.generation_record.id)},
                    ),
                    output_refs=tuple(output_refs),
                    reason="Generated ordered implementation steps and falsifiable checks from an exact Blueprint state.",
                    idempotency_key=f"implementation-plan:{refreshed.blueprint.id}",
                    input_fingerprint=refreshed.fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return ImplementationPlan(
                refreshed.blueprint.id,
                tuple(steps),
                tuple(checks),
                tuple(dependencies),
                event,
                outcome.replayed,
            )

    async def revise_step(self, command: ReviseImplementationStepCommand) -> StepRevisionResult:
        title = validate_human_text(command.title, reason="implementation_step_title_invalid")
        description = validate_human_text(
            command.description, reason="implementation_step_description_invalid"
        )
        rationale = validate_human_text(command.rationale, reason="review_rationale_invalid")
        fingerprint = canonical_fingerprint(
            {
                "policy_version": IMPLEMENTATION_POLICY_VERSION,
                "step_id": command.step_id,
                "expected_version": command.expected_version,
                "title": title,
                "description": description,
                "rationale": rationale,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            replay = await uow.events.get_by_idempotency_key(_REVISED, str(command.command_id))
            if replay is not None:
                result = await _step_revision_replay(uow, replay, fingerprint)
                await uow.rollback()
                return result
            await _require_owned(uow, self._actor)
            predecessor = await uow.blueprints.get_step_for_update(command.step_id)
            if predecessor is None:
                raise ResourceNotFoundError(details={"resource": "implementation_step"})
            if (
                predecessor.version != command.expected_version
                or predecessor.lifecycle_status != "current"
                or predecessor.freshness_status != "current"
            ):
                raise ResourceConflictError(details={"reason": "implementation_step_state_changed"})
            blueprint = await uow.blueprints.get_exact_for_update(predecessor.blueprint_id)
            if blueprint is None or blueprint.status not in _ALLOWED_BLUEPRINT_STATUSES:
                raise ResourceConflictError(
                    details={"reason": "implementation_step_blueprint_stale"}
                )
            prior_dependencies = await uow.dependencies.list_dependencies(
                "implementation_step", predecessor.id
            )
            await uow.blueprints.mark_step_superseded(
                predecessor.id, expected_version=predecessor.version
            )
            successor = await uow.blueprints.add_step(
                NewImplementationStep(
                    id=uuid5(_NAMESPACE, f"step-revision:{command.command_id}"),
                    project_id=command.project_id,
                    blueprint_id=predecessor.blueprint_id,
                    lineage_id=predecessor.lineage_id,
                    version=predecessor.version + 1,
                    sequence_index=predecessor.sequence_index,
                    title=title,
                    description=description,
                    generation_record_id=None,
                    derived_from_id=predecessor.id,
                    supersedes_id=predecessor.id,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                )
            )
            for dependency in prior_dependencies:
                await uow.dependencies.append_dependency(
                    replace(
                        dependency,
                        id=uuid5(
                            _NAMESPACE, f"step-revision-dependency:{successor.id}:{dependency.id}"
                        ),
                        artifact_id=successor.id,
                    )
                )
            await StalenessService(
                actor=self._actor, uow_factory=self._uow_factory
            ).artifact_changed(
                uow=uow,
                project_id=command.project_id,
                cause_type="implementation_step",
                before_id=predecessor.id,
                after_id=successor.id,
                operation_type=_REVISED,
            )
            affected = await uow.dependencies.list_dependents("implementation_step", predecessor.id)
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"step-revision-event:{command.command_id}"),
                    project_id=command.project_id,
                    workflow_run_id=None,
                    operation_type=_REVISED,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"type": "implementation_step", "id": str(predecessor.id)},),
                    output_refs=(
                        {
                            "type": "implementation_step",
                            "id": str(successor.id),
                            "version": successor.version,
                        },
                    ),
                    reason=rationale,
                    idempotency_key=str(command.command_id),
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return StepRevisionResult(
                successor,
                tuple(
                    item.artifact_id
                    for item in affected
                    if item.artifact_type == "verification_check"
                ),
                event,
                False,
            )


class ImplementationQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def detail(self, project_id: UUID, blueprint_id: UUID) -> ImplementationPlan:
        async with self._uow_factory(project_id) as uow:
            await _require_owned(uow, self._actor)
            result = await _plan_view(uow, blueprint_id, replayed=False)
            await uow.rollback()
            return result


async def _load_plan_context(
    uow: UnitOfWork,
    actor: ActorContext,
    command: GenerateImplementationPlanCommand,
    *,
    lock: bool,
) -> _PlanContext:
    await _require_owned(uow, actor)
    blueprint = await (
        uow.blueprints.get_exact_for_update(command.blueprint_id)
        if lock
        else uow.blueprints.get_exact(command.blueprint_id)
    )
    if blueprint is None:
        raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
    if blueprint.status not in _ALLOWED_BLUEPRINT_STATUSES:
        raise ResourceConflictError(details={"reason": "implementation_blueprint_not_eligible"})
    if await uow.dependencies.list_unresolved_invalidations(
        "implementation_blueprint", blueprint.id
    ):
        raise ResourceConflictError(details={"reason": "implementation_blueprint_stale"})
    workflow = await (
        uow.workflows.get_for_update(command.workflow_run_id)
        if lock
        else uow.workflows.get_exact(command.workflow_run_id)
    )
    if workflow is None or workflow.primary_research_question_id != blueprint.research_question_id:
        raise ResourceConflictError(details={"reason": "implementation_workflow_mismatch"})
    base = await _load_context(
        uow,
        actor,
        GenerateBlueprintCommand(
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            eligibility_assessment_id=blueprint.eligibility_assessment_id,
            model_policy=command.model_policy,
        ),
        lock=lock,
    )
    if base.assessment.id != blueprint.eligibility_assessment_id:
        raise ResourceConflictError(details={"reason": "implementation_blueprint_input_changed"})
    sections = await uow.blueprints.list_sections(blueprint.id)
    if len(sections) != 10:
        raise ResourceConflictError(details={"reason": "blueprint_section_set_incomplete"})

    references: dict[str, tuple[str, UUID, str | None]] = {}
    blueprint_aliases: dict[str, str] = {}
    available: list[dict[str, object]] = []
    for index, target in enumerate(
        sorted(
            base.references.values(),
            key=lambda item: (item.dependency_type, str(item.dependency_id)),
        ),
        1,
    ):
        alias = f"ip{index:04d}"
        references[alias] = (target.dependency_type, target.dependency_id, target.semantic_type)
        blueprint_aliases[target.alias] = alias
        source = next(
            item
            for item in cast(list[dict[str, object]], base.payload["available_references"])
            if item["ref"] == target.alias
        )
        available.append({**source, "ref": alias})
    section_catalog = {f"bs{index:04d}": section for index, section in enumerate(sections, 1)}
    warnings = tuple(
        item
        for item in base.eligibility_items
        if item.criticality == "material" and item.outcome != "satisfied"
    )
    warning_catalog = {
        f"warning-{index:04d}": (item.expected_dependency_type, item.target_id)
        for index, item in enumerate(warnings, 1)
    }
    project_context = copy.deepcopy(base.payload)
    project_context.pop("available_references", None)
    eligibility = cast(dict[str, object], project_context["eligibility"])
    for item in cast(list[dict[str, object]], eligibility["items"]):
        reference = item.get("reference")
        if isinstance(reference, str):
            item["reference"] = blueprint_aliases[reference]

    payload: dict[str, object] = {
        "policy_version": IMPLEMENTATION_POLICY_VERSION,
        "project_context": project_context,
        "blueprint": {
            "id": str(blueprint.id),
            "version": blueprint.version,
            "status": blueprint.status,
            "sections": [
                {
                    "ref": alias,
                    "section_type": section.section_type,
                    "content": section.structured_content,
                }
                for alias, section in section_catalog.items()
            ],
        },
        "available_references": available,
        "warnings": [
            {
                "ref": alias,
                "dependency_type": value[0],
                "dependency_id": None if value[1] is None else str(value[1]),
            }
            for alias, value in warning_catalog.items()
        ],
        "constraints": {
            "ordered_backward_dependencies_only": True,
            "no_code_execution": True,
            "no_new_scientific_conclusions": True,
            "all_checks_are_falsifiable": True,
        },
    }
    return _PlanContext(
        blueprint,
        sections,
        references,
        section_catalog,
        warning_catalog,
        frozenset(
            target.dependency_id
            for target in base.references.values()
            if target.dependency_type == "knowledge_object"
            and target.semantic_type == "implementation_requirement"
        ),
        payload,
        canonical_fingerprint(payload),
    )


def _provider_schema(context: _PlanContext) -> dict[str, JsonValue]:
    schema = cast(
        dict[str, JsonValue],
        copy.deepcopy(StepsAndChecksGenerationResult.model_json_schema()),
    )
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise DomainValidationError(details={"reason": "implementation_schema_defs_missing"})
    _set_reference_enum(definitions, "PlanReference", tuple(context.reference_catalog))
    _set_reference_enum(definitions, "SectionReference", tuple(context.section_catalog))
    check = definitions.get("VerificationCheckProposal")
    if not isinstance(check, dict) or not isinstance(check.get("properties"), dict):
        raise DomainValidationError(details={"reason": "verification_schema_missing"})
    warning_schema = cast(dict[str, Any], check["properties"])["warning_refs"]
    if not isinstance(warning_schema, dict) or not isinstance(warning_schema.get("items"), dict):
        raise DomainValidationError(details={"reason": "verification_warning_schema_missing"})
    if context.warning_catalog:
        cast(dict[str, Any], warning_schema["items"])["enum"] = list(context.warning_catalog)
    else:
        warning_schema["maxItems"] = 0
    return schema


def _set_reference_enum(definitions: dict[str, Any], name: str, values: tuple[str, ...]) -> None:
    definition = definitions.get(name)
    if not isinstance(definition, dict) or not isinstance(definition.get("properties"), dict):
        raise DomainValidationError(details={"reason": "implementation_reference_schema_missing"})
    ref = cast(dict[str, Any], definition["properties"]).get("ref")
    if not isinstance(ref, dict):
        raise DomainValidationError(details={"reason": "implementation_reference_schema_missing"})
    ref["enum"] = list(values)


def _validate_output(context: _PlanContext, output: StepsAndChecksGenerationResult) -> None:
    validate_ordered_graph(
        (item.client_id, item.sequence_index, tuple(item.depends_on)) for item in output.steps
    )
    step_ids = {item.client_id for item in output.steps}
    requirement_aliases = {
        alias
        for alias, (dependency_type, _, semantic_type) in context.reference_catalog.items()
        if dependency_type == "knowledge_object" and semantic_type == "implementation_requirement"
    }
    required_requirement_aliases = frozenset(
        alias
        for alias in requirement_aliases
        if context.reference_catalog[alias][1] in context.required_requirement_ids
    )
    validate_requirement_coverage(
        required_aliases=required_requirement_aliases,
        supplied_aliases=(ref.ref for item in output.steps for ref in item.requirement_refs),
    )
    for step in output.steps:
        for reference in step.requirement_refs:
            if reference.ref not in requirement_aliases:
                raise DomainValidationError(details={"reason": "invented_requirement_reference"})
        _require_known_refs(context, tuple(item.ref for item in step.references))
    check_ids = [item.client_id for item in output.checks]
    if len(set(check_ids)) != len(check_ids):
        raise DomainValidationError(details={"reason": "verification_check_id_duplicate"})
    by_step: dict[str, list[int]] = {item: [] for item in step_ids}
    supplied_warnings: set[str] = set()
    for check in output.checks:
        if check.step_client_id not in step_ids:
            raise DomainValidationError(details={"reason": "verification_step_unknown"})
        if check.section_ref.ref not in context.section_catalog:
            raise DomainValidationError(details={"reason": "verification_section_unknown"})
        by_step[check.step_client_id].append(check.sequence_index)
        _require_known_refs(context, tuple(item.ref for item in check.references))
        if not any(
            context.reference_catalog[item.ref][0]
            in {"knowledge_object", "method_element", "paper_code_mapping", "human_judgment"}
            for item in check.references
        ):
            raise DomainValidationError(
                details={"reason": "verification_validation_target_missing"}
            )
        if any(item not in context.warning_catalog for item in check.warning_refs):
            raise DomainValidationError(details={"reason": "verification_warning_unknown"})
        supplied_warnings.update(check.warning_refs)
    if supplied_warnings != set(context.warning_catalog):
        raise DomainValidationError(details={"reason": "verification_warning_coverage_mismatch"})
    for indices in by_step.values():
        if not indices or sorted(indices) != list(range(1, len(indices) + 1)):
            raise DomainValidationError(details={"reason": "verification_check_order_invalid"})


def _require_known_refs(context: _PlanContext, aliases: tuple[str, ...]) -> None:
    if len(set(aliases)) != len(aliases) or any(
        item not in context.reference_catalog for item in aliases
    ):
        raise DomainValidationError(details={"reason": "implementation_reference_not_in_context"})


def _step_targets(context: _PlanContext, proposal: Any) -> list[tuple[str, UUID, str, str]]:
    values: list[tuple[str, UUID, str, str]] = [
        ("implementation_blueprint", context.blueprint.id, "derivation_input", "blocking")
    ]
    requirement_aliases = {item.ref for item in proposal.requirement_refs}
    for reference in (*proposal.references, *proposal.requirement_refs):
        dependency_type, dependency_id, _ = context.reference_catalog[reference.ref]
        values.append(
            (
                dependency_type,
                dependency_id,
                "validation_target" if reference.ref in requirement_aliases else "provenance_trace",
                "blocking" if reference.ref in requirement_aliases else "material",
            )
        )
    return _deduplicate_targets(values)


def _check_targets(
    context: _PlanContext,
    proposal: Any,
    step: ImplementationStepData,
    section: BlueprintSectionData,
) -> list[tuple[str, UUID, str, str]]:
    values: list[tuple[str, UUID, str, str]] = [
        ("implementation_blueprint", context.blueprint.id, "derivation_input", "blocking"),
        ("blueprint_section", section.id, "derivation_input", "material"),
        ("implementation_step", step.id, "derivation_input", "blocking"),
    ]
    for reference in proposal.references:
        dependency_type, dependency_id, _ = context.reference_catalog[reference.ref]
        values.append((dependency_type, dependency_id, "validation_target", "material"))
    for warning in proposal.warning_refs:
        warning_type, warning_id = context.warning_catalog[warning]
        if warning_id is None:
            values.append(
                (
                    "blueprint_eligibility_assessment",
                    context.blueprint.eligibility_assessment_id,
                    "validation_target",
                    "material",
                )
            )
        else:
            values.append((warning_type, warning_id, "validation_target", "material"))
    return _deduplicate_targets(values)


def _deduplicate_targets(
    values: list[tuple[str, UUID, str, str]],
) -> list[tuple[str, UUID, str, str]]:
    return list(dict.fromkeys(values))


async def _persist_edges(
    uow: UnitOfWork,
    project_id: UUID,
    artifact_type: str,
    artifact_id: UUID,
    targets: list[tuple[str, UUID, str, str]],
) -> tuple[ArtifactDependencyData, ...]:
    persisted = []
    for dependency_type, dependency_id, role, criticality in targets:
        persisted.append(
            await uow.dependencies.append_dependency(
                ArtifactDependencyData(
                    id=uuid5(
                        _NAMESPACE,
                        f"edge:{artifact_type}:{artifact_id}:{dependency_type}:{dependency_id}:{role}",
                    ),
                    project_id=project_id,
                    artifact_type=artifact_type,
                    artifact_id=artifact_id,
                    dependency_type=dependency_type,
                    dependency_id=dependency_id,
                    dependency_role=DependencyRole(role).value,
                    criticality=DependencyCriticality(criticality).value,
                )
            )
        )
    return tuple(persisted)


async def _plan_view(uow: UnitOfWork, blueprint_id: UUID, *, replayed: bool) -> ImplementationPlan:
    blueprint = await uow.blueprints.get_exact(blueprint_id)
    if blueprint is None:
        raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
    steps = await uow.blueprints.list_steps(blueprint_id)
    checks = await uow.blueprints.list_checks(blueprint_id)
    if not steps:
        raise ResourceNotFoundError(details={"resource": "implementation_plan"})
    dependencies: list[ArtifactDependencyData] = []
    for step in steps:
        dependencies.extend(
            await uow.dependencies.list_dependencies("implementation_step", step.id)
        )
    for check in checks:
        dependencies.extend(
            await uow.dependencies.list_dependencies("verification_check", check.id)
        )
    event = await uow.events.get_by_idempotency_key(
        _GENERATED, f"implementation-plan:{blueprint_id}"
    )
    return ImplementationPlan(blueprint_id, steps, checks, tuple(dependencies), event, replayed)


async def _step_revision_replay(
    uow: UnitOfWork, event: OperationEventData, fingerprint: str
) -> StepRevisionResult:
    if event.input_fingerprint != fingerprint:
        raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
    reference = next(
        (item for item in event.output_refs if item.get("type") == "implementation_step"),
        None,
    )
    if reference is None:
        raise InternalError(details={"reason": "step_revision_replay_output_missing"})
    step = await uow.blueprints.get_step_exact(UUID(str(reference["id"])))
    if step is None:
        raise InternalError(details={"reason": "step_revision_replay_target_missing"})
    affected = await uow.dependencies.list_dependents(
        "implementation_step", step.derived_from_id or step.id
    )
    return StepRevisionResult(
        step,
        tuple(item.artifact_id for item in affected if item.artifact_type == "verification_check"),
        event,
        True,
    )


async def _require_owned(uow: UnitOfWork, actor: ActorContext) -> None:
    project = await uow.projects.get()
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})


def _canonical_json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
