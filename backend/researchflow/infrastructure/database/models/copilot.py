"""Project-scoped, noncanonical Copilot conversation and action audit rows."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.copilot import (
    CopilotMessageRole,
    CopilotProposalStatus,
    CopilotThreadStatus,
    CopilotTurnStatus,
)
from researchflow.domain.copilot_context import CopilotManifestKind
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import check_values

_SHA256 = "^[0-9a-f]{64}$"


class CopilotManifestRecord(Base):
    """Immutable audit of one exact bounded context or retrieval selection."""

    __tablename__ = "copilot_manifests"
    __table_args__ = (
        CheckConstraint(check_values("kind", CopilotManifestKind), name="kind_valid"),
        CheckConstraint("jsonb_typeof(selector) = 'object'", name="selector_object"),
        CheckConstraint("pg_column_size(selector) <= 262144", name="selector_bounded"),
        CheckConstraint("jsonb_typeof(ordered_items) = 'array'", name="ordered_items_array"),
        CheckConstraint("jsonb_typeof(limitations) = 'array'", name="limitations_array"),
        CheckConstraint("pg_column_size(ordered_items) <= 2097152", name="items_bounded"),
        CheckConstraint("pg_column_size(limitations) <= 262144", name="limitations_bounded"),
        CheckConstraint(f"selector_fingerprint ~ '{_SHA256}'", name="selector_fingerprint_sha256"),
        CheckConstraint(f"state_fingerprint ~ '{_SHA256}'", name="state_fingerprint_sha256"),
        CheckConstraint(f"manifest_fingerprint ~ '{_SHA256}'", name="manifest_fingerprint_sha256"),
        CheckConstraint("btrim(policy_version) <> ''", name="policy_version_not_blank"),
        CheckConstraint("btrim(bounds_policy_version) <> ''", name="bounds_version_not_blank"),
        UniqueConstraint("id", "project_id", name="uq_copilot_manifests_id_project"),
        UniqueConstraint(
            "project_id", "manifest_fingerprint", name="uq_copilot_manifests_project_fingerprint"
        ),
        ForeignKeyConstraint(
            ["parent_manifest_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_manifests.id",
                f"{CANONICAL_SCHEMA}.copilot_manifests.project_id",
            ],
            name="fk_copilot_manifests_parent_project",
            ondelete="RESTRICT",
        ),
        Index("ix_copilot_manifests_project_created", "project_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_copilot_manifests_project",
        ),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    parent_manifest_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    selector: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    selector_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    state_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    bounds_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    ordered_items: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    truncated: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    manifest_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CopilotThreadRecord(Base):
    __tablename__ = "copilot_threads"
    __table_args__ = (
        CheckConstraint(check_values("status", CopilotThreadStatus), name="status_valid"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint(f"input_fingerprint ~ '{_SHA256}'", name="input_fingerprint_sha256"),
        CheckConstraint("title IS NULL OR btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'active' AND archived_at IS NULL)",
            name="archive_state_consistent",
        ),
        UniqueConstraint("id", "project_id", name="uq_copilot_threads_id_project"),
        UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_threads_creation_command"
        ),
        Index("ix_copilot_threads_project_updated", "project_id", "updated_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_copilot_threads_project_id_projects",
        ),
        nullable=False,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=CopilotThreadStatus.ACTIVE.value
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    creation_command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CopilotMessageRecord(Base):
    __tablename__ = "copilot_messages"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(check_values("role", CopilotMessageRole), name="role_valid"),
        CheckConstraint("btrim(content) <> ''", name="content_not_blank"),
        CheckConstraint(
            "semantic_status = 'conversational_noncanonical'",
            name="semantic_status_noncanonical",
        ),
        CheckConstraint(
            "(role = 'user' AND created_by_id IS NOT NULL) OR "
            "(role = 'assistant' AND created_by_id IS NULL)",
            name="role_actor_consistent",
        ),
        CheckConstraint(f"input_fingerprint ~ '{_SHA256}'", name="input_fingerprint_sha256"),
        UniqueConstraint(
            "id", "thread_id", "project_id", name="uq_copilot_messages_id_thread_project"
        ),
        UniqueConstraint("thread_id", "ordinal", name="uq_copilot_messages_thread_ordinal"),
        UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_messages_creation_command"
        ),
        ForeignKeyConstraint(
            ["thread_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_threads.id",
                f"{CANONICAL_SCHEMA}.copilot_threads.project_id",
            ],
            name="fk_copilot_messages_thread_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reply_to_message_id", "thread_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_messages.id",
                f"{CANONICAL_SCHEMA}.copilot_messages.thread_id",
                f"{CANONICAL_SCHEMA}.copilot_messages.project_id",
            ],
            name="fk_copilot_messages_reply_exact",
            ondelete="RESTRICT",
        ),
        Index("ix_copilot_messages_thread_created", "thread_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_copilot_messages_project_id_projects",
        ),
        nullable=False,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    semantic_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="conversational_noncanonical"
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    creation_command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CopilotTurnRecord(Base):
    __tablename__ = "copilot_turns"
    __table_args__ = (
        CheckConstraint(check_values("status", CopilotTurnStatus), name="status_valid"),
        CheckConstraint(
            f"context_manifest_fingerprint IS NULL OR context_manifest_fingerprint ~ '{_SHA256}'",
            name="context_manifest_fingerprint_sha256",
        ),
        CheckConstraint(
            "retrieval_manifest_fingerprint IS NULL OR "
            f"retrieval_manifest_fingerprint ~ '{_SHA256}'",
            name="retrieval_manifest_fingerprint_sha256",
        ),
        CheckConstraint(f"input_fingerprint ~ '{_SHA256}'", name="input_fingerprint_sha256"),
        CheckConstraint(
            "(status = 'pending_generation' AND assistant_message_id IS NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'succeeded' AND assistant_message_id IS NOT NULL "
            "AND completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND assistant_message_id IS NULL "
            "AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="completion_state_consistent",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completion_not_before_start",
        ),
        UniqueConstraint("id", "project_id", name="uq_copilot_turns_id_project"),
        UniqueConstraint(
            "id", "thread_id", "project_id", name="uq_copilot_turns_id_thread_project"
        ),
        UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_turns_creation_command"
        ),
        ForeignKeyConstraint(
            ["thread_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_threads.id",
                f"{CANONICAL_SCHEMA}.copilot_threads.project_id",
            ],
            name="fk_copilot_turns_thread_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_message_id", "thread_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_messages.id",
                f"{CANONICAL_SCHEMA}.copilot_messages.thread_id",
                f"{CANONICAL_SCHEMA}.copilot_messages.project_id",
            ],
            name="fk_copilot_turns_user_message_exact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assistant_message_id", "thread_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_messages.id",
                f"{CANONICAL_SCHEMA}.copilot_messages.thread_id",
                f"{CANONICAL_SCHEMA}.copilot_messages.project_id",
            ],
            name="fk_copilot_turns_assistant_message_exact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workflow_run_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.workflow_runs.id",
                f"{CANONICAL_SCHEMA}.workflow_runs.project_id",
            ],
            name="fk_copilot_turns_workflow_project",
            ondelete="RESTRICT",
        ),
        Index("ix_copilot_turns_thread_started", "thread_id", "started_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_copilot_turns_project_id_projects",
        ),
        nullable=False,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=CopilotTurnStatus.PENDING_GENERATION.value
    )
    context_manifest_fingerprint: Mapped[str | None] = mapped_column(Text)
    retrieval_manifest_fingerprint: Mapped[str | None] = mapped_column(Text)
    context_policy_version: Mapped[str | None] = mapped_column(Text)
    bounds_policy_version: Mapped[str | None] = mapped_column(Text)
    creation_command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CopilotActionProposalRecord(Base):
    __tablename__ = "copilot_action_proposals"
    __table_args__ = (
        CheckConstraint("btrim(action_type) <> ''", name="action_type_not_blank"),
        CheckConstraint("btrim(schema_version) <> ''", name="schema_version_not_blank"),
        CheckConstraint("btrim(model_explanation) <> ''", name="model_explanation_not_blank"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint("jsonb_typeof(expected_state) = 'object'", name="expected_state_object"),
        CheckConstraint(f"payload_fingerprint ~ '{_SHA256}'", name="payload_fingerprint_sha256"),
        CheckConstraint(
            f"expected_state_fingerprint ~ '{_SHA256}'",
            name="expected_state_fingerprint_sha256",
        ),
        CheckConstraint(
            f"context_manifest_fingerprint ~ '{_SHA256}'",
            name="context_manifest_fingerprint_sha256",
        ),
        CheckConstraint(
            f"impact_preview_fingerprint IS NULL OR impact_preview_fingerprint ~ '{_SHA256}'",
            name="impact_preview_fingerprint_sha256",
        ),
        CheckConstraint("btrim(confirmation_class) <> ''", name="confirmation_class_not_blank"),
        CheckConstraint(
            "btrim(action_policy_version) <> ''", name="action_policy_version_not_blank"
        ),
        CheckConstraint(check_values("status", CopilotProposalStatus), name="status_valid"),
        CheckConstraint(
            "(status = 'executed') = (outcome_event_id IS NOT NULL)",
            name="executed_outcome_consistent",
        ),
        CheckConstraint(
            "(status = 'invalidated') = (invalidation_reason IS NOT NULL)",
            name="invalidation_reason_consistent",
        ),
        CheckConstraint(f"input_fingerprint ~ '{_SHA256}'", name="input_fingerprint_sha256"),
        UniqueConstraint("id", "project_id", name="uq_copilot_proposals_id_project"),
        UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_proposals_creation_command"
        ),
        ForeignKeyConstraint(
            ["turn_id", "thread_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_turns.id",
                f"{CANONICAL_SCHEMA}.copilot_turns.thread_id",
                f"{CANONICAL_SCHEMA}.copilot_turns.project_id",
            ],
            name="fk_copilot_proposals_turn_exact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["outcome_event_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.operation_events.id",
                f"{CANONICAL_SCHEMA}.operation_events.project_id",
            ],
            name="fk_copilot_proposals_outcome_event_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["generation_record_id", "turn_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.generation_records.id",
                f"{CANONICAL_SCHEMA}.generation_records.copilot_turn_id",
                f"{CANONICAL_SCHEMA}.generation_records.project_id",
            ],
            name="fk_copilot_proposals_generation_turn_exact",
            ondelete="RESTRICT",
        ),
        Index("ix_copilot_proposals_turn_created", "turn_id", "created_at"),
        Index("ix_copilot_proposals_outcome_event", "outcome_event_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_copilot_proposals_project_id_projects",
        ),
        nullable=False,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    generation_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_explanation: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="Legacy audit-only proposal."
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    expected_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expected_state_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    context_manifest_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_class: Mapped[str] = mapped_column(Text, nullable=False)
    impact_preview_fingerprint: Mapped[str | None] = mapped_column(Text)
    action_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=CopilotProposalStatus.PENDING.value
    )
    outcome_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    creation_command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CopilotActionConfirmationRecord(Base):
    __tablename__ = "copilot_action_confirmations"
    __table_args__ = (
        CheckConstraint(f"proposal_fingerprint ~ '{_SHA256}'", name="proposal_fingerprint_sha256"),
        CheckConstraint(
            f"preview_fingerprint IS NULL OR preview_fingerprint ~ '{_SHA256}'",
            name="preview_fingerprint_sha256",
        ),
        CheckConstraint("rationale IS NULL OR btrim(rationale) <> ''", name="rationale_not_blank"),
        CheckConstraint(f"input_fingerprint ~ '{_SHA256}'", name="input_fingerprint_sha256"),
        UniqueConstraint("project_id", "command_id", name="uq_copilot_confirmations_command"),
        ForeignKeyConstraint(
            ["proposal_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.copilot_action_proposals.id",
                f"{CANONICAL_SCHEMA}.copilot_action_proposals.project_id",
            ],
            name="fk_copilot_confirmations_proposal_project",
            ondelete="RESTRICT",
        ),
        Index("ix_copilot_confirmations_proposal_created", "proposal_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_copilot_confirmations_project_id_projects",
        ),
        nullable=False,
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    preview_fingerprint: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
