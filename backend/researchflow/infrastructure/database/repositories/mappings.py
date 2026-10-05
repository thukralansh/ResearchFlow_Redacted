"""Paper-to-code mapping queries with accepted/current semantics."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.infrastructure.database.models.mapping import PaperCodeMappingRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import NewPaperCodeMapping, PaperCodeMappingData


def _to_mapping(row: PaperCodeMappingRecord) -> PaperCodeMappingData:
    return PaperCodeMappingData(
        id=row.id,
        project_id=row.project_id,
        repository_id=row.repository_id,
        repository_snapshot_id=row.repository_snapshot_id,
        repository_inspection_run_id=row.repository_inspection_run_id,
        lineage_id=row.lineage_id,
        version=row.version,
        paper_anchor_type=row.paper_anchor_type,
        source_chunk_id=row.source_chunk_id,
        knowledge_object_id=row.knowledge_object_id,
        method_model_id=row.method_model_id,
        method_component_id=row.method_component_id,
        method_equation_id=row.method_equation_id,
        method_symbol_id=row.method_symbol_id,
        method_algorithm_step_id=row.method_algorithm_step_id,
        method_data_flow_id=row.method_data_flow_id,
        method_validity_condition_id=row.method_validity_condition_id,
        repository_component_id=row.repository_component_id,
        mapping_type=row.mapping_type,
        explanation=row.explanation,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        freshness_status=row.freshness_status,
        generation_record_id=row.generation_record_id,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
    )


class SqlAlchemyMappingsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, mapping: NewPaperCodeMapping) -> PaperCodeMappingData:
        if mapping.project_id != self._project_id:
            from researchflow.errors import ResourceConflictError

            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        row = PaperCodeMappingRecord(**asdict(mapping))
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_mapping(row)

    async def get_exact(self, mapping_id: UUID) -> PaperCodeMappingData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord).where(
                    PaperCodeMappingRecord.id == mapping_id,
                    PaperCodeMappingRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_mapping(row)

    async def get_exact_for_update(
        self, mapping_id: UUID, *, nowait: bool = False
    ) -> PaperCodeMappingData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord)
                .where(
                    PaperCodeMappingRecord.id == mapping_id,
                    PaperCodeMappingRecord.project_id == self._project_id,
                )
                .with_for_update(nowait=nowait)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_mapping(row)

    async def list_lineage_for_update(
        self, lineage_id: UUID, *, nowait: bool = False
    ) -> tuple[PaperCodeMappingData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord)
                .where(
                    PaperCodeMappingRecord.project_id == self._project_id,
                    PaperCodeMappingRecord.lineage_id == lineage_id,
                )
                .order_by(PaperCodeMappingRecord.version, PaperCodeMappingRecord.id)
                .with_for_update(nowait=nowait)
            )
        )
        return tuple(_to_mapping(row) for row in result.scalars())

    async def list_all(self) -> tuple[PaperCodeMappingData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord)
                .where(PaperCodeMappingRecord.project_id == self._project_id)
                .order_by(PaperCodeMappingRecord.created_at, PaperCodeMappingRecord.id)
            )
        )
        return tuple(_to_mapping(row) for row in result.scalars())

    async def list_by_generation_record(
        self, generation_record_id: UUID
    ) -> tuple[PaperCodeMappingData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord)
                .where(
                    PaperCodeMappingRecord.project_id == self._project_id,
                    PaperCodeMappingRecord.generation_record_id == generation_record_id,
                )
                .order_by(PaperCodeMappingRecord.created_at, PaperCodeMappingRecord.id)
            )
        )
        return tuple(_to_mapping(row) for row in result.scalars())

    async def find_candidate_duplicate(
        self,
        *,
        repository_inspection_run_id: UUID,
        paper_anchor_type: str,
        anchor_id: UUID,
        repository_component_id: UUID | None,
        mapping_type: str,
    ) -> PaperCodeMappingData | None:
        anchor_name = {
            "source_chunk": "source_chunk_id",
            "knowledge_object": "knowledge_object_id",
            "method_model": "method_model_id",
            "method_component": "method_component_id",
            "equation": "method_equation_id",
            "method_symbol": "method_symbol_id",
            "algorithm_step": "method_algorithm_step_id",
            "data_flow": "method_data_flow_id",
            "validity_condition": "method_validity_condition_id",
        }[paper_anchor_type]
        anchor_column = getattr(PaperCodeMappingRecord, anchor_name)
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord)
                .where(
                    PaperCodeMappingRecord.project_id == self._project_id,
                    PaperCodeMappingRecord.repository_inspection_run_id
                    == repository_inspection_run_id,
                    PaperCodeMappingRecord.paper_anchor_type == paper_anchor_type,
                    anchor_column == anchor_id,
                    PaperCodeMappingRecord.repository_component_id.is_not_distinct_from(
                        repository_component_id
                    ),
                    PaperCodeMappingRecord.mapping_type == mapping_type,
                )
                .order_by(PaperCodeMappingRecord.created_at, PaperCodeMappingRecord.id)
                .limit(1)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_mapping(row)

    async def list_accepted_current(self) -> tuple[PaperCodeMappingData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperCodeMappingRecord)
                .where(
                    PaperCodeMappingRecord.project_id == self._project_id,
                    PaperCodeMappingRecord.review_status == "accepted",
                    PaperCodeMappingRecord.lifecycle_status == "current",
                    PaperCodeMappingRecord.freshness_status == "current",
                )
                .order_by(PaperCodeMappingRecord.created_at, PaperCodeMappingRecord.id)
            )
        )
        return tuple(_to_mapping(row) for row in result.scalars())

    async def update_review_lifecycle(
        self,
        mapping_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> PaperCodeMappingData:
        result = await execute_database(
            lambda: self._session.execute(
                update(PaperCodeMappingRecord)
                .where(
                    PaperCodeMappingRecord.id == mapping_id,
                    PaperCodeMappingRecord.project_id == self._project_id,
                    PaperCodeMappingRecord.version == expected_version,
                    PaperCodeMappingRecord.review_status == expected_review_status,
                    PaperCodeMappingRecord.lifecycle_status == expected_lifecycle_status,
                )
                .values(
                    review_status=review_status,
                    lifecycle_status=lifecycle_status,
                    supersedes_id=supersedes_id,
                )
                .returning(PaperCodeMappingRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            from researchflow.errors import ResourceConflictError

            raise ResourceConflictError(details={"reason": "stale_paper_code_mapping"})
        return _to_mapping(row)
