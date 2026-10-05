"""Async SQLAlchemy Unit of Work with explicit application-owned completion."""

from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.repositories import (
    SqlAlchemyBlueprintsRepository,
    SqlAlchemyCopilotRepository,
    SqlAlchemyDependenciesRepository,
    SqlAlchemyEventsRepository,
    SqlAlchemyGenerationsRepository,
    SqlAlchemyJudgmentsRepository,
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyMappingsRepository,
    SqlAlchemyMentalModelsRepository,
    SqlAlchemyMethodModelsRepository,
    SqlAlchemyProjectsRepository,
    SqlAlchemyRepositoriesRepository,
    SqlAlchemyResearchQuestionsRepository,
    SqlAlchemySourcesRepository,
    SqlAlchemyWorkflowsRepository,
)
from researchflow.infrastructure.database.repositories._errors import (
    execute_database,
    internal_database_error,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory


class SqlAlchemyUnitOfWork:
    """Own exactly one session and one transaction for one application command."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: UUID,
    ) -> None:
        self.project_id = project_id
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None
        self._finished = False

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise ResourceConflictError(details={"reason": "unit_of_work_already_active"})
        self._session = self._session_factory()
        self._transaction = await self._session.begin()
        self._finished = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if not self._finished:
            await self._rollback_and_close()

    @property
    def projects(self) -> SqlAlchemyProjectsRepository:
        return SqlAlchemyProjectsRepository(self._active_session(), self.project_id)

    @property
    def research_questions(self) -> SqlAlchemyResearchQuestionsRepository:
        return SqlAlchemyResearchQuestionsRepository(self._active_session(), self.project_id)

    @property
    def sources(self) -> SqlAlchemySourcesRepository:
        return SqlAlchemySourcesRepository(self._active_session(), self.project_id)

    @property
    def repositories(self) -> SqlAlchemyRepositoriesRepository:
        return SqlAlchemyRepositoriesRepository(self._active_session(), self.project_id)

    @property
    def generations(self) -> SqlAlchemyGenerationsRepository:
        return SqlAlchemyGenerationsRepository(self._active_session(), self.project_id)

    @property
    def copilot(self) -> SqlAlchemyCopilotRepository:
        return SqlAlchemyCopilotRepository(self._active_session(), self.project_id)

    @property
    def knowledge(self) -> SqlAlchemyKnowledgeRepository:
        return SqlAlchemyKnowledgeRepository(self._active_session(), self.project_id)

    @property
    def method_models(self) -> SqlAlchemyMethodModelsRepository:
        return SqlAlchemyMethodModelsRepository(self._active_session(), self.project_id)

    @property
    def mental_models(self) -> SqlAlchemyMentalModelsRepository:
        return SqlAlchemyMentalModelsRepository(self._active_session(), self.project_id)

    @property
    def mappings(self) -> SqlAlchemyMappingsRepository:
        return SqlAlchemyMappingsRepository(self._active_session(), self.project_id)

    @property
    def judgments(self) -> SqlAlchemyJudgmentsRepository:
        return SqlAlchemyJudgmentsRepository(self._active_session(), self.project_id)

    @property
    def blueprints(self) -> SqlAlchemyBlueprintsRepository:
        return SqlAlchemyBlueprintsRepository(self._active_session(), self.project_id)

    @property
    def workflows(self) -> SqlAlchemyWorkflowsRepository:
        return SqlAlchemyWorkflowsRepository(self._active_session(), self.project_id)

    @property
    def events(self) -> SqlAlchemyEventsRepository:
        return SqlAlchemyEventsRepository(self._active_session(), self.project_id)

    @property
    def dependencies(self) -> SqlAlchemyDependenciesRepository:
        return SqlAlchemyDependenciesRepository(self._active_session(), self.project_id)

    async def commit(self) -> None:
        transaction = self._active_transaction()
        try:
            await transaction.commit()
        except SQLAlchemyError as error:
            await self._rollback_and_close()
            raise internal_database_error(error) from error
        await self._close()

    async def rollback(self) -> None:
        self._active_transaction()
        await self._rollback_and_close()

    async def configure_consistent_snapshot(self, *, read_only: bool) -> None:
        """Make the current transaction a PostgreSQL repeatable-read snapshot."""
        session = self._active_session()
        statement = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
        if read_only:
            statement += " READ ONLY"
        await execute_database(lambda: session.execute(text(statement)))

    def _active_session(self) -> AsyncSession:
        if self._session is None or self._finished:
            raise ResourceConflictError(details={"reason": "unit_of_work_not_active"})
        return self._session

    def _active_transaction(self) -> AsyncSessionTransaction:
        transaction = self._transaction
        if transaction is None or self._finished or not transaction.is_active:
            raise ResourceConflictError(details={"reason": "unit_of_work_not_active"})
        return transaction

    async def _rollback_and_close(self) -> None:
        transaction = self._transaction
        if transaction is not None and transaction.is_active:
            await transaction.rollback()
        await self._close()

    async def _close(self) -> None:
        session = self._session
        if session is not None:
            await session.close()
        self._session = None
        self._transaction = None
        self._finished = True


class SqlAlchemyUnitOfWorkFactory(UnitOfWorkFactory):
    """Infrastructure-owned constructor for Project-scoped units of work."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self, project_id: UUID) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory, project_id)
