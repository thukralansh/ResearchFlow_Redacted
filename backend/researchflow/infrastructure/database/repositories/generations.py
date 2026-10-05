"""Append-only provider-neutral generation history adapter."""

from dataclasses import fields
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.generation import GenerationRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import GenerationRecordData, NewGenerationRecord


def _to_generation(row: GenerationRecord) -> GenerationRecordData:
    return GenerationRecordData(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        copilot_turn_id=row.copilot_turn_id,
        task_name=row.task_name,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        provider=row.provider,
        model_version=row.model_version,
        reasoning_version=row.reasoning_version,
        input_fingerprint=row.input_fingerprint,
        status=row.status,
        model_configuration=dict(row.model_configuration),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        raw_structured_output=row.raw_structured_output,
        validation_summary=row.validation_summary,
        error_code=row.error_code,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
    )


def _matches(existing: GenerationRecordData, candidate: NewGenerationRecord) -> bool:
    return all(
        getattr(existing, field.name) == getattr(candidate, field.name)
        for field in fields(candidate)
    )


class SqlAlchemyGenerationsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, record: NewGenerationRecord) -> GenerationRecordData:
        if record.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_exact(record.id)
        if existing is not None:
            if _matches(existing, record):
                return existing
            raise ResourceConflictError(details={"reason": "generation_record_id_reused"})

        row = GenerationRecord(
            id=record.id,
            project_id=record.project_id,
            workflow_run_id=record.workflow_run_id,
            copilot_turn_id=record.copilot_turn_id,
            task_name=record.task_name,
            prompt_version=record.prompt_version,
            schema_version=record.schema_version,
            provider=record.provider,
            model_version=record.model_version,
            reasoning_version=record.reasoning_version,
            input_fingerprint=record.input_fingerprint,
            status=record.status,
            model_configuration=record.model_configuration,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
            raw_structured_output=record.raw_structured_output,
            validation_summary=record.validation_summary,
            error_code=record.error_code,
            started_at=record.started_at,
            completed_at=record.completed_at,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_generation(row)

    async def get_exact(self, record_id: UUID) -> GenerationRecordData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(GenerationRecord).where(
                    GenerationRecord.id == record_id,
                    GenerationRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_generation(row)

    async def list_prompt_version_records(
        self, *, task_name: str, prompt_version: str
    ) -> tuple[GenerationRecordData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(GenerationRecord)
                .where(
                    GenerationRecord.project_id == self._project_id,
                    GenerationRecord.task_name == task_name,
                    GenerationRecord.prompt_version == prompt_version,
                )
                .order_by(GenerationRecord.created_at, GenerationRecord.id)
            )
        )
        return tuple(_to_generation(row) for row in result.scalars())

    async def find_reusable_success(
        self,
        *,
        workflow_run_id: UUID,
        task_name: str,
        prompt_version: str,
        schema_version: str,
        provider: str,
        model_version: str,
        reasoning_version: str | None,
        input_fingerprint: str,
        model_configuration: dict[str, object],
    ) -> GenerationRecordData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(GenerationRecord)
                .where(
                    GenerationRecord.project_id == self._project_id,
                    GenerationRecord.workflow_run_id == workflow_run_id,
                    GenerationRecord.task_name == task_name,
                    GenerationRecord.prompt_version == prompt_version,
                    GenerationRecord.schema_version == schema_version,
                    GenerationRecord.provider == provider,
                    GenerationRecord.model_version == model_version,
                    GenerationRecord.reasoning_version == reasoning_version,
                    GenerationRecord.input_fingerprint == input_fingerprint,
                    GenerationRecord.model_configuration.contains(model_configuration),
                    GenerationRecord.status == "succeeded",
                )
                .order_by(GenerationRecord.created_at, GenerationRecord.id)
            )
        )
        row = result.scalars().first()
        return None if row is None else _to_generation(row)

    async def find_reusable_copilot_success(
        self,
        *,
        copilot_turn_id: UUID,
        task_name: str,
        prompt_version: str,
        schema_version: str,
        provider: str,
        model_version: str,
        reasoning_version: str | None,
        input_fingerprint: str,
        model_configuration: dict[str, object],
    ) -> GenerationRecordData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(GenerationRecord)
                .where(
                    GenerationRecord.project_id == self._project_id,
                    GenerationRecord.copilot_turn_id == copilot_turn_id,
                    GenerationRecord.task_name == task_name,
                    GenerationRecord.prompt_version == prompt_version,
                    GenerationRecord.schema_version == schema_version,
                    GenerationRecord.provider == provider,
                    GenerationRecord.model_version == model_version,
                    GenerationRecord.reasoning_version == reasoning_version,
                    GenerationRecord.input_fingerprint == input_fingerprint,
                    GenerationRecord.model_configuration.contains(model_configuration),
                    GenerationRecord.status == "succeeded",
                )
                .order_by(GenerationRecord.created_at, GenerationRecord.id)
            )
        )
        row = result.scalars().first()
        return None if row is None else _to_generation(row)

    async def list_for_workflow(self, workflow_run_id: UUID) -> tuple[GenerationRecordData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(GenerationRecord)
                .where(
                    GenerationRecord.project_id == self._project_id,
                    GenerationRecord.workflow_run_id == workflow_run_id,
                )
                .order_by(GenerationRecord.created_at, GenerationRecord.id)
            )
        )
        return tuple(_to_generation(row) for row in result.scalars())

    async def list_for_copilot_turn(
        self, copilot_turn_id: UUID
    ) -> tuple[GenerationRecordData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(GenerationRecord)
                .where(
                    GenerationRecord.project_id == self._project_id,
                    GenerationRecord.copilot_turn_id == copilot_turn_id,
                )
                .order_by(GenerationRecord.created_at, GenerationRecord.id)
            )
        )
        return tuple(_to_generation(row) for row in result.scalars())
