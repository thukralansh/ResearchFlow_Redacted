"""Temporary extraction-only WorkflowRun execution boundary.

This seam may create and execute proposition extraction only. Adding any other
workflow stage before  requires a new architecture review.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.application.extraction import (
    ExtractionRequestView,
    ExtractionSelection,
    ExtractionService,
    ExtractPropositionsCommand,
)
from researchflow.domain.actor import ActorContext
from researchflow.errors import (
    InternalError,
    ResearchFlowError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.providers import StructuredGenerationModelPolicy
from researchflow.ports.repositories import NewOperationEvent, NewWorkflowRun, WorkflowRunData
from researchflow.ports.unit_of_work import UnitOfWorkFactory

EXTRACTION_EXECUTION_START: Final = "workflow.extraction.start"
EXTRACTION_EXECUTION_FINISH: Final = "workflow.extraction.finish"
EXTRACTION_EXECUTION_FAILURE: Final = "workflow.extraction.failure"
EXTRACTION_EXECUTION_STAGE: Final = "candidate_extraction"
EXTRACTION_REVIEW_STAGE: Final = "candidate_review"
_IDENTITY_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/pre-fs34-extraction-execution"
)


@dataclass(frozen=True, slots=True)
class ExecuteExtractionCommand:
    project_id: UUID
    source_processing_run_id: UUID
    selection: ExtractionSelection
    model_policy: StructuredGenerationModelPolicy
    idempotency_key: str
    max_batches: int = 16
    workflow_run_id: UUID | None = None
    continuation_cursor: str | None = None
    explicit_retry: bool = False
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionWorkflowView:
    id: UUID
    status: str
    current_stage: str | None
    lock_version: int
    last_error_code: str | None
    last_error_detail: dict[str, object] | None

    def json_value(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "status": self.status,
            "current_stage": self.current_stage,
            "lock_version": self.lock_version,
            "last_error_code": self.last_error_code,
            "last_error_detail": self.last_error_detail,
        }

    @classmethod
    def from_json_value(cls, value: dict[str, object]) -> ExtractionWorkflowView:
        raw_error_detail = value.get("last_error_detail")
        if raw_error_detail is not None and not isinstance(raw_error_detail, dict):
            raise InternalError(details={"reason": "workflow_finish_event_invalid"})
        try:
            return cls(
                id=UUID(str(value["id"])),
                status=str(value["status"]),
                current_stage=(
                    None if value.get("current_stage") is None else str(value["current_stage"])
                ),
                lock_version=int(str(value["lock_version"])),
                last_error_code=(
                    None if value.get("last_error_code") is None else str(value["last_error_code"])
                ),
                last_error_detail=(None if raw_error_detail is None else dict(raw_error_detail)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InternalError(
                details={"reason": "workflow_finish_event_invalid"}, cause=error
            ) from error


@dataclass(frozen=True, slots=True)
class ExtractionExecutionView:
    workflow: ExtractionWorkflowView
    extraction: ExtractionRequestView
    replayed: bool


class ExtractionExecutionService:
    """Create/replay one run and synchronously execute proposition extraction."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        extraction_service: ExtractionService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._extraction = extraction_service

    async def execute(self, command: ExecuteExtractionCommand) -> ExtractionExecutionView:
        fingerprint = _fingerprint(command)
        workflow, exact_replay = await self._start_or_replay(command, fingerprint)
        if exact_replay is not None:
            replayed_workflow, replayed_extraction = exact_replay
            return ExtractionExecutionView(
                workflow=replayed_workflow,
                extraction=replayed_extraction,
                replayed=True,
            )

        try:
            extraction = await self._extraction.extract(
                ExtractPropositionsCommand(
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    source_processing_run_id=command.source_processing_run_id,
                    selection=command.selection,
                    model_policy=command.model_policy,
                    max_batches=command.max_batches,
                    continuation_cursor=command.continuation_cursor,
                    explicit_rerun=False,
                    request_id=command.request_id,
                )
            )
        except ResearchFlowError as error:
            await self._fail(command, workflow.id, fingerprint, error)
            raise
        except Exception as error:
            normalized = InternalError(
                details={"reason": "extraction_execution_failed"}, cause=error
            )
            await self._fail(command, workflow.id, fingerprint, normalized)
            raise

        status = (
            "failed"
            if extraction.status == "failed"
            else ("waiting_for_review" if extraction.complete else "running")
        )
        stage = EXTRACTION_REVIEW_STAGE if extraction.complete else EXTRACTION_EXECUTION_STAGE
        failed_batch = next(
            (batch for batch in extraction.batches if batch.error_code is not None),
            None,
        )
        failure_code = "provider.failure" if failed_batch is None else failed_batch.error_code
        failure_detail: dict[str, object] | None = None
        if status == "failed":
            failure_detail = {
                "reason": (
                    "extraction_failed"
                    if failed_batch is None
                    else f"extraction_{failed_batch.status}"
                ),
                "batch_sequence": (None if failed_batch is None else failed_batch.batch_sequence),
                "details": (None if failed_batch is None else failed_batch.error_detail),
            }
        async with self._uow_factory(command.project_id) as uow:
            current = await uow.workflows.get_for_update(workflow.id)
            if current is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            updated = await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=current.lock_version,
                status=status,
                current_stage=stage,
                error_code=(failure_code if status == "failed" else None),
                error_detail=failure_detail,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=_id(command.project_id, command.idempotency_key, "finish"),
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type=EXTRACTION_EXECUTION_FINISH,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"workflow_run_id": str(workflow.id)},),
                    output_refs=(
                        {
                            "workflow": _workflow_view(updated).json_value(),
                            "extraction_execution": extraction.json_value(),
                        },
                    ),
                    reason="Bounded proposition extraction execution completed.",
                    idempotency_key=command.idempotency_key,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        return ExtractionExecutionView(
            workflow=_workflow_view(updated), extraction=extraction, replayed=False
        )

    async def _start_or_replay(
        self,
        command: ExecuteExtractionCommand,
        fingerprint: str,
    ) -> tuple[
        WorkflowRunData,
        tuple[ExtractionWorkflowView, ExtractionRequestView] | None,
    ]:
        async with self._uow_factory(command.project_id) as uow:
            existing_start = await uow.events.get_by_idempotency_key(
                EXTRACTION_EXECUTION_START, command.idempotency_key
            )
            if existing_start is not None:
                if existing_start.input_fingerprint != fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                workflow_id = _event_workflow_id(existing_start.output_refs)
                workflow = await uow.workflows.get_exact(workflow_id)
                if workflow is None:
                    raise InternalError(details={"reason": "workflow_start_event_orphaned"})
                finish = await uow.events.get_by_idempotency_key(
                    EXTRACTION_EXECUTION_FINISH, command.idempotency_key
                )
                if finish is not None:
                    execution = _event_execution(finish.output_refs)
                    await uow.rollback()
                    return workflow, execution
                failure = await uow.events.get_by_idempotency_key(
                    EXTRACTION_EXECUTION_FAILURE, command.idempotency_key
                )
                generation_attempts = await uow.generations.list_for_workflow(workflow.id)
                if failure is not None or any(
                    attempt.status == "failed" for attempt in generation_attempts
                ):
                    raise ResourceConflictError(details={"reason": "explicit_retry_required"})
                await uow.rollback()
                return workflow, None

            project = await uow.projects.get()
            if project is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            question = await uow.research_questions.get_current_primary()
            if question is None or question.id != project.primary_research_question_id:
                raise ResourceConflictError(details={"reason": "primary_question_not_current"})
            paper = await uow.sources.get_current_primary_paper()
            if paper is None or paper.research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "accepted_primary_paper_missing"})
            run = await uow.sources.get_processing_run_exact(command.source_processing_run_id)
            if run is None or run.source_id != paper.source_id or run.status != "succeeded":
                raise ResourceConflictError(details={"reason": "processing_run_not_eligible"})

            repository = await uow.repositories.get_accepted_current_relationship()
            if command.workflow_run_id is None:
                if command.explicit_retry or command.continuation_cursor is not None:
                    raise ResourceConflictError(details={"reason": "existing_workflow_required"})
                workflow_id = _id(command.project_id, command.idempotency_key, "workflow")
                workflow = await uow.workflows.add(
                    NewWorkflowRun(
                        id=workflow_id,
                        project_id=command.project_id,
                        workflow_type="full_analysis",
                        status="running",
                        current_stage=EXTRACTION_EXECUTION_STAGE,
                        langgraph_thread_id=f"pre-fs34-extraction:{workflow_id}",
                        primary_research_question_id=question.id,
                        primary_source_processing_run_id=run.id,
                        primary_question_source_relationship_id=paper.id,
                        paper_repository_relationship_id=(
                            None if repository is None else repository.id
                        ),
                        repository_snapshot_id=(
                            None if repository is None else repository.repository_snapshot_id
                        ),
                        started_at=datetime.now(UTC),
                    )
                )
            else:
                workflow = await uow.workflows.get_for_update(command.workflow_run_id)
                if workflow is None:
                    raise ResourceNotFoundError(details={"resource": "workflow_run"})
                if (
                    workflow.primary_research_question_id != question.id
                    or workflow.primary_source_processing_run_id != run.id
                    or workflow.primary_question_source_relationship_id != paper.id
                ):
                    raise ResourceConflictError(details={"reason": "workflow_context_mismatch"})
                if command.explicit_retry:
                    if workflow.status != "failed":
                        raise ResourceConflictError(details={"reason": "workflow_not_retryable"})
                    workflow = await uow.workflows.update_status(
                        workflow.id,
                        expected_lock_version=workflow.lock_version,
                        status="running",
                        current_stage=EXTRACTION_EXECUTION_STAGE,
                    )
                elif command.continuation_cursor is None or workflow.status != "running":
                    raise ResourceConflictError(details={"reason": "continuation_required"})

            await uow.events.append(
                NewOperationEvent(
                    id=_id(command.project_id, command.idempotency_key, "start"),
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type=EXTRACTION_EXECUTION_START,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=(
                        {
                            "source_processing_run_id": str(run.id),
                            "research_question_id": str(question.id),
                            "question_source_relationship_id": str(paper.id),
                        },
                    ),
                    output_refs=({"workflow_run_id": str(workflow.id)},),
                    reason="Started the bounded pre- proposition extraction execution.",
                    idempotency_key=command.idempotency_key,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return workflow, None

    async def _fail(
        self,
        command: ExecuteExtractionCommand,
        workflow_id: UUID,
        fingerprint: str,
        error: ResearchFlowError,
    ) -> None:
        async with self._uow_factory(command.project_id) as uow:
            workflow = await uow.workflows.get_for_update(workflow_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            await uow.workflows.update_status(
                workflow.id,
                expected_lock_version=workflow.lock_version,
                status="failed",
                current_stage=EXTRACTION_EXECUTION_STAGE,
                error_code=error.code.value,
                error_detail={"details": dict(error.public_details)},
            )
            await uow.events.append(
                NewOperationEvent(
                    id=_id(command.project_id, command.idempotency_key, "failure"),
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type=EXTRACTION_EXECUTION_FAILURE,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"workflow_run_id": str(workflow.id)},),
                    output_refs=({"error_code": error.code.value},),
                    reason="Bounded proposition extraction execution failed.",
                    idempotency_key=command.idempotency_key,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()


def _fingerprint(command: ExecuteExtractionCommand) -> str:
    identity = {
        "project_id": str(command.project_id),
        "source_processing_run_id": str(command.source_processing_run_id),
        "selection": command.selection.identity(),
        "model_policy": {
            "provider": command.model_policy.provider,
            "model_version": command.model_policy.model_version,
            "policy_version": command.model_policy.policy_version,
            "reasoning_version": command.model_policy.reasoning_version,
            "temperature": command.model_policy.temperature,
            "configuration": dict(command.model_policy.configuration),
        },
        "max_batches": command.max_batches,
        "workflow_run_id": (
            None if command.workflow_run_id is None else str(command.workflow_run_id)
        ),
        "continuation_cursor": command.continuation_cursor,
        "explicit_retry": command.explicit_retry,
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _id(project_id: UUID, idempotency_key: str, kind: str) -> UUID:
    return uuid5(_IDENTITY_NAMESPACE, f"{project_id}:{idempotency_key}:{kind}")


def _event_workflow_id(refs: tuple[dict[str, object], ...]) -> UUID:
    if len(refs) != 1 or refs[0].get("workflow_run_id") is None:
        raise InternalError(details={"reason": "workflow_start_event_invalid"})
    return UUID(str(refs[0]["workflow_run_id"]))


def _workflow_view(value: WorkflowRunData) -> ExtractionWorkflowView:
    return ExtractionWorkflowView(
        id=value.id,
        status=value.status,
        current_stage=value.current_stage,
        lock_version=value.lock_version,
        last_error_code=value.last_error_code,
        last_error_detail=value.last_error_detail,
    )


def _event_execution(
    refs: tuple[dict[str, object], ...],
) -> tuple[ExtractionWorkflowView, ExtractionRequestView]:
    if (
        len(refs) != 1
        or not isinstance(refs[0].get("workflow"), dict)
        or not isinstance(refs[0].get("extraction_execution"), dict)
    ):
        raise InternalError(details={"reason": "workflow_finish_event_invalid"})
    workflow = refs[0]["workflow"]
    extraction = refs[0]["extraction_execution"]
    assert isinstance(workflow, dict)
    assert isinstance(extraction, dict)
    return (
        ExtractionWorkflowView.from_json_value(workflow),
        ExtractionRequestView.from_json_value(extraction),
    )
