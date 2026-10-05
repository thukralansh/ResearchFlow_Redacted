"""Versioned Judgment Requests, immutable options, and Human Judgments."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.model_catalog import JudgmentRequestStatus
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import (
    ActorType,
    LifecycleStatus,
    check_values,
)


class JudgmentRequestRecord(Base):
    """One immutable request version for a human decision on an exact Ambiguity."""

    __tablename__ = "judgment_requests"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(decision_question) <> ''", name="decision_question_not_blank"),
        CheckConstraint("btrim(why_human_required) <> ''", name="why_human_required_not_blank"),
        CheckConstraint(check_values("status", JudgmentRequestStatus), name="status_valid"),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        CheckConstraint(
            "(status = 'open' AND resolved_at IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL)",
            name="resolution_timestamp_matches_status",
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        UniqueConstraint("lineage_id", "version", name="uq_judgment_requests_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_judgment_requests_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_judgment_requests_id_project"),
        UniqueConstraint("supersedes_id", name="uq_judgment_requests_supersedes_once"),
        ForeignKeyConstraint(
            ["ambiguity_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.project_id",
            ],
            name="fk_judgment_requests_ambiguity_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.judgment_requests.id",
                f"{CANONICAL_SCHEMA}.judgment_requests.lineage_id",
            ],
            name="fk_judgment_requests_derived_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.judgment_requests.id",
                f"{CANONICAL_SCHEMA}.judgment_requests.lineage_id",
            ],
            name="fk_judgment_requests_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_judgment_requests_current_open_ambiguity",
            "ambiguity_id",
            unique=True,
            postgresql_where=text("status = 'open' AND lifecycle_status = 'current'"),
        ),
        Index(
            "uq_judgment_requests_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        Index("ix_judgment_requests_project_status", "project_id", "status"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_judgment_requests_project",
        ),
        nullable=False,
    )
    ambiguity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.ambiguities.knowledge_object_id",
            ondelete="RESTRICT",
            name="fk_judgment_requests_ambiguity",
        ),
        nullable=False,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    decision_question: Mapped[str] = mapped_column(Text, nullable=False)
    why_human_required: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=LifecycleStatus.CURRENT.value
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.workflow_runs.id",
            ondelete="RESTRICT",
            name="fk_judgment_requests_workflow_run",
        ),
        nullable=False,
    )
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_judgment_requests_generation_record",
        ),
    )
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JudgmentOptionRecord(Base):
    """One immutable, ordered option owned by an exact JudgmentRequest version."""

    __tablename__ = "judgment_options"
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(description) <> ''", name="description_not_blank"),
        CheckConstraint(
            "jsonb_typeof(supporting_evidence) = 'array'",
            name="supporting_evidence_array",
        ),
        CheckConstraint(
            "jsonb_typeof(conflicting_evidence) = 'array'",
            name="conflicting_evidence_array",
        ),
        CheckConstraint(
            "btrim(implementation_consequences) <> ''",
            name="implementation_consequences_not_blank",
        ),
        CheckConstraint("rank >= 1", name="rank_positive"),
        UniqueConstraint("id", "judgment_request_id", name="uq_judgment_options_id_request"),
        UniqueConstraint("judgment_request_id", "rank", name="uq_judgment_options_request_rank"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    judgment_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.judgment_requests.id",
            ondelete="RESTRICT",
            name="fk_judgment_options_request",
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    conflicting_evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    implementation_consequences: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class HumanJudgmentRecord(Base):
    """One immutable human decision version with rationale and confidence."""

    __tablename__ = "human_judgments"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "num_nonnulls(selected_option_id, custom_decision) = 1",
            name="exactly_one_decision",
        ),
        CheckConstraint(
            "custom_decision IS NULL OR btrim(custom_decision) <> ''",
            name="custom_decision_not_blank",
        ),
        CheckConstraint("btrim(rationale) <> ''", name="rationale_not_blank"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit_interval"),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        UniqueConstraint("lineage_id", "version", name="uq_human_judgments_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_human_judgments_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_human_judgments_id_project"),
        UniqueConstraint("supersedes_id", name="uq_human_judgments_supersedes_once"),
        ForeignKeyConstraint(
            ["judgment_request_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.judgment_requests.id",
                f"{CANONICAL_SCHEMA}.judgment_requests.project_id",
            ],
            name="fk_human_judgments_request_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["selected_option_id", "judgment_request_id"],
            [
                f"{CANONICAL_SCHEMA}.judgment_options.id",
                f"{CANONICAL_SCHEMA}.judgment_options.judgment_request_id",
            ],
            name="fk_human_judgments_selected_option",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.human_judgments.id",
                f"{CANONICAL_SCHEMA}.human_judgments.lineage_id",
            ],
            name="fk_human_judgments_derived_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.human_judgments.id",
                f"{CANONICAL_SCHEMA}.human_judgments.lineage_id",
            ],
            name="fk_human_judgments_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_human_judgments_current_request",
            "judgment_request_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        Index(
            "uq_human_judgments_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        Index("ix_human_judgments_project_created", "project_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_human_judgments_project",
        ),
        nullable=False,
    )
    judgment_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    custom_decision: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=LifecycleStatus.CURRENT.value
    )
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
