"""Persist exact immutable Copilot context and retrieval manifests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None

_SHA = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "copilot_manifests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("parent_manifest_id", sa.UUID(), nullable=True),
        sa.Column("selector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("selector_fingerprint", sa.Text(), nullable=False),
        sa.Column("state_fingerprint", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("bounds_policy_version", sa.Text(), nullable=False),
        sa.Column("ordered_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("manifest_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('context','retrieval')", name="kind_valid"),
        sa.CheckConstraint("jsonb_typeof(selector) = 'object'", name="selector_object"),
        sa.CheckConstraint(
            "pg_column_size(selector) <= 262144",
            name="selector_bounded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ordered_items) = 'array'",
            name="ordered_items_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(limitations) = 'array'",
            name="limitations_array",
        ),
        sa.CheckConstraint(
            "pg_column_size(ordered_items) <= 2097152",
            name="items_bounded",
        ),
        sa.CheckConstraint(
            "pg_column_size(limitations) <= 262144",
            name="limitations_bounded",
        ),
        sa.CheckConstraint(
            f"selector_fingerprint ~ '{_SHA}'",
            name="selector_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"state_fingerprint ~ '{_SHA}'",
            name="state_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            f"manifest_fingerprint ~ '{_SHA}'",
            name="manifest_fingerprint_sha256",
        ),
        sa.CheckConstraint("btrim(policy_version) <> ''", name="policy_version_not_blank"),
        sa.CheckConstraint(
            "btrim(bounds_policy_version) <> ''",
            name="bounds_version_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="fk_copilot_manifests_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_manifest_id", "project_id"],
            ["public.copilot_manifests.id", "public.copilot_manifests.project_id"],
            name="fk_copilot_manifests_parent_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_manifests"),
        sa.UniqueConstraint("id", "project_id", name="uq_copilot_manifests_id_project"),
        sa.UniqueConstraint(
            "project_id", "manifest_fingerprint", name="uq_copilot_manifests_project_fingerprint"
        ),
        schema="public",
    )
    op.create_index(
        "ix_copilot_manifests_project_created",
        "copilot_manifests",
        ["project_id", "created_at"],
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    manifest_count = connection.scalar(sa.text("SELECT count(*) FROM public.copilot_manifests"))
    if manifest_count:
        raise RuntimeError(
            "Copilot context/retrieval manifest audit exists; downgrade would discard history"
        )
    op.drop_index(
        "ix_copilot_manifests_project_created", table_name="copilot_manifests", schema="public"
    )
    op.drop_table("copilot_manifests", schema="public")
