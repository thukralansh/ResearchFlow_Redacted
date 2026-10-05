"""Typed proposition knowledge and presentation history records."""

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.knowledge_catalog import (
    STATEMENT_NORMALIZATION_VERSION,
    AmbiguityType,
    AssumptionType,
    ClaimType,
    EpistemicStatus,
    GroundingStatus,
    ImpactLevel,
    KnowledgeGapType,
    KnowledgeLifecycleStatus,
    KnowledgeObjectType,
    KnowledgeOrigin,
    KnowledgeReviewStatus,
    PriorityLevel,
    RequirementCategory,
    RequirementExplicitness,
    ResolutionStatus,
    RiskLevel,
    SatisfactionStatus,
    ValidationStatus,
    VerificationStatus,
)
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class KnowledgeObjectRecord(Base):
    """One immutable semantic version of a proposition-oriented object."""

    __tablename__ = "knowledge_objects"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(check_values("object_type", KnowledgeObjectType), name="object_type_valid"),
        CheckConstraint("btrim(statement) <> ''", name="statement_not_blank"),
        CheckConstraint(
            f"statement_normalization_version = '{STATEMENT_NORMALIZATION_VERSION}'",
            name="statement_normalization_version_valid",
        ),
        CheckConstraint(
            "normalized_statement_hash ~ '^[0-9a-f]{64}$'",
            name="normalized_statement_hash_sha256",
        ),
        CheckConstraint(check_values("origin", KnowledgeOrigin), name="origin_valid"),
        CheckConstraint(
            check_values("epistemic_status", EpistemicStatus), name="epistemic_status_valid"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint(
            check_values("grounding_status_cache", GroundingStatus),
            name="grounding_status_cache_valid",
        ),
        CheckConstraint(
            check_values("review_status", KnowledgeReviewStatus), name="review_status_valid"
        ),
        CheckConstraint(
            check_values("lifecycle_status", KnowledgeLifecycleStatus),
            name="lifecycle_status_valid",
        ),
        CheckConstraint(
            "(review_status IN ('candidate', 'needs_revision', 'rejected') "
            "AND lifecycle_status = 'current') OR "
            "(review_status = 'accepted' "
            "AND lifecycle_status IN ('current', 'challenged', 'superseded'))",
            name="review_lifecycle_combination_valid",
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        UniqueConstraint("lineage_id", "version", name="uq_knowledge_objects_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_knowledge_objects_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_knowledge_objects_id_project"),
        UniqueConstraint("supersedes_id", name="uq_knowledge_objects_supersedes_once"),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.lineage_id",
            ],
            name="fk_knowledge_objects_derived_from_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.lineage_id",
            ],
            name="fk_knowledge_objects_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["grounding_assessment_id", "id"],
            [
                f"{CANONICAL_SCHEMA}.grounding_assessments.id",
                f"{CANONICAL_SCHEMA}.grounding_assessments.knowledge_object_id",
            ],
            name="fk_knowledge_objects_grounding_assessment",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index(
            "uq_knowledge_objects_unsuperseded_accepted_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text(
                "review_status = 'accepted' AND lifecycle_status IN ('current', 'challenged')"
            ),
        ),
        Index(
            "ix_knowledge_objects_project_accepted_current",
            "project_id",
            "object_type",
            postgresql_where=text("review_status = 'accepted' AND lifecycle_status = 'current'"),
        ),
        Index(
            "ix_knowledge_objects_project_normalized_statement",
            "project_id",
            "object_type",
            "normalized_statement_hash",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_knowledge_objects_project_id_projects",
        ),
        nullable=False,
    )
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    statement_normalization_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=STATEMENT_NORMALIZATION_VERSION
    )
    normalized_statement_hash: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    epistemic_status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    grounding_status_cache: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=GroundingStatus.UNVALIDATED.value
    )
    grounding_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeReviewStatus.CANDIDATE.value
    )
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeLifecycleStatus.CURRENT.value
    )
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_knowledge_objects_generation_record_id_generation_records",
        ),
    )
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClaimRecord(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(check_values("claim_type", ClaimType), name="claim_type_valid"),
        CheckConstraint(
            check_values("verification_status", VerificationStatus),
            name="verification_status_valid",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_claims_knowledge_object_id_knowledge_objects",
        ),
        primary_key=True,
    )
    claim_type: Mapped[str] = mapped_column(Text, nullable=False)
    verification_status: Mapped[str] = mapped_column(Text, nullable=False)


class AssumptionRecord(Base):
    __tablename__ = "assumptions"
    __table_args__ = (
        CheckConstraint(
            check_values("assumption_type", AssumptionType), name="assumption_type_valid"
        ),
        CheckConstraint(check_values("risk_level", RiskLevel), name="risk_level_valid"),
        CheckConstraint(
            check_values("validation_status", ValidationStatus), name="validation_status_valid"
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_assumptions_knowledge_object_id_knowledge_objects",
        ),
        primary_key=True,
    )
    assumption_type: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    validation_status: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeGapRecord(Base):
    __tablename__ = "knowledge_gaps"
    __table_args__ = (
        CheckConstraint(check_values("gap_type", KnowledgeGapType), name="gap_type_valid"),
        CheckConstraint(check_values("priority", PriorityLevel), name="priority_valid"),
        {"schema": CANONICAL_SCHEMA},
    )

    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_knowledge_gaps_knowledge_object_id_knowledge_objects",
        ),
        primary_key=True,
    )
    gap_type: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_notes: Mapped[str | None] = mapped_column(Text)


class ImplementationRequirementRecord(Base):
    __tablename__ = "implementation_requirements"
    __table_args__ = (
        CheckConstraint(check_values("category", RequirementCategory), name="category_valid"),
        CheckConstraint(
            check_values("explicitness", RequirementExplicitness), name="explicitness_valid"
        ),
        CheckConstraint(check_values("priority", PriorityLevel), name="priority_valid"),
        CheckConstraint(
            check_values("satisfaction_status", SatisfactionStatus),
            name="satisfaction_status_valid",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_impl_requirements_knowledge_object_id_knowledge_objects",
        ),
        primary_key=True,
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    explicitness: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    satisfaction_status: Mapped[str] = mapped_column(Text, nullable=False)


class AmbiguityRecord(Base):
    __tablename__ = "ambiguities"
    __table_args__ = (
        CheckConstraint(check_values("ambiguity_type", AmbiguityType), name="ambiguity_type_valid"),
        CheckConstraint(check_values("impact_level", ImpactLevel), name="impact_level_valid"),
        CheckConstraint(
            check_values("resolution_status", ResolutionStatus), name="resolution_status_valid"
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_ambiguities_knowledge_object_id_knowledge_objects",
        ),
        primary_key=True,
    )
    ambiguity_type: Mapped[str] = mapped_column(Text, nullable=False)
    impact_level: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_status: Mapped[str] = mapped_column(Text, nullable=False)


class AmbiguityInterpretationRecord(Base):
    __tablename__ = "ambiguity_interpretations"
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(description) <> ''", name="description_not_blank"),
        CheckConstraint(
            "btrim(implementation_consequences) <> ''",
            name="consequences_present",
        ),
        CheckConstraint("rank >= 1", name="rank_positive"),
        UniqueConstraint(
            "ambiguity_id", "rank", name="uq_ambiguity_interpretations_ambiguity_rank"
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ambiguity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.ambiguities.knowledge_object_id",
            ondelete="RESTRICT",
            name="fk_ambiguity_interpretations_ambiguity_id_ambiguities",
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    implementation_consequences: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class KnowledgeObjectPresentationRecord(Base):
    """Immutable presentation-only revision for one exact semantic version."""

    __tablename__ = "knowledge_object_presentations"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("btrim(display_text) <> ''", name="display_text_not_blank"),
        CheckConstraint(
            "display_label IS NULL OR btrim(display_label) <> ''",
            name="display_label_not_blank",
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        CheckConstraint(
            "(version = 1 AND supersedes_id IS NULL) OR "
            "(version > 1 AND supersedes_id IS NOT NULL)",
            name="version_chain_valid",
        ),
        UniqueConstraint(
            "knowledge_object_id",
            "version",
            name="uq_knowledge_object_presentations_object_version",
        ),
        UniqueConstraint(
            "id",
            "knowledge_object_id",
            name="uq_knowledge_object_presentations_id_object",
        ),
        UniqueConstraint("supersedes_id", name="uq_knowledge_object_presentations_supersedes_once"),
        ForeignKeyConstraint(
            ["supersedes_id", "knowledge_object_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_object_presentations.id",
                f"{CANONICAL_SCHEMA}.knowledge_object_presentations.knowledge_object_id",
            ],
            name="fk_ko_presentations_supersedes_object",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_knowledge_object_presentations_object_created",
            "knowledge_object_id",
            "created_at",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    knowledge_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_ko_presentations_knowledge_object",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    display_label: Mapped[str | None] = mapped_column(Text)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
