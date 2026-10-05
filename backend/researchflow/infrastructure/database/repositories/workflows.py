"""Workflow lifecycle adapter with explicit row locks and optimistic writes."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.workflow import (
    WorkflowInterruptRecord,
    WorkflowRunRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    NewWorkflowInterrupt,
    NewWorkflowRun,
    WorkflowInterruptData,
    WorkflowRunData,
)


def _to_workflow(row: WorkflowRunRecord) -> WorkflowRunData:
    return WorkflowRunData(
        id=row.id,
        project_id=row.project_id,
        workflow_type=row.workflow_type,
        status=row.status,
        current_stage=row.current_stage,
        langgraph_thread_id=row.langgraph_thread_id,
        primary_source_processing_run_id=row.primary_source_processing_run_id,
        primary_research_question_id=row.primary_research_question_id,
        primary_question_source_relationship_id=row.primary_question_source_relationship_id,
        paper_repository_relationship_id=row.paper_repository_relationship_id,
        repository_snapshot_id=row.repository_snapshot_id,
        current_method_model_id=row.current_method_model_id,
        current_mental_model_id=row.current_mental_model_id,
        pending_judgment_request_id=row.pending_judgment_request_id,
        graph_version=row.graph_version,
        checkpoint_namespace=row.checkpoint_namespace,
        lock_version=row.lock_version,
        started_at=row.started_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        last_error_code=row.last_error_code,
        last_error_detail=(None if row.last_error_detail is None else dict(row.last_error_detail)),
    )


def _to_interrupt(row: WorkflowInterruptRecord) -> WorkflowInterruptData:
    return WorkflowInterruptData(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        interrupt_kind=row.interrupt_kind,
        status=row.status,
        expected_context_fingerprint=row.expected_context_fingerprint,
        target_refs=tuple(dict(item) for item in row.target_refs),
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
        resolution_event_id=row.resolution_event_id,
        resolved_at=row.resolved_at,
        resumed_at=row.resumed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        lock_version=row.lock_version,
    )


class SqlAlchemyWorkflowsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, workflow: NewWorkflowRun) -> WorkflowRunData:
        if workflow.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_exact(workflow.id)
        if existing is not None:
            expected = (
                workflow.workflow_type,
                workflow.status,
                workflow.current_stage,
                workflow.langgraph_thread_id,
                workflow.primary_source_processing_run_id,
                workflow.primary_research_question_id,
                workflow.primary_question_source_relationship_id,
                workflow.paper_repository_relationship_id,
                workflow.repository_snapshot_id,
                workflow.current_method_model_id,
                workflow.current_mental_model_id,
                workflow.pending_judgment_request_id,
                workflow.graph_version,
                workflow.checkpoint_namespace,
            )
            actual = (
                existing.workflow_type,
                existing.status,
                existing.current_stage,
                existing.langgraph_thread_id,
                existing.primary_source_processing_run_id,
                existing.primary_research_question_id,
                existing.primary_question_source_relationship_id,
                existing.paper_repository_relationship_id,
                existing.repository_snapshot_id,
                existing.current_method_model_id,
                existing.current_mental_model_id,
                existing.pending_judgment_request_id,
                existing.graph_version,
                existing.checkpoint_namespace,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "workflow_id_reused"})

        row = WorkflowRunRecord(
            id=workflow.id,
            project_id=workflow.project_id,
            workflow_type=workflow.workflow_type,
            status=workflow.status,
            current_stage=workflow.current_stage,
            langgraph_thread_id=workflow.langgraph_thread_id,
            primary_source_processing_run_id=workflow.primary_source_processing_run_id,
            primary_research_question_id=workflow.primary_research_question_id,
            primary_question_source_relationship_id=(
                workflow.primary_question_source_relationship_id
            ),
            paper_repository_relationship_id=workflow.paper_repository_relationship_id,
            repository_snapshot_id=workflow.repository_snapshot_id,
            current_method_model_id=workflow.current_method_model_id,
            current_mental_model_id=workflow.current_mental_model_id,
            pending_judgment_request_id=workflow.pending_judgment_request_id,
            graph_version=workflow.graph_version,
            checkpoint_namespace=workflow.checkpoint_namespace,
            started_at=workflow.started_at,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_workflow(row)

    async def add_interrupt(self, interrupt: NewWorkflowInterrupt) -> WorkflowInterruptData:
        if interrupt.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_interrupt_exact(interrupt.id)
        if existing is not None:
            expected = (
                interrupt.workflow_run_id,
                interrupt.interrupt_kind,
                interrupt.expected_context_fingerprint,
                interrupt.target_refs,
                interrupt.created_by_type,
                interrupt.created_by_id,
            )
            actual = (
                existing.workflow_run_id,
                existing.interrupt_kind,
                existing.expected_context_fingerprint,
                existing.target_refs,
                existing.created_by_type,
                existing.created_by_id,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "workflow_interrupt_id_reused"})
        row = WorkflowInterruptRecord(
            id=interrupt.id,
            project_id=interrupt.project_id,
            workflow_run_id=interrupt.workflow_run_id,
            interrupt_kind=interrupt.interrupt_kind,
            status="pending",
            expected_context_fingerprint=interrupt.expected_context_fingerprint,
            target_refs=[dict(item) for item in interrupt.target_refs],
            created_by_type=interrupt.created_by_type,
            created_by_id=interrupt.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_interrupt(row)

    async def get_interrupt_exact(self, interrupt_id: UUID) -> WorkflowInterruptData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(WorkflowInterruptRecord).where(
                    WorkflowInterruptRecord.id == interrupt_id,
                    WorkflowInterruptRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_interrupt(row)

    async def get_interrupt_for_update(
        self, interrupt_id: UUID, *, nowait: bool = False
    ) -> WorkflowInterruptData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(WorkflowInterruptRecord)
                .where(
                    WorkflowInterruptRecord.id == interrupt_id,
                    WorkflowInterruptRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_interrupt(row)

    async def list_interrupts(self, workflow_run_id: UUID) -> tuple[WorkflowInterruptData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(WorkflowInterruptRecord)
                .where(
                    WorkflowInterruptRecord.project_id == self._project_id,
                    WorkflowInterruptRecord.workflow_run_id == workflow_run_id,
                )
                .order_by(WorkflowInterruptRecord.created_at, WorkflowInterruptRecord.id)
            )
        )
        return tuple(_to_interrupt(row) for row in result.scalars())

    async def update_interrupt(
        self,
        interrupt_id: UUID,
        *,
        expected_lock_version: int,
        status: str,
        resolution_event_id: UUID | None,
    ) -> WorkflowInterruptData:
        values: dict[str, object] = {
            "status": status,
            "resolution_event_id": resolution_event_id,
            "lock_version": WorkflowInterruptRecord.lock_version + 1,
            "updated_at": func.now(),
        }
        if status == "resolved":
            values["resolved_at"] = func.now()
        elif status == "resumed":
            values["resumed_at"] = func.now()
        statement = (
            update(WorkflowInterruptRecord)
            .where(
                WorkflowInterruptRecord.id == interrupt_id,
                WorkflowInterruptRecord.project_id == self._project_id,
                WorkflowInterruptRecord.lock_version == expected_lock_version,
            )
            .values(**values)
            .returning(WorkflowInterruptRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            if await self.get_interrupt_exact(interrupt_id) is None:
                raise ResourceConflictError(details={"reason": "workflow_interrupt_not_found"})
            raise ResourceConflictError(details={"reason": "stale_lock_version"})
        return _to_interrupt(row)

    async def get_exact(self, workflow_id: UUID) -> WorkflowRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(WorkflowRunRecord).where(
                    WorkflowRunRecord.id == workflow_id,
                    WorkflowRunRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_workflow(row)

    async def get_for_update(
        self, workflow_id: UUID, *, nowait: bool = False
    ) -> WorkflowRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.id == workflow_id,
                    WorkflowRunRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_workflow(row)

    async def get_active_full_analysis_for_update(self) -> WorkflowRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.project_id == self._project_id,
                    WorkflowRunRecord.workflow_type == "full_analysis",
                    WorkflowRunRecord.status.in_(
                        (
                            "queued",
                            "running",
                            "waiting_for_review",
                            "waiting_for_judgment",
                        )
                    ),
                )
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_workflow(row)

    async def update_status(
        self,
        workflow_id: UUID,
        *,
        expected_lock_version: int,
        status: str,
        current_stage: str | None,
        error_code: str | None = None,
        error_detail: dict[str, object] | None = None,
    ) -> WorkflowRunData:
        current = await self.get_exact(workflow_id)
        if current is None:
            raise ResourceConflictError(details={"reason": "workflow_not_found"})
        return await self.update_progress(
            workflow_id,
            expected_lock_version=expected_lock_version,
            status=status,
            current_stage=current_stage,
            current_method_model_id=current.current_method_model_id,
            current_mental_model_id=current.current_mental_model_id,
            error_code=error_code,
            error_detail=error_detail,
        )

    async def update_progress(
        self,
        workflow_id: UUID,
        *,
        expected_lock_version: int,
        status: str,
        current_stage: str | None,
        current_method_model_id: UUID | None,
        current_mental_model_id: UUID | None,
        error_code: str | None = None,
        error_detail: dict[str, object] | None = None,
    ) -> WorkflowRunData:
        values: dict[str, object] = {
            "status": status,
            "current_stage": current_stage,
            "current_method_model_id": current_method_model_id,
            "current_mental_model_id": current_mental_model_id,
            "lock_version": WorkflowRunRecord.lock_version + 1,
            "updated_at": func.now(),
            "last_error_code": error_code,
            "last_error_detail": error_detail,
        }
        if status == "running":
            values["started_at"] = func.coalesce(WorkflowRunRecord.started_at, func.now())
            values["completed_at"] = None
        elif status in {"completed", "failed", "cancelled"}:
            values["completed_at"] = func.greatest(
                func.clock_timestamp(), WorkflowRunRecord.started_at
            )
        statement = (
            update(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.id == workflow_id,
                WorkflowRunRecord.project_id == self._project_id,
                WorkflowRunRecord.lock_version == expected_lock_version,
            )
            .values(**values)
            .returning(WorkflowRunRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            if await self.get_exact(workflow_id) is None:
                raise ResourceConflictError(details={"reason": "workflow_not_found"})
            raise ResourceConflictError(details={"reason": "stale_lock_version"})
        return _to_workflow(row)
