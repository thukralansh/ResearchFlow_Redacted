"""Artifact-only source and page-aware processing records."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
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
    ProcessingStatus,
    SourceType,
    check_values,
)


class SourceRecord(Base):
    """Immutable uploaded artifact identity without a semantic paper role."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(check_values("source_type", SourceType), name="source_type_valid"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(original_filename) <> ''", name="original_filename_not_blank"),
        CheckConstraint("btrim(storage_bucket) <> ''", name="storage_bucket_not_blank"),
        CheckConstraint("btrim(storage_object_key) <> ''", name="storage_object_key_not_blank"),
        CheckConstraint("btrim(mime_type) <> ''", name="mime_type_not_blank"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
        CheckConstraint(
            check_values("processing_status", ProcessingStatus),
            name="processing_status_valid",
        ),
        UniqueConstraint(
            "storage_bucket", "storage_object_key", name="uq_sources_storage_location"
        ),
        Index("ix_sources_project_content_hash", "project_id", "content_hash"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_sources_project_id_projects",
        ),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    identifiers: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    storage_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=ProcessingStatus.PENDING.value
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceProcessingRunRecord(Base):
    """One immutable parser/chunker execution against exact source bytes."""

    __tablename__ = "source_processing_runs"
    __table_args__ = (
        CheckConstraint("source_hash ~ '^[0-9a-f]{64}$'", name="source_hash_sha256"),
        CheckConstraint("btrim(parser_name) <> ''", name="parser_name_not_blank"),
        CheckConstraint("btrim(parser_version) <> ''", name="parser_version_not_blank"),
        CheckConstraint("btrim(chunker_name) <> ''", name="chunker_name_not_blank"),
        CheckConstraint("btrim(chunker_version) <> ''", name="chunker_version_not_blank"),
        CheckConstraint(
            "btrim(normalization_version) <> ''", name="normalization_version_not_blank"
        ),
        CheckConstraint(check_values("status", ProcessingStatus), name="status_valid"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completion_not_before_start",
        ),
        UniqueConstraint(
            "source_id",
            "source_hash",
            "parser_name",
            "parser_version",
            "chunker_name",
            "chunker_version",
            "normalization_version",
            name="uq_source_processing_runs_input_versions",
        ),
        Index("ix_source_processing_runs_source_status", "source_id", "status"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.sources.id",
            ondelete="RESTRICT",
            name="fk_source_processing_runs_source_id_sources",
        ),
        nullable=False,
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    parser_name: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    chunker_name: Mapped[str] = mapped_column(Text, nullable=False)
    chunker_version: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)


class SourceChunkRecord(Base):
    """Page-local normalized text span from one processing run."""

    __tablename__ = "source_chunks"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="page_number_positive"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
        CheckConstraint("start_offset >= 0", name="start_offset_nonnegative"),
        CheckConstraint("end_offset > start_offset", name="offset_range_valid"),
        CheckConstraint("btrim(chunk_text) <> ''", name="chunk_text_not_blank"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
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
        UniqueConstraint(
            "processing_run_id", "chunk_index", name="uq_source_chunks_run_chunk_index"
        ),
        Index("ix_source_chunks_run_page", "processing_run_id", "page_number"),
        Index(
            "ix_source_chunks_full_text",
            text("to_tsvector('english', chunk_text)"),
            postgresql_using="gin",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processing_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.source_processing_runs.id",
            ondelete="RESTRICT",
            name="fk_source_chunks_processing_run_id_source_processing_runs",
        ),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(Text)
    subsection: Mapped[str | None] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_provider: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
