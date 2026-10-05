"""Immutable Primary Paper upload commands and project-scoped queries."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.projects import normalize_idempotency_key
from researchflow.domain.sources import (
    AsyncUpload,
    PrimaryPaperRelationship,
    SourceMetadata,
    primary_paper_fingerprint,
    primary_paper_identities,
    primary_paper_object_key,
    read_and_validate_pdf,
    validate_replacement,
)
from researchflow.errors import (
    ExternalServiceError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewResearchQuestionSource,
    NewSource,
    ProjectData,
    ResearchQuestionSourceData,
    SourceData,
)
from researchflow.ports.storage import StorageProvider
from researchflow.ports.unit_of_work import UnitOfWorkFactory

_OPERATION = "source.primary_paper.upload"


@dataclass(frozen=True, slots=True)
class UploadPrimaryPaperCommand:
    project_id: UUID
    research_question_id: UUID
    upload: AsyncUpload
    original_filename: str
    mime_type: str
    title: str
    authors: tuple[str, ...]
    identifiers: dict[str, str]
    relationship_rationale: str
    relevance_scope: str | None = None
    confidence: Decimal | None = None
    replace_current: bool = False
    idempotency_key: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PrimaryPaperView:
    source: SourceData
    relationship: ResearchQuestionSourceData


def _require_owned(actor: ActorContext, project: ProjectData | None) -> None:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})


class SourceApplicationService:
    """Own the one DB transaction that follows a bounded external upload."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        storage: StorageProvider,
        storage_bucket: str,
        maximum_upload_bytes: int,
        staleness_hook: StalenessService | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._storage = storage
        self._storage_bucket = storage_bucket
        self._maximum_upload_bytes = maximum_upload_bytes
        self._staleness_hook = staleness_hook

    async def upload_primary_paper(self, command: UploadPrimaryPaperCommand) -> PrimaryPaperView:
        metadata = SourceMetadata.validate(
            title=command.title,
            authors=command.authors,
            identifiers=command.identifiers,
        )
        relationship = PrimaryPaperRelationship.validate(
            rationale=command.relationship_rationale,
            relevance_scope=command.relevance_scope,
            confidence=command.confidence,
        )
        idempotency_key = normalize_idempotency_key(command.idempotency_key)
        pdf = await read_and_validate_pdf(
            command.upload,
            original_filename=command.original_filename,
            mime_type=command.mime_type,
            maximum_bytes=self._maximum_upload_bytes,
        )
        fingerprint = primary_paper_fingerprint(
            actor_id=self._actor.actor_id,
            project_id=command.project_id,
            research_question_id=command.research_question_id,
            pdf=pdf,
            metadata=metadata,
            relationship=relationship,
            replace_current=command.replace_current,
        )
        identities = primary_paper_identities(
            actor_id=self._actor.actor_id,
            project_id=command.project_id,
            idempotency_key=idempotency_key,
            input_fingerprint=fingerprint,
        )
        object_key = primary_paper_object_key(
            project_id=command.project_id,
            source_id=identities.source_id,
            sha256=pdf.sha256,
        )
        stored = await self._storage.put(
            object_key=object_key,
            content=pdf.content,
            content_type=pdf.mime_type,
        )
        if (
            stored.object_key != object_key
            or stored.sha256 != pdf.sha256
            or stored.size_bytes != pdf.size_bytes
        ):
            await self._compensate(object_key)
            raise ExternalServiceError(
                details={"service_name": "storage", "operation": "integrity_check"}
            )

        preserve_object = False
        try:
            async with self._uow_factory(command.project_id) as uow:
                project = await uow.projects.get_for_update()
                _require_owned(self._actor, project)
                assert project is not None
                question = await uow.research_questions.get_exact_for_update(
                    command.research_question_id
                )
                if (
                    question is None
                    or question.lifecycle_status != "current"
                    or question.question_type != "primary"
                    or project.primary_research_question_id != question.id
                ):
                    raise ResourceConflictError(details={"reason": "stale_primary_question"})

                if idempotency_key is not None:
                    event = await uow.events.get_by_idempotency_key(_OPERATION, idempotency_key)
                    if event is not None:
                        if event.input_fingerprint != fingerprint:
                            raise ResourceConflictError(
                                details={"reason": "idempotency_fingerprint_mismatch"}
                            )
                        source = await uow.sources.get_exact(identities.source_id)
                        relation = await uow.sources.get_question_source_exact(
                            identities.relationship_id
                        )
                        if source is None or relation is None:
                            raise InternalError(details={"reason": "idempotent_output_missing"})
                        if (
                            source.storage_object_key != object_key
                            or source.content_hash != pdf.sha256
                            or relation.source_id != source.id
                        ):
                            raise InternalError(details={"reason": "idempotent_output_mismatch"})
                        preserve_object = True
                        await uow.rollback()
                        return PrimaryPaperView(source, relation)

                current = await uow.sources.get_current_primary_paper_for_update()
                prior_source = (
                    await uow.sources.get_exact(current.source_id) if current is not None else None
                )
                validate_replacement(
                    has_current=current is not None,
                    replace_current=command.replace_current,
                )
                source = await uow.sources.add(
                    NewSource(
                        id=identities.source_id,
                        project_id=command.project_id,
                        source_type="pdf",
                        title=metadata.title,
                        authors=metadata.authors,
                        identifiers=metadata.identifiers,
                        original_filename=pdf.original_filename,
                        storage_bucket=self._storage_bucket,
                        storage_object_key=object_key,
                        mime_type=pdf.mime_type,
                        size_bytes=pdf.size_bytes,
                        content_hash=pdf.sha256,
                        processing_status="pending",
                        created_by_id=self._actor.actor_id,
                    )
                )

                lineage_id = identities.relationship_id
                version = 1
                predecessor_id = None
                if current is not None:
                    await uow.sources.mark_question_source_superseded(
                        current.id,
                        expected_version=current.version,
                    )
                    lineage_id = current.lineage_id
                    version = current.version + 1
                    predecessor_id = current.id
                relation = await uow.sources.add_question_source(
                    NewResearchQuestionSource(
                        id=identities.relationship_id,
                        project_id=command.project_id,
                        research_question_id=question.id,
                        source_id=source.id,
                        lineage_id=lineage_id,
                        version=version,
                        relationship_type="primary_paper",
                        relevance_scope=relationship.relevance_scope,
                        rationale=relationship.rationale,
                        confidence=relationship.confidence,
                        review_status="accepted",
                        lifecycle_status="current",
                        created_by_type="human",
                        derived_from_id=predecessor_id,
                        supersedes_id=predecessor_id,
                    )
                )
                await uow.events.append(
                    NewOperationEvent(
                        id=identities.event_id,
                        project_id=command.project_id,
                        workflow_run_id=None,
                        operation_type=_OPERATION,
                        created_by_type="human",
                        created_by_id=self._actor.actor_id,
                        input_refs=(
                            {
                                "type": "research_question",
                                "id": str(question.id),
                                "version": question.version,
                            },
                        ),
                        output_refs=(
                            {"type": "source", "id": str(source.id)},
                            {
                                "type": "research_question_source",
                                "id": str(relation.id),
                                "version": relation.version,
                            },
                        ),
                        reason="Primary Paper selected by the researcher.",
                        idempotency_key=idempotency_key,
                        input_fingerprint=fingerprint if idempotency_key is not None else None,
                        request_id=command.request_id,
                    )
                )
                if current is not None and self._staleness_hook is not None:
                    await self._staleness_hook.artifact_changed(
                        uow=uow,
                        project_id=command.project_id,
                        cause_type="research_question_source",
                        before_id=current.id,
                        after_id=relation.id,
                        operation_type=(
                            "source.question_relationship.revised"
                            if prior_source is not None
                            and prior_source.content_hash == source.content_hash
                            else "source.primary_paper.replaced"
                        ),
                    )
                await uow.commit()
                preserve_object = True
                return PrimaryPaperView(source, relation)
        except BaseException:
            if not preserve_object:
                await self._compensate(object_key)
            raise

    async def _compensate(self, object_key: str) -> None:
        try:
            await self._storage.delete(object_key=object_key)
        except BaseException as error:
            raise ExternalServiceError(
                details={"service_name": "storage", "operation": "compensation"},
                cause=error if isinstance(error, Exception) else None,
            ) from error


class SourceQueryService:
    """Read Source metadata without requiring access to private object bytes."""

    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def list_primary_papers(self, project_id: UUID) -> tuple[PrimaryPaperView, ...]:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            sources = {source.id: source for source in await uow.sources.list_all()}
            relationships = await uow.sources.list_primary_paper_history()
            try:
                views = tuple(
                    PrimaryPaperView(sources[relationship.source_id], relationship)
                    for relationship in relationships
                )
            except KeyError as error:
                raise InternalError(details={"reason": "primary_paper_source_missing"}) from error
            await uow.rollback()
            return views

    async def get(self, project_id: UUID, source_id: UUID) -> PrimaryPaperView:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            source = await uow.sources.get_exact(source_id)
            if source is None:
                raise ResourceNotFoundError(details={"resource": "source"})
            relationships = await uow.sources.list_primary_paper_history()
            relation = next(
                (
                    relationship
                    for relationship in relationships
                    if relationship.source_id == source.id
                ),
                None,
            )
            if relation is None:
                raise InternalError(details={"reason": "primary_paper_relationship_missing"})
            await uow.rollback()
            return PrimaryPaperView(source, relation)
