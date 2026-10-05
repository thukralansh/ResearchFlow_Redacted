"""Product-visible workflow lifecycle records."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import (
    ActorType,
    WorkflowInterruptStatus,
    WorkflowStatus,
    WorkflowType,
    check_values,
)


class WorkflowRunRecord(Base):
    """Canonical product lifecycle for an ID-only LangGraph execution."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(check_values("workflow_type", WorkflowType), name="workflow_type_valid"),
        CheckConstraint(check_values("status", WorkflowStatus), name="status_valid"),
        CheckConstraint("btrim(langgraph_thread_id) <> ''", name="thread_id_not_blank"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="completion_not_before_start",
        ),
        Index(
            "uq_workflow_runs_active_full_analysis",
            "project_id",
            unique=True,
            postgresql_where=text(
                "workflow_type = 'full_analysis' AND status IN "
                "('queued', 'running', 'waiting_for_review', 'waiting_for_judgment')"
            ),
        ),
        Index("uq_workflow_runs_langgraph_thread", "langgraph_thread_id", unique=True),
        Index("ix_workflow_runs_project_updated", "project_id", "updated_at"),
        UniqueConstraint("id", "project_id", name="uq_workflow_runs_id_project"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_workflow_runs_project_id_projects",
        ),
        nullable=False,
    )
    workflow_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_stage: Mapped[str | None] = mapped_column(Text)
    langgraph_thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    primary_source_processing_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.source_processing_runs.id",
            ondelete="RESTRICT",
            name="fk_workflow_primary_source_run",
        ),
    )
    primary_research_question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_workflow_primary_question",
        ),
        nullable=False,
    )
    primary_question_source_relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_question_sources.id",
            ondelete="RESTRICT",
            name="fk_workflow_primary_paper_rel",
        ),
    )
    paper_repository_relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
            ondelete="RESTRICT",
            name="fk_workflow_paper_repo_rel",
        ),
    )
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_workflow_runs_repository_snapshot_id_repository_snapshots",
        ),
    )
    graph_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="rf-project-analysis-graph-v1"
    )
    checkpoint_namespace: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="project-analysis"
    )
    current_method_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_workflow_runs_current_method_model",
            use_alter=True,
        ),
    )
    current_mental_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_workflow_runs_current_mental_model",
            use_alter=True,
        ),
    )
    pending_judgment_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.judgment_requests.id",
            ondelete="RESTRICT",
            name="fk_workflow_runs_pending_judgment_request",
            use_alter=True,
        ),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_detail: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class WorkflowInterruptRecord(Base):
    """Channel-neutral durable authority for one workflow pause boundary."""

    __tablename__ = "workflow_interrupts"
    __table_args__ = (
        CheckConstraint(check_values("status", WorkflowInterruptStatus), name="status_valid"),
        CheckConstraint(check_values("created_by_type", ActorType), name="actor_type_valid"),
        CheckConstraint("btrim(interrupt_kind) <> ''", name="kind_not_blank"),
        CheckConstraint(
            "expected_context_fingerprint ~ '^[0-9a-f]{64}$'",
            name="context_fingerprint_sha256",
        ),
        CheckConstraint("jsonb_typeof(target_refs) = 'array'", name="target_refs_array"),
        CheckConstraint("pg_column_size(target_refs) <= 65536", name="target_refs_bounded"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint(
            "(status = 'pending' AND resolution_event_id IS NULL AND resolved_at IS NULL "
            "AND resumed_at IS NULL) OR "
            "(status = 'resolved' AND resolution_event_id IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resumed_at IS NULL) OR "
            "(status = 'resumed' AND resolution_event_id IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resumed_at IS NOT NULL) OR "
            "(status = 'cancelled' AND resumed_at IS NULL)",
            name="resolution_state_consistent",
        ),
        UniqueConstraint("id", "project_id", name="uq_workflow_interrupts_id_project"),
        Index(
            "uq_workflow_interrupts_active_run",
            "workflow_run_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'resolved')"),
        ),
        Index("ix_workflow_interrupts_project_created", "project_id", "created_at"),
        ForeignKeyConstraint(
            ["workflow_run_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.workflow_runs.id",
                f"{CANONICAL_SCHEMA}.workflow_runs.project_id",
            ],
            name="fk_workflow_interrupts_run_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["resolution_event_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.operation_events.id",
                f"{CANONICAL_SCHEMA}.operation_events.project_id",
            ],
            name="fk_workflow_interrupts_resolution_event_project",
            ondelete="RESTRICT",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_workflow_interrupts_project",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    interrupt_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    expected_context_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    target_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolution_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
