"""Derived Blueprint eligibility, section, step, and verification records."""

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

from researchflow.domain.blueprint_catalog import (
    BLUEPRINT_SECTION_CONTENT_MAX_BYTES,
    ELIGIBILITY_MAPPING_COVERAGE_MAX_BYTES,
    VERIFICATION_LIKELY_CAUSES_MAX_BYTES,
    BlueprintEligibilityStatus,
    BlueprintSectionExplicitness,
    BlueprintSectionType,
    BlueprintStatus,
    DependencyCriticality,
    DependencyTargetType,
    EligibilityItemOutcome,
    VerificationResultStatus,
)
from researchflow.domain.knowledge_catalog import KnowledgeReviewStatus
from researchflow.domain.method_catalog import FreshnessStatus
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import (
    ActorType,
    LifecycleStatus,
    check_values,
)

_ELIGIBILITY_TARGET_COLUMNS = (
    "research_question_id",
    "research_question_source_id",
    "source_processing_run_id",
    "paper_repository_relationship_id",
    "repository_snapshot_id",
    "repository_component_id",
    "knowledge_object_id",
    "evidence_link_id",
    "grounding_assessment_id",
    "method_model_id",
    "method_model_assessment_id",
    "method_element_id",
    "paper_code_mapping_id",
    "mental_model_id",
    "mental_model_assessment_id",
    "human_judgment_id",
    "blueprint_eligibility_assessment_id",
    "implementation_blueprint_id",
    "blueprint_section_id",
    "implementation_step_id",
    "verification_check_id",
)


def _nonnull_count(columns: tuple[str, ...]) -> str:
    return f"num_nonnulls({', '.join(columns)})"


def _target_type_matches() -> str:
    pairs = zip(DependencyTargetType, _ELIGIBILITY_TARGET_COLUMNS, strict=True)
    return " OR ".join(
        f"(expected_dependency_type = '{target_type.value}' AND {column} IS NOT NULL)"
        for target_type, column in pairs
    )


def _eligibility_target_fk(table: str, suffix: str) -> ForeignKey:
    return ForeignKey(
        f"{CANONICAL_SCHEMA}.{table}.id",
        ondelete="RESTRICT",
        name=f"fk_eligibility_items_target_{suffix}",
    )


class BlueprintEligibilityAssessmentRecord(Base):
    """Input-versioned eligibility result, separate from any generated Blueprint."""

    __tablename__ = "blueprint_eligibility_assessments"
    __table_args__ = (
        CheckConstraint("btrim(reproduction_depth) <> ''", name="depth_not_blank"),
        CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'", name="input_fingerprint_sha256"),
        CheckConstraint("btrim(eligibility_rule_version) <> ''", name="rule_version_not_blank"),
        CheckConstraint(check_values("status", BlueprintEligibilityStatus), name="status_valid"),
        CheckConstraint(
            "jsonb_typeof(mapping_coverage) = 'object'",
            name="mapping_coverage_object",
        ),
        CheckConstraint(
            f"pg_column_size(mapping_coverage) <= {ELIGIBILITY_MAPPING_COVERAGE_MAX_BYTES}",
            name="mapping_coverage_bounded",
        ),
        CheckConstraint(
            "(paper_repository_relationship_id IS NULL) = (repository_snapshot_id IS NULL)",
            name="repository_pair_complete",
        ),
        UniqueConstraint(
            "project_id",
            "input_fingerprint",
            "eligibility_rule_version",
            "reproduction_depth",
            name="uq_blueprint_eligibility_assessments_input",
        ),
        UniqueConstraint(
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
            name="uq_blueprint_eligibility_assessments_exact_input",
        ),
        UniqueConstraint(
            "id",
            "project_id",
            "status",
            "research_question_id",
            "method_model_id",
            "method_model_assessment_id",
            "mental_model_id",
            "mental_model_assessment_id",
            name="uq_blueprint_eligibility_assessments_exact_base_input",
        ),
        UniqueConstraint(
            "id",
            "paper_repository_relationship_id",
            "repository_snapshot_id",
            name="uq_blueprint_eligibility_assessments_repository_input",
        ),
        ForeignKeyConstraint(
            ["research_question_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.research_questions.id",
                f"{CANONICAL_SCHEMA}.research_questions.project_id",
            ],
            name="fk_blueprint_eligibility_question_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.method_models.id",
                f"{CANONICAL_SCHEMA}.method_models.project_id",
            ],
            name="fk_blueprint_eligibility_method_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["method_model_assessment_id", "method_model_id"],
            [
                f"{CANONICAL_SCHEMA}.method_model_assessments.id",
                f"{CANONICAL_SCHEMA}.method_model_assessments.method_model_id",
            ],
            name="fk_blueprint_eligibility_method_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["mental_model_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.mental_models.id",
                f"{CANONICAL_SCHEMA}.mental_models.project_id",
            ],
            name="fk_blueprint_eligibility_mental_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["mental_model_assessment_id", "mental_model_id"],
            [
                f"{CANONICAL_SCHEMA}.mental_model_assessments.id",
                f"{CANONICAL_SCHEMA}.mental_model_assessments.assessed_model_id",
            ],
            name="fk_blueprint_eligibility_mental_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["paper_repository_relationship_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
                f"{CANONICAL_SCHEMA}.paper_repository_relationships.project_id",
            ],
            name="fk_blueprint_eligibility_repo_relation_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["repository_snapshot_id", "paper_repository_relationship_id"],
            [
                f"{CANONICAL_SCHEMA}.paper_repository_relationships.repository_snapshot_id",
                f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
            ],
            name="fk_blueprint_eligibility_relation_snapshot",
            ondelete="RESTRICT",
        ),
        Index("ix_blueprint_eligibility_project_created", "project_id", "created_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_blueprint_eligibility_project",
        ),
        nullable=False,
    )
    research_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    mental_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mental_model_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    paper_repository_relationship_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reproduction_depth: Mapped[str] = mapped_column(Text, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_coverage: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_blueprint_eligibility_generation_record",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImplementationBlueprintRecord(Base):
    """One fully derived Blueprint version over an exact approved input chain."""

    __tablename__ = "implementation_blueprints"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(check_values("status", BlueprintStatus), name="status_valid"),
        CheckConstraint(
            check_values("eligibility_status", BlueprintEligibilityStatus),
            name="eligibility_status_valid",
        ),
        CheckConstraint("eligibility_status <> 'blocked'", name="blocked_assessment_forbidden"),
        CheckConstraint(
            "eligibility_status <> 'eligible_with_warnings' "
            "OR status NOT IN ('review_ready', 'accepted')",
            name="warnings_prevent_acceptance",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint(
            "(paper_repository_relationship_id IS NULL) = (repository_snapshot_id IS NULL)",
            name="repository_pair_complete",
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        UniqueConstraint(
            "lineage_id", "version", name="uq_implementation_blueprints_lineage_version"
        ),
        UniqueConstraint("id", "lineage_id", name="uq_implementation_blueprints_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_implementation_blueprints_id_project"),
        UniqueConstraint("supersedes_id", name="uq_implementation_blueprints_supersedes_once"),
        ForeignKeyConstraint(
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
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.project_id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.status",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.research_question_id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.method_model_id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.method_model_assessment_id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.mental_model_id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.mental_model_assessment_id",
            ],
            name="fk_implementation_blueprints_exact_eligibility_input",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "eligibility_assessment_id",
                "paper_repository_relationship_id",
                "repository_snapshot_id",
            ],
            [
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.paper_repository_relationship_id",
                f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.repository_snapshot_id",
            ],
            name="fk_implementation_blueprints_eligibility_repository_input",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
                f"{CANONICAL_SCHEMA}.implementation_blueprints.lineage_id",
            ],
            name="fk_implementation_blueprints_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_implementation_blueprints_unsuperseded_lineage",
            "lineage_id",
            unique=True,
            postgresql_where=text("status <> 'superseded'"),
        ),
        Index(
            "ix_implementation_blueprints_eligibility_assessment",
            "eligibility_assessment_id",
        ),
        Index("ix_implementation_blueprints_project_status", "project_id", "status"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_implementation_blueprints_project",
        ),
        nullable=False,
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    eligibility_assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    eligibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    research_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method_model_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    mental_model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mental_model_assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    paper_repository_relationship_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_implementation_blueprints_generation_record",
        ),
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BlueprintSectionRecord(Base):
    """One immutable, bounded typed section within a Blueprint version."""

    __tablename__ = "blueprint_sections"
    __table_args__ = (
        CheckConstraint("sequence_index >= 1", name="sequence_positive"),
        CheckConstraint(
            check_values("section_type", BlueprintSectionType), name="section_type_valid"
        ),
        CheckConstraint("btrim(content_schema_version) <> ''", name="schema_version_not_blank"),
        CheckConstraint(
            "jsonb_typeof(structured_content) = 'object'", name="structured_content_object"
        ),
        CheckConstraint(
            f"pg_column_size(structured_content) <= {BLUEPRINT_SECTION_CONTENT_MAX_BYTES}",
            name="structured_content_bounded",
        ),
        CheckConstraint(
            check_values("explicitness", BlueprintSectionExplicitness),
            name="explicitness_valid",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_unit_interval",
        ),
        CheckConstraint(
            check_values("review_status", KnowledgeReviewStatus), name="review_status_valid"
        ),
        UniqueConstraint("blueprint_id", "section_type", name="uq_blueprint_sections_type"),
        UniqueConstraint("blueprint_id", "sequence_index", name="uq_blueprint_sections_sequence"),
        UniqueConstraint("id", "project_id", name="uq_blueprint_sections_id_project"),
        UniqueConstraint(
            "id", "blueprint_id", "project_id", name="uq_blueprint_sections_exact_parent"
        ),
        ForeignKeyConstraint(
            ["blueprint_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
                f"{CANONICAL_SCHEMA}.implementation_blueprints.project_id",
            ],
            name="fk_blueprint_sections_blueprint_project",
            ondelete="RESTRICT",
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blueprint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    structured_content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    explicitness: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=KnowledgeReviewStatus.CANDIDATE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImplementationStepRecord(Base):
    """One immutable semantic version of an ordered implementation step."""

    __tablename__ = "implementation_steps"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("sequence_index >= 1", name="sequence_positive"),
        CheckConstraint("btrim(title) <> ''", name="title_not_blank"),
        CheckConstraint("btrim(description) <> ''", name="description_not_blank"),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        CheckConstraint(
            check_values("freshness_status", FreshnessStatus), name="freshness_status_valid"
        ),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        UniqueConstraint("lineage_id", "version", name="uq_implementation_steps_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_implementation_steps_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_implementation_steps_id_project"),
        UniqueConstraint(
            "id", "blueprint_id", "project_id", name="uq_implementation_steps_exact_parent"
        ),
        UniqueConstraint("supersedes_id", name="uq_implementation_steps_supersedes_once"),
        ForeignKeyConstraint(
            ["blueprint_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
                f"{CANONICAL_SCHEMA}.implementation_blueprints.project_id",
            ],
            name="fk_implementation_steps_blueprint_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_steps.id",
                f"{CANONICAL_SCHEMA}.implementation_steps.lineage_id",
            ],
            name="fk_implementation_steps_derived_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_steps.id",
                f"{CANONICAL_SCHEMA}.implementation_steps.lineage_id",
            ],
            name="fk_implementation_steps_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_implementation_steps_current_sequence",
            "blueprint_id",
            "sequence_index",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blueprint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=LifecycleStatus.CURRENT.value
    )
    freshness_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=FreshnessStatus.CURRENT.value
    )
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_implementation_steps_generation_record",
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


class VerificationCheckRecord(Base):
    """Immutable check definition with a narrowly mutable audited result projection."""

    __tablename__ = "verification_checks"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("sequence_index >= 1", name="sequence_positive"),
        CheckConstraint("btrim(condition) <> ''", name="condition_not_blank"),
        CheckConstraint("btrim(test_type) <> ''", name="test_type_not_blank"),
        CheckConstraint("btrim(expected_result) <> ''", name="expected_result_not_blank"),
        CheckConstraint("btrim(failure_meaning) <> ''", name="failure_meaning_not_blank"),
        CheckConstraint("jsonb_typeof(likely_causes) = 'array'", name="likely_causes_array"),
        CheckConstraint(
            f"pg_column_size(likely_causes) <= {VERIFICATION_LIKELY_CAUSES_MAX_BYTES}",
            name="likely_causes_bounded",
        ),
        CheckConstraint(
            check_values("lifecycle_status", LifecycleStatus), name="lifecycle_status_valid"
        ),
        CheckConstraint(
            check_values("freshness_status", FreshnessStatus), name="freshness_status_valid"
        ),
        CheckConstraint(
            "result_status IS NULL OR " + check_values("result_status", VerificationResultStatus),
            name="result_status_valid",
        ),
        CheckConstraint(
            "(actual_result IS NULL AND result_status IS NULL AND result_actor_id IS NULL "
            "AND result_recorded_at IS NULL) OR "
            "(actual_result IS NOT NULL AND btrim(actual_result) <> '' "
            "AND result_status IS NOT NULL AND result_actor_id IS NOT NULL "
            "AND result_recorded_at IS NOT NULL)",
            name="result_fields_complete",
        ),
        CheckConstraint(
            "derived_from_id IS NULL OR derived_from_id <> id", name="not_self_derived"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        CheckConstraint(check_values("created_by_type", ActorType), name="created_by_type_valid"),
        UniqueConstraint("lineage_id", "version", name="uq_verification_checks_lineage_version"),
        UniqueConstraint("id", "lineage_id", name="uq_verification_checks_id_lineage"),
        UniqueConstraint("id", "project_id", name="uq_verification_checks_id_project"),
        UniqueConstraint("supersedes_id", name="uq_verification_checks_supersedes_once"),
        ForeignKeyConstraint(
            ["implementation_step_id", "blueprint_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.implementation_steps.id",
                f"{CANONICAL_SCHEMA}.implementation_steps.blueprint_id",
                f"{CANONICAL_SCHEMA}.implementation_steps.project_id",
            ],
            name="fk_verification_checks_exact_step",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["blueprint_section_id", "blueprint_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.blueprint_sections.id",
                f"{CANONICAL_SCHEMA}.blueprint_sections.blueprint_id",
                f"{CANONICAL_SCHEMA}.blueprint_sections.project_id",
            ],
            name="fk_verification_checks_exact_section",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["derived_from_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.verification_checks.id",
                f"{CANONICAL_SCHEMA}.verification_checks.lineage_id",
            ],
            name="fk_verification_checks_derived_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "lineage_id"],
            [
                f"{CANONICAL_SCHEMA}.verification_checks.id",
                f"{CANONICAL_SCHEMA}.verification_checks.lineage_id",
            ],
            name="fk_verification_checks_supersedes_lineage",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_verification_checks_current_sequence",
            "implementation_step_id",
            "sequence_index",
            unique=True,
            postgresql_where=text("lifecycle_status = 'current'"),
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blueprint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blueprint_section_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    implementation_step_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    test_type: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)
    failure_meaning: Mapped[str] = mapped_column(Text, nullable=False)
    likely_causes: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=LifecycleStatus.CURRENT.value
    )
    freshness_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=FreshnessStatus.CURRENT.value
    )
    generation_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.generation_records.id",
            ondelete="RESTRICT",
            name="fk_verification_checks_generation_record",
        ),
    )
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actual_result: Mapped[str | None] = mapped_column(Text)
    result_status: Mapped[str | None] = mapped_column(Text)
    result_actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    result_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BlueprintEligibilityItemRecord(Base):
    """One typed eligibility finding, including honest missing-prerequisite results."""

    __tablename__ = "blueprint_eligibility_items"
    __table_args__ = (
        CheckConstraint("sequence_index >= 1", name="sequence_positive"),
        CheckConstraint(
            check_values("expected_dependency_type", DependencyTargetType),
            name="expected_dependency_type_valid",
        ),
        CheckConstraint(
            check_values("criticality", DependencyCriticality), name="criticality_valid"
        ),
        CheckConstraint(check_values("outcome", EligibilityItemOutcome), name="outcome_valid"),
        CheckConstraint(
            f"{_nonnull_count(_ELIGIBILITY_TARGET_COLUMNS)} <= 1",
            name="at_most_one_dependency_target",
        ),
        CheckConstraint(
            f"({_nonnull_count(_ELIGIBILITY_TARGET_COLUMNS)} = 0) OR ({_target_type_matches()})",
            name="dependency_target_matches_type",
        ),
        CheckConstraint(
            f"outcome <> 'satisfied' OR {_nonnull_count(_ELIGIBILITY_TARGET_COLUMNS)} = 1",
            name="satisfied_requires_target",
        ),
        CheckConstraint(
            f"outcome <> 'not_applicable' OR {_nonnull_count(_ELIGIBILITY_TARGET_COLUMNS)} <= 1",
            name="not_applicable_target_optional",
        ),
        CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$'",
            name="reason_code_dotted",
        ),
        CheckConstraint("btrim(explanation) <> ''", name="explanation_not_blank"),
        UniqueConstraint(
            "assessment_id", "sequence_index", name="uq_blueprint_eligibility_items_sequence"
        ),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
            ondelete="RESTRICT",
            name="fk_blueprint_eligibility_items_assessment",
        ),
        nullable=False,
    )
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_dependency_type: Mapped[str] = mapped_column(Text, nullable=False)
    research_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("research_questions", "question")
    )
    research_question_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        _eligibility_target_fk("research_question_sources", "question_source"),
    )
    source_processing_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("source_processing_runs", "source_run")
    )
    paper_repository_relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        _eligibility_target_fk("paper_repository_relationships", "repo_relation"),
    )
    repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("repository_snapshots", "snapshot")
    )
    repository_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("repository_components", "component")
    )
    knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("knowledge_objects", "knowledge")
    )
    evidence_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("evidence_links", "evidence")
    )
    grounding_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("grounding_assessments", "grounding")
    )
    method_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("method_models", "method_model")
    )
    method_model_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        _eligibility_target_fk("method_model_assessments", "method_assessment"),
    )
    method_element_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("method_elements", "method_element")
    )
    paper_code_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("paper_code_mappings", "mapping")
    )
    mental_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("mental_models", "mental_model")
    )
    mental_model_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        _eligibility_target_fk("mental_model_assessments", "mental_assessment"),
    )
    human_judgment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("human_judgments", "judgment")
    )
    blueprint_eligibility_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        _eligibility_target_fk("blueprint_eligibility_assessments", "eligibility"),
    )
    implementation_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        _eligibility_target_fk("implementation_blueprints", "blueprint"),
    )
    blueprint_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("blueprint_sections", "section")
    )
    implementation_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("implementation_steps", "step")
    )
    verification_check_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _eligibility_target_fk("verification_checks", "check")
    )
    criticality: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
