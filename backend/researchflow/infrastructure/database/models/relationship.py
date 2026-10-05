"""Versioned typed semantic links without generic endpoint identifiers."""

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
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import ObjectLinkRelationship
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class ObjectLinkRecord(Base):
    """One immutable version of a reviewed cross-object semantic relationship."""

    __tablename__ = "object_links"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "num_nonnulls(source_knowledge_object_id, source_method_model_id, "
            "source_method_element_id, source_implementation_step_id, "
            "source_verification_check_id) = 1",
            name="exactly_one_source",
        ),
        CheckConstraint(
            "num_nonnulls(target_knowledge_object_id, target_method_model_id, "
            "target_method_element_id) = 1",
            name="exactly_one_target",
        ),
        CheckConstraint(
            "(source_knowledge_object_id IS NULL OR target_knowledge_object_id IS NULL "
            "OR source_knowledge_object_id <> target_knowledge_object_id) AND "
            "(source_method_model_id IS NULL OR target_method_model_id IS NULL "
            "OR source_method_model_id <> target_method_model_id) AND "
            "(source_method_element_id IS NULL OR target_method_element_id IS NULL "
            "OR source_method_element_id <> target_method_element_id)",
            name="endpoints_not_self",
        ),
        CheckConstraint(
            check_values("relationship_type", ObjectLinkRelationship),
            name="relationship_type_valid",
        ),
        CheckConstraint("btrim(rationale) <> ''", name="rationale_not_blank"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
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
        UniqueConstraint("lineage_id", "version", name="uq_object_links_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_object_links_id_lineage"),
        UniqueConstraint("supersedes_id", name="uq_object_links_supersedes_once"),
        ForeignKeyConstraint(
            ["source_knowledge_object_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.project_id",
            ],
            name="fk_object_links_source_knowledge_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_method_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.project_id",
            ],
            name="fk_object_links_source_method_model_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_method_element_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_elements.id",
                f"{CANONICAL_SCHEMA}.method_elements.project_id",
            ],
            name="fk_object_links_source_method_element_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_implementation_step_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_steps.id",
                f"{CANONICAL_SCHEMA}.implementation_steps.project_id",
            ],
            name="fk_object_links_source_step_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_verification_check_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.verification_checks.id",
                f"{CANONICAL_SCHEMA}.verification_checks.project_id",
            ],
            name="fk_object_links_source_check_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_knowledge_object_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.project_id",
            ],
            name="fk_object_links_target_knowledge_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_method_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.project_id",
            ],
            name="fk_object_links_target_method_model_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_method_element_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_elements.id",
                f"{CANONICAL_SCHEMA}.method_elements.project_id",
            ],
            name="fk_object_links_target_method_element_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.object_links.id",
                f"{CANONICAL_SCHEMA}.object_links.lineage_id",
            ],
            name="fk_object_links_derived_from_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.object_links.id",
                f"{CANONICAL_SCHEMA}.object_links.lineage_id",
            ],
            name="fk_object_links_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_object_links_unsuperseded_accepted_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text(
                "review_status = 'accepted' AND lifecycle_status IN ('current', 'challenged')"
            ),
        ),
        Index(
            "ix_object_links_project_relationship_review",
            "project_id",
            "relationship_type",
            "review_status",
        ),
        Index("ix_object_links_source_knowledge", "source_knowledge_object_id"),
        Index("ix_object_links_source_method_model", "source_method_model_id"),
        Index("ix_object_links_source_method_element", "source_method_element_id"),
        Index("ix_object_links_source_step", "source_implementation_step_id"),
        Index("ix_object_links_source_check", "source_verification_check_id"),
        Index("ix_object_links_target_knowledge", "target_knowledge_object_id"),
        Index("ix_object_links_target_method_model", "target_method_model_id"),
        Index("ix_object_links_target_method_element", "target_method_element_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_object_links_project_id_projects",
        ),
        nullable=False,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_method_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_method_element_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_implementation_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_verification_check_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_method_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_method_element_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeReviewStatus.CANDIDATE.value
    )
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeLifecycleStatus.CURRENT.value
    )
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_object_links_generation_record_id_generation_records",
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
