"""Framework-neutral launcher/checkpoint contracts for durable workflows."""

from typing import Protocol
from uuid import UUID

from researchflow.workflows.state import ProjectWorkflowState


class WorkflowLauncher(Protocol):
    async def launch(self, state: ProjectWorkflowState) -> None: ...
    async def retry(
        self, *, project_id: UUID, workflow_run_id: UUID, thread_id: str, namespace: str
    ) -> None: ...
    async def resume(
        self,
        *,
        project_id: UUID,
        workflow_run_id: UUID,
        thread_id: str,
        namespace: str,
        interrupt_id: UUID,
        context_fingerprint: str,
    ) -> None: ...
    async def cancel(self, workflow_run_id: UUID) -> bool: ...
    async def has_active_task(self, workflow_run_id: UUID) -> bool: ...


class WorkflowCheckpointStore(Protocol):
    async def setup(self) -> None: ...
    async def has_checkpoint(self, *, thread_id: str, namespace: str) -> bool: ...
    async def delete_thread(self, thread_id: str) -> None: ...
