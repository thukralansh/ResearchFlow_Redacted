"""Structured, versioned Mental Model composition and reassessment records."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.model_catalog import (
    MentalModelAssessmentResult,
    MentalModelMemberRole,
    MentalModelRenderingType,
    MentalModelStatus,
)
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class MentalModelRecord(Base):
    """One immutable semantic composition of accepted project understanding."""

    __tablename__ = "mental_models"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint(check_values("status", MentalModelStatus), name="status_valid"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint(
            "composition_fingerprint ~ '^[0-9a-f]{64}$'",
            name="composition_fingerprint_sha256",
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        UniqueConstraint("lineage_id", "version", name="uq_mental_models_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_mental_models_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_mental_models_id_project"),
        UniqueConstraint("supersedes_id", name="uq_mental_models_supersedes_once"),
        ForeignKeyConstraint(
            ["research_question_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.research_questions.id",
                f"{CANONICAL_SCHEMA}.research_questions.project_id",
            ],
            name="fk_mental_models_question_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.project_id",
            ],
            name="fk_mental_models_method_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.mental_models.id",
                f"{CANONICAL_SCHEMA}.mental_models.lineage_id",
            ],
            name="fk_mental_models_derived_from_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.mental_models.id",
                f"{CANONICAL_SCHEMA}.mental_models.lineage_id",
            ],
            name="fk_mental_models_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_mental_models_current_project",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        Index(
            "uq_mental_models_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        Index("ix_mental_models_question_method", "research_question_id", "method_model_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_mental_models_project",
        ),
        nullable=False,
    )
    research_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    composition_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_mental_models_generation_record",
        ),
    )
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MentalModelMemberRecord(Base):
    """One exact accepted cognitive member or exceptional EvidenceLink."""

    __tablename__ = "mental_model_members"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(knowledge_object_id, human_judgment_id, evidence_link_id) = 1",
            name="exactly_one_member_target",
        ),
        CheckConstraint(check_values("role", MentalModelMemberRole), name="role_valid"),
        CheckConstraint(
            "evidence_link_id IS NULL OR role IN "
            "('decisive_supporting_evidence', 'central_contradicting_evidence')",
            name="evidence_role_exception_only",
        ),
        CheckConstraint("btrim(inclusion_rationale) <> ''", name="rationale_not_blank"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        Index(
            "uq_mental_model_members_knowledge",
            "mental_model_id",
            "knowledge_object_id",
            unique=True,
            postgresql_where=text("knowledge_object_id IS NOT NULL"),
        ),
        Index(
            "uq_mental_model_members_judgment",
            "mental_model_id",
            "human_judgment_id",
            unique=True,
            postgresql_where=text("human_judgment_id IS NOT NULL"),
        ),
        Index(
            "uq_mental_model_members_evidence",
            "mental_model_id",
            "evidence_link_id",
            unique=True,
            postgresql_where=text("evidence_link_id IS NOT NULL"),
        ),
        Index("ix_mental_model_members_role", "mental_model_id", "role"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    mental_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_mental_model_members_model",
        ),
        nullable=False,
    )
    knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_mental_model_members_knowledge",
        ),
    )
    human_judgment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.human_judgments.id",
            ondelete="RESTRICT",
            name="fk_mental_model_members_judgment",
        ),
    )
    evidence_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.evidence_links.id",
            ondelete="RESTRICT",
            name="fk_mental_model_members_evidence",
        ),
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    inclusion_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MentalModelRenderingRecord(Base):
    """Replaceable prose view over one exact structured Mental Model."""

    __tablename__ = "mental_model_renderings"
    __table_args__ = (
        CheckConstraint(
            check_values("rendering_type", MentalModelRenderingType),
            name="rendering_type_valid",
        ),
        CheckConstraint("btrim(content) <> ''", name="content_not_blank"),
        Index("ix_mental_model_renderings_model_created", "mental_model_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    mental_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_mental_model_renderings_model",
        ),
        nullable=False,
    )
    rendering_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_mental_model_renderings_generation_record",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MentalModelAssessmentRecord(Base):
    """Append-only proof that a model was reassessed against exact current inputs."""

    __tablename__ = "mental_model_assessments"
    __table_args__ = (
        CheckConstraint(
            "method_model_fingerprint ~ '^[0-9a-f]{64}$'",
            name="method_model_fingerprint_sha256",
        ),
        CheckConstraint(
            "repository_evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="repository_evidence_fp_sha256",
        ),
        CheckConstraint(
            "accepted_knowledge_fingerprint ~ '^[0-9a-f]{64}$'",
            name="accepted_knowledge_fp_sha256",
        ),
        CheckConstraint(
            "accepted_mapping_fingerprint ~ '^[0-9a-f]{64}$'",
            name="accepted_mapping_fingerprint_sha256",
        ),
        CheckConstraint(
            "current_judgment_fingerprint ~ '^[0-9a-f]{64}$'",
            name="current_judgment_fingerprint_sha256",
        ),
        CheckConstraint(check_values("result", MentalModelAssessmentResult), name="result_valid"),
        CheckConstraint(
            "(result = 'no_change' AND resulting_model_id IS NULL) OR "
            "(result = 'new_version_created' AND resulting_model_id IS NOT NULL)",
            name="resulting_model_matches_result",
        ),
        CheckConstraint("btrim(rationale) <> ''", name="rationale_not_blank"),
        UniqueConstraint("id", "assessed_model_id", name="uq_mental_model_assessments_id_model"),
        ForeignKeyConstraint(
            ["assessed_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.mental_models.id",
                f"{CANONICAL_SCHEMA}.mental_models.project_id",
            ],
            name="fk_mental_model_assessments_assessed_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["resulting_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.mental_models.id",
                f"{CANONICAL_SCHEMA}.mental_models.project_id",
            ],
            name="fk_mental_model_assessments_result_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["research_question_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.research_questions.id",
                f"{CANONICAL_SCHEMA}.research_questions.project_id",
            ],
            name="fk_mental_model_assessments_question_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.project_id",
            ],
            name="fk_mental_model_assessments_method_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_model_assessment_id", "method_model_id"],
            [
                f"{CANONICAL_SCHEMA}.method_model_assessments.id",
                f"{CANONICAL_SCHEMA}.method_model_assessments.method_model_id",
            ],
            name="fk_mental_model_assessments_method_assessment",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_mental_model_assessments_model_created",
            "assessed_model_id",
            "created_at",
        ),
        Index(
            "ix_mental_model_assessments_coverage",
            "project_id",
            "repository_snapshot_id",
            "method_model_id",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_mental_model_assessments_project",
        ),
        nullable=False,
    )
    assessed_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_mental_model_assessments_snapshot",
        ),
    )
    research_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    method_model_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    repository_evidence_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_knowledge_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_mapping_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    current_judgment_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    resulting_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_mental_model_assessments_generation_record",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
