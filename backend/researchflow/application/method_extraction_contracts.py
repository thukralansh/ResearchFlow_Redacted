"""Question-conditioned MethodModel extraction input and evidence validation."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.api.schemas.method_extraction import (
    MethodDataFlowCandidate,
    MethodEvidenceValidationBatch,
    MethodExtractionBatch,
    MethodMergeResult,
)
from researchflow.domain.method_validation import (
    validate_data_flow_graph,
)
from researchflow.errors import DomainValidationError
from researchflow.ports.providers import StructuredGenerationMessage

METHOD_EXTRACTION_SCHEMA_NAME = "method_extraction_batch"
METHOD_EXTRACTION_SCHEMA_VERSION: Final = "v3"
METHOD_EXTRACTION_TASK = "extract_method_model"
METHOD_EXTRACTION_PROMPT_VERSION: Final = "v3"
METHOD_MERGE_SCHEMA_NAME = "method_merge_result"
METHOD_MERGE_SCHEMA_VERSION: Final = "v3"
METHOD_MERGE_TASK = "merge_method_model"
METHOD_MERGE_PROMPT_VERSION: Final = "v3"
METHOD_EVIDENCE_VALIDATION_SCHEMA_NAME = "method_evidence_validation_batch"
METHOD_EVIDENCE_VALIDATION_SCHEMA_VERSION = "v1"
METHOD_EVIDENCE_VALIDATION_TASK = "validate_method_evidence"
METHOD_EVIDENCE_VALIDATION_PROMPT_VERSION = "v1"
METHOD_BATCHING_POLICY_VERSION: Final = "rf-method-batching-v1"
MAX_METHOD_EXTRACTION_CHUNKS: Final = 24
MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS: Final = 160_000
MAX_METHOD_AGGREGATE_ELEMENTS = 128


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodExtractionContext(_StrictModel):
    project_id: UUID
    primary_research_question_id: UUID
    research_question_version: Annotated[int, Field(ge=1)]
    research_question: Annotated[str, Field(min_length=1, max_length=8_000)]
    project_objective: Annotated[str, Field(min_length=1, max_length=8_000)]
    scope: Annotated[str, Field(min_length=1, max_length=8_000)] | None
    research_reproduction_depth: Annotated[str, Field(min_length=1, max_length=200)]
    question_source_relationship_id: UUID
    relationship_type: Literal["primary_paper"]
    relationship_rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    relevance_scope: Annotated[str, Field(min_length=1, max_length=4_000)] | None
    relationship_review_status: Literal["accepted"]
    relationship_lifecycle_status: Literal["current"]
    source_id: UUID
    source_processing_run_id: UUID


class MethodExtractionChunk(_StrictModel):
    id: UUID
    source_id: UUID
    processing_run_id: UUID
    page_number: Annotated[int, Field(ge=1)]
    section: Annotated[str, Field(min_length=1, max_length=500)] | None
    subsection: Annotated[str, Field(min_length=1, max_length=500)] | None
    start_offset: Annotated[int, Field(ge=0)]
    end_offset: Annotated[int, Field(gt=0)]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    text: Annotated[str, Field(min_length=1, max_length=20_000)]

    @model_validator(mode="after")
    def validate_offsets_and_hash(self) -> MethodExtractionChunk:
        if self.end_offset <= self.start_offset:
            raise ValueError("chunk_offsets_invalid")
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("chunk_offset_length_mismatch")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.content_hash:
            raise ValueError("chunk_hash_mismatch")
        return self


class MethodExtractionInput(_StrictModel):
    prompt_version: Literal["v1", "v2", "v3"]
    schema_name: Literal["method_extraction_batch"]
    schema_version: Literal["v1", "v2", "v3"]
    validation_policy_version: Literal["rf-method-extraction-validation-v1"]
    required_element_policy_version: Literal["rf-method-required-v1"]
    section_policy_version: Literal["rf-method-section-v1"]
    batching_policy_version: Literal["rf-method-batching-v1"]
    batch_chunk_limit: Literal[24]
    batch_context_codepoint_limit: Literal[160000]
    allow_data_flow_cycles: bool
    context: MethodExtractionContext
    chunks: Annotated[
        list[MethodExtractionChunk], Field(min_length=1, max_length=MAX_METHOD_EXTRACTION_CHUNKS)
    ]

    @model_validator(mode="after")
    def validate_exact_context(self) -> MethodExtractionInput:
        chunk_ids = [chunk.id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("duplicate_source_chunk")
        if any(
            chunk.source_id != self.context.source_id
            or chunk.processing_run_id != self.context.source_processing_run_id
            for chunk in self.chunks
        ):
            raise ValueError("chunk_context_mismatch")
        if sum(len(chunk.text) for chunk in self.chunks) > MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS:
            raise ValueError("method_extraction_context_too_large")
        return self

    def fingerprint(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json", exclude_none=False),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def messages(self) -> tuple[StructuredGenerationMessage, ...]:
        payload = self.model_dump(mode="json", exclude_none=False)
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return (StructuredGenerationMessage(role="user", content=content),)


class MethodMergeConstituent(_StrictModel):
    batch_sequence: Annotated[int, Field(ge=0)]
    generation_record_id: UUID
    generation_input_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    output: MethodExtractionBatch


class MethodMergeInput(_StrictModel):
    prompt_version: Literal["v1", "v2", "v3"]
    schema_name: Literal["method_merge_result"]
    schema_version: Literal["v1", "v2", "v3"]
    validation_policy_version: Literal["rf-method-extraction-validation-v1"]
    required_element_policy_version: Literal["rf-method-required-v1"]
    section_policy_version: Literal["rf-method-section-v1"]
    merge_policy_version: Literal["rf-method-merge-v1"]
    batching_policy_version: Literal["rf-method-batching-v1"]
    batch_chunk_limit: Literal[24]
    batch_context_codepoint_limit: Literal[160000]
    grounding_policy_version: Literal["rf-grounding-policy-v1"]
    assessment_policy_version: Literal["rf-method-assessment-policy-v1"]
    aggregate_element_limit: Literal[128]
    model_configuration: dict[str, object]
    context: MethodExtractionContext
    source_processing_run_id: UUID
    constituents: Annotated[list[MethodMergeConstituent], Field(min_length=2)]

    @model_validator(mode="after")
    def validate_constituent_identity(self) -> MethodMergeInput:
        if self.source_processing_run_id != self.context.source_processing_run_id:
            raise ValueError("merge_processing_run_mismatch")
        sequences = [item.batch_sequence for item in self.constituents]
        generation_ids = [item.generation_record_id for item in self.constituents]
        if sequences != list(range(len(sequences))):
            raise ValueError("merge_constituent_order_invalid")
        if len(generation_ids) != len(set(generation_ids)):
            raise ValueError("merge_constituent_generation_duplicate")
        return self

    def fingerprint(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json", exclude_none=False),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def messages(self) -> tuple[StructuredGenerationMessage, ...]:
        content = json.dumps(
            self.model_dump(mode="json", exclude_none=False),
            sort_keys=True,
            separators=(",", ":"),
        )
        return (StructuredGenerationMessage(role="user", content=content),)


def validate_method_merge(
    merge_input: MethodMergeInput,
    result: MethodMergeResult,
    *,
    chunks: tuple[MethodExtractionChunk, ...],
) -> MethodMergeResult:
    expected = {
        (item.generation_record_id, element.client_id)
        for item in merge_input.constituents
        for element in item.output.elements
    }
    accounted = {
        (item.constituent_generation_record_id, item.input_client_id)
        for item in result.accountability
    }
    if len(accounted) != len(result.accountability) or accounted != expected:
        raise DomainValidationError(details={"reason": "method_merge_accountability_invalid"})
    merged_ids = {element.client_id for element in result.elements}
    for item in result.accountability:
        if item.merged_client_id is not None and item.merged_client_id not in merged_ids:
            raise DomainValidationError(details={"reason": "method_merge_target_unresolved"})
    traced_merged_ids = {
        item.merged_client_id for item in result.accountability if item.merged_client_id is not None
    }
    if traced_merged_ids != merged_ids:
        raise DomainValidationError(details={"reason": "method_merge_output_unaccounted"})
    if len(result.elements) > MAX_METHOD_AGGREGATE_ELEMENTS:
        raise DomainValidationError(details={"reason": "method_merge_element_overflow"})
    allowed_evidence = {
        (reference.chunk_id, reference.excerpt, reference.relation, reference.confidence)
        for constituent in merge_input.constituents
        for element in constituent.output.elements
        for reference in element.evidence
    }
    if any(
        (reference.chunk_id, reference.excerpt, reference.relation, reference.confidence)
        not in allowed_evidence
        for element in result.elements
        for reference in element.evidence
    ):
        raise DomainValidationError(details={"reason": "method_merge_evidence_invented"})
    _validate_batch_against_chunks(result, chunks=chunks, allow_data_flow_cycles=False)
    return result


def validate_method_extraction_batch(
    extraction_input: MethodExtractionInput,
    batch: MethodExtractionBatch,
) -> MethodExtractionBatch:
    """Resolve evidence and graph identities against only the supplied bounded input."""
    _validate_batch_against_chunks(
        batch,
        chunks=tuple(extraction_input.chunks),
        allow_data_flow_cycles=extraction_input.allow_data_flow_cycles,
    )
    return batch


def _validate_batch_against_chunks(
    batch: MethodExtractionBatch,
    *,
    chunks: tuple[MethodExtractionChunk, ...],
    allow_data_flow_cycles: bool,
) -> None:
    chunks_by_id = {chunk.id: chunk for chunk in chunks}
    for item in (*batch.elements, *batch.rejections):
        identities: set[tuple[UUID, str, str]] = set()
        for reference in item.evidence:
            chunk = chunks_by_id.get(reference.chunk_id)
            if chunk is None:
                raise DomainValidationError(
                    details={"reason": "unsupported_chunk_id", "client_id": item.client_id}
                )
            if reference.excerpt not in chunk.text:
                raise DomainValidationError(
                    details={"reason": "excerpt_not_in_chunk", "client_id": item.client_id}
                )
            identity = (reference.chunk_id, reference.excerpt, reference.relation)
            if identity in identities:
                raise DomainValidationError(
                    details={
                        "reason": "duplicate_evidence_reference",
                        "client_id": item.client_id,
                    }
                )
            identities.add(identity)

    edges = tuple(
        (element.source_client_id, element.target_client_id)
        for element in batch.elements
        if isinstance(element, MethodDataFlowCandidate)
    )
    validate_data_flow_graph(edges, allow_cycles=allow_data_flow_cycles)


def validate_method_evidence_diagnostics(
    extraction_input: MethodExtractionInput,
    batch: MethodExtractionBatch,
    diagnostics: MethodEvidenceValidationBatch,
) -> MethodEvidenceValidationBatch:
    """Validate diagnostic identities without converting them into grounding decisions."""
    elements = {element.client_id for element in batch.elements}
    chunks = {chunk.id: chunk for chunk in extraction_input.chunks}
    proposed_references = {
        (element.client_id, reference.chunk_id, reference.excerpt, reference.relation)
        for element in batch.elements
        for reference in element.evidence
    }
    identities: set[tuple[str, UUID, str, str]] = set()
    for item in diagnostics.items:
        if item.element_client_id not in elements:
            raise DomainValidationError(details={"reason": "unknown_method_element_client_id"})
        chunk = chunks.get(item.chunk_id)
        if chunk is None:
            raise DomainValidationError(details={"reason": "unsupported_chunk_id"})
        if item.excerpt not in chunk.text:
            raise DomainValidationError(details={"reason": "excerpt_not_in_chunk"})
        identity = (item.element_client_id, item.chunk_id, item.excerpt, item.relation)
        if identity not in proposed_references:
            raise DomainValidationError(details={"reason": "unrequested_evidence_diagnostic"})
        if identity in identities:
            raise DomainValidationError(details={"reason": "duplicate_evidence_diagnostic"})
        identities.add(identity)
    return diagnostics
