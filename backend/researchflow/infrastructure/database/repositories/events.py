"""Append-only OperationEvent adapter with exact idempotent replay."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import DomainValidationError, ResourceConflictError
from researchflow.infrastructure.database.models.audit import OperationEventRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import NewOperationEvent, OperationEventData


def _to_event(row: OperationEventRecord) -> OperationEventData:
    return OperationEventData(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        operation_type=row.operation_type,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
        input_refs=tuple(dict(item) for item in row.input_refs),
        output_refs=tuple(dict(item) for item in row.output_refs),
        reason=row.reason,
        idempotency_key=row.idempotency_key,
        input_fingerprint=row.input_fingerprint,
        request_id=row.request_id,
        created_at=row.created_at,
    )


def _event_matches(existing: OperationEventData, candidate: NewOperationEvent) -> bool:
    return (
        existing.project_id == candidate.project_id
        and existing.workflow_run_id == candidate.workflow_run_id
        and existing.operation_type == candidate.operation_type
        and existing.created_by_type == candidate.created_by_type
        and existing.created_by_id == candidate.created_by_id
        and existing.input_refs == candidate.input_refs
        and existing.output_refs == candidate.output_refs
        and existing.reason == candidate.reason
        and existing.idempotency_key == candidate.idempotency_key
        and existing.input_fingerprint == candidate.input_fingerprint
        and existing.request_id == candidate.request_id
    )


class SqlAlchemyEventsRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def append(self, event: NewOperationEvent) -> OperationEventData:
        if event.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        if (event.idempotency_key is None) != (event.input_fingerprint is None):
            raise DomainValidationError(details={"reason": "idempotency_pair_incomplete"})

        if event.idempotency_key is not None:
            existing = await self.get_by_idempotency_key(
                event.operation_type, event.idempotency_key
            )
            if existing is not None:
                if existing.input_fingerprint == event.input_fingerprint:
                    return existing
                raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})

        by_id = await self._get_by_id(event.id)
        if by_id is not None:
            if _event_matches(by_id, event):
                return by_id
            raise ResourceConflictError(details={"reason": "operation_event_id_reused"})

        row = OperationEventRecord(
            id=event.id,
            project_id=event.project_id,
            workflow_run_id=event.workflow_run_id,
            operation_type=event.operation_type,
            created_by_type=event.created_by_type,
            created_by_id=event.created_by_id,
            input_refs=[dict(item) for item in event.input_refs],
            output_refs=[dict(item) for item in event.output_refs],
            reason=event.reason,
            idempotency_key=event.idempotency_key,
            input_fingerprint=event.input_fingerprint,
            request_id=event.request_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_event(row)

    async def get_by_idempotency_key(
        self, operation_type: str, idempotency_key: str
    ) -> OperationEventData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(OperationEventRecord).where(
                    OperationEventRecord.project_id == self._project_id,
                    OperationEventRecord.operation_type == operation_type,
                    OperationEventRecord.idempotency_key == idempotency_key,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_event(row)

    async def get_exact(self, event_id: UUID) -> OperationEventData | None:
        return await self._get_by_id(event_id)

    async def list_for_workflow(
        self, workflow_run_id: UUID, *, operation_type: str | None = None
    ) -> tuple[OperationEventData, ...]:
        statement = select(OperationEventRecord).where(
            OperationEventRecord.project_id == self._project_id,
            OperationEventRecord.workflow_run_id == workflow_run_id,
        )
        if operation_type is not None:
            statement = statement.where(OperationEventRecord.operation_type == operation_type)
        statement = statement.order_by(OperationEventRecord.created_at, OperationEventRecord.id)
        result = await execute_database(lambda: self._session.execute(statement))
        return tuple(_to_event(row) for row in result.scalars())

    async def list_for_knowledge_lineage(self, lineage_id: UUID) -> tuple[OperationEventData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(OperationEventRecord)
                .where(
                    OperationEventRecord.project_id == self._project_id,
                )
                .order_by(OperationEventRecord.created_at, OperationEventRecord.id)
            )
        )
        identity = str(lineage_id)
        return tuple(
            event
            for row in result.scalars()
            if any(
                reference.get("lineage_id") == identity
                for reference in (*row.input_refs, *row.output_refs)
            )
            for event in (_to_event(row),)
        )

    async def _get_by_id(self, event_id: UUID) -> OperationEventData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(OperationEventRecord).where(
                    OperationEventRecord.id == event_id,
                    OperationEventRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_event(row)
