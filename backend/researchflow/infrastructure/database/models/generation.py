"""Provider-neutral generation history records."""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.knowledge_catalog import GenerationStatus
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import check_values

MODEL_CONFIGURATION_MAX_BYTES: Final = 32 * 1024
RAW_STRUCTURED_OUTPUT_MAX_BYTES: Final = 4 * 1024 * 1024
VALIDATION_SUMMARY_MAX_BYTES: Final = 256 * 1024


class GenerationRecord(Base):
    """Immutable history of one completed structured-generation attempt."""

    __tablename__ = "generation_records"
    __table_args__ = (
        CheckConstraint("btrim(task_name) <> ''", name="task_name_not_blank"),
        CheckConstraint("btrim(prompt_version) <> ''", name="prompt_version_not_blank"),
        CheckConstraint("btrim(schema_version) <> ''", name="schema_version_not_blank"),
        CheckConstraint("btrim(provider) <> ''", name="provider_not_blank"),
        CheckConstraint("btrim(model_version) <> ''", name="model_version_not_blank"),
        CheckConstraint(
            "reasoning_version IS NULL OR btrim(reasoning_version) <> ''",
            name="reasoning_version_not_blank",
        ),
        CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'", name="input_fingerprint_sha256"),
        CheckConstraint(check_values("status", GenerationStatus), name="status_valid"),
        CheckConstraint(
            "jsonb_typeof(model_configuration) = 'object'",
            name="model_configuration_object",
        ),
        CheckConstraint(
            f"octet_length(model_configuration::text) <= {MODEL_CONFIGURATION_MAX_BYTES}",
            name="model_configuration_bounded",
        ),
        CheckConstraint(
            "raw_structured_output IS NULL OR "
            f"octet_length(raw_structured_output::text) <= {RAW_STRUCTURED_OUTPUT_MAX_BYTES}",
            name="raw_output_bounded",
        ),
        CheckConstraint(
            "validation_summary IS NULL OR jsonb_typeof(validation_summary) IN ('object', 'array')",
            name="validation_summary_structured",
        ),
        CheckConstraint(
            "validation_summary IS NULL OR "
            f"octet_length(validation_summary::text) <= {VALIDATION_SUMMARY_MAX_BYTES}",
            name="validation_summary_bounded",
        ),
        CheckConstraint(
            "error_code IS NULL OR btrim(error_code) <> ''", name="error_code_not_blank"
        ),
        CheckConstraint(
            "(status = 'succeeded' AND raw_structured_output IS NOT NULL "
            "AND error_code IS NULL) OR (status = 'failed' AND error_code IS NOT NULL)",
            name="status_payload_consistent",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_nonnegative"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_nonnegative"
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint("completed_at >= started_at", name="completion_not_before_start"),
        CheckConstraint(
            "(workflow_run_id IS NOT NULL)::int + (copilot_turn_id IS NOT NULL)::int = 1",
            name="exactly_one_execution_owner",
        ),
        UniqueConstraint("id", "project_id", name="uq_generation_records_id_project"),
        UniqueConstraint(
            "id",
            "copilot_turn_id",
            "project_id",
            name="uq_generation_records_id_copilot_turn_project",
        ),
        ForeignKeyConstraint(
            ["workflow_run_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.workflow_runs.id",
                f"{CANONICAL_SCHEMA}.workflow_runs.project_id",
            ],
            name="fk_generation_records_workflow_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["copilot_turn_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_turns.id",
                f"{CANONICAL_SCHEMA}.copilot_turns.project_id",
            ],
            name="fk_generation_records_copilot_turn_project",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_generation_records_project_task_input",
            "project_id",
            "task_name",
            "input_fingerprint",
        ),
        Index("ix_generation_records_workflow_created", "workflow_run_id", "created_at"),
        Index("ix_generation_records_copilot_turn_created", "copilot_turn_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_generation_records_project_id_projects",
        ),
        nullable=False,
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    copilot_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    task_name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning_version: Mapped[str | None] = mapped_column(Text)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    model_configuration: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    raw_structured_output: Mapped[object | None] = mapped_column(JSONB(none_as_null=True))
    validation_summary: Mapped[dict[str, object] | list[object] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
