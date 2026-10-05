"""Project application commands and actor-scoped queries."""

from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from researchflow.domain.actor import ActorContext
from researchflow.domain.projects import (
    ProjectDefinition,
    command_fingerprint,
    normalize_idempotency_key,
    project_create_identities,
)
from researchflow.domain.research_questions import (
    InquiryStatus,
    QuestionDefinition,
    QuestionPriority,
    QuestionType,
)
from researchflow.errors import InternalError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.project_directory import ProjectDirectory
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewProject,
    NewResearchQuestion,
    ProjectData,
    ResearchQuestionData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    title: str
    primary_question: str
    scope: str | None
    target_outcome: str
    reproduction_depth: str
    context_constraints: str | None = None
    idempotency_key: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateProjectCommand:
    project_id: UUID
    expected_lock_version: int
    title: str
    target_outcome: str
    reproduction_depth: str
    context_constraints: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectInquiryView:
    project: ProjectData
    primary_question: ResearchQuestionData
    current_questions: tuple[ResearchQuestionData, ...]


class ProjectApplicationService:
    """Own Project transactions; directory discovery is actor-scoped and read-only."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        directory: ProjectDirectory,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._directory = directory

    async def create(self, command: CreateProjectCommand) -> ProjectInquiryView:
        project_definition = ProjectDefinition.validate(
            title=command.title,
            description=command.context_constraints,
            target_outcome=command.target_outcome,
            reproduction_depth=command.reproduction_depth,
        )
        question_definition = QuestionDefinition.validate(
            question_text=command.primary_question,
            scope_text=command.scope,
        )
        idempotency_key = normalize_idempotency_key(command.idempotency_key)
        fingerprint = command_fingerprint(
            {
                "actor_id": str(self._actor.actor_id),
                "operation": "project.create",
                "project": asdict(project_definition),
                "question": asdict(question_definition),
            }
        )
        identities = project_create_identities(
            actor_id=self._actor.actor_id,
            idempotency_key=idempotency_key,
        )

        async with self._uow_factory(identities.project_id) as uow:
            if idempotency_key is not None:
                existing_event = await uow.events.get_by_idempotency_key(
                    "project.create", idempotency_key
                )
                if existing_event is not None:
                    if existing_event.input_fingerprint != fingerprint:
                        raise ResourceConflictError(
                            details={"reason": "idempotency_fingerprint_mismatch"}
                        )
                    view = await self._load_view(uow)
                    await uow.commit()
                    return view

            await uow.projects.add(
                NewProject(
                    id=identities.project_id,
                    primary_research_question_id=identities.primary_question_id,
                    title=project_definition.title,
                    description=project_definition.description,
                    target_outcome=project_definition.target_outcome,
                    reproduction_depth=project_definition.reproduction_depth,
                    created_by_id=self._actor.actor_id,
                )
            )
            await uow.research_questions.add(
                NewResearchQuestion(
                    id=identities.primary_question_id,
                    project_id=identities.project_id,
                    parent_question_id=None,
                    lineage_id=identities.primary_question_id,
                    version=1,
                    question_text=question_definition.question_text,
                    question_type=QuestionType.PRIMARY.value,
                    scope_text=question_definition.scope_text,
                    inquiry_status=InquiryStatus.OPEN.value,
                    lifecycle_status="current",
                    priority=QuestionPriority.HIGH.value,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                )
            )
            await uow.events.append(
                NewOperationEvent(
                    id=identities.event_id,
                    project_id=identities.project_id,
                    workflow_run_id=None,
                    operation_type="project.create",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    output_refs=(
                        {"type": "project", "id": str(identities.project_id)},
                        {
                            "type": "research_question",
                            "id": str(identities.primary_question_id),
                            "version": 1,
                        },
                    ),
                    idempotency_key=idempotency_key,
                    input_fingerprint=fingerprint if idempotency_key is not None else None,
                    request_id=command.request_id,
                )
            )
            view = await self._load_view(uow)
            await uow.commit()
            return view

    async def list_projects(self) -> tuple[ProjectData, ...]:
        return await self._directory.list_for_actor(self._actor.actor_id)

    async def get(self, project_id: UUID) -> ProjectInquiryView:
        async with self._uow_factory(project_id) as uow:
            view = await self._load_view(uow)
            await uow.rollback()
            return view

    async def update(self, command: UpdateProjectCommand) -> ProjectInquiryView:
        definition = ProjectDefinition.validate(
            title=command.title,
            description=command.context_constraints,
            target_outcome=command.target_outcome,
            reproduction_depth=command.reproduction_depth,
        )
        async with self._uow_factory(command.project_id) as uow:
            current = await uow.projects.get_for_update()
            self._require_owned(current)
            updated = await uow.projects.update_configuration(
                expected_lock_version=command.expected_lock_version,
                title=definition.title,
                description=definition.description,
                target_outcome=definition.target_outcome,
                reproduction_depth=definition.reproduction_depth,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=None,
                    operation_type="project.configuration_updated",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=(
                        {
                            "type": "project",
                            "id": str(command.project_id),
                            "lock_version": command.expected_lock_version,
                        },
                    ),
                    output_refs=(
                        {
                            "type": "project",
                            "id": str(command.project_id),
                            "lock_version": updated.lock_version,
                        },
                    ),
                    request_id=command.request_id,
                )
            )
            view = await self._load_view(uow)
            await uow.commit()
            return view

    async def _load_view(self, uow: UnitOfWork) -> ProjectInquiryView:
        project = await uow.projects.get()
        self._require_owned(project)
        assert project is not None
        primary = await uow.research_questions.get_exact(project.primary_research_question_id)
        if primary is None or primary.lifecycle_status != "current":
            raise InternalError(details={"reason": "current_primary_question_missing"})
        current_questions = await uow.research_questions.list_current()
        return ProjectInquiryView(project, primary, current_questions)

    def _require_owned(self, project: ProjectData | None) -> None:
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
