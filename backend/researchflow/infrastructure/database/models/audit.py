"""Append-only operation audit records."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class OperationEventRecord(Base):
    """Audit identity for one consequential state transition."""

    __tablename__ = "operation_events"
    __table_args__ = (
        CheckConstraint("btrim(operation_type) <> ''", name="operation_type_not_blank"),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        UniqueConstraint("id", "project_id", name="uq_operation_events_id_project"),
        Index("ix_operation_events_project_created", "project_id", "created_at"),
        Index("ix_operation_events_workflow_run", "workflow_run_id"),
        Index(
            "uq_operation_events_idempotency",
            "project_id",
            "operation_type",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_operation_events_project_id_projects",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.workflow_runs.id",
            ondelete="RESTRICT",
            name="fk_operation_events_workflow_run_id_workflow_runs",
        ),
    )
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    output_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    input_fingerprint: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
