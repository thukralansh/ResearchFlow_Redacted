"""Independent MethodElement versions and their six typed extensions."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.knowledge_catalog import (
    GroundingStatus,
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import (
    DATA_SHAPE_MAX_BYTES,
    DATA_SHAPE_MAX_DIMENSIONS,
    PARAMETER_VALUE_MAX_BYTES,
    MethodComponentType,
    MethodElementType,
    MethodEpistemicStatus,
    MethodEquationType,
    MethodSymbolType,
    ParameterRole,
    ValidityConditionType,
    VariableRole,
)
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import ActorType, check_values

_PARAMETER_VALUE_CHECK = f"""
value_or_range IS NULL OR (
    jsonb_typeof(value_or_range) = 'object'
    AND octet_length(value_or_range::text) <= {PARAMETER_VALUE_MAX_BYTES}
    AND (value_or_range - ARRAY[
        'kind', 'value', 'minimum', 'maximum',
        'minimum_inclusive', 'maximum_inclusive'
    ]::text[]) = '{{}}'::jsonb
    AND (
        (
            value_or_range->>'kind' = 'scalar'
            AND value_or_range ? 'value'
            AND NOT value_or_range ?| ARRAY[
                'minimum', 'maximum', 'minimum_inclusive', 'maximum_inclusive'
            ]
            AND jsonb_typeof(value_or_range->'value') IN ('number', 'string', 'boolean')
            AND (
                jsonb_typeof(value_or_range->'value') <> 'string'
                OR btrim(value_or_range->>'value') <> ''
            )
        ) OR (
            value_or_range->>'kind' = 'range'
            AND value_or_range ?& ARRAY[
                'minimum', 'maximum', 'minimum_inclusive', 'maximum_inclusive'
            ]
            AND NOT value_or_range ? 'value'
            AND jsonb_typeof(value_or_range->'minimum') = 'number'
            AND jsonb_typeof(value_or_range->'maximum') = 'number'
            AND jsonb_typeof(value_or_range->'minimum_inclusive') = 'boolean'
            AND jsonb_typeof(value_or_range->'maximum_inclusive') = 'boolean'
            AND CASE
                WHEN jsonb_typeof(value_or_range->'minimum') = 'number'
                 AND jsonb_typeof(value_or_range->'maximum') = 'number'
                THEN (value_or_range->>'minimum')::numeric
                   <= (value_or_range->>'maximum')::numeric
                ELSE false
            END
        )
    )
)
"""

_DATA_SHAPE_CHECK = f"""
data_type_or_shape IS NULL OR (
    jsonb_typeof(data_type_or_shape) = 'object'
    AND octet_length(data_type_or_shape::text) <= {DATA_SHAPE_MAX_BYTES}
    AND (data_type_or_shape - ARRAY['data_type', 'shape']::text[]) = '{{}}'::jsonb
    AND (data_type_or_shape ? 'data_type' OR data_type_or_shape ? 'shape')
    AND (
        NOT data_type_or_shape ? 'data_type'
        OR (
            jsonb_typeof(data_type_or_shape->'data_type') = 'string'
            AND btrim(data_type_or_shape->>'data_type') <> ''
        )
    )
    AND (
        NOT data_type_or_shape ? 'shape'
        OR (
            jsonb_typeof(data_type_or_shape->'shape') = 'array'
            AND jsonb_array_length(data_type_or_shape->'shape') <= {DATA_SHAPE_MAX_DIMENSIONS}
            AND NOT jsonb_path_exists(
                data_type_or_shape,
                '$.shape[*] ? (@.type() != "number" && @.type() != "string" && @.type() != "null")'
            )
            AND NOT jsonb_path_exists(
                data_type_or_shape,
                '$.shape[*] ? (@.type() == "number" && (@ <= 0 || @.floor() != @))'
            )
            AND NOT jsonb_path_exists(
                data_type_or_shape,
                '$.shape[*] ? (@.type() == "string" && @ like_regex "^[[:space:]]*$")'
            )
        )
    )
)
"""


class MethodElementRecord(Base):
    """One immutable semantic version of an independently reviewable method child."""

    __tablename__ = "method_elements"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(check_values("element_type", MethodElementType), name="element_type_valid"),
        CheckConstraint(
            check_values("epistemic_status", MethodEpistemicStatus),
            name="epistemic_status_valid",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint(
            check_values("grounding_status_cache", GroundingStatus),
            name="grounding_status_cache_valid",
        ),
        CheckConstraint(
            check_values("review_status", KnowledgeReviewStatus), name="review_status_valid"
        ),
        CheckConstraint(
            check_values("lifecycle_status", KnowledgeLifecycleStatus),
            name="lifecycle_status_valid",
        ),
        CheckConstraint(
            "(review_status IN ('candidate', 'needs_revision', 'rejected') "
            "AND lifecycle_status = 'current') OR "
            "(review_status = 'accepted' "
            "AND lifecycle_status IN ('current', 'challenged', 'superseded'))",
            name="review_lifecycle_combination_valid",
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        UniqueConstraint("lineage_id", "version", name="uq_method_elements_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_method_elements_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_method_elements_id_project"),
        UniqueConstraint("supersedes_id", name="uq_method_elements_supersedes_once"),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.method_elements.id",
                f"{CANONICAL_SCHEMA}.method_elements.lineage_id",
            ],
            name="fk_method_elements_derived_from_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.method_elements.id",
                f"{CANONICAL_SCHEMA}.method_elements.lineage_id",
            ],
            name="fk_method_elements_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["grounding_assessment_id", "id"],
            [
                f"{CANONICAL_SCHEMA}.grounding_assessments.id",
                f"{CANONICAL_SCHEMA}.grounding_assessments.method_element_id",
            ],
            name="fk_method_elements_grounding_assessment",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index(
            "uq_method_elements_unsuperseded_accepted_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text(
                "review_status = 'accepted' AND lifecycle_status IN ('current', 'challenged')"
            ),
        ),
        Index(
            "ix_method_elements_project_accepted_current",
            "project_id",
            "element_type",
            postgresql_where=text("review_status = 'accepted' AND lifecycle_status = 'current'"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_method_elements_project_id_projects",
        ),
        nullable=False,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    element_type: Mapped[str] = mapped_column(Text, nullable=False)
    epistemic_status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    grounding_status_cache: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=GroundingStatus.UNVALIDATED.value
    )
    grounding_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeReviewStatus.CANDIDATE.value
    )
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeLifecycleStatus.CURRENT.value
    )
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_method_elements_generation_record_id_generation_records",
        ),
    )
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MethodComponentRecord(Base):
    __tablename__ = "method_components"
    __table_args__ = (
        CheckConstraint(
            check_values("component_type", MethodComponentType), name="component_type_valid"
        ),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("btrim(description) <> ''", name="description_not_blank"),
        {"schema": CANONICAL_SCHEMA},
    )

    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_components_element",
        ),
        primary_key=True,
    )
    component_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class MethodEquationRecord(Base):
    __tablename__ = "method_equations"
    __table_args__ = (
        CheckConstraint("label IS NULL OR btrim(label) <> ''", name="label_not_blank"),
        CheckConstraint("btrim(latex_expression) <> ''", name="latex_not_blank"),
        CheckConstraint(
            "normalized_expression IS NULL OR btrim(normalized_expression) <> ''",
            name="normalized_expression_not_blank",
        ),
        CheckConstraint("btrim(explanation) <> ''", name="explanation_not_blank"),
        CheckConstraint(
            check_values("equation_type", MethodEquationType), name="equation_type_valid"
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_equations_element",
        ),
        primary_key=True,
    )
    label: Mapped[str | None] = mapped_column(Text)
    latex_expression: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_expression: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    equation_type: Mapped[str] = mapped_column(Text, nullable=False)


class MethodSymbolRecord(Base):
    __tablename__ = "method_symbols"
    __table_args__ = (
        CheckConstraint(check_values("symbol_type", MethodSymbolType), name="symbol_type_valid"),
        CheckConstraint("btrim(symbol) <> ''", name="symbol_not_blank"),
        CheckConstraint("btrim(canonical_name) <> ''", name="canonical_name_not_blank"),
        CheckConstraint("btrim(definition) <> ''", name="definition_not_blank"),
        CheckConstraint("units IS NULL OR btrim(units) <> ''", name="units_not_blank"),
        CheckConstraint(
            "domain_or_data_type IS NULL OR btrim(domain_or_data_type) <> ''",
            name="domain_not_blank",
        ),
        CheckConstraint(
            "constraints IS NULL OR btrim(constraints) <> ''", name="constraints_not_blank"
        ),
        CheckConstraint("btrim(role) <> ''", name="role_not_blank"),
        CheckConstraint(
            f"variable_role IS NULL OR {check_values('variable_role', VariableRole)}",
            name="variable_role_valid",
        ),
        CheckConstraint(
            f"parameter_role IS NULL OR {check_values('parameter_role', ParameterRole)}",
            name="parameter_role_valid",
        ),
        CheckConstraint(
            "(symbol_type = 'variable' AND variable_role IS NOT NULL "
            "AND parameter_role IS NULL AND value_or_range IS NULL) OR "
            "(symbol_type = 'parameter' AND parameter_role IS NOT NULL "
            "AND variable_role IS NULL)",
            name="symbol_role_family_valid",
        ),
        CheckConstraint(_PARAMETER_VALUE_CHECK, name="value_or_range_valid"),
        {"schema": CANONICAL_SCHEMA},
    )

    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_symbols_element",
        ),
        primary_key=True,
    )
    symbol_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    units: Mapped[str | None] = mapped_column(Text)
    domain_or_data_type: Mapped[str | None] = mapped_column(Text)
    constraints: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    variable_role: Mapped[str | None] = mapped_column(Text)
    parameter_role: Mapped[str | None] = mapped_column(Text)
    value_or_range: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))


class MethodAlgorithmStepRecord(Base):
    __tablename__ = "method_algorithm_steps"
    __table_args__ = (
        CheckConstraint("sequence_index >= 0", name="sequence_index_nonnegative"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(action) <> ''", name="action_not_blank"),
        CheckConstraint(
            "conditions IS NULL OR btrim(conditions) <> ''", name="conditions_not_blank"
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_algorithm_steps_element",
        ),
        primary_key=True,
    )
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[str | None] = mapped_column(Text)


class MethodDataFlowRecord(Base):
    __tablename__ = "method_data_flows"
    __table_args__ = (
        CheckConstraint("source_element_id <> target_element_id", name="endpoints_not_self"),
        CheckConstraint("btrim(transformation) <> ''", name="transformation_not_blank"),
        CheckConstraint(_DATA_SHAPE_CHECK, name="data_type_or_shape_valid"),
        Index("ix_method_data_flows_source", "source_element_id", "target_element_id"),
        Index("ix_method_data_flows_target", "target_element_id", "source_element_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_data_flows_element",
        ),
        primary_key=True,
    )
    source_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_data_flows_source_element",
        ),
        nullable=False,
    )
    target_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_data_flows_target_element",
        ),
        nullable=False,
    )
    transformation: Mapped[str] = mapped_column(Text, nullable=False)
    data_type_or_shape: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class MethodValidityConditionRecord(Base):
    __tablename__ = "method_validity_conditions"
    __table_args__ = (
        CheckConstraint("btrim(statement) <> ''", name="statement_not_blank"),
        CheckConstraint(
            check_values("condition_type", ValidityConditionType), name="condition_type_valid"
        ),
        CheckConstraint(
            "btrim(consequence_if_violated) <> ''",
            name="consequence_if_violated_not_blank",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    method_element_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_method_validity_conditions_element",
        ),
        primary_key=True,
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    condition_type: Mapped[str] = mapped_column(Text, nullable=False)
    consequence_if_violated: Mapped[str] = mapped_column(Text, nullable=False)
