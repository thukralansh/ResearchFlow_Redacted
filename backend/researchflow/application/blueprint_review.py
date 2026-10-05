"""Human Blueprint review over immutable, provenance-bound versions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel

from researchflow.api.schemas.blueprints import BlueprintGenerationResult
from researchflow.application.blueprints import (
    BlueprintDetail,
    GenerateBlueprintCommand,
    _detail,
    _load_context,
    _persist_dependencies,
    _provider_schema,
    _validate_output,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationOutcome,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprints import (
    BLUEPRINT_REFERENCE_SCHEMA_POLICY_VERSION,
    BLUEPRINT_REVIEW_POLICY_VERSION,
    BLUEPRINT_REVISION_PROMPT_VERSION,
    BLUEPRINT_REVISION_SCHEMA_VERSION,
    BLUEPRINT_REVISION_TASK_NAME,
    canonical_fingerprint,
    canonical_json,
    require_acceptance_status,
    require_revision_status,
    status_for_eligibility,
    validate_review_rationale,
)
from researchflow.errors import InternalError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import (
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    BlueprintSectionData,
    GenerationRecordData,
    ImplementationBlueprintData,
    NewBlueprintSection,
    NewImplementationBlueprint,
    NewOperationEvent,
    OperationEventData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_IDENTITY_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/blueprint-review")
_ACCEPT = "blueprint.accepted"
_REVISE = "blueprint.revision_requested"


class BlueprintRevisionGenerationService(Protocol):
    async def generate_structured[OutputT: BaseModel](
        self, command: GenerateStructuredCommand[OutputT]
    ) -> StructuredGenerationOutcome[OutputT]: ...


@dataclass(frozen=True, slots=True)
class AcceptBlueprintCommand:
    project_id: UUID
    blueprint_id: UUID
    command_id: UUID
    expected_version: int
    expected_status: str
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviseBlueprintCommand:
    project_id: UUID
    blueprint_id: UUID
    command_id: UUID
    expected_version: int
    expected_status: str
    rationale: str
    feedback: str
    model_policy: StructuredGenerationModelPolicy
    eligibility_assessment_id: UUID | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class BlueprintReviewResult(BlueprintDetail):
    event: OperationEventData
    replayed: bool


class BlueprintReviewService:
    """Channel-neutral acceptance and semantic-revision boundary."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: BlueprintRevisionGenerationService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def accept(self, command: AcceptBlueprintCommand) -> BlueprintReviewResult:
        rationale = validate_review_rationale(command.rationale)
        fingerprint = canonical_fingerprint(
            {
                "policy_version": BLUEPRINT_REVIEW_POLICY_VERSION,
                "blueprint_id": command.blueprint_id,
                "expected_version": command.expected_version,
                "expected_status": command.expected_status,
                "rationale": rationale,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            replay = await self._replay(uow, _ACCEPT, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            blueprint = await self._locked_expected(uow, command)
            require_acceptance_status(
                status=blueprint.status,
                eligibility_status=blueprint.eligibility_status,
            )
            await self._require_current_and_valid(uow, blueprint)
            sections = await uow.blueprints.list_sections(blueprint.id)
            if len(sections) != 10 or any(item.review_status != "candidate" for item in sections):
                raise ResourceConflictError(details={"reason": "blueprint_sections_not_reviewable"})
            accepted = await uow.blueprints.update_status(
                blueprint.id,
                expected_status="review_ready",
                status="accepted",
            )
            await uow.blueprints.update_section_review_statuses(
                blueprint.id,
                expected_status="candidate",
                status="accepted",
            )
            event = await self._append_event(
                uow,
                operation_type=_ACCEPT,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(_blueprint_ref(blueprint),),
                outputs=(_blueprint_ref(accepted),),
            )
            detail = await _detail(uow, accepted)
            await uow.commit()
            return BlueprintReviewResult(
                blueprint=detail.blueprint,
                sections=detail.sections,
                dependencies=detail.dependencies,
                history=detail.history,
                event=event,
                replayed=False,
            )

    async def request_revision(self, command: ReviseBlueprintCommand) -> BlueprintReviewResult:
        rationale = validate_review_rationale(command.rationale)
        feedback = validate_review_rationale(command.feedback)
        assessment_id: UUID
        async with self._uow_factory(command.project_id) as uow:
            fingerprint = _revision_command_fingerprint(command, rationale, feedback)
            replay = await self._replay(uow, _REVISE, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            predecessor = await self._expected(uow, command)
            require_revision_status(predecessor.status)
            current = await uow.blueprints.get_current()
            if current is None or current.id != predecessor.id:
                raise ResourceConflictError(details={"reason": "blueprint_not_current"})
            assessment_id = (
                command.eligibility_assessment_id or predecessor.eligibility_assessment_id
            )
            generation = (
                None
                if predecessor.generation_record_id is None
                else await uow.generations.get_exact(predecessor.generation_record_id)
            )
            if generation is None or generation.workflow_run_id is None:
                raise ResourceConflictError(
                    details={"reason": "blueprint_generation_record_missing"}
                )
            context_command = GenerateBlueprintCommand(
                project_id=command.project_id,
                workflow_run_id=generation.workflow_run_id,
                eligibility_assessment_id=assessment_id,
                model_policy=command.model_policy,
                request_id=command.request_id,
            )
            context = await _load_context(uow, self._actor, context_command, lock=False)
            prior_sections = await uow.blueprints.list_sections(predecessor.id)
            if len(prior_sections) != 10:
                raise ResourceConflictError(details={"reason": "blueprint_section_set_incomplete"})
            await uow.rollback()

        revision_payload = {
            **context.payload,
            "revision": {
                "policy_version": BLUEPRINT_REVIEW_POLICY_VERSION,
                "predecessor": {
                    "id": str(predecessor.id),
                    "version": predecessor.version,
                    "status": predecessor.status,
                    "sections": [item.structured_content for item in prior_sections],
                },
                "human_feedback": feedback,
                "human_rationale": rationale,
            },
        }
        generation_fingerprint = canonical_fingerprint(revision_payload)
        outcome = await self._generation.generate_structured(
            GenerateStructuredCommand(
                project_id=command.project_id,
                workflow_run_id=context.workflow.id,
                task_name=BLUEPRINT_REVISION_TASK_NAME,
                input_fingerprint=generation_fingerprint,
                prompt_version=BLUEPRINT_REVISION_PROMPT_VERSION,
                schema_name="implementation_blueprint_revision",
                schema_version=BLUEPRINT_REVISION_SCHEMA_VERSION,
                output_type=BlueprintGenerationResult,
                messages=(
                    StructuredGenerationMessage(
                        role="user", content=canonical_json(revision_payload)
                    ),
                ),
                model_policy=command.model_policy,
                provider_json_schema=_provider_schema(tuple(context.references)),
                schema_variant_policy_version=BLUEPRINT_REFERENCE_SCHEMA_POLICY_VERSION,
            )
        )
        normalized_sections, section_targets = _validate_output(context, outcome.value)

        async with self._uow_factory(command.project_id) as uow:
            replay = await self._replay(uow, _REVISE, command.command_id, fingerprint)
            if replay is not None:
                await uow.rollback()
                return replay
            locked = await self._locked_expected(uow, command)
            require_revision_status(locked.status)
            current = await uow.blueprints.get_current_for_update()
            if current is None or current.id != locked.id:
                raise ResourceConflictError(details={"reason": "blueprint_not_current"})
            refreshed = await _load_context(uow, self._actor, context_command, lock=True)
            if refreshed.fingerprint != context.fingerprint:
                raise ResourceConflictError(details={"reason": "blueprint_revision_stale_context"})
            assessment_owner = await uow.blueprints.get_by_eligibility(assessment_id)
            if assessment_owner is not None and assessment_owner.id != locked.id:
                raise ResourceConflictError(
                    details={"reason": "blueprint_revision_assessment_in_use"}
                )
            await uow.blueprints.mark_superseded(locked.id, expected_status=locked.status)
            blueprint = await uow.blueprints.add(
                NewImplementationBlueprint(
                    id=uuid5(_IDENTITY_NAMESPACE, f"blueprint:{outcome.generation_record.id}"),
                    project_id=command.project_id,
                    lineage_id=locked.lineage_id,
                    version=locked.version + 1,
                    eligibility_assessment_id=refreshed.assessment.id,
                    eligibility_status=refreshed.assessment.status,
                    research_question_id=refreshed.assessment.research_question_id,
                    method_model_id=refreshed.assessment.method_model_id,
                    method_model_assessment_id=refreshed.assessment.method_model_assessment_id,
                    mental_model_id=refreshed.assessment.mental_model_id,
                    mental_model_assessment_id=refreshed.assessment.mental_model_assessment_id,
                    paper_repository_relationship_id=(
                        refreshed.assessment.paper_repository_relationship_id
                    ),
                    repository_snapshot_id=refreshed.assessment.repository_snapshot_id,
                    status=status_for_eligibility(refreshed.assessment.status).value,
                    confidence=outcome.value.confidence,
                    generation_record_id=outcome.generation_record.id,
                    supersedes_id=locked.id,
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
                            content_schema_version=BLUEPRINT_REVISION_SCHEMA_VERSION,
                            structured_content=structured_content,
                            explicitness=proposal.explicitness.value,
                            confidence=proposal.confidence,
                        )
                    )
                )
            dependencies = await _persist_dependencies(
                uow=uow,
                context=refreshed,
                blueprint=blueprint,
                sections=tuple(sections),
                section_targets=section_targets,
            )
            event = await self._append_event(
                uow,
                operation_type=_REVISE,
                command_id=command.command_id,
                fingerprint=fingerprint,
                rationale=rationale,
                request_id=command.request_id,
                inputs=(
                    _blueprint_ref(locked),
                    {"type": "generation_record", "id": str(outcome.generation_record.id)},
                ),
                outputs=(_blueprint_ref(blueprint),),
            )
            history = await uow.events.list_for_knowledge_lineage(blueprint.lineage_id)
            await uow.commit()
            return BlueprintReviewResult(
                blueprint=blueprint,
                sections=tuple(sections),
                dependencies=dependencies,
                history=history,
                event=event,
                replayed=False,
            )

    async def _expected(
        self, uow: UnitOfWork, command: AcceptBlueprintCommand | ReviseBlueprintCommand
    ) -> ImplementationBlueprintData:
        project = await uow.projects.get()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        blueprint = await uow.blueprints.get_exact(command.blueprint_id)
        return _require_expected(blueprint, command)

    async def _locked_expected(
        self, uow: UnitOfWork, command: AcceptBlueprintCommand | ReviseBlueprintCommand
    ) -> ImplementationBlueprintData:
        project = await uow.projects.get_for_update()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        blueprint = await uow.blueprints.get_exact_for_update(command.blueprint_id)
        return _require_expected(blueprint, command)

    async def _require_current_and_valid(
        self, uow: UnitOfWork, blueprint: ImplementationBlueprintData
    ) -> None:
        current = await uow.blueprints.get_current_for_update()
        if current is None or current.id != blueprint.id:
            raise ResourceConflictError(details={"reason": "blueprint_not_current"})
        if await uow.dependencies.list_unresolved_invalidations(
            "implementation_blueprint", blueprint.id
        ):
            raise ResourceConflictError(details={"reason": "blueprint_stale"})
        generation = (
            None
            if blueprint.generation_record_id is None
            else await uow.generations.get_exact(blueprint.generation_record_id)
        )
        if generation is None or generation.workflow_run_id is None:
            raise ResourceConflictError(details={"reason": "blueprint_generation_record_missing"})
        context = await _load_context(
            uow,
            self._actor,
            GenerateBlueprintCommand(
                project_id=blueprint.project_id,
                workflow_run_id=generation.workflow_run_id,
                eligibility_assessment_id=blueprint.eligibility_assessment_id,
                model_policy=_recorded_model_policy(generation),
            ),
            lock=True,
        )
        if context.assessment.id != blueprint.eligibility_assessment_id:
            raise ResourceConflictError(details={"reason": "blueprint_dependency_changed"})

    async def _replay(
        self,
        uow: UnitOfWork,
        operation_type: str,
        command_id: UUID,
        fingerprint: str,
    ) -> BlueprintReviewResult | None:
        project = await uow.projects.get()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        event = await uow.events.get_by_idempotency_key(operation_type, str(command_id))
        if event is None:
            return None
        if event.input_fingerprint != fingerprint:
            raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
        output = _event_output(event)
        blueprint_id = UUID(str(output["id"]))
        blueprint = await uow.blueprints.get_exact(blueprint_id)
        if blueprint is None:
            raise InternalError(details={"reason": "blueprint_review_replay_target_missing"})
        version = output.get("version", blueprint.version)
        status = output.get("status", blueprint.status)
        if not isinstance(version, int) or not isinstance(status, str):
            raise InternalError(details={"reason": "blueprint_review_replay_output_invalid"})
        snapshot = replace(
            blueprint,
            version=version,
            status=status,
        )
        detail = await _detail(uow, snapshot)
        sections = tuple(
            replace(
                section,
                review_status="accepted" if operation_type == _ACCEPT else "candidate",
            )
            for section in detail.sections
        )
        return BlueprintReviewResult(
            blueprint=detail.blueprint,
            sections=sections,
            dependencies=detail.dependencies,
            history=detail.history,
            event=event,
            replayed=True,
        )

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
    ) -> OperationEventData:
        return await uow.events.append(
            NewOperationEvent(
                id=uuid4(),
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


def _require_expected(
    blueprint: ImplementationBlueprintData | None,
    command: AcceptBlueprintCommand | ReviseBlueprintCommand,
) -> ImplementationBlueprintData:
    if blueprint is None:
        raise ResourceNotFoundError(details={"resource": "implementation_blueprint"})
    if blueprint.version != command.expected_version or blueprint.status != command.expected_status:
        raise ResourceConflictError(details={"reason": "blueprint_expected_state_mismatch"})
    return blueprint


def _revision_command_fingerprint(
    command: ReviseBlueprintCommand, rationale: str, feedback: str
) -> str:
    return canonical_fingerprint(
        {
            "policy_version": BLUEPRINT_REVIEW_POLICY_VERSION,
            "blueprint_id": command.blueprint_id,
            "expected_version": command.expected_version,
            "expected_status": command.expected_status,
            "eligibility_assessment_id": command.eligibility_assessment_id,
            "rationale": rationale,
            "feedback": feedback,
            "model_policy": {
                "provider": command.model_policy.provider,
                "model_version": command.model_policy.model_version,
                "policy_version": command.model_policy.policy_version,
                "reasoning_version": command.model_policy.reasoning_version,
                "temperature": command.model_policy.temperature,
                "configuration": dict(command.model_policy.configuration),
            },
        }
    )


def _blueprint_ref(value: ImplementationBlueprintData) -> dict[str, object]:
    return {
        "type": "implementation_blueprint",
        "id": str(value.id),
        "lineage_id": str(value.lineage_id),
        "version": value.version,
        "status": value.status,
    }


def _event_output(event: OperationEventData) -> dict[str, object]:
    for reference in event.output_refs:
        if reference.get("type") == "implementation_blueprint":
            return reference
    raise InternalError(details={"reason": "blueprint_review_replay_output_missing"})


def _recorded_model_policy(generation: GenerationRecordData) -> StructuredGenerationModelPolicy:
    return StructuredGenerationModelPolicy(
        provider=generation.provider,
        model_version=generation.model_version,
        policy_version="recorded-generation",
        reasoning_version=generation.reasoning_version,
        temperature=0,
        configuration={},
    )
