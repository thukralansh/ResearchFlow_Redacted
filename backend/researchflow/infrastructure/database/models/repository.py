"""Repository identity, two-stage analysis, and paper relationship records."""

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import (
    ActorType,
    CoverageStatus,
    LifecycleStatus,
    ProcessingStatus,
    RepositoryAnalysisStage,
    RepositoryAuthorityLevel,
    RepositoryRelationshipType,
    RepositorySnapshotStatus,
    ReviewStatus,
    check_values,
)


class RepositoryRecord(Base):
    """One public GitHub repository attached to a Project."""

    __tablename__ = "repositories"
    __table_args__ = (
        CheckConstraint("host = 'github.com'", name="host_github_only"),
        CheckConstraint("is_private = false", name="public_only"),
        CheckConstraint("btrim(owner) <> ''", name="owner_not_blank"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("btrim(normalized_url) <> ''", name="normalized_url_not_blank"),
        UniqueConstraint(
            "project_id", "host", "owner", "name", name="uq_repositories_project_identity"
        ),
        UniqueConstraint("id", "project_id", name="uq_repositories_id_project"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_repositories_project_id_projects",
        ),
        nullable=False,
    )
    host: Mapped[str] = mapped_column(Text, nullable=False, server_default="github.com")
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepositorySnapshotRecord(Base):
    """Immutable repository state pinned to one exact commit SHA."""

    __tablename__ = "repository_snapshots"
    __table_args__ = (
        CheckConstraint("btrim(requested_ref) <> ''", name="requested_ref_not_blank"),
        CheckConstraint("commit_sha ~ '^[0-9a-f]{40}$'", name="commit_sha_git_sha1"),
        CheckConstraint(check_values("status", RepositorySnapshotStatus), name="status_valid"),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        UniqueConstraint(
            "repository_id", "commit_sha", name="uq_repository_snapshots_repository_commit"
        ),
        UniqueConstraint("id", "repository_id", name="uq_repository_snapshots_id_repository"),
        Index(
            "uq_repository_snapshots_current_repository",
            "repository_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repositories.id",
            ondelete="RESTRICT",
            name="fk_repository_snapshots_repository_id_repositories",
        ),
        nullable=False,
    )
    requested_ref: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_repository_snapshots_supersedes_id_repository_snapshots",
        ),
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperRepositoryRelationshipRecord(Base):
    """Versioned qualitative role and authority of a repository for a paper."""

    __tablename__ = "paper_repository_relationships"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            check_values("relationship_type", RepositoryRelationshipType),
            name="relationship_type_valid",
        ),
        CheckConstraint(
            check_values("authority_level", RepositoryAuthorityLevel),
            name="authority_level_valid",
        ),
        CheckConstraint("btrim(rationale) <> ''", name="rationale_not_blank"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint(check_values("review_status", ReviewStatus), name="review_status_valid"),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        UniqueConstraint(
            "lineage_id", "version", name="uq_paper_repository_relationships_lineage_version"
        ),
        UniqueConstraint("id", "project_id", name="uq_paper_repository_relationships_id_project"),
        UniqueConstraint(
            "repository_snapshot_id",
            "id",
            name="uq_paper_repository_relationships_snapshot_id",
        ),
        ForeignKeyConstraint(
            ["repository_snapshot_id", "repository_id"],
            [
                f"{CANONICAL_SCHEMA}.repository_snapshots.id",
                f"{CANONICAL_SCHEMA}.repository_snapshots.repository_id",
            ],
            name="fk_paper_repository_relationships_snapshot_exact",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_paper_repository_relationships_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        Index(
            "uq_paper_repository_relationships_current_project",
            "project_id",
            unique=True,
            postgresql_where=text("review_status = 'accepted' AND lifecycle_status = 'current'"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_paper_repository_relationships_project_id_projects",
        ),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.sources.id",
            ondelete="RESTRICT",
            name="fk_paper_repository_relationships_source_id_sources",
        ),
        nullable=False,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repositories.id",
            ondelete="RESTRICT",
            name="fk_paper_repository_relationships_repository_id_repositories",
        ),
        nullable=False,
    )
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    authority_level: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
            ondelete="RESTRICT",
            name="fk_paper_repo_rel_derived_from",
        ),
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
            ondelete="RESTRICT",
            name="fk_paper_repo_rel_supersedes",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepositoryDiscoveryRunRecord(Base):
    """Bounded structural discovery over one immutable snapshot."""

    __tablename__ = "repository_discovery_runs"
    __table_args__ = (
        CheckConstraint("btrim(discovery_version) <> ''", name="discovery_version_not_blank"),
        CheckConstraint(
            "tree_hash IS NULL OR tree_hash ~ '^[0-9a-f]{64}$'",
            name="tree_hash_sha256",
        ),
        CheckConstraint(
            check_values("coverage_status", CoverageStatus), name="coverage_status_valid"
        ),
        CheckConstraint(check_values("status", ProcessingStatus), name="status_valid"),
        UniqueConstraint(
            "id", "repository_snapshot_id", name="uq_repository_discovery_runs_id_snapshot"
        ),
        Index(
            "ix_repository_discovery_runs_snapshot_status",
            "repository_snapshot_id",
            "status",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    repository_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_repo_discovery_snapshot",
        ),
        nullable=False,
    )
    discovery_version: Mapped[str] = mapped_column(Text, nullable=False)
    tree_hash: Mapped[str | None] = mapped_column(Text)
    configured_limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    coverage_status: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepositoryTreeEntryRecord(Base):
    """One bounded tree entry discovered without deep source analysis."""

    __tablename__ = "repository_tree_entries"
    __table_args__ = (
        CheckConstraint("btrim(path) <> ''", name="path_not_blank"),
        CheckConstraint("btrim(entry_type) <> ''", name="entry_type_not_blank"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_bytes_nonnegative"),
        UniqueConstraint("discovery_run_id", "path", name="uq_repository_tree_entries_run_path"),
        Index("ix_repository_tree_entries_path", "path"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_discovery_runs.id",
            ondelete="RESTRICT",
            name="fk_repo_tree_entry_discovery",
        ),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(Text)
    coarse_role: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    excluded_reason: Mapped[str | None] = mapped_column(Text)


class RepositoryInspectionRunRecord(Base):
    """Knowledge-guided deep inspection over selected snapshot paths."""

    __tablename__ = "repository_inspection_runs"
    __table_args__ = (
        CheckConstraint(
            "guidance_fingerprint ~ '^[0-9a-f]{64}$'",
            name="guidance_fingerprint_sha256",
        ),
        CheckConstraint("btrim(ranking_version) <> ''", name="ranking_version_not_blank"),
        CheckConstraint("btrim(analysis_version) <> ''", name="analysis_version_not_blank"),
        CheckConstraint(
            "jsonb_typeof(configured_limits) = 'object'",
            name="configured_limits_object",
        ),
        CheckConstraint(
            "jsonb_typeof(selected_paths) = 'array'",
            name="selected_paths_array",
        ),
        CheckConstraint(
            "jsonb_typeof(inspected_paths) = 'array'",
            name="inspected_paths_array",
        ),
        CheckConstraint(
            check_values("coverage_status", CoverageStatus), name="coverage_status_valid"
        ),
        CheckConstraint(check_values("status", ProcessingStatus), name="status_valid"),
        UniqueConstraint(
            "id", "repository_snapshot_id", name="uq_repository_inspection_runs_id_snapshot"
        ),
        ForeignKeyConstraint(
            ["discovery_run_id", "repository_snapshot_id"],
            [
                f"{CANONICAL_SCHEMA}.repository_discovery_runs.id",
                f"{CANONICAL_SCHEMA}.repository_discovery_runs.repository_snapshot_id",
            ],
            name="fk_repository_inspection_runs_discovery_snapshot_exact",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_repository_inspection_runs_snapshot_status",
            "repository_snapshot_id",
            "status",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    repository_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_repo_inspection_snapshot",
        ),
        nullable=False,
    )
    discovery_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    guidance_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    ranking_version: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_version: Mapped[str] = mapped_column(Text, nullable=False)
    configured_limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    selected_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    inspected_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    coverage_status: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepositoryComponentRecord(Base):
    """Stage-specific repository evidence with exact run and commit provenance."""

    __tablename__ = "repository_components"
    __table_args__ = (
        CheckConstraint(
            check_values("analysis_stage", RepositoryAnalysisStage),
            name="analysis_stage_valid",
        ),
        CheckConstraint(
            "(analysis_stage = 'stage_a' AND repository_discovery_run_id IS NOT NULL "
            "AND repository_inspection_run_id IS NULL) OR "
            "(analysis_stage = 'stage_b' AND repository_discovery_run_id IS NULL "
            "AND repository_inspection_run_id IS NOT NULL)",
            name="analysis_stage_run_matches",
        ),
        CheckConstraint("btrim(path) <> ''", name="path_not_blank"),
        CheckConstraint("btrim(component_type) <> ''", name="component_type_not_blank"),
        CheckConstraint(
            "(line_start IS NULL AND line_end IS NULL) OR "
            "(line_start >= 1 AND line_end >= line_start)",
            name="line_range_valid",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
        CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 1)",
            name="relevance_score_unit_interval",
        ),
        CheckConstraint(
            "embedding_dimensions IS NULL OR embedding_dimensions > 0",
            name="embedding_dimensions_positive",
        ),
        CheckConstraint(
            "(embedding IS NULL AND embedding_provider IS NULL "
            "AND embedding_model IS NULL AND embedding_dimensions IS NULL "
            "AND embedded_at IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_provider IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_dimensions IS NOT NULL "
            "AND embedded_at IS NOT NULL)",
            name="embedding_metadata_complete",
        ),
        ForeignKeyConstraint(
            ["repository_discovery_run_id", "repository_snapshot_id"],
            [
                f"{CANONICAL_SCHEMA}.repository_discovery_runs.id",
                f"{CANONICAL_SCHEMA}.repository_discovery_runs.repository_snapshot_id",
            ],
            name="fk_repository_components_discovery_snapshot_exact",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "repository_inspection_run_id",
            "repository_snapshot_id",
            name="uq_repository_components_id_inspection_snapshot",
        ),
        ForeignKeyConstraint(
            ["repository_inspection_run_id", "repository_snapshot_id"],
            [
                f"{CANONICAL_SCHEMA}.repository_inspection_runs.id",
                f"{CANONICAL_SCHEMA}.repository_inspection_runs.repository_snapshot_id",
            ],
            name="fk_repository_components_inspection_snapshot_exact",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_repository_components_stage_a_identity",
            "repository_discovery_run_id",
            "path",
            text("coalesce(symbol, '')"),
            text("coalesce(line_start, 0)"),
            unique=True,
            postgresql_where=text("analysis_stage = 'stage_a'"),
        ),
        Index(
            "uq_repository_components_stage_b_identity",
            "repository_inspection_run_id",
            "path",
            text("coalesce(symbol, '')"),
            text("coalesce(line_start, 0)"),
            unique=True,
            postgresql_where=text("analysis_stage = 'stage_b'"),
        ),
        Index("ix_repository_components_snapshot_path", "repository_snapshot_id", "path"),
        Index(
            "ix_repository_components_full_text",
            text("to_tsvector('english', content_excerpt)"),
            postgresql_using="gin",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    repository_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    analysis_stage: Mapped[str] = mapped_column(Text, nullable=False)
    repository_discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    repository_inspection_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    path: Mapped[str] = mapped_column(Text, nullable=False)
    component_type: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(Text)
    symbol: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    content_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_provider: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
