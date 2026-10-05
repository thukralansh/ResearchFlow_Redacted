"""Add durable workflow orchestration identity and interrupt authority."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    op.add_column(
        "workflow_runs",
        sa.Column(
            "graph_version",
            sa.Text(),
            server_default="rf-project-analysis-graph-v1",
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "checkpoint_namespace",
            sa.Text(),
            server_default="project-analysis",
            nullable=False,
        ),
        schema="public",
    )
    op.create_check_constraint(
        "ck_workflow_runs_graph_version_not_blank",
        "workflow_runs",
        "btrim(graph_version) <> ''",
        schema="public",
    )
    op.create_check_constraint(
        "ck_workflow_runs_checkpoint_namespace_not_blank",
        "workflow_runs",
        "btrim(checkpoint_namespace) <> ''",
        schema="public",
    )
    op.create_table(
        "workflow_interrupts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("interrupt_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("expected_context_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "target_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("resolution_event_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','resolved','resumed','cancelled')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "created_by_type IN ('human','ai','system')",
            name="actor_type_valid",
        ),
        sa.CheckConstraint("btrim(interrupt_kind) <> ''", name="kind_not_blank"),
        sa.CheckConstraint(
            "expected_context_fingerprint ~ '^[0-9a-f]{64}$'",
            name="context_fingerprint_sha256",
        ),
        sa.CheckConstraint("jsonb_typeof(target_refs) = 'array'", name="target_refs_array"),
        sa.CheckConstraint("pg_column_size(target_refs) <= 65536", name="target_refs_bounded"),
        sa.CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        sa.CheckConstraint(
            "(status = 'pending' AND resolution_event_id IS NULL AND resolved_at IS NULL "
            "AND resumed_at IS NULL) OR "
            "(status = 'resolved' AND resolution_event_id IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resumed_at IS NULL) OR "
            "(status = 'resumed' AND resolution_event_id IS NOT NULL AND resolved_at IS NOT NULL "
            "AND resumed_at IS NOT NULL) OR "
            "(status = 'cancelled' AND resumed_at IS NULL)",
            name="resolution_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_workflow_interrupts_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "project_id"],
            ["public.workflow_runs.id", "public.workflow_runs.project_id"],
            name="fk_workflow_interrupts_run_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolution_event_id", "project_id"],
            ["public.operation_events.id", "public.operation_events.project_id"],
            name="fk_workflow_interrupts_resolution_event_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_interrupts"),
        sa.UniqueConstraint("id", "project_id", name="uq_workflow_interrupts_id_project"),
        schema="public",
    )
    op.create_index(
        "uq_workflow_interrupts_active_run",
        "workflow_interrupts",
        ["workflow_run_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status IN ('pending', 'resolved')"),
    )
    op.create_index(
        "ix_workflow_interrupts_project_created",
        "workflow_interrupts",
        ["project_id", "created_at"],
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM public.workflow_interrupts")):
        raise RuntimeError(
            "downgrade to 0013 refused: durable workflow interrupt state cannot be represented"
        )
    op.drop_index(
        "ix_workflow_interrupts_project_created",
        table_name="workflow_interrupts",
        schema="public",
    )
    op.drop_index(
        "uq_workflow_interrupts_active_run",
        table_name="workflow_interrupts",
        schema="public",
    )
    op.drop_table("workflow_interrupts", schema="public")
    op.drop_constraint(
        "ck_workflow_runs_checkpoint_namespace_not_blank",
        "workflow_runs",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_workflow_runs_graph_version_not_blank",
        "workflow_runs",
        schema="public",
        type_="check",
    )
    op.drop_column("workflow_runs", "checkpoint_namespace", schema="public")
    op.drop_column("workflow_runs", "graph_version", schema="public")
    # LangGraph owns its tables. Never drop the schema or checkpoint history here.
