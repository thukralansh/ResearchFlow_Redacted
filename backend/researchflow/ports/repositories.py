"""Framework-neutral persistence records and consequential repository ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProjectData:
    id: UUID
    primary_research_question_id: UUID
    title: str
    description: str | None
    target_outcome: str
    reproduction_depth: str
    created_by_id: UUID
    lock_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewProject:
    id: UUID
    primary_research_question_id: UUID
    title: str
    description: str | None
    target_outcome: str
    reproduction_depth: str
    created_by_id: UUID


@dataclass(frozen=True, slots=True)
class ResearchQuestionData:
    id: UUID
    project_id: UUID
    parent_question_id: UUID | None
    lineage_id: UUID
    version: int
    question_text: str
    question_type: str
    scope_text: str | None
    inquiry_status: str
    lifecycle_status: str
    priority: str
    created_by_type: str
    created_by_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewResearchQuestion:
    id: UUID
    project_id: UUID
    parent_question_id: UUID | None
    lineage_id: UUID
    version: int
    question_text: str
    question_type: str
    scope_text: str | None
    inquiry_status: str
    lifecycle_status: str
    priority: str
    created_by_type: str
    created_by_id: UUID | None
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SourceData:
    id: UUID
    project_id: UUID
    source_type: str
    title: str
    authors: tuple[str, ...]
    identifiers: dict[str, str]
    original_filename: str
    mime_type: str
    size_bytes: int
    content_hash: str
    processing_status: str
    storage_bucket: str
    storage_object_key: str
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewSource:
    id: UUID
    project_id: UUID
    source_type: str
    title: str
    authors: tuple[str, ...]
    identifiers: dict[str, str]
    original_filename: str
    storage_bucket: str
    storage_object_key: str
    mime_type: str
    size_bytes: int
    content_hash: str
    processing_status: str
    created_by_id: UUID


@dataclass(frozen=True, slots=True)
class SourceProcessingRunData:
    id: UUID
    source_id: UUID
    source_hash: str
    parser_name: str
    parser_version: str
    chunker_name: str
    chunker_version: str
    normalization_version: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None


@dataclass(frozen=True, slots=True)
class NewSourceProcessingRun:
    id: UUID
    source_id: UUID
    source_hash: str
    parser_name: str
    parser_version: str
    chunker_name: str
    chunker_version: str
    normalization_version: str
    status: str = "running"


@dataclass(frozen=True, slots=True)
class SourceChunkData:
    id: UUID
    processing_run_id: UUID
    page_number: int
    section: str | None
    subsection: str | None
    chunk_index: int
    start_offset: int
    end_offset: int
    chunk_text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class NewSourceChunk:
    id: UUID
    processing_run_id: UUID
    page_number: int
    section: str | None
    subsection: str | None
    chunk_index: int
    start_offset: int
    end_offset: int
    chunk_text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ResearchQuestionSourceData:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    source_id: UUID
    lineage_id: UUID
    version: int
    relationship_type: str
    relevance_scope: str | None
    rationale: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    created_by_type: str
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewResearchQuestionSource:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    source_id: UUID
    lineage_id: UUID
    version: int
    relationship_type: str
    relevance_scope: str | None
    rationale: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    created_by_type: str
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RepositoryData:
    id: UUID
    project_id: UUID
    owner: str
    name: str
    normalized_url: str
    default_branch: str | None
    description: str | None
    is_private: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewRepository:
    id: UUID
    project_id: UUID
    owner: str
    name: str
    normalized_url: str
    default_branch: str
    description: str | None
    is_private: bool = False


@dataclass(frozen=True, slots=True)
class RepositorySnapshotData:
    id: UUID
    repository_id: UUID
    requested_ref: str
    commit_sha: str
    status: str
    lifecycle_status: str
    supersedes_id: UUID | None
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class NewRepositorySnapshot:
    id: UUID
    repository_id: UUID
    requested_ref: str
    commit_sha: str
    status: str
    lifecycle_status: str
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PaperRepositoryRelationshipData:
    id: UUID
    project_id: UUID
    source_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID | None
    lineage_id: UUID
    version: int
    relationship_type: str
    authority_level: str
    rationale: str
    evidence_text: str | None
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    created_by_type: str
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewPaperRepositoryRelationship:
    id: UUID
    project_id: UUID
    source_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID
    lineage_id: UUID
    version: int
    relationship_type: str
    authority_level: str
    rationale: str
    evidence_text: str | None
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    created_by_type: str
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RepositoryDiscoveryRunData:
    id: UUID
    repository_snapshot_id: UUID
    discovery_version: str
    tree_hash: str | None
    configured_limits: dict[str, object]
    coverage_status: str
    status: str
    error_code: str | None
    error_detail: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewRepositoryDiscoveryRun:
    id: UUID
    repository_snapshot_id: UUID
    discovery_version: str
    configured_limits: dict[str, object]
    coverage_status: str = "partial"
    status: str = "running"


@dataclass(frozen=True, slots=True)
class RepositoryTreeEntryData:
    id: UUID
    discovery_run_id: UUID
    path: str
    entry_type: str
    language: str | None
    coarse_role: str | None
    size_bytes: int | None
    excluded_reason: str | None


@dataclass(frozen=True, slots=True)
class NewRepositoryTreeEntry:
    id: UUID
    discovery_run_id: UUID
    path: str
    entry_type: str
    language: str | None
    coarse_role: str | None
    size_bytes: int | None
    excluded_reason: str | None


@dataclass(frozen=True, slots=True)
class RepositoryComponentData:
    id: UUID
    repository_snapshot_id: UUID
    analysis_stage: str
    repository_discovery_run_id: UUID | None
    repository_inspection_run_id: UUID | None
    path: str
    component_type: str
    language: str | None
    symbol: str | None
    line_start: int | None
    line_end: int | None
    content_excerpt: str
    content_hash: str
    relevance_score: Decimal | None


@dataclass(frozen=True, slots=True)
class NewRepositoryComponent:
    id: UUID
    repository_snapshot_id: UUID
    repository_discovery_run_id: UUID
    path: str
    component_type: str
    language: str | None
    line_start: int | None
    line_end: int | None
    content_excerpt: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class RepositoryInspectionRunData:
    id: UUID
    repository_snapshot_id: UUID
    discovery_run_id: UUID
    guidance_fingerprint: str
    ranking_version: str
    analysis_version: str
    configured_limits: dict[str, object]
    selected_paths: tuple[str, ...]
    inspected_paths: tuple[str, ...]
    coverage_status: str
    status: str
    error_code: str | None
    error_detail: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewRepositoryInspectionRun:
    id: UUID
    repository_snapshot_id: UUID
    discovery_run_id: UUID
    guidance_fingerprint: str
    ranking_version: str
    analysis_version: str
    configured_limits: dict[str, object]
    selected_paths: tuple[str, ...]
    inspected_paths: tuple[str, ...]
    coverage_status: str
    status: str = "succeeded"
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class NewRepositoryInspectionComponent:
    id: UUID
    repository_snapshot_id: UUID
    repository_inspection_run_id: UUID
    path: str
    component_type: str
    language: str | None
    symbol: str | None
    line_start: int
    line_end: int
    content_excerpt: str
    content_hash: str
    relevance_score: Decimal


@dataclass(frozen=True, slots=True)
class GenerationRecordData:
    id: UUID
    project_id: UUID
    workflow_run_id: UUID | None
    task_name: str
    prompt_version: str
    schema_version: str
    provider: str
    model_version: str
    reasoning_version: str | None
    input_fingerprint: str
    status: str
    model_configuration: dict[str, object]
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    raw_structured_output: object | None
    validation_summary: dict[str, object] | list[object] | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    created_at: datetime
    copilot_turn_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewGenerationRecord:
    id: UUID
    project_id: UUID
    workflow_run_id: UUID | None
    task_name: str
    prompt_version: str
    schema_version: str
    provider: str
    model_version: str
    reasoning_version: str | None
    input_fingerprint: str
    status: str
    model_configuration: dict[str, object]
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    raw_structured_output: object | None
    validation_summary: dict[str, object] | list[object] | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    copilot_turn_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeObjectData:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    object_type: str
    statement: str
    statement_normalization_version: str
    normalized_statement_hash: str
    origin: str
    epistemic_status: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    grounding_status: str
    grounding_assessment_id: UUID | None
    generation_record_id: UUID | None
    supersedes_id: UUID | None
    derived_from_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeObjectPayloadData:
    knowledge_object_id: UUID
    subtype_payload: dict[str, object]
    ambiguity_interpretations: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class NewKnowledgeCandidate:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    object_type: str
    statement: str
    statement_normalization_version: str
    normalized_statement_hash: str
    origin: str
    epistemic_status: str
    confidence: Decimal | None
    subtype_payload: dict[str, object]
    ambiguity_interpretations: tuple[dict[str, object], ...]
    generation_record_id: UUID
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewKnowledgeRevision:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    object_type: str
    statement: str
    statement_normalization_version: str
    normalized_statement_hash: str
    origin: str
    epistemic_status: str
    confidence: Decimal | None
    subtype_payload: dict[str, object]
    ambiguity_interpretations: tuple[dict[str, object], ...]
    derived_from_id: UUID
    created_by_type: str
    created_by_id: UUID | None


@dataclass(frozen=True, slots=True)
class KnowledgePresentationData:
    id: UUID
    knowledge_object_id: UUID
    version: int
    display_text: str
    display_label: str | None
    created_by_type: str
    created_by_id: UUID | None
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewKnowledgePresentation:
    id: UUID
    knowledge_object_id: UUID
    version: int
    display_text: str
    display_label: str | None
    created_by_type: str
    created_by_id: UUID | None
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class EvidenceLinkData:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    knowledge_object_id: UUID
    source_chunk_id: UUID | None
    evidence_relation: str
    evidence_excerpt: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None
    repository_component_id: UUID | None = None
    source_anchor_start_offset: int | None = None
    source_anchor_end_offset: int | None = None
    source_anchor_hash: str | None = None
    source_anchor_policy_version: str | None = None


@dataclass(frozen=True, slots=True)
class NewEvidenceLink:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    knowledge_object_id: UUID
    source_chunk_id: UUID | None
    repository_component_id: UUID | None
    evidence_relation: str
    evidence_excerpt: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None
    source_anchor_start_offset: int | None = None
    source_anchor_end_offset: int | None = None
    source_anchor_hash: str | None = None
    source_anchor_policy_version: str | None = None


@dataclass(frozen=True, slots=True)
class GroundingAssessmentData:
    id: UUID
    project_id: UUID
    knowledge_object_id: UUID
    evidence_set_fingerprint: str
    status: str
    supporting_count: int
    contradicting_count: int
    qualifying_count: int
    rationale: str
    validator_name: str
    validator_version: str


@dataclass(frozen=True, slots=True)
class NewGroundingAssessment:
    id: UUID
    project_id: UUID
    knowledge_object_id: UUID
    evidence_set_fingerprint: str
    status: str
    supporting_count: int
    contradicting_count: int
    qualifying_count: int
    rationale: str
    validator_name: str
    validator_version: str


@dataclass(frozen=True, slots=True)
class MethodElementData:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    element_type: str
    epistemic_status: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    grounding_status: str
    supersedes_id: UUID | None
    grounding_assessment_id: UUID | None = None
    generation_record_id: UUID | None = None
    derived_from_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MethodModelData:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    lineage_id: UUID
    version: int
    name: str
    objective: str
    review_status: str
    lifecycle_status: str
    freshness_status: str
    composition_fingerprint: str
    current_assessment_id: UUID | None
    supersedes_id: UUID | None
    generation_record_id: UUID | None = None
    derived_from_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewMethodModelCandidate:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    lineage_id: UUID
    version: int
    name: str
    objective: str
    confidence: Decimal | None
    composition_fingerprint: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None = None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewMethodElementCandidate:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    element_type: str
    epistemic_status: str
    confidence: Decimal | None
    subtype_payload: dict[str, object]
    generation_record_id: UUID | None
    derived_from_id: UUID | None = None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewMethodModelMember:
    id: UUID
    method_model_id: UUID
    method_element_id: UUID
    review_section: str
    member_role: str
    sequence_index: int | None
    required: bool
    inclusion_rationale: str


@dataclass(frozen=True, slots=True)
class NewMethodObjectLink:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    source_method_element_id: UUID
    relationship_type: str
    target_method_element_id: UUID
    rationale: str
    confidence: Decimal | None
    generation_record_id: UUID | None
    review_status: str = "candidate"
    lifecycle_status: str = "current"
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MethodModelMemberData:
    id: UUID
    method_model_id: UUID
    method_element_id: UUID
    review_section: str
    member_role: str
    sequence_index: int | None
    required: bool
    inclusion_rationale: str
    element: MethodElementData
    subtype_payload: dict[str, object]
    evidence: tuple[MethodEvidenceLinkData, ...] = ()
    object_links: tuple[MethodObjectLinkData, ...] = ()


@dataclass(frozen=True, slots=True)
class MethodObjectLinkData:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    source_method_element_id: UUID
    relationship_type: str
    target_method_element_id: UUID
    rationale: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class MethodModelSectionReviewData:
    id: UUID
    method_model_id: UUID
    review_section: str
    version: int
    outcome: str
    member_set_fingerprint: str
    reviewer_id: UUID
    rationale: str | None
    operation_event_id: UUID
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewMethodModelSectionReview:
    id: UUID
    method_model_id: UUID
    review_section: str
    version: int
    outcome: str
    member_set_fingerprint: str
    reviewer_id: UUID
    rationale: str | None
    operation_event_id: UUID
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewMethodModelRendering:
    id: UUID
    method_model_id: UUID
    rendering_type: str
    content: str
    prompt_version: str | None
    provider: str | None
    model_version: str | None
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class MethodModelRenderingData:
    id: UUID
    method_model_id: UUID
    rendering_type: str
    content: str
    prompt_version: str | None
    provider: str | None
    model_version: str | None
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewMethodEvidenceLink:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    method_element_id: UUID
    source_chunk_id: UUID | None
    repository_component_id: UUID | None
    evidence_relation: str
    evidence_excerpt: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MethodEvidenceLinkData:
    id: UUID
    project_id: UUID
    lineage_id: UUID
    version: int
    method_element_id: UUID
    source_chunk_id: UUID | None
    evidence_relation: str
    evidence_excerpt: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    generation_record_id: UUID | None
    repository_component_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewMethodGroundingAssessment:
    id: UUID
    project_id: UUID
    method_element_id: UUID
    evidence_set_fingerprint: str
    status: str
    supporting_count: int
    contradicting_count: int
    qualifying_count: int
    rationale: str
    validator_name: str
    validator_version: str


@dataclass(frozen=True, slots=True)
class NewMethodModelAssessment:
    id: UUID
    method_model_id: UUID
    composition_fingerprint: str
    member_grounding_fingerprint: str
    source_evidence_fingerprint: str
    repository_evidence_fingerprint: str | None
    unsupported_required_count: int
    mixed_evidence_count: int
    contradicted_count: int
    missing_provenance_count: int
    unresolved_scientific_ambiguity_count: int
    outcome: str
    rationale: str
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class MethodModelAssessmentData:
    id: UUID
    method_model_id: UUID
    outcome: str
    unsupported_required_count: int
    mixed_evidence_count: int
    contradicted_count: int
    missing_provenance_count: int
    unresolved_scientific_ambiguity_count: int
    rationale: str


@dataclass(frozen=True, slots=True)
class MentalModelData:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    method_model_id: UUID
    lineage_id: UUID
    version: int
    title: str
    status: str
    confidence: Decimal | None
    composition_fingerprint: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewMentalModel:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    method_model_id: UUID
    lineage_id: UUID
    version: int
    title: str
    status: str
    confidence: Decimal | None
    composition_fingerprint: str
    generation_record_id: UUID
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MentalModelMemberData:
    id: UUID
    mental_model_id: UUID
    knowledge_object_id: UUID | None
    human_judgment_id: UUID | None
    evidence_link_id: UUID | None
    role: str
    inclusion_rationale: str
    confidence: Decimal | None


@dataclass(frozen=True, slots=True)
class NewMentalModelMember:
    id: UUID
    mental_model_id: UUID
    knowledge_object_id: UUID | None
    human_judgment_id: UUID | None
    evidence_link_id: UUID | None
    role: str
    inclusion_rationale: str
    confidence: Decimal | None


@dataclass(frozen=True, slots=True)
class MentalModelRenderingData:
    id: UUID
    mental_model_id: UUID
    rendering_type: str
    content: str
    prompt_version: str | None
    provider: str | None
    model_version: str | None
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewMentalModelRendering:
    id: UUID
    mental_model_id: UUID
    rendering_type: str
    content: str
    prompt_version: str
    provider: str
    model_version: str
    generation_record_id: UUID


@dataclass(frozen=True, slots=True)
class MentalModelAssessmentData:
    id: UUID
    project_id: UUID
    assessed_model_id: UUID
    repository_snapshot_id: UUID | None
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    method_model_fingerprint: str
    repository_evidence_fingerprint: str
    accepted_knowledge_fingerprint: str
    accepted_mapping_fingerprint: str
    current_judgment_fingerprint: str
    result: str
    resulting_model_id: UUID | None
    rationale: str
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewMentalModelAssessment:
    id: UUID
    project_id: UUID
    assessed_model_id: UUID
    repository_snapshot_id: UUID | None
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    method_model_fingerprint: str
    repository_evidence_fingerprint: str
    accepted_knowledge_fingerprint: str
    accepted_mapping_fingerprint: str
    current_judgment_fingerprint: str
    result: str
    resulting_model_id: UUID | None
    rationale: str
    generation_record_id: UUID | None


@dataclass(frozen=True, slots=True)
class PaperCodeMappingData:
    id: UUID
    project_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID
    repository_inspection_run_id: UUID
    lineage_id: UUID
    version: int
    paper_anchor_type: str
    source_chunk_id: UUID | None
    knowledge_object_id: UUID | None
    method_model_id: UUID | None
    method_component_id: UUID | None
    method_equation_id: UUID | None
    method_symbol_id: UUID | None
    method_algorithm_step_id: UUID | None
    method_data_flow_id: UUID | None
    method_validity_condition_id: UUID | None
    repository_component_id: UUID | None
    mapping_type: str
    explanation: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_by_type: str
    created_by_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewPaperCodeMapping:
    id: UUID
    project_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID
    repository_inspection_run_id: UUID
    lineage_id: UUID
    version: int
    paper_anchor_type: str
    source_chunk_id: UUID | None
    knowledge_object_id: UUID | None
    method_model_id: UUID | None
    method_component_id: UUID | None
    method_equation_id: UUID | None
    method_symbol_id: UUID | None
    method_algorithm_step_id: UUID | None
    method_data_flow_id: UUID | None
    method_validity_condition_id: UUID | None
    repository_component_id: UUID | None
    mapping_type: str
    explanation: str
    confidence: Decimal | None
    generation_record_id: UUID | None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None
    review_status: str = "candidate"
    lifecycle_status: str = "current"
    freshness_status: str = "current"
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class JudgmentRequestData:
    id: UUID
    project_id: UUID
    ambiguity_id: UUID
    lineage_id: UUID
    version: int
    title: str
    decision_question: str
    why_human_required: str
    status: str
    lifecycle_status: str
    workflow_run_id: UUID
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewJudgmentRequest:
    id: UUID
    project_id: UUID
    ambiguity_id: UUID
    lineage_id: UUID
    version: int
    title: str
    decision_question: str
    why_human_required: str
    workflow_run_id: UUID
    generation_record_id: UUID | None
    created_by_type: str
    created_by_id: UUID | None
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class JudgmentOptionData:
    id: UUID
    judgment_request_id: UUID
    title: str
    description: str
    supporting_evidence: tuple[dict[str, object], ...]
    conflicting_evidence: tuple[dict[str, object], ...]
    implementation_consequences: str
    rank: int


@dataclass(frozen=True, slots=True)
class NewJudgmentOption:
    id: UUID
    judgment_request_id: UUID
    title: str
    description: str
    supporting_evidence: tuple[dict[str, object], ...]
    conflicting_evidence: tuple[dict[str, object], ...]
    implementation_consequences: str
    rank: int


@dataclass(frozen=True, slots=True)
class HumanJudgmentData:
    id: UUID
    project_id: UUID
    judgment_request_id: UUID
    lineage_id: UUID
    version: int
    selected_option_id: UUID | None
    custom_decision: str | None
    rationale: str
    confidence: Decimal
    lifecycle_status: str
    derived_from_id: UUID | None
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewHumanJudgment:
    id: UUID
    project_id: UUID
    judgment_request_id: UUID
    lineage_id: UUID
    version: int
    selected_option_id: UUID | None
    custom_decision: str | None
    rationale: str
    confidence: Decimal
    created_by_id: UUID
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class BlueprintEligibilityAssessmentData:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    mental_model_id: UUID
    mental_model_assessment_id: UUID
    paper_repository_relationship_id: UUID | None
    repository_snapshot_id: UUID | None
    reproduction_depth: str
    input_fingerprint: str
    eligibility_rule_version: str
    mapping_coverage: dict[str, object]
    status: str


@dataclass(frozen=True, slots=True)
class NewBlueprintEligibilityAssessment:
    id: UUID
    project_id: UUID
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    mental_model_id: UUID
    mental_model_assessment_id: UUID
    paper_repository_relationship_id: UUID | None
    repository_snapshot_id: UUID | None
    reproduction_depth: str
    input_fingerprint: str
    eligibility_rule_version: str
    mapping_coverage: dict[str, object]
    status: str


@dataclass(frozen=True, slots=True)
class BlueprintEligibilityItemData:
    id: UUID
    assessment_id: UUID
    sequence_index: int
    expected_dependency_type: str
    target_id: UUID | None
    criticality: str
    outcome: str
    reason_code: str
    explanation: str


@dataclass(frozen=True, slots=True)
class NewBlueprintEligibilityItem:
    id: UUID
    assessment_id: UUID
    sequence_index: int
    expected_dependency_type: str
    target_id: UUID | None
    criticality: str
    outcome: str
    reason_code: str
    explanation: str


@dataclass(frozen=True, slots=True)
class ImplementationBlueprintData:
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
    confidence: Decimal | None
    generation_record_id: UUID | None
    supersedes_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewImplementationBlueprint:
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
    confidence: Decimal | None
    generation_record_id: UUID
    supersedes_id: UUID | None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class BlueprintSectionData:
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    sequence_index: int
    section_type: str
    content_schema_version: str
    structured_content: dict[str, object]
    explicitness: str
    confidence: Decimal | None
    review_status: str


@dataclass(frozen=True, slots=True)
class NewBlueprintSection:
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    sequence_index: int
    section_type: str
    content_schema_version: str
    structured_content: dict[str, object]
    explicitness: str
    confidence: Decimal | None
    review_status: str = "candidate"


@dataclass(frozen=True, slots=True)
class ImplementationStepData:
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    lineage_id: UUID
    version: int
    sequence_index: int
    title: str
    description: str
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_by_type: str
    created_by_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewImplementationStep:
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    lineage_id: UUID
    version: int
    sequence_index: int
    title: str
    description: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class VerificationCheckData:
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    blueprint_section_id: UUID | None
    implementation_step_id: UUID
    lineage_id: UUID
    version: int
    sequence_index: int
    condition: str
    test_type: str
    expected_result: str
    failure_meaning: str
    likely_causes: tuple[str, ...]
    lifecycle_status: str
    freshness_status: str
    generation_record_id: UUID | None
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    actual_result: str | None
    result_status: str | None
    result_actor_id: UUID | None
    result_recorded_at: datetime | None
    created_by_type: str
    created_by_id: UUID | None


@dataclass(frozen=True, slots=True)
class NewVerificationCheck:
    id: UUID
    project_id: UUID
    blueprint_id: UUID
    blueprint_section_id: UUID | None
    implementation_step_id: UUID
    lineage_id: UUID
    version: int
    sequence_index: int
    condition: str
    test_type: str
    expected_result: str
    failure_meaning: str
    likely_causes: tuple[str, ...]
    generation_record_id: UUID | None
    derived_from_id: UUID | None = None
    supersedes_id: UUID | None = None
    created_by_type: str = "ai"
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class WorkflowRunData:
    id: UUID
    project_id: UUID
    workflow_type: str
    status: str
    current_stage: str | None
    langgraph_thread_id: str
    primary_source_processing_run_id: UUID | None
    primary_research_question_id: UUID
    primary_question_source_relationship_id: UUID | None
    paper_repository_relationship_id: UUID | None
    repository_snapshot_id: UUID | None
    lock_version: int
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    last_error_code: str | None = None
    last_error_detail: dict[str, object] | None = None
    current_method_model_id: UUID | None = None
    current_mental_model_id: UUID | None = None
    pending_judgment_request_id: UUID | None = None
    graph_version: str = "rf-project-analysis-graph-v1"
    checkpoint_namespace: str = "project-analysis"


@dataclass(frozen=True, slots=True)
class NewWorkflowRun:
    id: UUID
    project_id: UUID
    workflow_type: str
    status: str
    current_stage: str | None
    langgraph_thread_id: str
    primary_research_question_id: UUID
    primary_source_processing_run_id: UUID | None = None
    primary_question_source_relationship_id: UUID | None = None
    paper_repository_relationship_id: UUID | None = None
    repository_snapshot_id: UUID | None = None
    current_method_model_id: UUID | None = None
    current_mental_model_id: UUID | None = None
    pending_judgment_request_id: UUID | None = None
    graph_version: str = "rf-project-analysis-graph-v1"
    checkpoint_namespace: str = "project-analysis"
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WorkflowInterruptData:
    id: UUID
    project_id: UUID
    workflow_run_id: UUID
    interrupt_kind: str
    status: str
    expected_context_fingerprint: str
    target_refs: tuple[dict[str, object], ...]
    created_by_type: str
    created_by_id: UUID | None
    resolution_event_id: UUID | None
    resolved_at: datetime | None
    resumed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lock_version: int


@dataclass(frozen=True, slots=True)
class NewWorkflowInterrupt:
    id: UUID
    project_id: UUID
    workflow_run_id: UUID
    interrupt_kind: str
    expected_context_fingerprint: str
    target_refs: tuple[dict[str, object], ...]
    created_by_type: str
    created_by_id: UUID | None


@dataclass(frozen=True, slots=True)
class OperationEventData:
    id: UUID
    project_id: UUID
    workflow_run_id: UUID | None
    operation_type: str
    created_by_type: str
    created_by_id: UUID | None
    input_refs: tuple[dict[str, object], ...]
    output_refs: tuple[dict[str, object], ...]
    reason: str | None
    idempotency_key: str | None
    input_fingerprint: str | None
    request_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewOperationEvent:
    id: UUID
    project_id: UUID
    workflow_run_id: UUID | None
    operation_type: str
    created_by_type: str
    created_by_id: UUID | None
    input_refs: tuple[dict[str, object], ...] = ()
    output_refs: tuple[dict[str, object], ...] = ()
    reason: str | None = None
    idempotency_key: str | None = None
    input_fingerprint: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactDependencyData:
    id: UUID
    project_id: UUID
    artifact_type: str
    artifact_id: UUID
    dependency_type: str
    dependency_id: UUID
    dependency_role: str
    criticality: str


@dataclass(frozen=True, slots=True)
class ArtifactInvalidationData:
    id: UUID
    project_id: UUID
    artifact_type: str
    artifact_id: UUID
    cause_type: str
    cause_id: UUID
    rule_code: str
    effect: str
    reason: str
    created_at: datetime | None = None
    resolved_at: datetime | None = None


@runtime_checkable
class ProjectScopedRepository(Protocol):
    @property
    def project_id(self) -> UUID: ...


class ProjectsRepository(ProjectScopedRepository, Protocol):
    async def add(self, project: NewProject) -> ProjectData: ...
    async def get(self) -> ProjectData | None: ...
    async def get_for_update(self, *, nowait: bool = False) -> ProjectData | None: ...
    async def update_configuration(
        self,
        *,
        expected_lock_version: int,
        title: str,
        description: str | None,
        target_outcome: str,
        reproduction_depth: str,
    ) -> ProjectData: ...
    async def replace_primary_research_question(
        self,
        *,
        expected_lock_version: int,
        expected_question_id: UUID,
        new_question_id: UUID,
    ) -> ProjectData: ...


class ResearchQuestionsRepository(ProjectScopedRepository, Protocol):
    async def add(self, question: NewResearchQuestion) -> ResearchQuestionData: ...
    async def get_exact(self, question_id: UUID) -> ResearchQuestionData | None: ...
    async def get_exact_for_update(
        self, question_id: UUID, *, nowait: bool = False
    ) -> ResearchQuestionData | None: ...
    async def get_current_primary(self) -> ResearchQuestionData | None: ...
    async def get_current_primary_for_update(
        self, *, nowait: bool = False
    ) -> ResearchQuestionData | None: ...
    async def list_current(self) -> tuple[ResearchQuestionData, ...]: ...
    async def mark_superseded(
        self, question_id: UUID, *, expected_version: int
    ) -> ResearchQuestionData: ...
    async def update_inquiry_status(
        self,
        question_id: UUID,
        *,
        expected_version: int,
        expected_status: str,
        new_status: str,
    ) -> ResearchQuestionData: ...


class SourcesRepository(ProjectScopedRepository, Protocol):
    async def add(self, source: NewSource) -> SourceData: ...
    async def get_exact(self, source_id: UUID) -> SourceData | None: ...
    async def list_all(self) -> tuple[SourceData, ...]: ...
    async def add_question_source(
        self, relationship: NewResearchQuestionSource
    ) -> ResearchQuestionSourceData: ...
    async def get_question_source_exact(
        self, relationship_id: UUID
    ) -> ResearchQuestionSourceData | None: ...
    async def get_current_primary_paper(self) -> ResearchQuestionSourceData | None: ...
    async def get_current_primary_paper_for_update(
        self, *, nowait: bool = False
    ) -> ResearchQuestionSourceData | None: ...
    async def list_primary_paper_history(self) -> tuple[ResearchQuestionSourceData, ...]: ...
    async def mark_question_source_superseded(
        self, relationship_id: UUID, *, expected_version: int
    ) -> ResearchQuestionSourceData: ...
    async def update_processing_status(self, source_id: UUID, status: str) -> SourceData: ...
    async def add_processing_run(self, run: NewSourceProcessingRun) -> SourceProcessingRunData: ...
    async def get_processing_run_exact(self, run_id: UUID) -> SourceProcessingRunData | None: ...
    async def get_processing_run_by_identity(
        self,
        *,
        source_id: UUID,
        source_hash: str,
        parser_name: str,
        parser_version: str,
        chunker_name: str,
        chunker_version: str,
        normalization_version: str,
    ) -> SourceProcessingRunData | None: ...
    async def list_processing_runs(
        self, source_id: UUID
    ) -> tuple[SourceProcessingRunData, ...]: ...
    async def finish_processing_run(
        self,
        run_id: UUID,
        *,
        expected_status: str,
        status: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> SourceProcessingRunData: ...
    async def add_chunks(
        self, chunks: tuple[NewSourceChunk, ...]
    ) -> tuple[SourceChunkData, ...]: ...
    async def list_chunks(self, run_id: UUID) -> tuple[SourceChunkData, ...]: ...
    async def get_chunk_exact(self, chunk_id: UUID) -> SourceChunkData | None: ...


class RepositoriesRepository(ProjectScopedRepository, Protocol):
    async def add(self, repository: NewRepository) -> RepositoryData: ...
    async def get_exact(self, repository_id: UUID) -> RepositoryData | None: ...
    async def get_by_identity(self, owner: str, name: str) -> RepositoryData | None: ...
    async def add_snapshot(self, snapshot: NewRepositorySnapshot) -> RepositorySnapshotData: ...
    async def get_current_snapshot(self, repository_id: UUID) -> RepositorySnapshotData | None: ...
    async def get_snapshot_exact(self, snapshot_id: UUID) -> RepositorySnapshotData | None: ...
    async def get_snapshot_by_commit(
        self, repository_id: UUID, commit_sha: str
    ) -> RepositorySnapshotData | None: ...
    async def mark_snapshot_superseded(self, snapshot_id: UUID) -> RepositorySnapshotData: ...
    async def mark_snapshot_current(self, snapshot_id: UUID) -> RepositorySnapshotData: ...
    async def add_relationship(
        self, relationship: NewPaperRepositoryRelationship
    ) -> PaperRepositoryRelationshipData: ...
    async def get_accepted_current_relationship(
        self,
    ) -> PaperRepositoryRelationshipData | None: ...
    async def get_relationship_exact(
        self, relationship_id: UUID
    ) -> PaperRepositoryRelationshipData | None: ...
    async def list_relationship_history(
        self,
    ) -> tuple[PaperRepositoryRelationshipData, ...]: ...
    async def mark_relationship_superseded(
        self, relationship_id: UUID, *, expected_version: int
    ) -> PaperRepositoryRelationshipData: ...
    async def add_discovery_run(
        self, run: NewRepositoryDiscoveryRun
    ) -> RepositoryDiscoveryRunData: ...
    async def get_discovery_run_exact(self, run_id: UUID) -> RepositoryDiscoveryRunData | None: ...
    async def get_successful_discovery_run(
        self,
        *,
        repository_snapshot_id: UUID,
        discovery_version: str,
        limit_fingerprint: str,
    ) -> RepositoryDiscoveryRunData | None: ...
    async def list_discovery_runs(
        self, repository_snapshot_id: UUID | None = None
    ) -> tuple[RepositoryDiscoveryRunData, ...]: ...

    async def finish_discovery_run(
        self,
        run_id: UUID,
        *,
        expected_status: str,
        status: str,
        coverage_status: str,
        configured_limits: dict[str, object],
        tree_hash: str | None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> RepositoryDiscoveryRunData: ...
    async def add_tree_entries(
        self, entries: tuple[NewRepositoryTreeEntry, ...]
    ) -> tuple[RepositoryTreeEntryData, ...]: ...
    async def list_tree_entries(
        self, discovery_run_id: UUID
    ) -> tuple[RepositoryTreeEntryData, ...]: ...
    async def add_components(
        self, components: tuple[NewRepositoryComponent, ...]
    ) -> tuple[RepositoryComponentData, ...]: ...
    async def list_discovery_components(
        self, discovery_run_id: UUID
    ) -> tuple[RepositoryComponentData, ...]: ...
    async def add_inspection_run(
        self, run: NewRepositoryInspectionRun
    ) -> RepositoryInspectionRunData: ...
    async def get_inspection_run_exact(
        self, run_id: UUID
    ) -> RepositoryInspectionRunData | None: ...
    async def get_successful_inspection_run(
        self,
        *,
        repository_snapshot_id: UUID,
        discovery_run_id: UUID,
        guidance_fingerprint: str,
        ranking_version: str,
        analysis_version: str,
    ) -> RepositoryInspectionRunData | None: ...
    async def list_inspection_runs(
        self, repository_snapshot_id: UUID | None = None
    ) -> tuple[RepositoryInspectionRunData, ...]: ...
    async def add_inspection_components(
        self, components: tuple[NewRepositoryInspectionComponent, ...]
    ) -> tuple[RepositoryComponentData, ...]: ...
    async def list_inspection_components(
        self, inspection_run_id: UUID
    ) -> tuple[RepositoryComponentData, ...]: ...
    async def get_component_exact(self, component_id: UUID) -> RepositoryComponentData | None: ...


class GenerationsRepository(ProjectScopedRepository, Protocol):
    async def add(self, record: NewGenerationRecord) -> GenerationRecordData: ...
    async def get_exact(self, record_id: UUID) -> GenerationRecordData | None: ...
    async def list_prompt_version_records(
        self, *, task_name: str, prompt_version: str
    ) -> tuple[GenerationRecordData, ...]: ...
    async def find_reusable_success(
        self,
        *,
        workflow_run_id: UUID,
        task_name: str,
        prompt_version: str,
        schema_version: str,
        provider: str,
        model_version: str,
        reasoning_version: str | None,
        input_fingerprint: str,
        model_configuration: dict[str, object],
    ) -> GenerationRecordData | None: ...
    async def find_reusable_copilot_success(
        self,
        *,
        copilot_turn_id: UUID,
        task_name: str,
        prompt_version: str,
        schema_version: str,
        provider: str,
        model_version: str,
        reasoning_version: str | None,
        input_fingerprint: str,
        model_configuration: dict[str, object],
    ) -> GenerationRecordData | None: ...
    async def list_for_workflow(
        self, workflow_run_id: UUID
    ) -> tuple[GenerationRecordData, ...]: ...
    async def list_for_copilot_turn(
        self, copilot_turn_id: UUID
    ) -> tuple[GenerationRecordData, ...]: ...


class KnowledgeRepository(ProjectScopedRepository, Protocol):
    async def add_candidate(self, candidate: NewKnowledgeCandidate) -> KnowledgeObjectData: ...
    async def get_exact(self, object_id: UUID) -> KnowledgeObjectData | None: ...
    async def get_exact_for_update(
        self, object_id: UUID, *, nowait: bool = False
    ) -> KnowledgeObjectData | None: ...
    async def get_payload(self, object_id: UUID) -> KnowledgeObjectPayloadData | None: ...
    async def list_review_items(
        self,
        *,
        review_status: str | None = None,
        object_type: str | None = None,
    ) -> tuple[KnowledgeObjectData, ...]: ...
    async def list_by_normalized_identity(
        self,
        *,
        object_type: str,
        statement_normalization_version: str,
        normalized_statement_hash: str,
    ) -> tuple[KnowledgeObjectData, ...]: ...
    async def list_active_dedup_candidates(
        self, *, object_type: str
    ) -> tuple[KnowledgeObjectData, ...]: ...
    async def list_accepted_current(self) -> tuple[KnowledgeObjectData, ...]: ...
    async def list_lineage(self, lineage_id: UUID) -> tuple[KnowledgeObjectData, ...]: ...
    async def list_lineage_for_update(
        self, lineage_id: UUID, *, nowait: bool = False
    ) -> tuple[KnowledgeObjectData, ...]: ...
    async def update_review_lifecycle(
        self,
        object_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> KnowledgeObjectData: ...
    async def add_revision(self, revision: NewKnowledgeRevision) -> KnowledgeObjectData: ...
    async def add_presentation(
        self, presentation: NewKnowledgePresentation
    ) -> KnowledgePresentationData: ...
    async def get_current_presentation_for_update(
        self, knowledge_object_id: UUID, *, nowait: bool = False
    ) -> KnowledgePresentationData | None: ...
    async def list_presentations(
        self, knowledge_object_id: UUID
    ) -> tuple[KnowledgePresentationData, ...]: ...
    async def add_evidence_link(self, link: NewEvidenceLink) -> EvidenceLinkData: ...
    async def find_evidence_link(
        self,
        *,
        knowledge_object_id: UUID,
        source_chunk_id: UUID | None,
        repository_component_id: UUID | None,
        evidence_relation: str,
        evidence_excerpt: str,
    ) -> EvidenceLinkData | None: ...
    async def list_evidence_links(
        self, knowledge_object_id: UUID
    ) -> tuple[EvidenceLinkData, ...]: ...
    async def get_evidence_link_exact(self, evidence_link_id: UUID) -> EvidenceLinkData | None: ...
    async def get_evidence_link_for_update(
        self, evidence_link_id: UUID, *, nowait: bool = False
    ) -> EvidenceLinkData | None: ...
    async def update_evidence_review_lifecycle(
        self,
        evidence_link_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
    ) -> EvidenceLinkData: ...
    async def add_grounding_assessment(
        self, assessment: NewGroundingAssessment
    ) -> GroundingAssessmentData: ...
    async def get_grounding_assessment(
        self, assessment_id: UUID
    ) -> GroundingAssessmentData | None: ...
    async def set_grounding_cache(
        self,
        *,
        knowledge_object_id: UUID,
        grounding_assessment_id: UUID,
        grounding_status: str,
    ) -> KnowledgeObjectData: ...
    async def update_ambiguity_resolution(
        self, object_id: UUID, *, expected_status: str, resolution_status: str
    ) -> KnowledgeObjectPayloadData: ...


class MethodModelsRepository(ProjectScopedRepository, Protocol):
    async def add_candidate_model(self, model: NewMethodModelCandidate) -> MethodModelData: ...
    async def add_candidate_element(
        self, element: NewMethodElementCandidate
    ) -> MethodElementData: ...
    async def add_member(self, member: NewMethodModelMember) -> MethodModelMemberData: ...
    async def add_object_link(self, link: NewMethodObjectLink) -> None: ...
    async def list_object_links(
        self, source_element_ids: tuple[UUID, ...]
    ) -> tuple[MethodObjectLinkData, ...]: ...
    async def update_object_link_review_lifecycle(
        self,
        object_link_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
    ) -> MethodObjectLinkData: ...
    async def add_evidence_link(self, link: NewMethodEvidenceLink) -> MethodEvidenceLinkData: ...
    async def list_evidence_links(self, element_id: UUID) -> tuple[MethodEvidenceLinkData, ...]: ...
    async def get_evidence_link_exact(
        self, evidence_link_id: UUID
    ) -> MethodEvidenceLinkData | None: ...
    async def get_evidence_link_for_update(
        self, evidence_link_id: UUID, *, nowait: bool = False
    ) -> MethodEvidenceLinkData | None: ...
    async def update_evidence_review_lifecycle(
        self,
        evidence_link_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
    ) -> MethodEvidenceLinkData: ...
    async def add_grounding_assessment(self, assessment: NewMethodGroundingAssessment) -> None: ...
    async def set_element_grounding_cache(
        self, *, element_id: UUID, assessment_id: UUID, grounding_status: str
    ) -> MethodElementData: ...
    async def add_model_assessment(
        self, assessment: NewMethodModelAssessment
    ) -> MethodModelAssessmentData: ...
    async def set_current_assessment(
        self, *, method_model_id: UUID, assessment_id: UUID
    ) -> MethodModelData: ...
    async def list_members(self, method_model_id: UUID) -> tuple[MethodModelMemberData, ...]: ...
    async def list_members_for_update(
        self, method_model_id: UUID, *, nowait: bool = False
    ) -> tuple[MethodModelMemberData, ...]: ...
    async def get_element_for_update(
        self, element_id: UUID, *, nowait: bool = False
    ) -> MethodElementData | None: ...
    async def latest_element_lineage_for_update(
        self, lineage_id: UUID
    ) -> MethodElementData | None: ...
    async def get_accepted_element_lineage_for_update(
        self, lineage_id: UUID
    ) -> MethodElementData | None: ...
    async def get_model_for_update(
        self, model_id: UUID, *, nowait: bool = False
    ) -> MethodModelData | None: ...
    async def latest_model_lineage_for_update(self, lineage_id: UUID) -> MethodModelData | None: ...
    async def update_element_review_lifecycle(
        self,
        element_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> MethodElementData: ...
    async def update_model_review_lifecycle(
        self,
        model_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> MethodModelData: ...
    async def add_section_review(
        self, review: NewMethodModelSectionReview
    ) -> MethodModelSectionReviewData: ...
    async def add_rendering(
        self, rendering: NewMethodModelRendering
    ) -> MethodModelRenderingData: ...
    async def list_current_section_reviews(
        self, method_model_id: UUID, *, for_update: bool = False
    ) -> tuple[MethodModelSectionReviewData, ...]: ...
    async def get_current_section_review_for_update(
        self, method_model_id: UUID, review_section: str
    ) -> MethodModelSectionReviewData | None: ...
    async def get_assessment_exact(
        self, assessment_id: UUID
    ) -> MethodModelAssessmentData | None: ...
    async def get_element_exact(self, element_id: UUID) -> MethodElementData | None: ...
    async def get_element_payload_exact(self, element_id: UUID) -> dict[str, object] | None: ...
    async def list_accepted_current_elements(self) -> tuple[MethodElementData, ...]: ...
    async def get_model_exact(self, model_id: UUID) -> MethodModelData | None: ...
    async def list_models_for_question(
        self, research_question_id: UUID
    ) -> tuple[MethodModelData, ...]: ...
    async def get_accepted_current_model(
        self, research_question_id: UUID
    ) -> MethodModelData | None: ...
    async def get_accepted_current_model_for_update(
        self, research_question_id: UUID, *, nowait: bool = False
    ) -> MethodModelData | None: ...


class MentalModelsRepository(ProjectScopedRepository, Protocol):
    async def add(self, model: NewMentalModel) -> MentalModelData: ...
    async def add_member(self, member: NewMentalModelMember) -> MentalModelMemberData: ...
    async def add_rendering(
        self, rendering: NewMentalModelRendering
    ) -> MentalModelRenderingData: ...
    async def get_exact(self, model_id: UUID) -> MentalModelData | None: ...
    async def get_exact_for_update(
        self, model_id: UUID, *, nowait: bool = False
    ) -> MentalModelData | None: ...
    async def get_current(self) -> MentalModelData | None: ...
    async def get_current_for_update(self, *, nowait: bool = False) -> MentalModelData | None: ...
    async def list_history(self) -> tuple[MentalModelData, ...]: ...
    async def get_by_generation_record(
        self, generation_record_id: UUID
    ) -> MentalModelData | None: ...
    async def list_members(self, model_id: UUID) -> tuple[MentalModelMemberData, ...]: ...
    async def list_renderings(self, model_id: UUID) -> tuple[MentalModelRenderingData, ...]: ...
    async def add_assessment(
        self, assessment: NewMentalModelAssessment
    ) -> MentalModelAssessmentData: ...
    async def get_assessment_exact(
        self, assessment_id: UUID
    ) -> MentalModelAssessmentData | None: ...
    async def get_assessment_by_generation(
        self, generation_record_id: UUID
    ) -> MentalModelAssessmentData | None: ...
    async def latest_assessment_for_model(
        self, model_id: UUID
    ) -> MentalModelAssessmentData | None: ...
    async def mark_superseded(self, model_id: UUID) -> MentalModelData: ...


class MappingsRepository(ProjectScopedRepository, Protocol):
    async def add(self, mapping: NewPaperCodeMapping) -> PaperCodeMappingData: ...
    async def get_exact(self, mapping_id: UUID) -> PaperCodeMappingData | None: ...
    async def get_exact_for_update(
        self, mapping_id: UUID, *, nowait: bool = False
    ) -> PaperCodeMappingData | None: ...
    async def list_lineage_for_update(
        self, lineage_id: UUID, *, nowait: bool = False
    ) -> tuple[PaperCodeMappingData, ...]: ...
    async def list_all(self) -> tuple[PaperCodeMappingData, ...]: ...
    async def list_by_generation_record(
        self, generation_record_id: UUID
    ) -> tuple[PaperCodeMappingData, ...]: ...
    async def find_candidate_duplicate(
        self,
        *,
        repository_inspection_run_id: UUID,
        paper_anchor_type: str,
        anchor_id: UUID,
        repository_component_id: UUID | None,
        mapping_type: str,
    ) -> PaperCodeMappingData | None: ...
    async def list_accepted_current(self) -> tuple[PaperCodeMappingData, ...]: ...
    async def update_review_lifecycle(
        self,
        mapping_id: UUID,
        *,
        expected_version: int,
        expected_review_status: str,
        expected_lifecycle_status: str,
        review_status: str,
        lifecycle_status: str,
        supersedes_id: UUID | None = None,
    ) -> PaperCodeMappingData: ...


class JudgmentsRepository(ProjectScopedRepository, Protocol):
    async def add_request(self, request: NewJudgmentRequest) -> JudgmentRequestData: ...
    async def add_option(self, option: NewJudgmentOption) -> JudgmentOptionData: ...
    async def get_request_exact(self, request_id: UUID) -> JudgmentRequestData | None: ...
    async def get_open_request_for_ambiguity(
        self, ambiguity_id: UUID
    ) -> JudgmentRequestData | None: ...
    async def list_requests(self) -> tuple[JudgmentRequestData, ...]: ...
    async def list_options(self, request_id: UUID) -> tuple[JudgmentOptionData, ...]: ...
    async def get_request_for_update(
        self, request_id: UUID, *, nowait: bool = False
    ) -> JudgmentRequestData | None: ...
    async def resolve_request(self, request_id: UUID) -> JudgmentRequestData: ...
    async def add_judgment(self, judgment: NewHumanJudgment) -> HumanJudgmentData: ...
    async def supersede_judgment(
        self, judgment_id: UUID, *, expected_version: int
    ) -> HumanJudgmentData: ...
    async def get_judgment_exact(self, judgment_id: UUID) -> HumanJudgmentData | None: ...
    async def get_current_for_request(self, request_id: UUID) -> HumanJudgmentData | None: ...
    async def list_current(self) -> tuple[HumanJudgmentData, ...]: ...
    async def list_for_request(self, request_id: UUID) -> tuple[HumanJudgmentData, ...]: ...


class BlueprintsRepository(ProjectScopedRepository, Protocol):
    async def add_eligibility(
        self, assessment: NewBlueprintEligibilityAssessment
    ) -> BlueprintEligibilityAssessmentData: ...
    async def add_eligibility_item(
        self, item: NewBlueprintEligibilityItem
    ) -> BlueprintEligibilityItemData: ...
    async def get_eligibility_by_input(
        self, *, input_fingerprint: str, eligibility_rule_version: str, reproduction_depth: str
    ) -> BlueprintEligibilityAssessmentData | None: ...
    async def get_eligibility_exact(
        self, assessment_id: UUID
    ) -> BlueprintEligibilityAssessmentData | None: ...
    async def get_eligibility_for_update(
        self, assessment_id: UUID, *, nowait: bool = False
    ) -> BlueprintEligibilityAssessmentData | None: ...
    async def list_eligibility_items(
        self, assessment_id: UUID
    ) -> tuple[BlueprintEligibilityItemData, ...]: ...
    async def list_eligibility_assessments(
        self,
    ) -> tuple[BlueprintEligibilityAssessmentData, ...]: ...
    async def get_exact(self, blueprint_id: UUID) -> ImplementationBlueprintData | None: ...
    async def get_exact_for_update(
        self, blueprint_id: UUID, *, nowait: bool = False
    ) -> ImplementationBlueprintData | None: ...
    async def get_by_eligibility(
        self, assessment_id: UUID
    ) -> ImplementationBlueprintData | None: ...
    async def add(self, blueprint: NewImplementationBlueprint) -> ImplementationBlueprintData: ...
    async def add_section(self, section: NewBlueprintSection) -> BlueprintSectionData: ...
    async def list_sections(self, blueprint_id: UUID) -> tuple[BlueprintSectionData, ...]: ...
    async def list_history(self) -> tuple[ImplementationBlueprintData, ...]: ...
    async def mark_superseded(
        self, blueprint_id: UUID, *, expected_status: str
    ) -> ImplementationBlueprintData: ...
    async def update_status(
        self, blueprint_id: UUID, *, expected_status: str, status: str
    ) -> ImplementationBlueprintData: ...
    async def update_section_review_statuses(
        self, blueprint_id: UUID, *, expected_status: str, status: str
    ) -> tuple[BlueprintSectionData, ...]: ...
    async def get_current(self) -> ImplementationBlueprintData | None: ...
    async def get_current_for_update(
        self, *, nowait: bool = False
    ) -> ImplementationBlueprintData | None: ...
    async def add_step(self, step: NewImplementationStep) -> ImplementationStepData: ...
    async def get_step_exact(self, step_id: UUID) -> ImplementationStepData | None: ...
    async def get_step_for_update(
        self, step_id: UUID, *, nowait: bool = False
    ) -> ImplementationStepData | None: ...
    async def list_steps(self, blueprint_id: UUID) -> tuple[ImplementationStepData, ...]: ...
    async def mark_step_superseded(
        self, step_id: UUID, *, expected_version: int
    ) -> ImplementationStepData: ...
    async def add_check(self, check: NewVerificationCheck) -> VerificationCheckData: ...
    async def get_check_exact(self, check_id: UUID) -> VerificationCheckData | None: ...
    async def get_check_for_update(
        self, check_id: UUID, *, nowait: bool = False
    ) -> VerificationCheckData | None: ...
    async def list_checks(self, blueprint_id: UUID) -> tuple[VerificationCheckData, ...]: ...
    async def record_check_result(
        self,
        check_id: UUID,
        *,
        expected_result_status: str | None,
        result_status: str,
        actual_result: str,
        result_actor_id: UUID,
    ) -> VerificationCheckData: ...


class WorkflowsRepository(ProjectScopedRepository, Protocol):
    async def add(self, workflow: NewWorkflowRun) -> WorkflowRunData: ...
    async def get_exact(self, workflow_id: UUID) -> WorkflowRunData | None: ...
    async def get_for_update(
        self, workflow_id: UUID, *, nowait: bool = False
    ) -> WorkflowRunData | None: ...
    async def get_active_full_analysis_for_update(self) -> WorkflowRunData | None: ...
    async def update_status(
        self,
        workflow_id: UUID,
        *,
        expected_lock_version: int,
        status: str,
        current_stage: str | None,
        error_code: str | None = None,
        error_detail: dict[str, object] | None = None,
    ) -> WorkflowRunData: ...
    async def update_progress(
        self,
        workflow_id: UUID,
        *,
        expected_lock_version: int,
        status: str,
        current_stage: str | None,
        current_method_model_id: UUID | None,
        current_mental_model_id: UUID | None,
        error_code: str | None = None,
        error_detail: dict[str, object] | None = None,
    ) -> WorkflowRunData: ...
    async def add_interrupt(self, interrupt: NewWorkflowInterrupt) -> WorkflowInterruptData: ...
    async def get_interrupt_exact(self, interrupt_id: UUID) -> WorkflowInterruptData | None: ...
    async def get_interrupt_for_update(
        self, interrupt_id: UUID, *, nowait: bool = False
    ) -> WorkflowInterruptData | None: ...
    async def list_interrupts(self, workflow_run_id: UUID) -> tuple[WorkflowInterruptData, ...]: ...
    async def update_interrupt(
        self,
        interrupt_id: UUID,
        *,
        expected_lock_version: int,
        status: str,
        resolution_event_id: UUID | None,
    ) -> WorkflowInterruptData: ...


class EventsRepository(ProjectScopedRepository, Protocol):
    async def append(self, event: NewOperationEvent) -> OperationEventData: ...
    async def get_exact(self, event_id: UUID) -> OperationEventData | None: ...
    async def get_by_idempotency_key(
        self, operation_type: str, idempotency_key: str
    ) -> OperationEventData | None: ...
    async def list_for_workflow(
        self, workflow_run_id: UUID, *, operation_type: str | None = None
    ) -> tuple[OperationEventData, ...]: ...
    async def list_for_knowledge_lineage(
        self, lineage_id: UUID
    ) -> tuple[OperationEventData, ...]: ...


class DependenciesRepository(ProjectScopedRepository, Protocol):
    async def append_dependency(
        self, dependency: ArtifactDependencyData
    ) -> ArtifactDependencyData: ...
    async def append_invalidation(
        self, invalidation: ArtifactInvalidationData
    ) -> ArtifactInvalidationData: ...
    async def list_dependencies(
        self, artifact_type: str, artifact_id: UUID
    ) -> tuple[ArtifactDependencyData, ...]: ...
    async def list_dependents(
        self, dependency_type: str, dependency_id: UUID
    ) -> tuple[ArtifactDependencyData, ...]: ...
    async def list_unresolved_invalidations(
        self, artifact_type: str, artifact_id: UUID
    ) -> tuple[ArtifactInvalidationData, ...]: ...
    async def resolve_invalidation(self, invalidation_id: UUID) -> ArtifactInvalidationData: ...
    async def set_freshness_projection(
        self, artifact_type: str, artifact_id: UUID, effective_status: str
    ) -> None: ...
    async def artifact_is_superseded(self, artifact_type: str, artifact_id: UUID) -> bool: ...
