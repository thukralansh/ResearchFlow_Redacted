"""repository_optionality"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_implementation_blueprints_exact_eligibility_input",
        "implementation_blueprints",
        schema="public",
        type_="foreignkey",
    )

    op.alter_column(
        "mental_model_assessments",
        "repository_snapshot_id",
        existing_type=sa.UUID(),
        nullable=True,
        schema="public",
    )
    for table_name in (
        "blueprint_eligibility_assessments",
        "implementation_blueprints",
    ):
        op.alter_column(
            table_name,
            "paper_repository_relationship_id",
            existing_type=sa.UUID(),
            nullable=True,
            schema="public",
        )
        op.alter_column(
            table_name,
            "repository_snapshot_id",
            existing_type=sa.UUID(),
            nullable=True,
            schema="public",
        )
        op.create_check_constraint(
            op.f(f"ck_{table_name}_repository_pair_complete"),
            table_name,
            "(paper_repository_relationship_id IS NULL) = (repository_snapshot_id IS NULL)",
            schema="public",
        )

    op.create_unique_constraint(
        "uq_blueprint_eligibility_assessments_exact_base_input",
        "blueprint_eligibility_assessments",
        [
            "id",
            "project_id",
            "status",
            "research_question_id",
            "method_model_id",
            "method_model_assessment_id",
            "mental_model_id",
            "mental_model_assessment_id",
        ],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_blueprint_eligibility_assessments_repository_input",
        "blueprint_eligibility_assessments",
        ["id", "paper_repository_relationship_id", "repository_snapshot_id"],
        schema="public",
    )
    op.create_foreign_key(
        "fk_implementation_blueprints_exact_eligibility_input",
        "implementation_blueprints",
        "blueprint_eligibility_assessments",
        [
            "eligibility_assessment_id",
            "project_id",
            "eligibility_status",
            "research_question_id",
            "method_model_id",
            "method_model_assessment_id",
            "mental_model_id",
            "mental_model_assessment_id",
        ],
        [
            "id",
            "project_id",
            "status",
            "research_question_id",
            "method_model_id",
            "method_model_assessment_id",
            "mental_model_id",
            "mental_model_assessment_id",
        ],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_implementation_blueprints_eligibility_repository_input",
        "implementation_blueprints",
        "blueprint_eligibility_assessments",
        [
            "eligibility_assessment_id",
            "paper_repository_relationship_id",
            "repository_snapshot_id",
        ],
        ["id", "paper_repository_relationship_id", "repository_snapshot_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_blueprint_eligibility_repository_input()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM public.blueprint_eligibility_assessments assessment
                WHERE assessment.id = NEW.eligibility_assessment_id
                  AND assessment.paper_repository_relationship_id
                      IS NOT DISTINCT FROM NEW.paper_repository_relationship_id
                  AND assessment.repository_snapshot_id
                      IS NOT DISTINCT FROM NEW.repository_snapshot_id
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23503',
                    CONSTRAINT = 'fk_implementation_blueprints_eligibility_repository_input',
                    MESSAGE = 'Blueprint repository inputs must match its eligibility assessment';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_implementation_blueprints_repository_input
        BEFORE INSERT OR UPDATE OF eligibility_assessment_id,
            paper_repository_relationship_id, repository_snapshot_id
        ON public.implementation_blueprints
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_blueprint_eligibility_repository_input()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.protect_eligibility_repository_input()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.implementation_blueprints blueprint
                WHERE blueprint.eligibility_assessment_id = OLD.id
                  AND (
                      blueprint.paper_repository_relationship_id
                          IS DISTINCT FROM NEW.paper_repository_relationship_id
                      OR blueprint.repository_snapshot_id
                          IS DISTINCT FROM NEW.repository_snapshot_id
                  )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23503',
                    CONSTRAINT = 'fk_implementation_blueprints_eligibility_repository_input',
                    MESSAGE = 'Eligibility repository inputs are referenced by a Blueprint';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_blueprint_eligibility_repository_input
        BEFORE UPDATE OF paper_repository_relationship_id, repository_snapshot_id
        ON public.blueprint_eligibility_assessments
        FOR EACH ROW
        EXECUTE FUNCTION public.protect_eligibility_repository_input()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_blueprint_eligibility_repository_input "
        "ON public.blueprint_eligibility_assessments"
    )
    op.execute("DROP FUNCTION public.protect_eligibility_repository_input()")
    op.execute(
        "DROP TRIGGER trg_implementation_blueprints_repository_input "
        "ON public.implementation_blueprints"
    )
    op.execute("DROP FUNCTION public.enforce_blueprint_eligibility_repository_input()")
    op.drop_constraint(
        "fk_implementation_blueprints_eligibility_repository_input",
        "implementation_blueprints",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_implementation_blueprints_exact_eligibility_input",
        "implementation_blueprints",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_blueprint_eligibility_assessments_repository_input",
        "blueprint_eligibility_assessments",
        schema="public",
        type_="unique",
    )
    op.drop_constraint(
        "uq_blueprint_eligibility_assessments_exact_base_input",
        "blueprint_eligibility_assessments",
        schema="public",
        type_="unique",
    )
    for table_name in (
        "implementation_blueprints",
        "blueprint_eligibility_assessments",
    ):
        op.drop_constraint(
            op.f(f"ck_{table_name}_repository_pair_complete"),
            table_name,
            schema="public",
            type_="check",
        )
        op.alter_column(
            table_name,
            "repository_snapshot_id",
            existing_type=sa.UUID(),
            nullable=False,
            schema="public",
        )
        op.alter_column(
            table_name,
            "paper_repository_relationship_id",
            existing_type=sa.UUID(),
            nullable=False,
            schema="public",
        )
    op.alter_column(
        "mental_model_assessments",
        "repository_snapshot_id",
        existing_type=sa.UUID(),
        nullable=False,
        schema="public",
    )

    op.create_foreign_key(
        "fk_implementation_blueprints_exact_eligibility_input",
        "implementation_blueprints",
        "blueprint_eligibility_assessments",
        [
            "eligibility_assessment_id",
            "project_id",
            "eligibility_status",
            "research_question_id",
            "method_model_id",
            "method_model_assessment_id",
            "mental_model_id",
            "mental_model_assessment_id",
            "paper_repository_relationship_id",
            "repository_snapshot_id",
        ],
        [
            "id",
            "project_id",
            "status",
            "research_question_id",
            "method_model_id",
            "method_model_assessment_id",
            "mental_model_id",
            "mental_model_assessment_id",
            "paper_repository_relationship_id",
            "repository_snapshot_id",
        ],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
