"""source_evidence_anchors"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_links",
        sa.Column("source_anchor_start_offset", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "evidence_links",
        sa.Column("source_anchor_end_offset", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "evidence_links",
        sa.Column("source_anchor_hash", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "evidence_links",
        sa.Column("source_anchor_policy_version", sa.Text(), nullable=True),
        schema="public",
    )
    op.create_check_constraint(
        op.f("ck_evidence_links_source_anchor_metadata_complete"),
        "evidence_links",
        "num_nonnulls(source_anchor_start_offset, source_anchor_end_offset, "
        "source_anchor_hash, source_anchor_policy_version) IN (0, 4)",
        schema="public",
    )
    op.create_check_constraint(
        op.f("ck_evidence_links_source_anchor_requires_chunk"),
        "evidence_links",
        "source_chunk_id IS NOT NULL OR source_anchor_start_offset IS NULL",
        schema="public",
    )
    op.create_check_constraint(
        op.f("ck_evidence_links_source_anchor_offset_range_valid"),
        "evidence_links",
        "source_anchor_start_offset IS NULL OR "
        "(source_anchor_start_offset >= 0 "
        "AND source_anchor_end_offset > source_anchor_start_offset)",
        schema="public",
    )
    op.create_check_constraint(
        op.f("ck_evidence_links_source_anchor_hash_sha256"),
        "evidence_links",
        "source_anchor_hash IS NULL OR source_anchor_hash ~ '^[0-9a-f]{64}$'",
        schema="public",
    )
    op.create_check_constraint(
        op.f("ck_evidence_links_source_anchor_policy_version_not_blank"),
        "evidence_links",
        "source_anchor_policy_version IS NULL OR btrim(source_anchor_policy_version) <> ''",
        schema="public",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.evidence_links
                WHERE source_anchor_start_offset IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while exact source-evidence anchors exist'
                    USING ERRCODE = '23502';
            END IF;
        END;
        $$
        """
    )
    for name in (
        "source_anchor_policy_version_not_blank",
        "source_anchor_hash_sha256",
        "source_anchor_offset_range_valid",
        "source_anchor_requires_chunk",
        "source_anchor_metadata_complete",
    ):
        op.drop_constraint(
            op.f(f"ck_evidence_links_{name}"),
            "evidence_links",
            schema="public",
            type_="check",
        )
    for name in (
        "source_anchor_policy_version",
        "source_anchor_hash",
        "source_anchor_end_offset",
        "source_anchor_start_offset",
    ):
        op.drop_column("evidence_links", name, schema="public")
