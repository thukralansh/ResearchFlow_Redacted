"""Strict structured composition and noncanonical rendering contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.api.schemas.repository_discovery import RepositoryComponentResponse
from researchflow.domain.model_catalog import MentalModelMemberRole

type Confidence = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MentalModelMemberCandidate(_StrictModel):
    target_type: Literal["knowledge_object", "human_judgment", "evidence_link"]
    target_id: UUID
    role: MentalModelMemberRole
    inclusion_rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    confidence: Confidence | None = None


class MentalModelCompositionResult(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Confidence | None = None
    members: Annotated[list[MentalModelMemberCandidate], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_targets(self) -> MentalModelCompositionResult:
        keys = [(item.target_type, item.target_id) for item in self.members]
        if len(keys) != len(set(keys)):
            raise ValueError("mental_model_member_duplicate")
        return self


class MentalModelRenderingParagraph(_StrictModel):
    member_ids: Annotated[list[UUID], Field(min_length=1)]
    content: Annotated[str, Field(min_length=1, max_length=20_000)]

    @field_validator("member_ids")
    @classmethod
    def unique_member_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("rendering_member_id_duplicate")
        return value


class MentalModelRenderingResult(_StrictModel):
    paragraphs: Annotated[list[MentalModelRenderingParagraph], Field(min_length=1)]


class MentalModelGenerateRequest(_StrictModel):
    workflow_run_id: UUID
    method_model_id: UUID


class MentalModelRenderingRefreshRequest(_StrictModel):
    workflow_run_id: UUID
    explicit_rerun: bool = True


class MentalModelResponse(_StrictModel):
    id: UUID
    project_id: UUID
    research_question_id: UUID
    method_model_id: UUID
    lineage_id: UUID
    version: int
    title: str
    status: str
    confidence: Confidence | None
    composition_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None


class ResearchQuestionContextResponse(_StrictModel):
    id: UUID
    version: int
    question_text: str
    scope_text: str | None
    inquiry_status: str
    lifecycle_status: str


class MethodModelContextResponse(_StrictModel):
    id: UUID
    version: int
    name: str
    objective: str
    review_status: str
    lifecycle_status: str
    freshness_status: str
    composition_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    current_assessment_id: UUID | None


class SourceChunkProvenanceResponse(_StrictModel):
    id: UUID
    processing_run_id: UUID
    page_number: int
    section: str | None
    subsection: str | None
    chunk_index: int
    start_offset: int
    end_offset: int
    chunk_text: str
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EvidenceProvenanceResponse(_StrictModel):
    evidence_type: Literal["knowledge_evidence", "method_evidence"]
    id: UUID
    relation: str
    excerpt: str
    confidence: Confidence | None
    review_status: str
    lifecycle_status: str
    source_chunk: SourceChunkProvenanceResponse | None
    repository_component: RepositoryComponentResponse | None


class MentalModelMemberRecordResponse(_StrictModel):
    id: UUID
    mental_model_id: UUID
    knowledge_object_id: UUID | None
    human_judgment_id: UUID | None
    evidence_link_id: UUID | None
    role: MentalModelMemberRole
    inclusion_rationale: str
    confidence: Confidence | None


class MentalModelMemberResponse(_StrictModel):
    member: MentalModelMemberRecordResponse
    target_type: Literal["knowledge_object", "human_judgment", "evidence_link"]
    target: dict[str, Any]
    evidence: tuple[EvidenceProvenanceResponse, ...]


class MethodElementRecordResponse(_StrictModel):
    id: UUID
    lineage_id: UUID
    version: int
    element_type: str
    epistemic_status: str
    confidence: Confidence | None
    review_status: str
    lifecycle_status: str
    grounding_status: str


class MethodObjectLinkResponse(_StrictModel):
    id: UUID
    lineage_id: UUID
    version: int
    source_method_element_id: UUID
    relationship_type: str
    target_method_element_id: UUID
    rationale: str
    confidence: Confidence | None
    review_status: str
    lifecycle_status: str


class MethodElementMemberResponse(_StrictModel):
    id: UUID
    method_model_id: UUID
    method_element_id: UUID
    review_section: str
    member_role: str
    sequence_index: int | None
    required: bool
    inclusion_rationale: str
    element: MethodElementRecordResponse
    subtype_payload: dict[str, Any]
    object_links: tuple[MethodObjectLinkResponse, ...]


class MethodElementInspectionResponse(_StrictModel):
    member: MethodElementMemberResponse
    evidence: tuple[EvidenceProvenanceResponse, ...]


class MentalModelRenderingResponse(_StrictModel):
    id: UUID
    mental_model_id: UUID
    rendering_type: str
    content: str
    prompt_version: str | None
    provider: str | None
    model_version: str | None
    generation_record_id: UUID | None


class ArtifactDependencyResponse(_StrictModel):
    id: UUID
    project_id: UUID
    artifact_type: str
    artifact_id: UUID
    dependency_type: str
    dependency_id: UUID
    dependency_role: str
    criticality: str


class MentalModelDetailResponse(_StrictModel):
    model: MentalModelResponse
    is_current: bool
    question: ResearchQuestionContextResponse
    method_model: MethodModelContextResponse
    method_elements: tuple[MethodElementInspectionResponse, ...]
    members: tuple[MentalModelMemberResponse, ...]
    renderings: tuple[MentalModelRenderingResponse, ...]
    dependencies: tuple[ArtifactDependencyResponse, ...]


class MentalModelSummaryResponse(_StrictModel):
    model: MentalModelResponse
    is_current: bool
    rendering_count: int
    latest_rendering_id: UUID | None


class MentalModelGenerationResponse(_StrictModel):
    detail: MentalModelDetailResponse
    event: OperationEventResponse
    replayed: bool


class MentalModelRenderingCommandResponse(_StrictModel):
    model: MentalModelResponse
    rendering: MentalModelRenderingResponse
    event: OperationEventResponse
    replayed: bool
