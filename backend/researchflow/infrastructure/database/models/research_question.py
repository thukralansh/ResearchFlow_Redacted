"""Research Question and question-relative source records."""

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

from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import (
    ActorType,
    InquiryStatus,
    LifecycleStatus,
    Priority,
    QuestionSourceRelationshipType,
    QuestionType,
    ReviewStatus,
    check_values,
)


class ResearchQuestionRecord(Base):
    """One immutable semantic version of a Research Question."""

    __tablename__ = "research_questions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("btrim(question_text) <> ''", name="question_text_not_blank"),
        CheckConstraint(check_values("question_type", QuestionType), name="question_type_valid"),
        CheckConstraint(check_values("inquiry_status", InquiryStatus), name="inquiry_status_valid"),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        CheckConstraint(check_values("priority", Priority), name="priority_valid"),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        UniqueConstraint("lineage_id", "version", name="uq_research_questions_lineage_version"),
        UniqueConstraint("id", "project_id", name="uq_research_questions_id_project"),
        ForeignKeyConstraint(
            ["project_id"],
            [f"{CANONICAL_SCHEMA}.projects.id"],
            name="fk_research_questions_project_id_projects",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_research_questions_current_primary_project",
            "project_id",
            unique=True,
            postgresql_where=text("question_type = 'primary' AND lifecycle_status = 'current'"),
        ),
        Index(
            "uq_research_questions_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_research_questions_parent_question_id_research_questions",
        ),
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_text: Mapped[str | None] = mapped_column(Text)
    inquiry_status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_research_questions_derived_from_id_research_questions",
        ),
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_research_questions_supersedes_id_research_questions",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchQuestionSourceRecord(Base):
    """Versioned semantic role of one Source relative to one Research Question."""

    __tablename__ = "research_question_sources"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            check_values("relationship_type", QuestionSourceRelationshipType),
            name="relationship_type_valid",
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
            "lineage_id", "version", name="uq_research_question_sources_lineage_version"
        ),
        Index(
            "uq_research_question_sources_current_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        Index(
            "uq_research_question_sources_current_primary_paper",
            "project_id",
            unique=True,
            postgresql_where=text(
                "relationship_type = 'primary_paper' "
                "AND review_status = 'accepted' AND lifecycle_status = 'current'"
            ),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_research_question_sources_project_id_projects",
        ),
        nullable=False,
    )
    research_question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_rq_sources_question",
        ),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.sources.id",
            ondelete="RESTRICT",
            name="fk_research_question_sources_source_id_sources",
        ),
        nullable=False,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_scope: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_question_sources.id",
            ondelete="RESTRICT",
            name="fk_rq_sources_derived_from",
        ),
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_question_sources.id",
            ondelete="RESTRICT",
            name="fk_rq_sources_supersedes",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
