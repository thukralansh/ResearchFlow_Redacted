"""Application-owned transaction boundary over consequential persistence ports."""

from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from researchflow.ports.copilot import CopilotRepository
from researchflow.ports.repositories import (
    BlueprintsRepository,
    DependenciesRepository,
    EventsRepository,
    GenerationsRepository,
    JudgmentsRepository,
    KnowledgeRepository,
    MappingsRepository,
    MentalModelsRepository,
    MethodModelsRepository,
    ProjectsRepository,
    RepositoriesRepository,
    ResearchQuestionsRepository,
    SourcesRepository,
    WorkflowsRepository,
)


class UnitOfWork(Protocol):
    """One application command's repositories and explicit commit decision."""

    project_id: UUID

    @property
    def projects(self) -> ProjectsRepository: ...

    @property
    def research_questions(self) -> ResearchQuestionsRepository: ...

    @property
    def sources(self) -> SourcesRepository: ...

    @property
    def repositories(self) -> RepositoriesRepository: ...

    @property
    def generations(self) -> GenerationsRepository: ...

    @property
    def copilot(self) -> CopilotRepository: ...

    @property
    def knowledge(self) -> KnowledgeRepository: ...

    @property
    def method_models(self) -> MethodModelsRepository: ...

    @property
    def mental_models(self) -> MentalModelsRepository: ...

    @property
    def mappings(self) -> MappingsRepository: ...

    @property
    def judgments(self) -> JudgmentsRepository: ...

    @property
    def blueprints(self) -> BlueprintsRepository: ...

    @property
    def workflows(self) -> WorkflowsRepository: ...

    @property
    def events(self) -> EventsRepository: ...

    @property
    def dependencies(self) -> DependenciesRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def configure_consistent_snapshot(self, *, read_only: bool) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Construct one Project-scoped UnitOfWork per application command."""

    def __call__(self, project_id: UUID) -> UnitOfWork: ...
