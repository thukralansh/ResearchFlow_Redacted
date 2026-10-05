"""Source and question-relative paper-role query adapter."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.research_question import (
    ResearchQuestionSourceRecord,
)
from researchflow.infrastructure.database.models.source import (
    SourceChunkRecord,
    SourceProcessingRunRecord,
    SourceRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    NewResearchQuestionSource,
    NewSource,
    NewSourceChunk,
    NewSourceProcessingRun,
    ResearchQuestionSourceData,
    SourceChunkData,
    SourceData,
    SourceProcessingRunData,
)


def _to_source(row: SourceRecord) -> SourceData:
    return SourceData(
        id=row.id,
        project_id=row.project_id,
        source_type=row.source_type,
        title=row.title,
        authors=tuple(row.authors),
        identifiers=dict(row.identifiers),
        original_filename=row.original_filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        content_hash=row.content_hash,
        processing_status=row.processing_status,
        storage_bucket=row.storage_bucket,
        storage_object_key=row.storage_object_key,
        created_by_id=row.created_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_question_source(row: ResearchQuestionSourceRecord) -> ResearchQuestionSourceData:
    return ResearchQuestionSourceData(
        id=row.id,
        project_id=row.project_id,
        research_question_id=row.research_question_id,
        source_id=row.source_id,
        lineage_id=row.lineage_id,
        version=row.version,
        relationship_type=row.relationship_type,
        relevance_scope=row.relevance_scope,
        rationale=row.rationale,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        created_by_type=row.created_by_type,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
        created_at=row.created_at,
    )


def _to_processing_run(row: SourceProcessingRunRecord) -> SourceProcessingRunData:
    return SourceProcessingRunData(
        id=row.id,
        source_id=row.source_id,
        source_hash=row.source_hash,
        parser_name=row.parser_name,
        parser_version=row.parser_version,
        chunker_name=row.chunker_name,
        chunker_version=row.chunker_version,
        normalization_version=row.normalization_version,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error_code=row.error_code,
        error_detail=row.error_detail,
    )


def _to_chunk(row: SourceChunkRecord) -> SourceChunkData:
    return SourceChunkData(
        id=row.id,
        processing_run_id=row.processing_run_id,
        page_number=row.page_number,
        section=row.section,
        subsection=row.subsection,
        chunk_index=row.chunk_index,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        chunk_text=row.chunk_text,
        content_hash=row.content_hash,
    )


class SqlAlchemySourcesRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, source: NewSource) -> SourceData:
        if source.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_exact(source.id)
        if existing is not None:
            expected = (
                source.source_type,
                source.title,
                source.authors,
                source.identifiers,
                source.original_filename,
                source.storage_bucket,
                source.storage_object_key,
                source.mime_type,
                source.size_bytes,
                source.content_hash,
                source.processing_status,
                source.created_by_id,
            )
            actual = (
                existing.source_type,
                existing.title,
                existing.authors,
                existing.identifiers,
                existing.original_filename,
                existing.storage_bucket,
                existing.storage_object_key,
                existing.mime_type,
                existing.size_bytes,
                existing.content_hash,
                existing.processing_status,
                existing.created_by_id,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "source_id_reused"})

        row = SourceRecord(
            id=source.id,
            project_id=source.project_id,
            source_type=source.source_type,
            title=source.title,
            authors=list(source.authors),
            identifiers=dict(source.identifiers),
            original_filename=source.original_filename,
            storage_bucket=source.storage_bucket,
            storage_object_key=source.storage_object_key,
            mime_type=source.mime_type,
            size_bytes=source.size_bytes,
            content_hash=source.content_hash,
            processing_status=source.processing_status,
            created_by_id=source.created_by_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_source(row)

    async def get_exact(self, source_id: UUID) -> SourceData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceRecord).where(
                    SourceRecord.id == source_id,
                    SourceRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_source(row)

    async def list_all(self) -> tuple[SourceData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceRecord)
                .where(SourceRecord.project_id == self._project_id)
                .order_by(SourceRecord.created_at, SourceRecord.id)
            )
        )
        return tuple(_to_source(row) for row in result.scalars())

    async def add_question_source(
        self, relationship: NewResearchQuestionSource
    ) -> ResearchQuestionSourceData:
        if relationship.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_question_source_exact(relationship.id)
        if existing is not None:
            expected = (
                relationship.research_question_id,
                relationship.source_id,
                relationship.lineage_id,
                relationship.version,
                relationship.relationship_type,
                relationship.relevance_scope,
                relationship.rationale,
                relationship.confidence,
                relationship.review_status,
                relationship.lifecycle_status,
                relationship.created_by_type,
                relationship.derived_from_id,
                relationship.supersedes_id,
            )
            actual = (
                existing.research_question_id,
                existing.source_id,
                existing.lineage_id,
                existing.version,
                existing.relationship_type,
                existing.relevance_scope,
                existing.rationale,
                existing.confidence,
                existing.review_status,
                existing.lifecycle_status,
                existing.created_by_type,
                existing.derived_from_id,
                existing.supersedes_id,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "question_source_id_reused"})

        row = ResearchQuestionSourceRecord(
            id=relationship.id,
            project_id=relationship.project_id,
            research_question_id=relationship.research_question_id,
            source_id=relationship.source_id,
            lineage_id=relationship.lineage_id,
            version=relationship.version,
            relationship_type=relationship.relationship_type,
            relevance_scope=relationship.relevance_scope,
            rationale=relationship.rationale,
            confidence=relationship.confidence,
            review_status=relationship.review_status,
            lifecycle_status=relationship.lifecycle_status,
            created_by_type=relationship.created_by_type,
            derived_from_id=relationship.derived_from_id,
            supersedes_id=relationship.supersedes_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_question_source(row)

    async def get_question_source_exact(
        self, relationship_id: UUID
    ) -> ResearchQuestionSourceData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(ResearchQuestionSourceRecord).where(
                    ResearchQuestionSourceRecord.id == relationship_id,
                    ResearchQuestionSourceRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_question_source(row)

    async def get_current_primary_paper(self) -> ResearchQuestionSourceData | None:
        return await self._get_current_primary_paper(lock=False, nowait=False)

    async def get_current_primary_paper_for_update(
        self, *, nowait: bool = False
    ) -> ResearchQuestionSourceData | None:
        return await self._get_current_primary_paper(lock=True, nowait=nowait)

    async def _get_current_primary_paper(
        self, *, lock: bool, nowait: bool
    ) -> ResearchQuestionSourceData | None:
        statement = select(ResearchQuestionSourceRecord).where(
            ResearchQuestionSourceRecord.project_id == self._project_id,
            ResearchQuestionSourceRecord.relationship_type == "primary_paper",
            ResearchQuestionSourceRecord.review_status == "accepted",
            ResearchQuestionSourceRecord.lifecycle_status == "current",
        )
        if lock:
            statement = statement.with_for_update(nowait=nowait)
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        return None if row is None else _to_question_source(row)

    async def list_primary_paper_history(self) -> tuple[ResearchQuestionSourceData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(ResearchQuestionSourceRecord)
                .where(
                    ResearchQuestionSourceRecord.project_id == self._project_id,
                    ResearchQuestionSourceRecord.relationship_type == "primary_paper",
                )
                .order_by(
                    ResearchQuestionSourceRecord.lineage_id,
                    ResearchQuestionSourceRecord.version,
                )
            )
        )
        return tuple(_to_question_source(row) for row in result.scalars())

    async def mark_question_source_superseded(
        self, relationship_id: UUID, *, expected_version: int
    ) -> ResearchQuestionSourceData:
        statement = (
            update(ResearchQuestionSourceRecord)
            .where(
                ResearchQuestionSourceRecord.id == relationship_id,
                ResearchQuestionSourceRecord.project_id == self._project_id,
                ResearchQuestionSourceRecord.version == expected_version,
                ResearchQuestionSourceRecord.lifecycle_status == "current",
            )
            .values(lifecycle_status="superseded")
            .returning(ResearchQuestionSourceRecord)
        )
        result = await execute_database(lambda: self._session.execute(statement))
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_primary_paper_relationship"})
        return _to_question_source(row)

    async def update_processing_status(self, source_id: UUID, status: str) -> SourceData:
        result = await execute_database(
            lambda: self._session.execute(
                update(SourceRecord)
                .where(
                    SourceRecord.id == source_id,
                    SourceRecord.project_id == self._project_id,
                )
                .values(processing_status=status, updated_at=func.now())
                .returning(SourceRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "source_processing_status_conflict"})
        return _to_source(row)

    async def add_processing_run(self, run: NewSourceProcessingRun) -> SourceProcessingRunData:
        if await self.get_exact(run.source_id) is None:
            raise ResourceConflictError(details={"reason": "source_project_scope_mismatch"})
        existing = await self.get_processing_run_exact(run.id)
        if existing is not None:
            expected = (
                run.source_id,
                run.source_hash,
                run.parser_name,
                run.parser_version,
                run.chunker_name,
                run.chunker_version,
                run.normalization_version,
            )
            actual = (
                existing.source_id,
                existing.source_hash,
                existing.parser_name,
                existing.parser_version,
                existing.chunker_name,
                existing.chunker_version,
                existing.normalization_version,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "processing_run_id_reused"})
        row = SourceProcessingRunRecord(
            id=run.id,
            source_id=run.source_id,
            source_hash=run.source_hash,
            parser_name=run.parser_name,
            parser_version=run.parser_version,
            chunker_name=run.chunker_name,
            chunker_version=run.chunker_version,
            normalization_version=run.normalization_version,
            status=run.status,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_processing_run(row)

    async def get_processing_run_exact(self, run_id: UUID) -> SourceProcessingRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceProcessingRunRecord)
                .join(SourceRecord, SourceRecord.id == SourceProcessingRunRecord.source_id)
                .where(
                    SourceProcessingRunRecord.id == run_id,
                    SourceRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_processing_run(row)

    async def get_processing_run_by_identity(
        self,
        *,
        source_id: UUID,
        source_hash: str,
        parser_name: str,
        parser_version: str,
        chunker_name: str,
        chunker_version: str,
        normalization_version: str,
    ) -> SourceProcessingRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceProcessingRunRecord)
                .join(SourceRecord, SourceRecord.id == SourceProcessingRunRecord.source_id)
                .where(
                    SourceRecord.project_id == self._project_id,
                    SourceProcessingRunRecord.source_id == source_id,
                    SourceProcessingRunRecord.source_hash == source_hash,
                    SourceProcessingRunRecord.parser_name == parser_name,
                    SourceProcessingRunRecord.parser_version == parser_version,
                    SourceProcessingRunRecord.chunker_name == chunker_name,
                    SourceProcessingRunRecord.chunker_version == chunker_version,
                    SourceProcessingRunRecord.normalization_version == normalization_version,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_processing_run(row)

    async def list_processing_runs(self, source_id: UUID) -> tuple[SourceProcessingRunData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceProcessingRunRecord)
                .join(SourceRecord, SourceRecord.id == SourceProcessingRunRecord.source_id)
                .where(
                    SourceRecord.project_id == self._project_id,
                    SourceProcessingRunRecord.source_id == source_id,
                )
                .order_by(SourceProcessingRunRecord.started_at.desc(), SourceProcessingRunRecord.id)
            )
        )
        return tuple(_to_processing_run(row) for row in result.scalars())

    async def finish_processing_run(
        self,
        run_id: UUID,
        *,
        expected_status: str,
        status: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> SourceProcessingRunData:
        if await self.get_processing_run_exact(run_id) is None:
            raise ResourceConflictError(details={"reason": "processing_run_project_scope_mismatch"})
        result = await execute_database(
            lambda: self._session.execute(
                update(SourceProcessingRunRecord)
                .where(
                    SourceProcessingRunRecord.id == run_id,
                    SourceProcessingRunRecord.status == expected_status,
                )
                .values(
                    status=status,
                    completed_at=func.now(),
                    error_code=error_code,
                    error_detail=error_detail,
                )
                .returning(SourceProcessingRunRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "processing_run_status_conflict"})
        return _to_processing_run(row)

    async def add_chunks(self, chunks: tuple[NewSourceChunk, ...]) -> tuple[SourceChunkData, ...]:
        if not chunks:
            return ()
        run_id = chunks[0].processing_run_id
        if any(chunk.processing_run_id != run_id for chunk in chunks):
            raise ResourceConflictError(details={"reason": "chunk_run_mismatch"})
        run = await self.get_processing_run_exact(run_id)
        if run is None or run.status != "running":
            raise ResourceConflictError(details={"reason": "processing_run_not_running"})
        rows = [
            SourceChunkRecord(
                id=chunk.id,
                processing_run_id=chunk.processing_run_id,
                page_number=chunk.page_number,
                section=chunk.section,
                subsection=chunk.subsection,
                chunk_index=chunk.chunk_index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                chunk_text=chunk.chunk_text,
                content_hash=chunk.content_hash,
            )
            for chunk in chunks
        ]
        self._session.add_all(rows)
        await execute_database(self._session.flush)
        return tuple(_to_chunk(row) for row in rows)

    async def list_chunks(self, run_id: UUID) -> tuple[SourceChunkData, ...]:
        if await self.get_processing_run_exact(run_id) is None:
            return ()
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceChunkRecord)
                .where(SourceChunkRecord.processing_run_id == run_id)
                .order_by(SourceChunkRecord.chunk_index)
            )
        )
        return tuple(_to_chunk(row) for row in result.scalars())

    async def get_chunk_exact(self, chunk_id: UUID) -> SourceChunkData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceChunkRecord)
                .join(
                    SourceProcessingRunRecord,
                    SourceProcessingRunRecord.id == SourceChunkRecord.processing_run_id,
                )
                .join(SourceRecord, SourceRecord.id == SourceProcessingRunRecord.source_id)
                .where(
                    SourceChunkRecord.id == chunk_id,
                    SourceRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_chunk(row)
