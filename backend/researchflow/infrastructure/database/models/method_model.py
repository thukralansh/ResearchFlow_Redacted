"""Versioned MethodModel composition, review, rendering, and assessment records."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import (
    FreshnessStatus,
    MethodAssessmentOutcome,
    MethodMemberRole,
    MethodRenderingType,
    MethodReviewSection,
    SectionReviewOutcome,
)
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class MethodModelRecord(Base):
    """One immutable semantic version of a structured scientific method."""

    __tablename__ = "method_models"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("btrim(objective) <> ''", name="objective_not_blank"),
        CheckConstraint(
            check_values("review_status", KnowledgeReviewStatus), name="review_status_valid"
        ),
        CheckConstraint(
            check_values("lifecycle_status", KnowledgeLifecycleStatus),
            name="lifecycle_status_valid",
        ),
        CheckConstraint(
            check_values("freshness_status", FreshnessStatus), name="freshness_status_valid"
        ),
        CheckConstraint(
            "(review_status IN ('candidate', 'needs_revision', 'rejected') "
            "AND lifecycle_status = 'current') OR "
            "(review_status = 'accepted' "
            "AND lifecycle_status IN ('current', 'challenged', 'superseded'))",
            name="review_lifecycle_combination_valid",
        ),
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
        UniqueConstraint("lineage_id", "version", name="uq_method_models_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_method_models_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_method_models_id_project"),
        UniqueConstraint("supersedes_id", name="uq_method_models_supersedes_once"),
        ForeignKeyConstraint(
            ["research_question_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.research_questions.id",
                f"{CANONICAL_SCHEMA}.research_questions.project_id",
            ],
            name="fk_method_models_project_question",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.lineage_id",
            ],
            name="fk_method_models_derived_from_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.lineage_id",
            ],
            name="fk_method_models_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_assessment_id", "id"],
            [
                f"{CANONICAL_SCHEMA}.method_model_assessments.id",
                f"{CANONICAL_SCHEMA}.method_model_assessments.method_model_id",
            ],
            name="fk_method_models_current_assessment",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index(
            "uq_method_models_current_accepted_question",
            "research_question_id",
            unique=True,
            postgresql_where=text("review_status = 'accepted' AND lifecycle_status = 'current'"),
        ),
        Index(
            "uq_method_models_unsuperseded_accepted_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text(
                "review_status = 'accepted' AND lifecycle_status IN ('current', 'challenged')"
            ),
        ),
        Index(
            "ix_method_models_project_review_freshness",
            "project_id",
            "review_status",
            "freshness_status",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    research_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeReviewStatus.CANDIDATE.value
    )
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeLifecycleStatus.CURRENT.value
    )
    freshness_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=FreshnessStatus.CURRENT.value
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    composition_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    current_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_method_models_generation_record_id_generation_records",
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


class MethodModelMemberRecord(Base):
    """Exact MethodElement membership in one MethodModel version."""

    __tablename__ = "method_model_members"
    __table_args__ = (
        CheckConstraint(
            check_values("review_section", MethodReviewSection), name="review_section_valid"
        ),
        CheckConstraint(check_values("member_role", MethodMemberRole), name="member_role_valid"),
        CheckConstraint(
            "sequence_index IS NULL OR sequence_index >= 0", name="sequence_index_nonnegative"
        ),
        CheckConstraint("btrim(inclusion_rationale) <> ''", name="rationale_not_blank"),
        UniqueConstraint(
            "method_model_id",
            "method_element_id",
            name="uq_method_model_members_model_element",
        ),
        Index(
            "uq_method_model_members_section_sequence",
            "method_model_id",
            "review_section",
            "sequence_index",
            unique=True,
            postgresql_where=text("sequence_index IS NOT NULL"),
        ),
        Index("ix_method_model_members_element", "method_element_id", "method_model_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    method_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_method_model_members_model",
        ),
        nullable=False,
    )
    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_model_members_element",
        ),
        nullable=False,
    )
    review_section: Mapped[str] = mapped_column(Text, nullable=False)
    member_role: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_index: Mapped[int | None] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inclusion_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MethodModelSectionReviewRecord(Base):
    """Immutable, branch-free review history for one required method section."""

    __tablename__ = "method_model_section_reviews"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            check_values("review_section", MethodReviewSection), name="review_section_valid"
        ),
        CheckConstraint(check_values("outcome", SectionReviewOutcome), name="outcome_valid"),
        CheckConstraint(
            "member_set_fingerprint ~ '^[0-9a-f]{64}$'",
            name="member_set_fingerprint_sha256",
        ),
        CheckConstraint(
            "(outcome = 'not_applicable' AND rationale IS NOT NULL "
            "AND btrim(rationale) <> '') OR "
            "(outcome = 'accepted' AND (rationale IS NULL OR btrim(rationale) <> ''))",
            name="outcome_rationale_valid",
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        CheckConstraint(
            "(version = 1 AND supersedes_id IS NULL) OR "
            "(version > 1 AND supersedes_id IS NOT NULL)",
            name="version_chain_valid",
        ),
        UniqueConstraint(
            "method_model_id",
            "review_section",
            "version",
            name="uq_method_section_reviews_section_version",
        ),
        UniqueConstraint(
            "id",
            "method_model_id",
            "review_section",
            name="uq_method_section_reviews_id_section",
        ),
        UniqueConstraint("supersedes_id", name="uq_method_section_reviews_supersedes_once"),
        ForeignKeyConstraint(
            ["supersedes_id", "method_model_id", "review_section"],
            [
                f"{CANONICAL_SCHEMA}.method_model_section_reviews.id",
                f"{CANONICAL_SCHEMA}.method_model_section_reviews.method_model_id",
                f"{CANONICAL_SCHEMA}.method_model_section_reviews.review_section",
            ],
            name="fk_method_section_reviews_supersedes_section",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_method_section_reviews_model_section_created",
            "method_model_id",
            "review_section",
            "created_at",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    method_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_method_section_reviews_model",
        ),
        nullable=False,
    )
    review_section: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    member_set_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    operation_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.operation_events.id",
            ondelete="RESTRICT",
            name="fk_method_section_reviews_operation_event",
        ),
        nullable=False,
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MethodModelRenderingRecord(Base):
    """Replaceable non-canonical view over one exact MethodModel version."""

    __tablename__ = "method_model_renderings"
    __table_args__ = (
        CheckConstraint(
            check_values("rendering_type", MethodRenderingType), name="rendering_type_valid"
        ),
        CheckConstraint("btrim(content) <> ''", name="content_not_blank"),
        CheckConstraint(
            "(prompt_version IS NULL AND provider IS NULL AND model_version IS NULL) OR "
            "(prompt_version IS NOT NULL AND btrim(prompt_version) <> '' "
            "AND provider IS NOT NULL AND btrim(provider) <> '' "
            "AND model_version IS NOT NULL AND btrim(model_version) <> '')",
            name="generation_identity_complete",
        ),
        Index("ix_method_model_renderings_model_created", "method_model_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    method_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_method_model_renderings_model",
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
            name="fk_method_model_renderings_generation_record",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MethodModelAssessmentRecord(Base):
    """Transparent aggregate assessment over exact member grounding state."""

    __tablename__ = "method_model_assessments"
    __table_args__ = (
        CheckConstraint(
            "composition_fingerprint ~ '^[0-9a-f]{64}$'",
            name="composition_fingerprint_sha256",
        ),
        CheckConstraint(
            "member_grounding_fingerprint ~ '^[0-9a-f]{64}$'",
            name="member_grounding_fingerprint_sha256",
        ),
        CheckConstraint(
            "source_evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="source_evidence_fingerprint_sha256",
        ),
        CheckConstraint(
            "repository_evidence_fingerprint IS NULL OR "
            "repository_evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="repository_evidence_fp_sha256",
        ),
        CheckConstraint("unsupported_required_count >= 0", name="unsupported_count_nonnegative"),
        CheckConstraint("mixed_evidence_count >= 0", name="mixed_count_nonnegative"),
        CheckConstraint("contradicted_count >= 0", name="contradicted_count_nonnegative"),
        CheckConstraint("missing_provenance_count >= 0", name="missing_provenance_nonnegative"),
        CheckConstraint(
            "unresolved_scientific_ambiguity_count >= 0",
            name="unresolved_ambiguity_nonnegative",
        ),
        CheckConstraint(check_values("outcome", MethodAssessmentOutcome), name="outcome_valid"),
        CheckConstraint("btrim(rationale) <> ''", name="rationale_not_blank"),
        UniqueConstraint("id", "method_model_id", name="uq_method_model_assessments_id_model"),
        Index("ix_method_model_assessments_model_created", "method_model_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    method_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_method_model_assessments_model",
        ),
        nullable=False,
    )
    composition_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    member_grounding_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    source_evidence_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    repository_evidence_fingerprint: Mapped[str | None] = mapped_column(Text)
    unsupported_required_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mixed_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    contradicted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_provenance_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unresolved_scientific_ambiguity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_method_model_assessments_generation_record",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
