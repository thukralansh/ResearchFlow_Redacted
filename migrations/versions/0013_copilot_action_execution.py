"""Add recoverable Copilot action execution state."""

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_operation_events_id_project",
        "operation_events",
        ["id", "project_id"],
        schema="public",
    )
    op.add_column(
        "copilot_action_proposals",
        sa.Column(
            "model_explanation",
            sa.Text(),
            server_default="Legacy audit-only proposal.",
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "copilot_action_proposals",
        sa.Column(
            "action_policy_version",
            sa.Text(),
            server_default="rf-copilot-action-v1",
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "copilot_action_proposals",
        sa.Column("outcome_event_id", sa.UUID(), nullable=True),
        schema="public",
    )
    op.add_column(
        "copilot_action_proposals",
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        schema="public",
    )
    op.drop_constraint(
        "ck_copilot_action_proposals_status_valid",
        "copilot_action_proposals",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_copilot_action_proposals_status_valid",
        "copilot_action_proposals",
        "status IN ('pending','confirmed','confirmed_pending','invalidated','executed')",
        schema="public",
    )
    op.create_check_constraint(
        "ck_copilot_action_proposals_model_explanation_not_blank",
        "copilot_action_proposals",
        "btrim(model_explanation) <> ''",
        schema="public",
    )
    op.create_check_constraint(
        "ck_copilot_action_proposals_action_policy_version_not_blank",
        "copilot_action_proposals",
        "btrim(action_policy_version) <> ''",
        schema="public",
    )
    op.create_check_constraint(
        "ck_copilot_action_proposals_executed_outcome_consistent",
        "copilot_action_proposals",
        "(status = 'executed') = (outcome_event_id IS NOT NULL)",
        schema="public",
    )
    op.create_check_constraint(
        "ck_copilot_action_proposals_invalidation_reason_consistent",
        "copilot_action_proposals",
        "(status = 'invalidated') = (invalidation_reason IS NOT NULL)",
        schema="public",
    )
    op.create_foreign_key(
        "fk_copilot_proposals_outcome_event_project",
        "copilot_action_proposals",
        "operation_events",
        ["outcome_event_id", "project_id"],
        ["id", "project_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_copilot_proposals_outcome_event",
        "copilot_action_proposals",
        ["outcome_event_id"],
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    active = connection.execute(
        sa.text(
            "SELECT count(*) FROM public.copilot_action_proposals "
            "WHERE status = 'confirmed_pending' OR outcome_event_id IS NOT NULL "
            "OR invalidation_reason IS NOT NULL "
            "OR model_explanation <> 'Legacy audit-only proposal.'"
        )
    ).scalar_one()
    if active:
        raise RuntimeError(
            "downgrade to 0012 refused: COP-04 action proposal/execution state cannot be represented"
        )
    op.drop_index(
        "ix_copilot_proposals_outcome_event",
        table_name="copilot_action_proposals",
        schema="public",
    )
    op.drop_constraint(
        "fk_copilot_proposals_outcome_event_project",
        "copilot_action_proposals",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_copilot_action_proposals_invalidation_reason_consistent",
        "copilot_action_proposals",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_copilot_action_proposals_executed_outcome_consistent",
        "copilot_action_proposals",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_copilot_action_proposals_model_explanation_not_blank",
        "copilot_action_proposals",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_copilot_action_proposals_action_policy_version_not_blank",
        "copilot_action_proposals",
        schema="public",
        type_="check",
    )
    op.drop_constraint(
        "ck_copilot_action_proposals_status_valid",
        "copilot_action_proposals",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_copilot_action_proposals_status_valid",
        "copilot_action_proposals",
        "status IN ('pending','confirmed','invalidated','executed')",
        schema="public",
    )
    op.drop_column("copilot_action_proposals", "invalidation_reason", schema="public")
    op.drop_column("copilot_action_proposals", "outcome_event_id", schema="public")
    op.drop_column("copilot_action_proposals", "action_policy_version", schema="public")
    op.drop_column("copilot_action_proposals", "model_explanation", schema="public")
    op.drop_constraint(
        "uq_operation_events_id_project",
        "operation_events",
        schema="public",
        type_="unique",
    )
