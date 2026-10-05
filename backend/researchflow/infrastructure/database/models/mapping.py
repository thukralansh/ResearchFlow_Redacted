"""Immutable paper-to-code mapping records with bounded Stage B provenance."""

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
from researchflow.domain.method_catalog import FreshnessStatus
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values


class PaperCodeMappingRecord(Base):
    """One reviewable mapping version from an exact paper anchor to inspected code."""

    __tablename__ = "paper_code_mappings"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            check_values("paper_anchor_type", PaperAnchorType),
            name="paper_anchor_type_valid",
        ),
        CheckConstraint(
            "num_nonnulls(source_chunk_id, knowledge_object_id, method_model_id, "
            "method_component_id, method_equation_id, method_symbol_id, "
            "method_algorithm_step_id, method_data_flow_id, "
            "method_validity_condition_id) = 1 AND ("
            "(paper_anchor_type = 'source_chunk' AND source_chunk_id IS NOT NULL) OR "
            "(paper_anchor_type = 'knowledge_object' AND knowledge_object_id IS NOT NULL) OR "
            "(paper_anchor_type = 'method_model' AND method_model_id IS NOT NULL) OR "
            "(paper_anchor_type = 'method_component' AND method_component_id IS NOT NULL) OR "
            "(paper_anchor_type = 'equation' AND method_equation_id IS NOT NULL) OR "
            "(paper_anchor_type = 'method_symbol' AND method_symbol_id IS NOT NULL) OR "
            "(paper_anchor_type = 'algorithm_step' AND method_algorithm_step_id IS NOT NULL) OR "
            "(paper_anchor_type = 'data_flow' AND method_data_flow_id IS NOT NULL) OR "
            "(paper_anchor_type = 'validity_condition' "
            "AND method_validity_condition_id IS NOT NULL))",
            name="exactly_one_paper_anchor",
        ),
        CheckConstraint(
            check_values("mapping_type", PaperCodeMappingType), name="mapping_type_valid"
        ),
        CheckConstraint(
            "(mapping_type = 'absent_from_repo' AND repository_component_id IS NULL) OR "
            "(mapping_type <> 'absent_from_repo' AND repository_component_id IS NOT NULL)",
            name="component_matches_mapping_type",
        ),
        CheckConstraint("btrim(explanation) <> ''", name="explanation_not_blank"),
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
            check_values("freshness_status", FreshnessStatus), name="freshness_status_valid"
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
        UniqueConstraint("lineage_id", "version", name="uq_paper_code_mappings_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_paper_code_mappings_id_lineage"),
        UniqueConstraint("supersedes_id", name="uq_paper_code_mappings_supersedes_once"),
        ForeignKeyConstraint(
            ["repository_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.repositories.id",
                f"{CANONICAL_SCHEMA}.repositories.project_id",
            ],
            name="fk_paper_code_mappings_repository_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["repository_snapshot_id", "repository_id"],
            [
                f"{CANONICAL_SCHEMA}.repository_snapshots.id",
                f"{CANONICAL_SCHEMA}.repository_snapshots.repository_id",
            ],
            name="fk_paper_code_mappings_snapshot_repository",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["repository_inspection_run_id", "repository_snapshot_id"],
            [
                f"{CANONICAL_SCHEMA}.repository_inspection_runs.id",
                f"{CANONICAL_SCHEMA}.repository_inspection_runs.repository_snapshot_id",
            ],
            name="fk_paper_code_mappings_inspection_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "repository_component_id",
                "repository_inspection_run_id",
                "repository_snapshot_id",
            ],
            [
                f"{CANONICAL_SCHEMA}.repository_components.id",
                f"{CANONICAL_SCHEMA}.repository_components.repository_inspection_run_id",
                f"{CANONICAL_SCHEMA}.repository_components.repository_snapshot_id",
            ],
            name="fk_paper_code_mappings_component_inspection",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["knowledge_object_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.knowledge_objects.id",
                f"{CANONICAL_SCHEMA}.knowledge_objects.project_id",
            ],
            name="fk_paper_code_mappings_knowledge_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.project_id",
            ],
            name="fk_paper_code_mappings_method_model_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.paper_code_mappings.id",
                f"{CANONICAL_SCHEMA}.paper_code_mappings.lineage_id",
            ],
            name="fk_paper_code_mappings_derived_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.paper_code_mappings.id",
                f"{CANONICAL_SCHEMA}.paper_code_mappings.lineage_id",
            ],
            name="fk_paper_code_mappings_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_paper_code_mappings_unsuperseded_accepted_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text(
                "review_status = 'accepted' AND lifecycle_status IN ('current', 'challenged')"
            ),
        ),
        Index(
            "ix_paper_code_mappings_project_review_freshness",
            "project_id",
            "review_status",
            "freshness_status",
        ),
        Index(
            "ix_paper_code_mappings_inspection_component",
            "repository_inspection_run_id",
            "repository_component_id",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_project",
        ),
        nullable=False,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    repository_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    repository_inspection_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_anchor_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.source_chunks.id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_source_chunk",
        ),
    )
    knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    method_model_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    method_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_components.method_element_id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_method_component",
        ),
    )
    method_equation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_equations.method_element_id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_method_equation",
        ),
    )
    method_symbol_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_symbols.method_element_id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_method_symbol",
        ),
    )
    method_algorithm_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_algorithm_steps.method_element_id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_algorithm_step",
        ),
    )
    method_data_flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_data_flows.method_element_id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_data_flow",
        ),
    )
    method_validity_condition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_validity_conditions.method_element_id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_validity_condition",
        ),
    )
    repository_component_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mapping_type: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeReviewStatus.CANDIDATE.value
    )
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeLifecycleStatus.CURRENT.value
    )
    freshness_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=FreshnessStatus.CURRENT.value
    )
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_paper_code_mappings_generation_record",
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
