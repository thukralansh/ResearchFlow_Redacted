"""Actor-scoped Project discovery before a Project ID is selected."""

from typing import Protocol
from uuid import UUID

from researchflow.ports.repositories import ProjectData


class ProjectDirectory(Protocol):
    """Read-only internal-MVP discovery scoped to one trusted actor."""

    async def list_for_actor(self, actor_id: UUID) -> tuple[ProjectData, ...]: ...
