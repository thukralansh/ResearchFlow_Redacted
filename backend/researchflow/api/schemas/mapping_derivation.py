"""Strict  contracts for repository-mapping consequences and review gates."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.api.schemas.method_review import MethodElementRevisionPayload
from researchflow.domain.knowledge_catalog import AmbiguityType, ImpactLevel
from researchflow.domain.method_catalog import MethodEpistemicStatus

type BoundedText = Annotated[str, Field(min_length=1, max_length=4_000)]
type ClientId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
type Confidence = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


MaterialityReason = Literal[
    "research_question_or_objective",
    "required_method_or_validity",
    "blocking_or_material_requirement",
    "material_ambiguity",
    "accepted_model_synthesis",
    "repository_dependent_eligibility",
    "blueprint_or_verification",
    "repository_detail_only",
    "uncertain_defaults_material",
]
DiscrepancyType = Literal[
    "consistent",
    "qualified",
    "implementation_detail",
    "parameter_or_configuration_difference",
    "missing_from_repository",
    "contradiction",
    "ambiguous",
]


class MaterialityAssessment(_StrictModel):
    material: bool
    reason_code: MaterialityReason
    rationale: BoundedText
    objective_consequence: BoundedText

    @model_validator(mode="after")
    def reason_matches_classification(self) -> MaterialityAssessment:
        nonmaterial = self.reason_code == "repository_detail_only"
        if self.material == nonmaterial:
            raise ValueError("materiality_reason_mismatch")
        return self


class RepositoryEvidenceReference(_StrictModel):
    repository_component_id: UUID
    excerpt: BoundedText
    relation: Literal["supports", "contradicts", "qualifies"]
    confidence: Confidence | None = None


class MappingDerivedRequirement(_StrictModel):
    kind: Literal["implementation_requirement"]
    client_id: ClientId
    source_mapping_ids: Annotated[list[UUID], Field(min_length=1, max_length=8)]
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    category: Literal[
        "mathematical",
        "data",
        "preprocessing",
        "algorithm",
        "architecture",
        "training",
        "evaluation",
        "environment",
        "compute",
        "reproducibility",
    ]
    priority: Literal["low", "medium", "high", "critical"]
    verification_criterion: Annotated[str, Field(min_length=1, max_length=2_000)]
    confidence: Confidence | None = None
    evidence: Annotated[list[RepositoryEvidenceReference], Field(min_length=1, max_length=8)]
    materiality: MaterialityAssessment


class MappingAmbiguityInterpretation(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(min_length=1, max_length=2_000)]
    implementation_consequences: Annotated[str, Field(min_length=1, max_length=2_000)]
    rank: Annotated[int, Field(ge=1, le=5)]


class MappingDerivedAmbiguity(_StrictModel):
    kind: Literal["ambiguity"]
    client_id: ClientId
    source_mapping_ids: Annotated[list[UUID], Field(min_length=1, max_length=8)]
    statement: Annotated[str, Field(min_length=1, max_length=2_000)]
    ambiguity_type: AmbiguityType
    impact_level: ImpactLevel
    confidence: Confidence | None = None
    interpretations: Annotated[
        list[MappingAmbiguityInterpretation], Field(min_length=1, max_length=5)
    ]
    single_interpretation_reason: Annotated[str, Field(min_length=1, max_length=1_000)] | None
    evidence: Annotated[list[RepositoryEvidenceReference], Field(min_length=1, max_length=8)]
    materiality: MaterialityAssessment

    @model_validator(mode="after")
    def validate_interpretations(self) -> MappingDerivedAmbiguity:
        ranks = sorted(item.rank for item in self.interpretations)
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("ambiguity_interpretation_ranks_not_contiguous")
        if (len(self.interpretations) == 1) != (self.single_interpretation_reason is not None):
            raise ValueError("ambiguity_single_interpretation_shape_invalid")
        return self


class MappingDerivedMethodRevision(_StrictModel):
    kind: Literal["method_element_revision"]
    client_id: ClientId
    source_mapping_ids: Annotated[list[UUID], Field(min_length=1, max_length=8)]
    target_method_element_id: UUID
    epistemic_status: Literal[MethodEpistemicStatus.REPOSITORY_OBSERVED]
    confidence: Confidence | None = None
    payload: MethodElementRevisionPayload
    evidence: Annotated[list[RepositoryEvidenceReference], Field(min_length=1, max_length=8)]
    materiality: MaterialityAssessment


class MappingDerivedMethodEvidence(_StrictModel):
    kind: Literal["method_element_evidence"]
    client_id: ClientId
    source_mapping_ids: Annotated[list[UUID], Field(min_length=1, max_length=8)]
    target_method_element_id: UUID
    evidence: Annotated[list[RepositoryEvidenceReference], Field(min_length=1, max_length=8)]
    materiality: MaterialityAssessment


type MappingDerivedItem = Annotated[
    MappingDerivedRequirement
    | MappingDerivedAmbiguity
    | MappingDerivedMethodRevision
    | MappingDerivedMethodEvidence,
    Field(discriminator="kind"),
]


class MappingDiscrepancy(_StrictModel):
    client_id: ClientId
    source_mapping_ids: Annotated[list[UUID], Field(min_length=1, max_length=8)]
    discrepancy_type: DiscrepancyType
    explanation: BoundedText
    repository_evidence: Annotated[list[RepositoryEvidenceReference], Field(max_length=8)]
    bounded_limitation: BoundedText | None = None
    materiality: MaterialityAssessment

    @model_validator(mode="after")
    def bounded_absence_only(self) -> MappingDiscrepancy:
        if (self.discrepancy_type == "missing_from_repository") != (
            self.bounded_limitation is not None
        ):
            raise ValueError("bounded_absence_scope_invalid")
        return self


class MappingDerivationDiagnostic(_StrictModel):
    client_id: ClientId | None = None
    reason: Annotated[str, Field(min_length=1, max_length=200)]
    detail: BoundedText


class MappingDerivationBatch(_StrictModel):
    derived_items: Annotated[list[MappingDerivedItem], Field(max_length=100)]
    discrepancies: Annotated[list[MappingDiscrepancy], Field(max_length=100)]
    diagnostics: Annotated[list[MappingDerivationDiagnostic], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_client_ids(self) -> MappingDerivationBatch:
        ids = [item.client_id for item in (*self.derived_items, *self.discrepancies)]
        if len(ids) != len(set(ids)):
            raise ValueError("mapping_derivation_client_id_duplicate")
        return self


class MappingDerivationRequest(_StrictModel):
    workflow_run_id: UUID
    repository_inspection_run_id: UUID | None = None
    applicable: bool = True
    applicability_rationale: BoundedText
    explicit_rerun: bool = False


class DerivedItemResponse(_StrictModel):
    client_id: str
    kind: str
    target_id: UUID
    material: bool
    materiality_reason: str
    source_mapping_ids: list[UUID]
    evidence_link_ids: list[UUID]


class MappingDerivationResponse(_StrictModel):
    outcome: Literal["applicable", "not_applicable"]
    generation_record_id: UUID | None
    proposal_method_model_id: UUID | None
    replayed: bool
    derived_items: list[DerivedItemResponse]
    discrepancies: list[MappingDiscrepancy]
    diagnostics: list[dict[str, object]]


class MappingReviewGateCompleteRequest(_StrictModel):
    command_id: UUID
    workflow_run_id: UUID
    derivation_generation_record_id: UUID | None = None
    applicable: bool = True
    rationale: BoundedText


class MappingReviewGateResponse(_StrictModel):
    outcome: Literal["complete", "blocked", "not_applicable"]
    fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    event_id: UUID | None
    replayed: bool
    material_count: int
    unresolved: list[dict[str, object]]
    accepted_mapping_ids: list[UUID]
    accepted_knowledge_ids: list[UUID]
    accepted_method_element_ids: list[UUID]
    partial_coverage_statement: str | None
