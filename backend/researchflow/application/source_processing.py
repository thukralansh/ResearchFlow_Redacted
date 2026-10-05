"""Versioned PDF parsing and exact page-local chunk persistence."""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from researchflow.application.sources import _require_owned
from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.provenance import (
    IMAGE_ONLY_MESSAGE,
    NORMALIZATION_VERSION,
    ChunkDraft,
    normalize_pages,
    processing_artifact_id,
    processing_run_id,
    validate_chunk_provenance,
)
from researchflow.errors import (
    DomainValidationError,
    ResearchFlowError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.infrastructure.pdf.chunker import SectionAwareChunker
from researchflow.ports.pdf import PdfParser, PdfParserError
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewSourceChunk,
    NewSourceProcessingRun,
    SourceChunkData,
    SourceData,
    SourceProcessingRunData,
)
from researchflow.ports.storage import StorageProvider
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_STARTED = "source.processing.started"
_SUCCEEDED = "source.processing.succeeded"
_FAILED = "source.processing.failed"


@dataclass(frozen=True, slots=True)
class ProcessSourceCommand:
    project_id: UUID
    source_id: UUID
    research_question_id: UUID
    question_source_relationship_id: UUID
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceProcessingView:
    run: SourceProcessingRunData
    chunks: tuple[SourceChunkData, ...]
    message: str | None = None


def _error_reason(error: Exception) -> str:
    if isinstance(error, PdfParserError):
        return error.reason
    if isinstance(error, DomainValidationError):
        reason = error.public_details.get("reason")
        return reason if isinstance(reason, str) else "pdf_processing_invalid"
    if isinstance(error, ResearchFlowError):
        return "source_storage_read_failed"
    return "pdf_processing_internal_error"


def _failure_message(reason: str) -> str:
    if reason == "pdf_image_only":
        return IMAGE_ONLY_MESSAGE
    return "PDF processing failed."


class SourceProcessingService:
    """Coordinate external parsing around explicit start and terminal transactions."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        storage: StorageProvider,
        parser: PdfParser,
        chunker: SectionAwareChunker | None = None,
        staleness_hook: StalenessService | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._storage = storage
        self._parser = parser
        self._chunker = chunker or SectionAwareChunker()
        self._staleness_hook = staleness_hook

    async def process(self, command: ProcessSourceCommand) -> SourceProcessingView:
        run_id: UUID
        source: SourceData
        prior_run_id: UUID | None = None
        prior_chunk_fingerprint: tuple[tuple[object, ...], ...] = ()
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            source = await self._validate_current_primary(uow, command)
            existing = await uow.sources.get_processing_run_by_identity(
                source_id=source.id,
                source_hash=source.content_hash,
                parser_name=self._parser.name,
                parser_version=self._parser.version,
                chunker_name=self._chunker.name,
                chunker_version=self._chunker.version,
                normalization_version=NORMALIZATION_VERSION,
            )
            if existing is not None:
                chunks = await uow.sources.list_chunks(existing.id)
                await uow.rollback()
                message = _failure_message(existing.error_code) if existing.error_code else None
                return SourceProcessingView(existing, chunks, message)

            prior_runs = await uow.sources.list_processing_runs(source.id)
            prior = next((item for item in prior_runs if item.status == "succeeded"), None)
            if prior is not None:
                prior_run_id = prior.id
                prior_chunk_fingerprint = _chunk_fingerprint(
                    await uow.sources.list_chunks(prior.id)
                )

            run_id = processing_run_id(
                source_id=source.id,
                source_hash=source.content_hash,
                parser_name=self._parser.name,
                parser_version=self._parser.version,
                chunker_name=self._chunker.name,
                chunker_version=self._chunker.version,
                normalization_version=NORMALIZATION_VERSION,
            )
            run = await uow.sources.add_processing_run(
                NewSourceProcessingRun(
                    id=run_id,
                    source_id=source.id,
                    source_hash=source.content_hash,
                    parser_name=self._parser.name,
                    parser_version=self._parser.version,
                    chunker_name=self._chunker.name,
                    chunker_version=self._chunker.version,
                    normalization_version=NORMALIZATION_VERSION,
                )
            )
            await uow.sources.update_processing_status(source.id, "running")
            await uow.events.append(
                self._event(
                    command,
                    run.id,
                    _STARTED,
                    output_refs=({"type": "source_processing_run", "id": str(run.id)},),
                )
            )
            await uow.commit()

        try:
            content = await self._storage.read(object_key=source.storage_object_key)
            if hashlib.sha256(content).hexdigest() != source.content_hash:
                raise DomainValidationError(details={"reason": "source_hash_mismatch"})
            parsed_pages = await self._parser.parse(content)
            pages = normalize_pages(parsed_pages)
            drafts = self._chunker.chunk(pages)
            validate_chunk_provenance(
                pages,
                drafts,
                maximum_chunk_codepoints=self._chunker.maximum_chunk_codepoints,
            )
        except Exception as error:
            reason = _error_reason(error)
            return await self._finish_failure(command, run_id, reason)

        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            await self._validate_current_primary(uow, command)
            chunks = await uow.sources.add_chunks(self._new_chunks(run_id, drafts))
            completed = await uow.sources.finish_processing_run(
                run_id,
                expected_status="running",
                status="succeeded",
            )
            await uow.sources.update_processing_status(source.id, "succeeded")
            await uow.events.append(
                self._event(
                    command,
                    run_id,
                    _SUCCEEDED,
                    output_refs=(
                        {"type": "source_processing_run", "id": str(run_id)},
                        {
                            "type": "source_chunks",
                            "processing_run_id": str(run_id),
                            "count": len(chunks),
                        },
                    ),
                )
            )
            if self._staleness_hook is not None and prior_run_id is not None:
                if _chunk_fingerprint(chunks) != prior_chunk_fingerprint:
                    await self._staleness_hook.artifact_changed(
                        uow=uow,
                        project_id=command.project_id,
                        cause_type="source_processing_run",
                        before_id=prior_run_id,
                        after_id=completed.id,
                        operation_type="source.processing.changed",
                    )
            await uow.commit()
            return SourceProcessingView(completed, chunks)

    async def _finish_failure(
        self, command: ProcessSourceCommand, run_id: UUID, reason: str
    ) -> SourceProcessingView:
        message = _failure_message(reason)
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            source = await uow.sources.get_exact(command.source_id)
            if source is None:
                raise ResourceNotFoundError(details={"resource": "source"})
            run = await uow.sources.finish_processing_run(
                run_id,
                expected_status="running",
                status="failed",
                error_code=reason,
                error_detail=message,
            )
            await uow.sources.update_processing_status(source.id, "failed")
            await uow.events.append(
                self._event(
                    command,
                    run_id,
                    _FAILED,
                    output_refs=({"type": "source_processing_run", "id": str(run_id)},),
                    reason=reason,
                )
            )
            await uow.commit()
            return SourceProcessingView(run, (), message)

    async def _validate_current_primary(
        self, uow: UnitOfWork, command: ProcessSourceCommand
    ) -> SourceData:
        question = await uow.research_questions.get_exact(command.research_question_id)
        relationship = await uow.sources.get_question_source_exact(
            command.question_source_relationship_id
        )
        source = await uow.sources.get_exact(command.source_id)
        project = await uow.projects.get()
        if source is None:
            raise ResourceNotFoundError(details={"resource": "source"})
        if (
            project is None
            or question is None
            or relationship is None
            or project.primary_research_question_id != question.id
            or question.lifecycle_status != "current"
            or relationship.research_question_id != question.id
            or relationship.source_id != source.id
            or relationship.relationship_type != "primary_paper"
            or relationship.review_status != "accepted"
            or relationship.lifecycle_status != "current"
        ):
            raise ResourceConflictError(details={"reason": "stale_primary_paper_context"})
        return source

    def _event(
        self,
        command: ProcessSourceCommand,
        run_id: UUID,
        operation: str,
        *,
        output_refs: tuple[dict[str, object], ...],
        reason: str | None = None,
    ) -> NewOperationEvent:
        phase = operation.rsplit(".", maxsplit=1)[-1]
        return NewOperationEvent(
            id=processing_artifact_id(run_id, f"event:{phase}"),
            project_id=command.project_id,
            workflow_run_id=None,
            operation_type=operation,
            created_by_type="system",
            created_by_id=self._actor.actor_id,
            input_refs=(
                {"type": "source", "id": str(command.source_id)},
                {"type": "research_question", "id": str(command.research_question_id)},
                {
                    "type": "research_question_source",
                    "id": str(command.question_source_relationship_id),
                },
            ),
            output_refs=output_refs,
            reason=reason,
            request_id=command.request_id,
        )

    @staticmethod
    def _new_chunks(run_id: UUID, drafts: tuple[ChunkDraft, ...]) -> tuple[NewSourceChunk, ...]:
        return tuple(
            NewSourceChunk(
                id=processing_artifact_id(run_id, f"chunk:{draft.chunk_index}"),
                processing_run_id=run_id,
                page_number=draft.page_number,
                section=draft.section,
                subsection=draft.subsection,
                chunk_index=draft.chunk_index,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
                chunk_text=draft.chunk_text,
                content_hash=draft.content_hash,
            )
            for draft in drafts
        )


def _chunk_fingerprint(chunks: tuple[SourceChunkData, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.page_number,
            item.chunk_index,
            item.start_offset,
            item.end_offset,
            item.content_hash,
        )
        for item in chunks
    )


class SourceProcessingQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def list_runs(
        self, project_id: UUID, source_id: UUID
    ) -> tuple[SourceProcessingView, ...]:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            if await uow.sources.get_exact(source_id) is None:
                raise ResourceNotFoundError(details={"resource": "source"})
            runs = await uow.sources.list_processing_runs(source_id)
            views: list[SourceProcessingView] = []
            for run in runs:
                views.append(
                    SourceProcessingView(
                        run,
                        await uow.sources.list_chunks(run.id),
                        _failure_message(run.error_code) if run.error_code else None,
                    )
                )
            await uow.rollback()
            return tuple(views)

    async def get_run(
        self, project_id: UUID, source_id: UUID, run_id: UUID
    ) -> SourceProcessingView:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            run = await uow.sources.get_processing_run_exact(run_id)
            if run is None or run.source_id != source_id:
                raise ResourceNotFoundError(details={"resource": "source_processing_run"})
            chunks = await uow.sources.list_chunks(run.id)
            await uow.rollback()
            message = _failure_message(run.error_code) if run.error_code else None
            return SourceProcessingView(run, chunks, message)
