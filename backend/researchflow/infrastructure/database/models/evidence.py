"""Versioned evidence links and append-only grounding assessments."""

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
from researchflow.domain.method_catalog import (
    EvidenceRelation,
    GroundingAssessmentStatus,
)
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class EvidenceLinkRecord(Base):
    """One immutable version of a reviewed relation from exact evidence to one target."""

    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "num_nonnulls(knowledge_object_id, method_element_id) = 1",
            name="exactly_one_target",
        ),
        CheckConstraint(
            "num_nonnulls(source_chunk_id, repository_component_id) = 1",
            name="exactly_one_source",
        ),
        CheckConstraint(
            check_values("evidence_relation", EvidenceRelation), name="evidence_relation_valid"
        ),
        CheckConstraint("btrim(evidence_excerpt) <> ''", name="evidence_excerpt_not_blank"),
        CheckConstraint(
            "num_nonnulls(source_anchor_start_offset, source_anchor_end_offset, "
            "source_anchor_hash, source_anchor_policy_version) IN (0, 4)",
            name="source_anchor_metadata_complete",
        ),
        CheckConstraint(
            "source_chunk_id IS NOT NULL OR source_anchor_start_offset IS NULL",
            name="source_anchor_requires_chunk",
        ),
        CheckConstraint(
            "source_anchor_start_offset IS NULL OR "
            "(source_anchor_start_offset >= 0 "
            "AND source_anchor_end_offset > source_anchor_start_offset)",
            name="source_anchor_offset_range_valid",
        ),
        CheckConstraint(
            "source_anchor_hash IS NULL OR source_anchor_hash ~ '^[0-9a-f]{64}$'",
            name="source_anchor_hash_sha256",
        ),
        CheckConstraint(
            "source_anchor_policy_version IS NULL OR btrim(source_anchor_policy_version) <> ''",
            name="source_anchor_policy_version_not_blank",
        ),
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
        UniqueConstraint("lineage_id", "version", name="uq_evidence_links_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_evidence_links_id_lineage"),
        UniqueConstraint("supersedes_id", name="uq_evidence_links_supersedes_once"),
        ForeignKeyConstraint(
            ["knowledge_object_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.project_id",
            ],
            name="fk_evidence_links_knowledge_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_element_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_elements.id",
                f"{CANONICAL_SCHEMA}.method_elements.project_id",
            ],
            name="fk_evidence_links_method_element_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.evidence_links.id",
                f"{CANONICAL_SCHEMA}.evidence_links.lineage_id",
            ],
            name="fk_evidence_links_derived_from_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.evidence_links.id",
                f"{CANONICAL_SCHEMA}.evidence_links.lineage_id",
            ],
            name="fk_evidence_links_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_evidence_links_unsuperseded_accepted_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text(
                "review_status = 'accepted' AND lifecycle_status IN ('current', 'challenged')"
            ),
        ),
        Index(
            "ix_evidence_links_knowledge_target",
            "knowledge_object_id",
            "review_status",
            postgresql_where=text("knowledge_object_id IS NOT NULL"),
        ),
        Index(
            "ix_evidence_links_method_target",
            "method_element_id",
            "review_status",
            postgresql_where=text("method_element_id IS NOT NULL"),
        ),
        Index("ix_evidence_links_source_chunk", "source_chunk_id"),
        Index("ix_evidence_links_repository_component", "repository_component_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_evidence_links_project_id_projects",
        ),
        nullable=False,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    method_element_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.source_chunks.id",
            ondelete="RESTRICT",
            name="fk_evidence_links_source_chunk_id_source_chunks",
        ),
    )
    repository_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_components.id",
            ondelete="RESTRICT",
            name="fk_evidence_links_repository_component_id_components",
        ),
    )
    evidence_relation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_anchor_start_offset: Mapped[int | None] = mapped_column(Integer)
    source_anchor_end_offset: Mapped[int | None] = mapped_column(Integer)
    source_anchor_hash: Mapped[str | None] = mapped_column(Text)
    source_anchor_policy_version: Mapped[str | None] = mapped_column(Text)
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
            name="fk_evidence_links_generation_record_id_generation_records",
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


class GroundingAssessmentRecord(Base):
    """Append-only evidence-derived assessment for exactly one scientific target."""

    __tablename__ = "grounding_assessments"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(knowledge_object_id, method_element_id) = 1",
            name="exactly_one_target",
        ),
        CheckConstraint(
            "evidence_set_fingerprint ~ '^[0-9a-f]{64}$'",
            name="evidence_set_fingerprint_sha256",
        ),
        CheckConstraint(check_values("status", GroundingAssessmentStatus), name="status_valid"),
        CheckConstraint("supporting_count >= 0", name="supporting_count_nonnegative"),
        CheckConstraint("contradicting_count >= 0", name="contradicting_count_nonnegative"),
        CheckConstraint("qualifying_count >= 0", name="qualifying_count_nonnegative"),
        CheckConstraint("btrim(rationale) <> ''", name="rationale_not_blank"),
        CheckConstraint("btrim(validator_name) <> ''", name="validator_name_not_blank"),
        CheckConstraint("btrim(validator_version) <> ''", name="validator_version_not_blank"),
        UniqueConstraint(
            "id",
            "knowledge_object_id",
            name="uq_grounding_assessments_id_knowledge",
        ),
        UniqueConstraint(
            "id", "method_element_id", name="uq_grounding_assessments_id_method_element"
        ),
        ForeignKeyConstraint(
            ["knowledge_object_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.project_id",
            ],
            name="fk_grounding_assessments_knowledge_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_element_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_elements.id",
                f"{CANONICAL_SCHEMA}.method_elements.project_id",
            ],
            name="fk_grounding_assessments_method_project",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_grounding_assessments_knowledge_created",
            "knowledge_object_id",
            "created_at",
            postgresql_where=text("knowledge_object_id IS NOT NULL"),
        ),
        Index(
            "ix_grounding_assessments_method_created",
            "method_element_id",
            "created_at",
            postgresql_where=text("method_element_id IS NOT NULL"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_grounding_assessments_project_id_projects",
        ),
        nullable=False,
    )
    knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    method_element_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_set_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_count: Mapped[int] = mapped_column(Integer, nullable=False)
    contradicting_count: Mapped[int] = mapped_column(Integer, nullable=False)
    qualifying_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    validator_name: Mapped[str] = mapped_column(Text, nullable=False)
    validator_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
