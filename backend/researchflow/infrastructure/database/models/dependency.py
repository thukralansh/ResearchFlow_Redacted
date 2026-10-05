"""Exact version dependencies and append-only invalidation causes."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from researchflow.domain.blueprint_catalog import (
    DependencyCriticality,
    DependencyRole,
    InvalidationEffect,
)
from researchflow.infrastructure.database.metadata import CANONICAL_SCHEMA, Base
from researchflow.infrastructure.database.models.enums import check_values

_ARTIFACT_COLUMNS = (
    "judgment_request_id",
    "human_judgment_id",
    "method_model_id",
    "paper_code_mapping_id",
    "mental_model_id",
    "blueprint_eligibility_assessment_id",
    "implementation_blueprint_id",
    "blueprint_section_id",
    "implementation_step_id",
    "verification_check_id",
)

_DEPENDENCY_COLUMNS = (
    "dependency_research_question_id",
    "dependency_research_question_source_id",
    "dependency_source_processing_run_id",
    "dependency_paper_repository_relationship_id",
    "dependency_repository_snapshot_id",
    "dependency_repository_component_id",
    "dependency_knowledge_object_id",
    "dependency_evidence_link_id",
    "dependency_grounding_assessment_id",
    "dependency_method_model_id",
    "dependency_method_model_assessment_id",
    "dependency_method_element_id",
    "dependency_paper_code_mapping_id",
    "dependency_mental_model_id",
    "dependency_mental_model_assessment_id",
    "dependency_human_judgment_id",
    "dependency_blueprint_eligibility_assessment_id",
    "dependency_implementation_blueprint_id",
    "dependency_blueprint_section_id",
    "dependency_implementation_step_id",
    "dependency_verification_check_id",
)

_CAUSE_COLUMNS = tuple(column.replace("dependency_", "cause_", 1) for column in _DEPENDENCY_COLUMNS)


def _exactly_one(columns: tuple[str, ...]) -> str:
    return f"num_nonnulls({', '.join(columns)}) = 1"


class ArtifactDependencyRecord(Base):
    """One exact existing dependency of one exact derived artifact version."""

    __tablename__ = "artifact_dependencies"
    __table_args__ = (
        CheckConstraint(_exactly_one(_ARTIFACT_COLUMNS), name="exactly_one_artifact"),
        CheckConstraint(_exactly_one(_DEPENDENCY_COLUMNS), name="exactly_one_dependency"),
        CheckConstraint(
            "(method_model_id IS NULL OR dependency_method_model_id IS NULL "
            "OR method_model_id <> dependency_method_model_id) AND "
            "(paper_code_mapping_id IS NULL OR dependency_paper_code_mapping_id IS NULL "
            "OR paper_code_mapping_id <> dependency_paper_code_mapping_id) AND "
            "(mental_model_id IS NULL OR dependency_mental_model_id IS NULL "
            "OR mental_model_id <> dependency_mental_model_id) AND "
            "(blueprint_eligibility_assessment_id IS NULL "
            "OR dependency_blueprint_eligibility_assessment_id IS NULL "
            "OR blueprint_eligibility_assessment_id "
            "<> dependency_blueprint_eligibility_assessment_id) AND "
            "(implementation_blueprint_id IS NULL "
            "OR dependency_implementation_blueprint_id IS NULL "
            "OR implementation_blueprint_id <> dependency_implementation_blueprint_id) AND "
            "(blueprint_section_id IS NULL OR dependency_blueprint_section_id IS NULL "
            "OR blueprint_section_id <> dependency_blueprint_section_id) AND "
            "(implementation_step_id IS NULL OR dependency_implementation_step_id IS NULL "
            "OR implementation_step_id <> dependency_implementation_step_id) AND "
            "(verification_check_id IS NULL OR dependency_verification_check_id IS NULL "
            "OR verification_check_id <> dependency_verification_check_id)",
            name="not_self_dependency",
        ),
        CheckConstraint(
            "implementation_blueprint_id IS NULL OR dependency_role <> 'derivation_input' "
            "OR num_nonnulls(dependency_blueprint_eligibility_assessment_id, "
            "dependency_research_question_id, dependency_method_model_id, "
            "dependency_method_model_assessment_id, dependency_mental_model_id, "
            "dependency_mental_model_assessment_id) = 1",
            name="blueprint_derivation_chain_only",
        ),
        CheckConstraint(
            "verification_check_id IS NULL OR dependency_role <> 'derivation_input' "
            "OR num_nonnulls(dependency_implementation_blueprint_id, "
            "dependency_blueprint_section_id, dependency_implementation_step_id) = 1",
            name="verification_derivation_chain_only",
        ),
        CheckConstraint(
            check_values("dependency_role", DependencyRole), name="dependency_role_valid"
        ),
        CheckConstraint(
            check_values("criticality", DependencyCriticality), name="criticality_valid"
        ),
        ForeignKeyConstraint(
            ["judgment_request_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.judgment_requests.id",
                f"{CANONICAL_SCHEMA}.judgment_requests.project_id",
            ],
            name="fk_artifact_dependencies_judgment_request_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["human_judgment_id", "project_id"],
            [
                f"{CANONICAL_SCHEMA}.human_judgments.id",
                f"{CANONICAL_SCHEMA}.human_judgments.project_id",
            ],
            name="fk_artifact_dependencies_human_judgment_project",
            ondelete="RESTRICT",
        ),
        Index("ix_artifact_dependencies_project_created", "project_id", "created_at"),
        Index("ix_artifact_dependencies_judgment_request", "judgment_request_id"),
        Index("ix_artifact_dependencies_human_judgment", "human_judgment_id"),
        Index("ix_artifact_dependencies_method_model", "method_model_id"),
        Index("ix_artifact_dependencies_mapping", "paper_code_mapping_id"),
        Index("ix_artifact_dependencies_mental_model", "mental_model_id"),
        Index(
            "ix_artifact_dependencies_eligibility",
            "blueprint_eligibility_assessment_id",
        ),
        Index("ix_artifact_dependencies_blueprint", "implementation_blueprint_id"),
        Index("ix_artifact_dependencies_section", "blueprint_section_id"),
        Index("ix_artifact_dependencies_step", "implementation_step_id"),
        Index("ix_artifact_dependencies_check", "verification_check_id"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_project",
        ),
        nullable=False,
    )
    judgment_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    human_judgment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    method_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_method_model",
        ),
    )
    paper_code_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_code_mappings.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_mapping",
        ),
    )
    mental_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_mental_model",
        ),
    )
    blueprint_eligibility_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_eligibility",
        ),
    )
    implementation_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_blueprint",
        ),
    )
    blueprint_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_sections.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_section",
        ),
    )
    implementation_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_steps.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_step",
        ),
    )
    verification_check_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.verification_checks.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_check",
        ),
    )

    dependency_research_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_question",
        ),
    )
    dependency_research_question_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_question_sources.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_question_source",
        ),
    )
    dependency_source_processing_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.source_processing_runs.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_source_run",
        ),
    )
    dependency_paper_repository_relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_repo_relation",
        ),
    )
    dependency_repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_snapshot",
        ),
    )
    dependency_repository_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_components.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_component",
        ),
    )
    dependency_knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_knowledge",
        ),
    )
    dependency_evidence_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.evidence_links.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_evidence",
        ),
    )
    dependency_grounding_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.grounding_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_grounding",
        ),
    )
    dependency_method_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_method_model",
        ),
    )
    dependency_method_model_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_model_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_method_assessment",
        ),
    )
    dependency_method_element_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_method_element",
        ),
    )
    dependency_paper_code_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_code_mappings.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_mapping",
        ),
    )
    dependency_mental_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_mental_model",
        ),
    )
    dependency_mental_model_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_model_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_mental_assessment",
        ),
    )
    dependency_human_judgment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.human_judgments.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_judgment",
        ),
    )
    dependency_blueprint_eligibility_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_eligibility",
        ),
    )
    dependency_implementation_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_blueprint",
        ),
    )
    dependency_blueprint_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_sections.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_section",
        ),
    )
    dependency_implementation_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_steps.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_step",
        ),
    )
    dependency_verification_check_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.verification_checks.id",
            ondelete="RESTRICT",
            name="fk_artifact_dependencies_dep_check",
        ),
    )
    dependency_role: Mapped[str] = mapped_column(Text, nullable=False)
    criticality: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactInvalidationRecord(Base):
    """Append-only reason that an exact artifact version is no longer fully usable."""

    __tablename__ = "artifact_invalidations"
    __table_args__ = (
        CheckConstraint(_exactly_one(_ARTIFACT_COLUMNS), name="exactly_one_artifact"),
        CheckConstraint(_exactly_one(_CAUSE_COLUMNS), name="exactly_one_cause"),
        CheckConstraint(
            "(method_model_id IS NULL OR cause_method_model_id IS NULL "
            "OR method_model_id <> cause_method_model_id) AND "
            "(paper_code_mapping_id IS NULL OR cause_paper_code_mapping_id IS NULL "
            "OR paper_code_mapping_id <> cause_paper_code_mapping_id) AND "
            "(mental_model_id IS NULL OR cause_mental_model_id IS NULL "
            "OR mental_model_id <> cause_mental_model_id) AND "
            "(blueprint_eligibility_assessment_id IS NULL "
            "OR cause_blueprint_eligibility_assessment_id IS NULL "
            "OR blueprint_eligibility_assessment_id "
            "<> cause_blueprint_eligibility_assessment_id) AND "
            "(implementation_blueprint_id IS NULL "
            "OR cause_implementation_blueprint_id IS NULL "
            "OR implementation_blueprint_id <> cause_implementation_blueprint_id) AND "
            "(blueprint_section_id IS NULL OR cause_blueprint_section_id IS NULL "
            "OR blueprint_section_id <> cause_blueprint_section_id) AND "
            "(implementation_step_id IS NULL OR cause_implementation_step_id IS NULL "
            "OR implementation_step_id <> cause_implementation_step_id) AND "
            "(verification_check_id IS NULL OR cause_verification_check_id IS NULL "
            "OR verification_check_id <> cause_verification_check_id)",
            name="not_self_invalidation",
        ),
        CheckConstraint(check_values("effect", InvalidationEffect), name="effect_valid"),
        CheckConstraint(
            "rule_code ~ '^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$'",
            name="rule_code_dotted",
        ),
        CheckConstraint("btrim(reason) <> ''", name="reason_not_blank"),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at", name="resolution_not_before_cause"
        ),
        Index("ix_artifact_invalidations_project_created", "project_id", "created_at"),
        Index("ix_artifact_invalidations_unresolved", "project_id", "resolved_at"),
        {"schema": CANONICAL_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.projects.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_project",
        ),
        nullable=False,
    )
    method_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_method_model",
        ),
    )
    paper_code_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_code_mappings.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_mapping",
        ),
    )
    mental_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_mental_model",
        ),
    )
    blueprint_eligibility_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_eligibility",
        ),
    )
    implementation_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_blueprint",
        ),
    )
    blueprint_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_sections.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_section",
        ),
    )
    implementation_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_steps.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_step",
        ),
    )
    verification_check_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.verification_checks.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_check",
        ),
    )

    cause_research_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_questions.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_question",
        ),
    )
    cause_research_question_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.research_question_sources.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_question_source",
        ),
    )
    cause_source_processing_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.source_processing_runs.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_source_run",
        ),
    )
    cause_paper_repository_relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_repository_relationships.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_repo_relation",
        ),
    )
    cause_repository_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_snapshots.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_snapshot",
        ),
    )
    cause_repository_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.repository_components.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_component",
        ),
    )
    cause_knowledge_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.knowledge_objects.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_knowledge",
        ),
    )
    cause_evidence_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.evidence_links.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_evidence",
        ),
    )
    cause_grounding_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.grounding_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_grounding",
        ),
    )
    cause_method_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_method_model",
        ),
    )
    cause_method_model_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_model_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_method_assessment",
        ),
    )
    cause_method_element_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.method_elements.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_method_element",
        ),
    )
    cause_paper_code_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.paper_code_mappings.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_mapping",
        ),
    )
    cause_mental_model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_models.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_mental_model",
        ),
    )
    cause_mental_model_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.mental_model_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_mental_assessment",
        ),
    )
    cause_human_judgment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.human_judgments.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_judgment",
        ),
    )
    cause_blueprint_eligibility_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_eligibility_assessments.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_eligibility",
        ),
    )
    cause_implementation_blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_blueprints.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_blueprint",
        ),
    )
    cause_blueprint_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.blueprint_sections.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_section",
        ),
    )
    cause_implementation_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.implementation_steps.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_step",
        ),
    )
    cause_verification_check_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{CANONICAL_SCHEMA}.verification_checks.id",
            ondelete="RESTRICT",
            name="fk_artifact_invalidations_cause_check",
        ),
    )
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    effect: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
