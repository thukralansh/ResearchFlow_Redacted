""" application composition behind control-plane-only graph nodes."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.application.extraction import (
    MAX_EXTRACTION_BATCHES_PER_REQUEST,
    ExtractionSelection,
    ExtractionService,
    ExtractPropositionsCommand,
)
from researchflow.application.mental_models import (
    ComposeInitialMentalModelCommand,
    MentalModelService,
)
from researchflow.application.method_extraction import (
    ExtractMethodModelCommand,
    MethodExtractionService,
)
from researchflow.application.proposition_extraction_contracts import PROPOSITION_EXTRACTION_TASK
from researchflow.application.repository_discovery import (
    DiscoverRepositoryCommand,
    RepositoryDiscoveryService,
)
from researchflow.application.review_gates import (
    PrimarySourceReviewGateService,
    accepted_method_model_id_from_review_event,
)
from researchflow.application.workflow_runs import (
    CreateWorkflowInterruptCommand,
    WorkflowLifecycleNodeContext,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.repository_roles import DiscoveryLimits
from researchflow.errors import ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import StructuredGenerationModelPolicy
from researchflow.ports.repositories import NewOperationEvent
from researchflow.ports.unit_of_work import UnitOfWorkFactory
from researchflow.workflows.context import (
    AcceptedReviewOutcome,
    MethodExtractionOutcome,
    PropositionExtractionOutcome,
    ScientificReviewOutcome,
)
from researchflow.workflows.state import ProjectWorkflowState, WorkflowStage

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/workflow-fs35/v1")


class ProjectAnalysisWorkflowContext:
    """Call authoritative services; retain no scientific state in the graph runtime."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        lifecycle: WorkflowLifecycleNodeContext,
        extraction: ExtractionService,
        method_extraction: MethodExtractionService,
        mental_models: MentalModelService,
        review_gate: PrimarySourceReviewGateService,
        repository_discovery: RepositoryDiscoveryService,
        model_policy: StructuredGenerationModelPolicy,
        repository_discovery_version: str,
        repository_limits: DiscoveryLimits,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._lifecycle = lifecycle
        self._extraction = extraction
        self._method_extraction = method_extraction
        self._mental_models = mental_models
        self._review_gate = review_gate
        self._repository_discovery = repository_discovery
        self._model_policy = model_policy
        self._repository_discovery_version = repository_discovery_version
        self._repository_limits = repository_limits

    async def commit_stage(
        self, state: ProjectWorkflowState, stage: WorkflowStage
    ) -> ProjectWorkflowState:
        return await self._lifecycle.commit_stage(state, stage)

    async def validate_inputs(self, state: ProjectWorkflowState) -> None:
        async with self._uow_factory(state.project_id) as uow:
            project = await uow.projects.get()
            question = await uow.research_questions.get_exact(state.primary_research_question_id)
            workflow = await uow.workflows.get_exact(state.workflow_run_id)
            if project is None or project.created_by_id != self._actor.actor_id:
                raise ResourceNotFoundError(details={"resource": "project"})
            if question is None or project.primary_research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "primary_question_not_current"})
            if workflow is None or workflow.primary_research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "workflow_question_mismatch"})
            if (
                state.primary_source_processing_run_id is None
                or state.primary_question_source_relationship_id is None
            ):
                raise ResourceConflictError(details={"reason": "primary_source_required"})
            await uow.rollback()

    async def validate_primary_source(self, state: ProjectWorkflowState) -> None:
        if (
            state.primary_source_processing_run_id is None
            or state.primary_question_source_relationship_id is None
        ):
            raise ResourceConflictError(details={"reason": "primary_source_required"})
        async with self._uow_factory(state.project_id) as uow:
            relationship = await uow.sources.get_current_primary_paper()
            run = await uow.sources.get_processing_run_exact(state.primary_source_processing_run_id)
            if (
                relationship is None
                or relationship.id != state.primary_question_source_relationship_id
                or relationship.research_question_id != state.primary_research_question_id
                or relationship.review_status != KnowledgeReviewStatus.ACCEPTED.value
                or relationship.lifecycle_status != KnowledgeLifecycleStatus.CURRENT.value
            ):
                raise ResourceConflictError(details={"reason": "accepted_primary_paper_missing"})
            if run is None or run.source_id != relationship.source_id or run.status != "succeeded":
                raise ResourceConflictError(details={"reason": "source_processing_not_current"})
            await uow.rollback()

    async def discover_repository(self, state: ProjectWorkflowState) -> UUID | None:
        if state.paper_repository_relationship_id is None:
            if state.repository_snapshot_id is not None:
                raise ResourceConflictError(
                    details={"reason": "repository_context_pair_incomplete"}
                )
            return None
        if state.repository_snapshot_id is None:
            raise ResourceConflictError(details={"reason": "repository_context_pair_incomplete"})
        view = await self._repository_discovery.discover(
            DiscoverRepositoryCommand(
                project_id=state.project_id,
                repository_snapshot_id=state.repository_snapshot_id,
                paper_repository_relationship_id=state.paper_repository_relationship_id,
                discovery_version=self._repository_discovery_version,
                limits=self._repository_limits,
            )
        )
        if view.run.status != "succeeded":
            raise ResourceConflictError(details={"reason": "repository_discovery_failed"})
        return view.run.id

    async def extract_propositions(
        self, state: ProjectWorkflowState
    ) -> PropositionExtractionOutcome:
        if state.primary_source_processing_run_id is None:
            return PropositionExtractionOutcome(False, None, "workflow.source_processing_missing")
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            view = await self._extraction.extract(
                ExtractPropositionsCommand(
                    project_id=state.project_id,
                    workflow_run_id=state.workflow_run_id,
                    source_processing_run_id=state.primary_source_processing_run_id,
                    selection=ExtractionSelection(),
                    model_policy=self._model_policy,
                    max_batches=MAX_EXTRACTION_BATCHES_PER_REQUEST,
                    continuation_cursor=cursor,
                )
            )
            if view.complete:
                return PropositionExtractionOutcome(True, None)
            next_cursor = view.continuation_cursor
            if next_cursor is None or next_cursor == cursor or next_cursor in seen:
                await self._record_incomplete_extraction(
                    state,
                    continuation_cursor=next_cursor,
                    status=view.status,
                )
                return PropositionExtractionOutcome(
                    False,
                    next_cursor,
                    "workflow.proposition_extraction_incomplete",
                )
            seen.add(next_cursor)
            cursor = next_cursor

    async def _record_incomplete_extraction(
        self,
        state: ProjectWorkflowState,
        *,
        continuation_cursor: str | None,
        status: str,
    ) -> None:
        identity = continuation_cursor or "missing-cursor"
        key = str(uuid5(_NAMESPACE, f"incomplete:{state.workflow_run_id}:{identity}"))
        async with self._uow_factory(state.project_id) as uow:
            if (
                await uow.events.get_by_idempotency_key(
                    "workflow.proposition_extraction_incomplete", key
                )
                is None
            ):
                await uow.events.append(
                    NewOperationEvent(
                        id=UUID(key),
                        project_id=state.project_id,
                        workflow_run_id=state.workflow_run_id,
                        operation_type="workflow.proposition_extraction_incomplete",
                        created_by_type="system",
                        created_by_id=None,
                        output_refs=(
                            {
                                "type": "extraction_continuation",
                                "cursor": continuation_cursor,
                                "status": status,
                            },
                        ),
                        reason=(
                            "Candidate extraction did not reach complete; durable generated "
                            "artifacts remain available for authorized same-run retry."
                        ),
                        idempotency_key=key,
                        input_fingerprint=state.node_input_fingerprint,
                    )
                )
            await uow.commit()

    async def extract_method_model(self, state: ProjectWorkflowState) -> MethodExtractionOutcome:
        if not state.proposition_extraction_complete:
            return MethodExtractionOutcome(None, "workflow.proposition_extraction_incomplete")
        if state.primary_source_processing_run_id is None:
            return MethodExtractionOutcome(None, "workflow.source_processing_missing")
        view = await self._method_extraction.extract(
            ExtractMethodModelCommand(
                project_id=state.project_id,
                workflow_run_id=state.workflow_run_id,
                source_processing_run_id=state.primary_source_processing_run_id,
                model_policy=self._model_policy,
            )
        )
        if view.status != "succeeded" or view.method_model_id is None:
            return MethodExtractionOutcome(
                view.method_model_id,
                view.error_code or "workflow.method_extraction_incomplete",
            )
        return MethodExtractionOutcome(view.method_model_id)

    async def validate_grounding(self, state: ProjectWorkflowState) -> None:
        if state.current_method_model_id is None:
            raise ResourceConflictError(details={"reason": "method_model_missing"})
        async with self._uow_factory(state.project_id) as uow:
            model = await uow.method_models.get_model_exact(state.current_method_model_id)
            if model is None or model.current_assessment_id is None:
                raise ResourceConflictError(details={"reason": "method_assessment_missing"})
            members = await uow.method_models.list_members(model.id)
            if not members or any(
                member.element.grounding_assessment_id is None for member in members
            ):
                raise ResourceConflictError(details={"reason": "method_grounding_incomplete"})
            generations = await uow.generations.list_for_workflow(state.workflow_run_id)
            generation_ids = {
                item.id
                for item in generations
                if item.task_name == PROPOSITION_EXTRACTION_TASK and item.status == "succeeded"
            }
            propositions = await uow.knowledge.list_review_items()
            generated = [
                item for item in propositions if item.generation_record_id in generation_ids
            ]
            if any(item.grounding_assessment_id is None for item in generated):
                raise ResourceConflictError(details={"reason": "proposition_grounding_incomplete"})
            await uow.rollback()

    async def prepare_scientific_review(
        self, state: ProjectWorkflowState
    ) -> ScientificReviewOutcome:
        if state.current_method_model_id is None:
            raise ResourceConflictError(details={"reason": "method_model_missing"})
        gate = await self._review_gate.status(state.project_id, state.current_method_model_id)
        command_id = uuid5(
            _NAMESPACE,
            f"review:{state.workflow_run_id}:{state.current_method_model_id}:{gate.gate_fingerprint}",
        )
        interrupt = await self._lifecycle.create_interrupt(
            CreateWorkflowInterruptCommand(
                project_id=state.project_id,
                workflow_run_id=state.workflow_run_id,
                interrupt_kind="scientific_review",
                expected_context_fingerprint=gate.gate_fingerprint,
                target_refs=(
                    {"type": "workflow_run", "id": str(state.workflow_run_id)},
                    {"type": "method_model", "id": str(state.current_method_model_id)},
                ),
                command_id=command_id,
            )
        )
        return ScientificReviewOutcome(interrupt.id, gate.gate_fingerprint)

    async def validate_completed_review(self, state: ProjectWorkflowState) -> AcceptedReviewOutcome:
        if state.current_method_model_id is None:
            return AcceptedReviewOutcome(None, 0, "workflow.method_model_missing")
        gate = await self._review_gate.status(state.project_id, state.current_method_model_id)
        accepted_method_id = accepted_method_model_id_from_review_event(gate.completed_event)
        if not gate.ready or accepted_method_id != state.current_method_model_id:
            return AcceptedReviewOutcome(None, 0, "workflow.scientific_review_incomplete")
        accepted_count = await self._accepted_proposition_count(state)
        return AcceptedReviewOutcome(accepted_method_id, accepted_count)

    async def compose_initial_mental_model(self, state: ProjectWorkflowState) -> UUID:
        if state.current_method_model_id is None:
            raise ResourceConflictError(details={"reason": "accepted_method_model_missing"})
        view = await self._mental_models.compose_initial(
            ComposeInitialMentalModelCommand(
                project_id=state.project_id,
                workflow_run_id=state.workflow_run_id,
                method_model_id=state.current_method_model_id,
                model_policy=self._model_policy,
            )
        )
        return view.model.id

    async def _accepted_proposition_count(self, state: ProjectWorkflowState) -> int:
        async with self._uow_factory(state.project_id) as uow:
            generations = await uow.generations.list_for_workflow(state.workflow_run_id)
            generation_ids = {
                item.id
                for item in generations
                if item.task_name == PROPOSITION_EXTRACTION_TASK and item.status == "succeeded"
            }
            values = await uow.knowledge.list_review_items()
            lineages = {
                item.lineage_id for item in values if item.generation_record_id in generation_ids
            }
            accepted = sum(
                item.lineage_id in lineages
                and item.review_status == KnowledgeReviewStatus.ACCEPTED.value
                and item.lifecycle_status == KnowledgeLifecycleStatus.CURRENT.value
                for item in values
            )
            await uow.rollback()
            return accepted
