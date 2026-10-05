"""judgment_request_dependencies"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "artifact_dependencies",
        sa.Column("judgment_request_id", sa.UUID(), nullable=True),
        schema="public",
    )
    op.drop_constraint(
        op.f("ck_artifact_dependencies_exactly_one_artifact"),
        "artifact_dependencies",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_artifact_dependencies_exactly_one_artifact"),
        "artifact_dependencies",
        "num_nonnulls(judgment_request_id, method_model_id, paper_code_mapping_id, "
        "mental_model_id, blueprint_eligibility_assessment_id, implementation_blueprint_id, "
        "blueprint_section_id, implementation_step_id, verification_check_id) = 1",
        schema="public",
    )
    op.create_foreign_key(
        op.f("fk_artifact_dependencies_judgment_request_project"),
        "artifact_dependencies",
        "judgment_requests",
        ["judgment_request_id", "project_id"],
        ["id", "project_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_artifact_dependencies_judgment_request"),
        "artifact_dependencies",
        ["judgment_request_id"],
        schema="public",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.artifact_dependencies
                WHERE judgment_request_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade while JudgmentRequest dependency edges exist'
                    USING ERRCODE = '23502';
            END IF;
        END;
        $$
        """
    )
    op.drop_index(
        op.f("ix_artifact_dependencies_judgment_request"),
        table_name="artifact_dependencies",
        schema="public",
    )
    op.drop_constraint(
        op.f("fk_artifact_dependencies_judgment_request_project"),
        "artifact_dependencies",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_artifact_dependencies_exactly_one_artifact"),
        "artifact_dependencies",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_artifact_dependencies_exactly_one_artifact"),
        "artifact_dependencies",
        "num_nonnulls(method_model_id, paper_code_mapping_id, mental_model_id, "
        "blueprint_eligibility_assessment_id, implementation_blueprint_id, "
        "blueprint_section_id, implementation_step_id, verification_check_id) = 1",
        schema="public",
    )
    op.drop_column("artifact_dependencies", "judgment_request_id", schema="public")
