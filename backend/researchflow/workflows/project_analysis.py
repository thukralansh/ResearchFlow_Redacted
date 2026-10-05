"""Thin, durable early project-analysis workflow for ."""

from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from researchflow.workflows.context import WorkflowNodeContext
from researchflow.workflows.state import (
    ProjectWorkflowState,
    ProjectWorkflowStatePayload,
    WorkflowControlStatus,
    WorkflowStage,
    validate_checkpoint_state,
)


def build_project_analysis_graph(*, context: WorkflowNodeContext, checkpointer: Any) -> Any:
    """Compile ID-only orchestration around authoritative application services."""

    async def entry(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        return (await context.commit_stage(_state(raw), WorkflowStage.ENTRY)).checkpoint_values()

    async def validate_inputs(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        await context.validate_inputs(state)
        return (
            await context.commit_stage(state, WorkflowStage.VALIDATE_INPUTS)
        ).checkpoint_values()

    async def process_source(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        await context.validate_primary_source(state)
        return (
            await context.commit_stage(state, WorkflowStage.PROCESS_PRIMARY_SOURCE)
        ).checkpoint_values()

    async def discover_repository(
        raw: ProjectWorkflowStatePayload,
    ) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        run_id = await context.discover_repository(state)
        state = state.model_copy(update={"repository_discovery_run_id": run_id})
        return (
            await context.commit_stage(state, WorkflowStage.DISCOVER_REPOSITORY)
        ).checkpoint_values()

    async def extract_propositions(
        raw: ProjectWorkflowStatePayload,
    ) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        outcome = await context.extract_propositions(state)
        state = state.model_copy(
            update={
                "proposition_extraction_complete": outcome.complete,
                "error_code": outcome.error_code,
            }
        )
        return (
            await context.commit_stage(state, WorkflowStage.EXTRACT_PROPOSITIONS)
        ).checkpoint_values()

    async def extract_method_model(
        raw: ProjectWorkflowStatePayload,
    ) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        outcome = await context.extract_method_model(state)
        state = state.model_copy(
            update={
                "current_method_model_id": outcome.method_model_id,
                "error_code": outcome.error_code,
            }
        )
        return (
            await context.commit_stage(state, WorkflowStage.EXTRACT_METHOD_MODEL)
        ).checkpoint_values()

    async def validate_grounding(
        raw: ProjectWorkflowStatePayload,
    ) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        await context.validate_grounding(state)
        return (
            await context.commit_stage(state, WorkflowStage.VALIDATE_GROUNDING)
        ).checkpoint_values()

    async def prepare_review(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        committed = await context.commit_stage(state, WorkflowStage.PREPARE_SCIENTIFIC_REVIEW)
        outcome = await context.prepare_scientific_review(committed)
        return committed.model_copy(
            update={
                "pending_workflow_interrupt_id": outcome.interrupt_id,
                "review_context_fingerprint": outcome.context_fingerprint,
                "status": WorkflowControlStatus.WAITING_FOR_REVIEW,
            }
        ).checkpoint_values()

    async def await_review(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        if state.pending_workflow_interrupt_id is None or state.review_context_fingerprint is None:
            raise ValueError("scientific_review_interrupt_missing")
        resumed = interrupt(
            {
                "workflow_interrupt_id": str(state.pending_workflow_interrupt_id),
                "context_fingerprint": state.review_context_fingerprint,
                "kind": "scientific_review",
            }
        )
        if not isinstance(resumed, dict) or resumed != {
            "workflow_interrupt_id": str(state.pending_workflow_interrupt_id),
            "context_fingerprint": state.review_context_fingerprint,
        }:
            raise ValueError("scientific_review_resume_payload_invalid")
        state = state.model_copy(update={"status": WorkflowControlStatus.RUNNING})
        return (
            await context.commit_stage(state, WorkflowStage.AWAIT_SCIENTIFIC_REVIEW)
        ).checkpoint_values()

    async def finalize_method(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        outcome = await context.validate_completed_review(state)
        error_code = outcome.error_code
        if outcome.accepted_proposition_count == 0:
            error_code = "workflow.zero_accepted_propositions"
        state = state.model_copy(
            update={
                "current_method_model_id": outcome.accepted_method_model_id,
                "error_code": error_code,
            }
        )
        return (
            await context.commit_stage(state, WorkflowStage.FINALIZE_ACCEPTED_METHOD)
        ).checkpoint_values()

    async def compose_mental_model(
        raw: ProjectWorkflowStatePayload,
    ) -> ProjectWorkflowStatePayload:
        state = _state(raw)
        model_id = await context.compose_initial_mental_model(state)
        state = state.model_copy(update={"current_mental_model_id": model_id})
        return (
            await context.commit_stage(state, WorkflowStage.COMPOSE_INITIAL_MENTAL_MODEL)
        ).checkpoint_values()

    async def final(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        return (await context.commit_stage(_state(raw), WorkflowStage.FINAL)).checkpoint_values()

    async def error(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowStatePayload:
        return (await context.commit_stage(_state(raw), WorkflowStage.ERROR)).checkpoint_values()

    builder = StateGraph(ProjectWorkflowStatePayload)
    nodes = (
        ("entry", entry),
        ("validate_inputs", validate_inputs),
        ("process_source", process_source),
        ("discover_repository", discover_repository),
        ("extract_propositions", extract_propositions),
        ("extract_method_model", extract_method_model),
        ("validate_grounding", validate_grounding),
        ("prepare_review", prepare_review),
        ("await_review", await_review),
        ("finalize_method", finalize_method),
        ("compose_mental_model", compose_mental_model),
        ("final", final),
        ("error", error),
    )
    for name, node in nodes:
        builder.add_node(name, cast(Any, node))
    builder.add_edge(START, "entry")
    builder.add_edge("entry", "validate_inputs")
    builder.add_edge("validate_inputs", "process_source")
    builder.add_edge("process_source", "discover_repository")
    builder.add_edge("discover_repository", "extract_propositions")
    builder.add_conditional_edges(
        "extract_propositions",
        cast(Any, _route_error),
        {"next": "extract_method_model", "error": "error"},
    )
    builder.add_conditional_edges(
        "extract_method_model",
        cast(Any, _route_error),
        {"next": "validate_grounding", "error": "error"},
    )
    builder.add_edge("validate_grounding", "prepare_review")
    builder.add_edge("prepare_review", "await_review")
    builder.add_edge("await_review", "finalize_method")
    builder.add_conditional_edges(
        "finalize_method",
        cast(Any, _route_error),
        {"next": "compose_mental_model", "error": "error"},
    )
    builder.add_edge("compose_mental_model", "final")
    builder.add_edge("final", END)
    builder.add_edge("error", END)
    return builder.compile(checkpointer=checkpointer)


def _state(raw: ProjectWorkflowStatePayload) -> ProjectWorkflowState:
    return validate_checkpoint_state(dict(raw))


def _route_error(raw: ProjectWorkflowStatePayload) -> str:
    return "error" if raw.get("error_code") else "next"
