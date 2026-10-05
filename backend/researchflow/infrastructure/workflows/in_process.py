"""Best-effort in-process launcher over durable LangGraph checkpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from uuid import UUID

from langgraph.types import Command

from researchflow.errors import ResourceConflictError
from researchflow.workflows.state import ProjectWorkflowState


class InProcessWorkflowLauncher:
    """Schedule graph work locally; checkpoint/Postgres state owns restart recovery."""

    def __init__(
        self,
        *,
        graph: Any,
        failure_handler: Callable[[ProjectWorkflowState, str], Awaitable[None]],
        recovery_failure_handler: Callable[[UUID, UUID, str], Awaitable[None]],
    ) -> None:
        self._graph = graph
        self._failure_handler = failure_handler
        self._recovery_failure_handler = recovery_failure_handler
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._known_states: dict[UUID, ProjectWorkflowState] = {}

    async def launch(self, state: ProjectWorkflowState) -> None:
        if await self.has_active_task(state.workflow_run_id):
            raise ResourceConflictError(details={"reason": "workflow_task_already_active"})
        self._known_states[state.workflow_run_id] = state
        self._start_task(state.workflow_run_id, self._invoke(state))

    async def retry(
        self, *, project_id: UUID, workflow_run_id: UUID, thread_id: str, namespace: str
    ) -> None:
        if await self.has_active_task(workflow_run_id):
            raise ResourceConflictError(details={"reason": "workflow_task_already_active"})
        state = self._known_states.get(workflow_run_id)
        if state is None:
            # The checkpoint, not reconstructed canonical content, is the only resume input.
            self._start_task(
                workflow_run_id,
                self._resume_checkpoint(project_id, workflow_run_id, thread_id, namespace),
            )
            return
        self._start_task(
            workflow_run_id,
            self._resume_checkpoint(project_id, workflow_run_id, thread_id, namespace),
        )

    async def resume(
        self,
        *,
        project_id: UUID,
        workflow_run_id: UUID,
        thread_id: str,
        namespace: str,
        interrupt_id: UUID,
        context_fingerprint: str,
    ) -> None:
        if await self.has_active_task(workflow_run_id):
            raise ResourceConflictError(details={"reason": "workflow_task_already_active"})
        payload = {
            "workflow_interrupt_id": str(interrupt_id),
            "context_fingerprint": context_fingerprint,
        }
        self._start_task(
            workflow_run_id,
            self._resume_checkpoint(
                project_id,
                workflow_run_id,
                thread_id,
                namespace,
                resume_payload=payload,
            ),
        )

    async def cancel(self, workflow_run_id: UUID) -> bool:
        task = self._tasks.get(workflow_run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def has_active_task(self, workflow_run_id: UUID) -> bool:
        task = self._tasks.get(workflow_run_id)
        return task is not None and not task.done()

    async def close(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._known_states.clear()

    def _start_task(self, workflow_run_id: UUID, coroutine: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(
            coroutine, name=f"researchflow-workflow-{workflow_run_id}"
        )
        self._tasks[workflow_run_id] = task
        task.add_done_callback(lambda completed: self._task_done(workflow_run_id, completed))

    def _task_done(self, workflow_run_id: UUID, task: asyncio.Task[None]) -> None:
        self._tasks.pop(workflow_run_id, None)
        if not task.cancelled():
            task.exception()

    async def _invoke(self, state: ProjectWorkflowState) -> None:
        config = _config(
            thread_id=str(state.workflow_run_id),
            namespace="project-analysis",
        )
        try:
            await self._graph.ainvoke(state.checkpoint_values(), config=config)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._failure_handler(state, "workflow.graph_execution_failed")

    async def _resume_checkpoint(
        self,
        project_id: UUID,
        workflow_run_id: UUID,
        thread_id: str,
        namespace: str,
        *,
        resume_payload: dict[str, str] | None = None,
    ) -> None:
        try:
            graph_input: Any = None if resume_payload is None else Command(resume=resume_payload)
            await self._graph.ainvoke(graph_input, config=_config(thread_id, namespace))
        except asyncio.CancelledError:
            raise
        except Exception:
            state = self._known_states.get(workflow_run_id)
            if state is not None:
                await self._failure_handler(state, "workflow.graph_resume_failed")
            else:
                await self._recovery_failure_handler(
                    project_id, workflow_run_id, "workflow.graph_resume_failed"
                )


def _config(thread_id: str, namespace: str) -> dict[str, dict[str, str]]:
    if namespace != "project-analysis":
        raise ResourceConflictError(details={"reason": "checkpoint_namespace_unsupported"})
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "",
        }
    }
