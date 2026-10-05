"""Allow immutable Blueprint revisions over one exact eligibility assessment."""

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_implementation_blueprints_eligibility_assessment",
        "implementation_blueprints",
        schema="public",
        type_="unique",
    )
    op.create_index(
        "ix_implementation_blueprints_eligibility_assessment",
        "implementation_blueprints",
        ["eligibility_assessment_id"],
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            """
            SELECT eligibility_assessment_id
            FROM public.implementation_blueprints
            GROUP BY eligibility_assessment_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(
            "downgrade to 0009 refused: immutable Blueprint revisions share an "
            "eligibility assessment and cannot be represented by 0009"
        )
    op.drop_index(
        "ix_implementation_blueprints_eligibility_assessment",
        table_name="implementation_blueprints",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_implementation_blueprints_eligibility_assessment",
        "implementation_blueprints",
        ["eligibility_assessment_id"],
        schema="public",
    )
