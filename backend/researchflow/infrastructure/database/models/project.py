"""Project persistence records."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base


class ProjectRecord(Base):
    """Durable project identity anchored to one exact Primary Research Question."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(target_outcome) <> ''", name="target_outcome_not_blank"),
        CheckConstraint(
            "btrim(reproduction_depth) <> ''",
            name="reproduction_depth_not_blank",
        ),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        ForeignKeyConstraint(
            ["primary_research_question_id", "id"],
            [
                f"{CANONICAL_SCHEMA}.research_questions.id",
                f"{CANONICAL_SCHEMA}.research_questions.project_id",
            ],
            name="fk_projects_primary_question_exact",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    primary_research_question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reproduction_depth: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
