"""Strict  Blueprint generation and inspection contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.domain.blueprint_catalog import BlueprintSectionExplicitness

type Confidence = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]
type BoundedText = Annotated[str, Field(min_length=1, max_length=4_000)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BlueprintReference(_StrictModel):
    """Provider-visible bounded alias; application resolution persists exact IDs."""

    ref: Annotated[str, Field(pattern=r"^bp[0-9]{4}$")]


class BlueprintContentItem(_StrictModel):
    statement: BoundedText
    explicitness: BlueprintSectionExplicitness
    references: Annotated[list[BlueprintReference], Field(min_length=1, max_length=32)]

    @field_validator("references")
    @classmethod
    def unique_references(cls, value: list[BlueprintReference]) -> list[BlueprintReference]:
        if len({item.ref for item in value}) != len(value):
            raise ValueError("blueprint_reference_duplicate")
        return value


class _SectionBase(_StrictModel):
    explicitness: BlueprintSectionExplicitness
    confidence: Confidence | None


class ObjectiveSection(_SectionBase):
    section_type: Literal["objective_and_reproduction_target"]
    objective: BlueprintContentItem
    target_outcome: BoundedText
    research_depth: BoundedText
    success_criteria: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=32)]


class ConceptualArchitectureSection(_SectionBase):
    section_type: Literal["conceptual_architecture"]
    components: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]
    relationships: Annotated[list[BlueprintContentItem], Field(max_length=64)]


class DataAndPreprocessingSection(_SectionBase):
    section_type: Literal["data_and_preprocessing"]
    inputs: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]
    preprocessing: Annotated[list[BlueprintContentItem], Field(max_length=64)]


class ModelAndAlgorithmSection(_SectionBase):
    section_type: Literal["model_and_algorithm"]
    mathematical_specification: Annotated[
        list[BlueprintContentItem], Field(min_length=1, max_length=64)
    ]
    algorithm: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]


class TrainingSection(_SectionBase):
    section_type: Literal["training"]
    plan: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]


class EvaluationSection(_SectionBase):
    section_type: Literal["evaluation"]
    metrics: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]
    decision_criteria: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]


class EnvironmentAndDependenciesSection(_SectionBase):
    section_type: Literal["environment_and_dependencies"]
    dependencies: Annotated[list[BlueprintContentItem], Field(max_length=64)]
    documented_gaps: Annotated[list[BlueprintContentItem], Field(max_length=64)]

    @model_validator(mode="after")
    def content_or_labeled_gap(self) -> EnvironmentAndDependenciesSection:
        if not self.dependencies and not self.documented_gaps:
            raise ValueError("environment_content_or_gap_required")
        return self


class RepositoryCorrespondenceSection(_SectionBase):
    section_type: Literal["repository_correspondence"]
    applicability: Literal["applicable", "not_applicable"]
    correspondences: Annotated[list[BlueprintContentItem], Field(max_length=64)]
    coverage_limits: Annotated[list[BlueprintContentItem], Field(max_length=64)]

    @model_validator(mode="after")
    def applicability_shape(self) -> RepositoryCorrespondenceSection:
        if self.applicability == "not_applicable" and self.correspondences:
            raise ValueError("repository_correspondence_forbidden_when_not_applicable")
        if self.applicability == "applicable" and not (
            self.correspondences or self.coverage_limits
        ):
            raise ValueError("repository_correspondence_or_limit_required")
        if self.applicability == "not_applicable" and not self.coverage_limits:
            raise ValueError("repository_not_applicable_explanation_required")
        return self


class AmbiguityDecisionSection(_SectionBase):
    section_type: Literal["ambiguity_and_decision_ledger"]
    entries: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]


class LimitationsSection(_SectionBase):
    section_type: Literal["limitations_and_open_decisions"]
    limitations: Annotated[list[BlueprintContentItem], Field(min_length=1, max_length=64)]
    open_decisions: Annotated[list[BlueprintContentItem], Field(max_length=64)]
    eligibility_warnings: Annotated[list[BlueprintContentItem], Field(max_length=64)]


type BlueprintSectionProposal = Annotated[
    ObjectiveSection
    | ConceptualArchitectureSection
    | DataAndPreprocessingSection
    | ModelAndAlgorithmSection
    | TrainingSection
    | EvaluationSection
    | EnvironmentAndDependenciesSection
    | RepositoryCorrespondenceSection
    | AmbiguityDecisionSection
    | LimitationsSection,
    Field(discriminator="section_type"),
]


class BlueprintGenerationResult(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Confidence | None
    sections: Annotated[list[BlueprintSectionProposal], Field(min_length=10, max_length=10)]

    @model_validator(mode="after")
    def exact_section_set(self) -> BlueprintGenerationResult:
        actual = [item.section_type for item in self.sections]
        expected = [
            "objective_and_reproduction_target",
            "conceptual_architecture",
            "data_and_preprocessing",
            "model_and_algorithm",
            "training",
            "evaluation",
            "environment_and_dependencies",
            "repository_correspondence",
            "ambiguity_and_decision_ledger",
            "limitations_and_open_decisions",
        ]
        if actual != expected:
            raise ValueError("blueprint_section_set_or_order_invalid")
        return self


class GenerateBlueprintRequest(_StrictModel):
    workflow_run_id: UUID
    eligibility_assessment_id: UUID


class AcceptBlueprintRequest(_StrictModel):
    command_id: UUID
    expected_version: Annotated[int, Field(ge=1)]
    expected_status: str
    rationale: BoundedText


class ReviseBlueprintRequest(AcceptBlueprintRequest):
    feedback: BoundedText
    eligibility_assessment_id: UUID | None = None


class ImplementationBlueprintResponse(_StrictModel):
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    eligibility_assessment_id: UUID
    eligibility_status: str
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    mental_model_id: UUID
    mental_model_assessment_id: UUID
    paper_repository_relationship_id: UUID | None
    repository_snapshot_id: UUID | None
    status: str
    confidence: Confidence | None
    generation_record_id: UUID | None
    supersedes_id: UUID | None


class BlueprintSectionResponse(_StrictModel):
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    sequence_index: int
    section_type: str
    content_schema_version: str
    structured_content: dict[str, object]
    explicitness: str
    confidence: Confidence | None
    review_status: str


class BlueprintDependencyResponse(_StrictModel):
    id: UUID
    project_id: UUID
    artifact_type: str
    artifact_id: UUID
    dependency_type: str
    dependency_id: UUID
    dependency_role: str
    criticality: str


class BlueprintDetailResponse(_StrictModel):
    blueprint: ImplementationBlueprintResponse
    sections: tuple[BlueprintSectionResponse, ...]
    dependencies: tuple[BlueprintDependencyResponse, ...]
    history: tuple[OperationEventResponse, ...]


class BlueprintGenerationResponse(BlueprintDetailResponse):
    event: OperationEventResponse
    replayed: bool


class BlueprintReviewResponse(BlueprintDetailResponse):
    event: OperationEventResponse
    replayed: bool
