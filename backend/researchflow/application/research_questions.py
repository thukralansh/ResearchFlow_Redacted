"""Versioned Research Question commands."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.research_questions import (
    InquiryStatus,
    QuestionDefinition,
    QuestionPriority,
    QuestionType,
    validate_reopen,
    validate_status_change,
)
from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewResearchQuestion,
    ProjectData,
    ResearchQuestionData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class RefineQuestionCommand:
    project_id: UUID
    question_id: UUID
    expected_version: int
    expected_project_lock_version: int | None
    question_text: str
    scope_text: str | None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateSubquestionCommand:
    project_id: UUID
    parent_question_id: UUID
    question_text: str
    scope_text: str | None
    priority: QuestionPriority = QuestionPriority.MEDIUM
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeInquiryStatusCommand:
    project_id: UUID
    question_id: UUID
    expected_version: int
    expected_status: InquiryStatus
    target_status: InquiryStatus
    request_id: str | None = None


class ResearchQuestionApplicationService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        staleness_hook: StalenessService | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._staleness_hook = staleness_hook

    async def list_current(self, project_id: UUID) -> tuple[ResearchQuestionData, ...]:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            self._require_owned(project)
            questions = await uow.research_questions.list_current()
            await uow.rollback()
            return questions

    async def refine(self, command: RefineQuestionCommand) -> ResearchQuestionData:
        definition = QuestionDefinition.validate(
            question_text=command.question_text,
            scope_text=command.scope_text,
        )
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get_for_update()
            self._require_owned(project)
            assert project is not None
            current = await uow.research_questions.get_exact_for_update(command.question_id)
            self._require_current(current, command.expected_version)
            assert current is not None

            if current.question_type == QuestionType.PRIMARY.value:
                if command.expected_project_lock_version is None:
                    raise DomainValidationError(
                        details={"field": "expected_project_lock_version", "reason": "required"}
                    )
                if project.lock_version != command.expected_project_lock_version:
                    raise ResourceConflictError(details={"reason": "stale_lock_version"})
                if project.primary_research_question_id != current.id:
                    raise ResourceConflictError(details={"reason": "stale_primary_question"})

            new_id = uuid4()
            await uow.research_questions.mark_superseded(
                current.id,
                expected_version=current.version,
            )
            revised = await uow.research_questions.add(
                NewResearchQuestion(
                    id=new_id,
                    project_id=current.project_id,
                    parent_question_id=current.parent_question_id,
                    lineage_id=current.lineage_id,
                    version=current.version + 1,
                    question_text=definition.question_text,
                    question_type=current.question_type,
                    scope_text=definition.scope_text,
                    inquiry_status=current.inquiry_status,
                    lifecycle_status="current",
                    priority=current.priority,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    derived_from_id=current.id,
                    supersedes_id=current.id,
                )
            )
            if current.question_type == QuestionType.PRIMARY.value:
                assert command.expected_project_lock_version is not None
                await uow.projects.replace_primary_research_question(
                    expected_lock_version=command.expected_project_lock_version,
                    expected_question_id=current.id,
                    new_question_id=revised.id,
                )
            await self._append_event(
                uow,
                operation_type="research_question.refined",
                before=current,
                after=revised,
                request_id=command.request_id,
            )
            if self._staleness_hook is not None:
                await self._staleness_hook.artifact_changed(
                    uow=uow,
                    project_id=command.project_id,
                    cause_type="research_question",
                    before_id=current.id,
                    after_id=revised.id,
                    operation_type="research_question.refined",
                )
            await uow.commit()
            return revised

    async def create_subquestion(self, command: CreateSubquestionCommand) -> ResearchQuestionData:
        definition = QuestionDefinition.validate(
            question_text=command.question_text,
            scope_text=command.scope_text,
        )
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            self._require_owned(project)
            parent = await uow.research_questions.get_exact(command.parent_question_id)
            if parent is None or parent.lifecycle_status != "current":
                raise ResourceNotFoundError(details={"resource": "parent_question"})
            if parent.question_type != QuestionType.PRIMARY.value:
                raise DomainValidationError(
                    details={"field": "parent_question_id", "reason": "primary_parent_required"}
                )
            question_id = uuid4()
            created = await uow.research_questions.add(
                NewResearchQuestion(
                    id=question_id,
                    project_id=command.project_id,
                    parent_question_id=parent.id,
                    lineage_id=question_id,
                    version=1,
                    question_text=definition.question_text,
                    question_type=QuestionType.SUBQUESTION.value,
                    scope_text=definition.scope_text,
                    inquiry_status=InquiryStatus.OPEN.value,
                    lifecycle_status="current",
                    priority=command.priority.value,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                )
            )
            await self._append_event(
                uow,
                operation_type="research_question.subquestion_created",
                before=None,
                after=created,
                request_id=command.request_id,
            )
            await uow.commit()
            return created

    async def change_status(self, command: ChangeInquiryStatusCommand) -> ResearchQuestionData:
        validate_status_change(
            current=command.expected_status,
            target=command.target_status,
        )
        return await self._change_status(command, operation_type="research_question.status_changed")

    async def reopen(self, command: ChangeInquiryStatusCommand) -> ResearchQuestionData:
        validate_reopen(
            current=command.expected_status,
            target=command.target_status,
        )
        return await self._change_status(command, operation_type="research_question.reopened")

    async def _change_status(
        self,
        command: ChangeInquiryStatusCommand,
        *,
        operation_type: str,
    ) -> ResearchQuestionData:
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            self._require_owned(project)
            current = await uow.research_questions.get_exact_for_update(command.question_id)
            self._require_current(current, command.expected_version)
            assert current is not None
            if current.inquiry_status != command.expected_status.value:
                raise ResourceConflictError(details={"reason": "stale_inquiry_status"})
            updated = await uow.research_questions.update_inquiry_status(
                current.id,
                expected_version=current.version,
                expected_status=current.inquiry_status,
                new_status=command.target_status.value,
            )
            await self._append_event(
                uow,
                operation_type=operation_type,
                before=current,
                after=updated,
                request_id=command.request_id,
            )
            if operation_type == "research_question.reopened" and self._staleness_hook is not None:
                await self._staleness_hook.artifact_changed(
                    uow=uow,
                    project_id=command.project_id,
                    cause_type="research_question",
                    before_id=current.id,
                    after_id=updated.id,
                    operation_type=operation_type,
                )
            await uow.commit()
            return updated

    async def _append_event(
        self,
        uow: UnitOfWork,
        *,
        operation_type: str,
        before: ResearchQuestionData | None,
        after: ResearchQuestionData,
        request_id: str | None,
    ) -> None:
        input_refs: tuple[dict[str, object], ...] = ()
        if before is not None:
            input_refs = (
                {"type": "research_question", "id": str(before.id), "version": before.version},
            )
        await uow.events.append(
            NewOperationEvent(
                id=uuid4(),
                project_id=after.project_id,
                workflow_run_id=None,
                operation_type=operation_type,
                created_by_type="human",
                created_by_id=self._actor.actor_id,
                input_refs=input_refs,
                output_refs=(
                    {"type": "research_question", "id": str(after.id), "version": after.version},
                ),
                request_id=request_id,
            )
        )

    def _require_owned(self, project: ProjectData | None) -> None:
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})

    @staticmethod
    def _require_current(question: ResearchQuestionData | None, expected_version: int) -> None:
        if question is None:
            raise ResourceNotFoundError(details={"resource": "research_question"})
        if question.lifecycle_status != "current" or question.version != expected_version:
            raise ResourceConflictError(details={"reason": "stale_research_question"})
