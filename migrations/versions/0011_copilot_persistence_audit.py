"""Add project-scoped Copilot audit persistence and exclusive generation ownership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None

_SHA = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_workflow_runs_id_project", "workflow_runs", ["id", "project_id"], schema="public"
    )
    op.create_table(
        "copilot_threads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("creation_command_id", sa.UUID(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','archived')", name="ck_copilot_threads_status_valid"
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_copilot_threads_lock_version_positive"),
        sa.CheckConstraint(
            f"input_fingerprint ~ '{_SHA}'", name="ck_copilot_threads_input_fingerprint_sha256"
        ),
        sa.CheckConstraint(
            "title IS NULL OR btrim(title) <> ''", name="ck_copilot_threads_title_not_blank"
        ),
        sa.CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR (status = 'active' AND archived_at IS NULL)",
            name="ck_copilot_threads_archive_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_copilot_threads_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_threads"),
        sa.UniqueConstraint("id", "project_id", name="uq_copilot_threads_id_project"),
        sa.UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_threads_creation_command"
        ),
        schema="public",
    )
    op.create_index(
        "ix_copilot_threads_project_updated",
        "copilot_threads",
        ["project_id", "updated_at"],
        schema="public",
    )
    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to_message_id", sa.UUID(), nullable=True),
        sa.Column(
            "semantic_status",
            sa.Text(),
            server_default="conversational_noncanonical",
            nullable=False,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("creation_command_id", sa.UUID(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_copilot_messages_ordinal_positive"),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_copilot_messages_role_valid"),
        sa.CheckConstraint("btrim(content) <> ''", name="ck_copilot_messages_content_not_blank"),
        sa.CheckConstraint(
            "semantic_status = 'conversational_noncanonical'",
            name="ck_copilot_messages_semantic_status_noncanonical",
        ),
        sa.CheckConstraint(
            "(role = 'user' AND created_by_id IS NOT NULL) OR (role = 'assistant' AND created_by_id IS NULL)",
            name="ck_copilot_messages_role_actor_consistent",
        ),
        sa.CheckConstraint(
            f"input_fingerprint ~ '{_SHA}'", name="ck_copilot_messages_input_fingerprint_sha256"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_copilot_messages_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "project_id"],
            ["public.copilot_threads.id", "public.copilot_threads.project_id"],
            name="fk_copilot_messages_thread_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id", "thread_id", "project_id"],
            [
                "public.copilot_messages.id",
                "public.copilot_messages.thread_id",
                "public.copilot_messages.project_id",
            ],
            name="fk_copilot_messages_reply_exact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_messages"),
        sa.UniqueConstraint(
            "id", "thread_id", "project_id", name="uq_copilot_messages_id_thread_project"
        ),
        sa.UniqueConstraint("thread_id", "ordinal", name="uq_copilot_messages_thread_ordinal"),
        sa.UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_messages_creation_command"
        ),
        schema="public",
    )
    op.create_index(
        "ix_copilot_messages_thread_created",
        "copilot_messages",
        ["thread_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "copilot_turns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("user_message_id", sa.UUID(), nullable=False),
        sa.Column("assistant_message_id", sa.UUID(), nullable=True),
        sa.Column("workflow_run_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending_generation", nullable=False),
        sa.Column("context_manifest_fingerprint", sa.Text(), nullable=True),
        sa.Column("retrieval_manifest_fingerprint", sa.Text(), nullable=True),
        sa.Column("context_policy_version", sa.Text(), nullable=True),
        sa.Column("bounds_policy_version", sa.Text(), nullable=True),
        sa.Column("creation_command_id", sa.UUID(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_generation','succeeded','failed')",
            name="ck_copilot_turns_status_valid",
        ),
        sa.CheckConstraint(
            f"context_manifest_fingerprint IS NULL OR context_manifest_fingerprint ~ '{_SHA}'",
            name="ck_copilot_turns_context_manifest_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"retrieval_manifest_fingerprint IS NULL OR retrieval_manifest_fingerprint ~ '{_SHA}'",
            name="ck_copilot_turns_retrieval_manifest_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"input_fingerprint ~ '{_SHA}'", name="ck_copilot_turns_input_fingerprint_sha256"
        ),
        sa.CheckConstraint(
            "(status = 'pending_generation' AND assistant_message_id IS NULL AND completed_at IS NULL AND error_code IS NULL) OR (status = 'succeeded' AND assistant_message_id IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NULL) OR (status = 'failed' AND assistant_message_id IS NULL AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_copilot_turns_completion_state_consistent",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_copilot_turns_completion_not_before_start",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_copilot_turns_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "project_id"],
            ["public.copilot_threads.id", "public.copilot_threads.project_id"],
            name="fk_copilot_turns_thread_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id", "thread_id", "project_id"],
            [
                "public.copilot_messages.id",
                "public.copilot_messages.thread_id",
                "public.copilot_messages.project_id",
            ],
            name="fk_copilot_turns_user_message_exact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id", "thread_id", "project_id"],
            [
                "public.copilot_messages.id",
                "public.copilot_messages.thread_id",
                "public.copilot_messages.project_id",
            ],
            name="fk_copilot_turns_assistant_message_exact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "project_id"],
            ["public.workflow_runs.id", "public.workflow_runs.project_id"],
            name="fk_copilot_turns_workflow_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_turns"),
        sa.UniqueConstraint("id", "project_id", name="uq_copilot_turns_id_project"),
        sa.UniqueConstraint(
            "id", "thread_id", "project_id", name="uq_copilot_turns_id_thread_project"
        ),
        sa.UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_turns_creation_command"
        ),
        schema="public",
    )
    op.create_index(
        "ix_copilot_turns_thread_started",
        "copilot_turns",
        ["thread_id", "started_at"],
        schema="public",
    )

    op.drop_constraint(
        "fk_generation_records_workflow_run_id_workflow_runs",
        "generation_records",
        schema="public",
        type_="foreignkey",
    )
    op.alter_column("generation_records", "workflow_run_id", schema="public", nullable=True)
    op.add_column(
        "generation_records",
        sa.Column("copilot_turn_id", sa.UUID(), nullable=True),
        schema="public",
    )
    op.create_unique_constraint(
        "uq_generation_records_id_project",
        "generation_records",
        ["id", "project_id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_generation_records_id_copilot_turn_project",
        "generation_records",
        ["id", "copilot_turn_id", "project_id"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_generation_records_workflow_project",
        "generation_records",
        "workflow_runs",
        ["workflow_run_id", "project_id"],
        ["id", "project_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_generation_records_copilot_turn_project",
        "generation_records",
        "copilot_turns",
        ["copilot_turn_id", "project_id"],
        ["id", "project_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_generation_records_exactly_one_execution_owner",
        "generation_records",
        "(workflow_run_id IS NOT NULL)::int + (copilot_turn_id IS NOT NULL)::int = 1",
        schema="public",
    )
    op.create_index(
        "ix_generation_records_copilot_turn_created",
        "generation_records",
        ["copilot_turn_id", "created_at"],
        schema="public",
    )

    op.create_table(
        "copilot_action_proposals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("turn_id", sa.UUID(), nullable=False),
        sa.Column("generation_record_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_fingerprint", sa.Text(), nullable=False),
        sa.Column("expected_state", postgresql.JSONB(), nullable=False),
        sa.Column("expected_state_fingerprint", sa.Text(), nullable=False),
        sa.Column("context_manifest_fingerprint", sa.Text(), nullable=False),
        sa.Column("confirmation_class", sa.Text(), nullable=False),
        sa.Column("impact_preview_fingerprint", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("creation_command_id", sa.UUID(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(action_type) <> ''", name="ck_copilot_action_proposals_action_type_not_blank"
        ),
        sa.CheckConstraint(
            "btrim(schema_version) <> ''",
            name="ck_copilot_action_proposals_schema_version_not_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_copilot_action_proposals_payload_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(expected_state) = 'object'",
            name="ck_copilot_action_proposals_expected_state_object",
        ),
        sa.CheckConstraint(
            f"payload_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_proposals_payload_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"expected_state_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_proposals_expected_state_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"context_manifest_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_proposals_context_manifest_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"impact_preview_fingerprint IS NULL OR impact_preview_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_proposals_impact_preview_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            "btrim(confirmation_class) <> ''",
            name="ck_copilot_action_proposals_confirmation_class_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','invalidated','executed')",
            name="ck_copilot_action_proposals_status_valid",
        ),
        sa.CheckConstraint(
            f"input_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_proposals_input_fingerprint_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_copilot_proposals_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "thread_id", "project_id"],
            [
                "public.copilot_turns.id",
                "public.copilot_turns.thread_id",
                "public.copilot_turns.project_id",
            ],
            name="fk_copilot_proposals_turn_exact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_record_id", "turn_id", "project_id"],
            [
                "public.generation_records.id",
                "public.generation_records.copilot_turn_id",
                "public.generation_records.project_id",
            ],
            name="fk_copilot_proposals_generation_turn_exact",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_action_proposals"),
        sa.UniqueConstraint("id", "project_id", name="uq_copilot_proposals_id_project"),
        sa.UniqueConstraint(
            "project_id", "creation_command_id", name="uq_copilot_proposals_creation_command"
        ),
        schema="public",
    )
    op.create_index(
        "ix_copilot_proposals_turn_created",
        "copilot_action_proposals",
        ["turn_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "copilot_action_confirmations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("proposal_fingerprint", sa.Text(), nullable=False),
        sa.Column("preview_fingerprint", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("command_id", sa.UUID(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"proposal_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_confirmations_proposal_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"preview_fingerprint IS NULL OR preview_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_confirmations_preview_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            "rationale IS NULL OR btrim(rationale) <> ''",
            name="ck_copilot_action_confirmations_rationale_not_blank",
        ),
        sa.CheckConstraint(
            f"input_fingerprint ~ '{_SHA}'",
            name="ck_copilot_action_confirmations_input_fingerprint_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_copilot_confirmations_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "project_id"],
            ["public.copilot_action_proposals.id", "public.copilot_action_proposals.project_id"],
            name="fk_copilot_confirmations_proposal_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_action_confirmations"),
        sa.UniqueConstraint("project_id", "command_id", name="uq_copilot_confirmations_command"),
        schema="public",
    )
    op.create_index(
        "ix_copilot_confirmations_proposal_created",
        "copilot_action_confirmations",
        ["proposal_id", "created_at"],
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "copilot_action_confirmations",
        "copilot_action_proposals",
        "copilot_turns",
        "copilot_messages",
        "copilot_threads",
    ):
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{table} LIMIT 1)")):
            raise RuntimeError("downgrade to 0010 refused: legitimate Copilot audit state exists")
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.generation_records WHERE copilot_turn_id IS NOT NULL LIMIT 1)"
        )
    ):
        raise RuntimeError("downgrade to 0010 refused: Copilot-owned GenerationRecords exist")
    op.drop_index(
        "ix_copilot_confirmations_proposal_created",
        table_name="copilot_action_confirmations",
        schema="public",
    )
    op.drop_table("copilot_action_confirmations", schema="public")
    op.drop_index(
        "ix_copilot_proposals_turn_created", table_name="copilot_action_proposals", schema="public"
    )
    op.drop_table("copilot_action_proposals", schema="public")
    op.drop_index(
        "ix_generation_records_copilot_turn_created",
        table_name="generation_records",
        schema="public",
    )
    op.drop_constraint(
        "ck_generation_records_exactly_one_execution_owner",
        "generation_records",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "fk_generation_records_copilot_turn_project",
        "generation_records",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_generation_records_workflow_project",
        "generation_records",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_generation_records_id_copilot_turn_project",
        "generation_records",
        schema="public",
        type_="unique",
    )
    op.drop_constraint(
        "uq_generation_records_id_project", "generation_records", schema="public", type_="unique"
    )
    op.drop_column("generation_records", "copilot_turn_id", schema="public")
    op.alter_column("generation_records", "workflow_run_id", schema="public", nullable=False)
    op.create_foreign_key(
        "fk_generation_records_workflow_run_id_workflow_runs",
        "generation_records",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.drop_index("ix_copilot_turns_thread_started", table_name="copilot_turns", schema="public")
    op.drop_table("copilot_turns", schema="public")
    op.drop_index(
        "ix_copilot_messages_thread_created", table_name="copilot_messages", schema="public"
    )
    op.drop_table("copilot_messages", schema="public")
    op.drop_index(
        "ix_copilot_threads_project_updated", table_name="copilot_threads", schema="public"
    )
    op.drop_table("copilot_threads", schema="public")
    op.drop_constraint(
        "uq_workflow_runs_id_project", "workflow_runs", schema="public", type_="unique"
    )
