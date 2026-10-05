"""Question-conditioned proposition extraction input and evidence validation."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.api.schemas.proposition_extraction import (
    PropositionCandidate,
    PropositionExtractionBatch,
    RejectedExtractionItem,
    SourceEvidenceReference,
)
from researchflow.domain.evidence_anchors import (
    ResolvedSourceEvidenceAnchor,
    resolve_source_evidence_anchor,
)
from researchflow.errors import DomainValidationError
from researchflow.ports.providers import StructuredGenerationMessage

PROPOSITION_EXTRACTION_SCHEMA_NAME = "proposition_extraction_batch"
PROPOSITION_EXTRACTION_SCHEMA_VERSION = "v3"
PROPOSITION_EXTRACTION_TASK = "extract_propositions"
PROPOSITION_EXTRACTION_PROMPT_VERSION = "v4"
MAX_EXTRACTION_CHUNKS = 24
MAX_EXTRACTION_CONTEXT_CODEPOINTS = 38_400
EXTRACTION_BATCHING_POLICY_VERSION = "rf-proposition-batching-v1"
EXTRACTION_SELECTION_POLICY_VERSION = "rf-source-chunk-selection-v1"
EXTRACTION_EVIDENCE_POLICY_VERSION = "rf-extracted-evidence-v3"
EXTRACTION_RELATIONSHIP_POLICY_VERSION = "rf-relationship-policy-v1"
EVIDENCE_ID_POLICY_VERSION = "rf-bounded-evidence-id-v1"
PROVIDER_SCHEMA_POLICY_VERSION = "rf-proposition-provider-schema-v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PropositionExtractionContext(_StrictModel):
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
    repository_context_included: bool = False


class PropositionExtractionChunk(_StrictModel):
    id: UUID
    evidence_id: Annotated[str, Field(pattern=r"^e[0-9]{3}$")]
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
    def validate_offsets_and_hash(self) -> PropositionExtractionChunk:
        if self.end_offset <= self.start_offset:
            raise ValueError("chunk_offsets_invalid")
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("chunk_offset_length_mismatch")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.content_hash:
            raise ValueError("chunk_hash_mismatch")
        return self


class PropositionExtractionInput(_StrictModel):
    prompt_version: Literal["v4"]
    schema_name: Literal["proposition_extraction_batch"]
    schema_version: Literal["v3"]
    statement_normalization_version: Literal["v1"]
    admission_policy_version: Literal["rf-proposition-admission-v1"]
    quality_policy_version: Literal["rf-proposition-quality-v1"]
    lexical_policy_version: Literal["rf-proposition-lexical-v1"]
    near_duplicate_threshold: Annotated[float, Field(gt=0, le=1)]
    batching_policy_version: Literal["rf-proposition-batching-v1"] = "rf-proposition-batching-v1"
    selection_policy_version: Literal["rf-source-chunk-selection-v1"] = (
        "rf-source-chunk-selection-v1"
    )
    evidence_policy_version: Literal["rf-extracted-evidence-v3"] = "rf-extracted-evidence-v3"
    evidence_id_policy_version: Literal["rf-bounded-evidence-id-v1"] = "rf-bounded-evidence-id-v1"
    source_anchor_policy_version: Literal["rf-source-evidence-anchor-v1"] = (
        "rf-source-evidence-anchor-v1"
    )
    relationship_policy_version: Literal["rf-relationship-policy-v1"] = "rf-relationship-policy-v1"
    batch_sequence: Annotated[int, Field(ge=0)] = 0
    selection_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] = "0" * 64
    context: PropositionExtractionContext
    chunks: Annotated[
        list[PropositionExtractionChunk], Field(min_length=1, max_length=MAX_EXTRACTION_CHUNKS)
    ]

    @model_validator(mode="after")
    def validate_exact_context(self) -> PropositionExtractionInput:
        chunk_ids = [chunk.id for chunk in self.chunks]
        evidence_ids = [chunk.evidence_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("duplicate_source_chunk")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate_evidence_id")
        if any(
            chunk.source_id != self.context.source_id
            or chunk.processing_run_id != self.context.source_processing_run_id
            for chunk in self.chunks
        ):
            raise ValueError("chunk_context_mismatch")
        if sum(len(chunk.text) for chunk in self.chunks) > MAX_EXTRACTION_CONTEXT_CODEPOINTS:
            raise ValueError("extraction_context_too_large")
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
        for chunk in payload["chunks"]:
            chunk.pop("id")
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return (StructuredGenerationMessage(role="user", content=content),)

    def provider_schema(self) -> dict[str, object]:
        """Bind selectable subtypes and evidence aliases to this exact context."""
        schema = deepcopy(PropositionExtractionBatch.model_json_schema())
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict):  # pragma: no cover - Pydantic invariant
            raise DomainValidationError(details={"reason": "provider_schema_invalid"})
        evidence = definitions.get("SourceEvidenceReference")
        gap = definitions.get("KnowledgeGapCandidate")
        ambiguity = definitions.get("AmbiguityCandidate")
        if (
            not isinstance(evidence, dict)
            or not isinstance(gap, dict)
            or not isinstance(ambiguity, dict)
        ):
            raise DomainValidationError(details={"reason": "provider_schema_invalid"})
        evidence_properties = evidence.get("properties")
        gap_properties = gap.get("properties")
        ambiguity_properties = ambiguity.get("properties")
        if (
            not isinstance(evidence_properties, dict)
            or not isinstance(gap_properties, dict)
            or not isinstance(ambiguity_properties, dict)
        ):
            raise DomainValidationError(details={"reason": "provider_schema_invalid"})
        evidence_id = evidence_properties.get("evidence_id")
        gap_type = gap_properties.get("gap_type")
        ambiguity_type = ambiguity_properties.get("ambiguity_type")
        if (
            not isinstance(evidence_id, dict)
            or not isinstance(gap_type, dict)
            or not isinstance(ambiguity_type, dict)
        ):
            raise DomainValidationError(details={"reason": "provider_schema_invalid"})
        evidence_id.pop("pattern", None)
        evidence_id["enum"] = [chunk.evidence_id for chunk in self.chunks]
        if not self.context.repository_context_included:
            allowed = gap_type.get("enum")
            if not isinstance(allowed, list):
                raise DomainValidationError(details={"reason": "provider_schema_invalid"})
            gap_type["enum"] = [item for item in allowed if item != "repository"]
            allowed_ambiguities = ambiguity_type.get("enum")
            if not isinstance(allowed_ambiguities, list):
                raise DomainValidationError(details={"reason": "provider_schema_invalid"})
            ambiguity_type["enum"] = [
                item for item in allowed_ambiguities if item != "paper_code_discrepancy"
            ]
        return schema


@dataclass(frozen=True, slots=True)
class ValidatedEvidenceAnchor:
    client_id: str
    evidence_id: str
    chunk_id: UUID
    relation: str
    anchor: ResolvedSourceEvidenceAnchor


@dataclass(frozen=True, slots=True)
class ValidatedPropositionBatch:
    batch: PropositionExtractionBatch
    anchors: tuple[ValidatedEvidenceAnchor, ...]

    def anchor_for(
        self, *, client_id: str, reference: SourceEvidenceReference
    ) -> ResolvedSourceEvidenceAnchor:
        return self._evidence_for(client_id=client_id, reference=reference).anchor

    def chunk_id_for(self, *, client_id: str, reference: SourceEvidenceReference) -> UUID:
        return self._evidence_for(client_id=client_id, reference=reference).chunk_id

    def _evidence_for(
        self, *, client_id: str, reference: SourceEvidenceReference
    ) -> ValidatedEvidenceAnchor:
        matches = tuple(
            item
            for item in self.anchors
            if item.client_id == client_id
            and item.evidence_id == reference.evidence_id
            and item.relation == reference.relation
            and item.anchor.excerpt == reference.excerpt
        )
        if len(matches) != 1:
            raise DomainValidationError(details={"reason": "evidence_anchor_lookup_invalid"})
        return matches[0]


def validate_proposition_batch(
    extraction_input: PropositionExtractionInput,
    batch: PropositionExtractionBatch,
) -> ValidatedPropositionBatch:
    """Resolve every proposed source reference against exactly supplied chunks."""
    chunks = {chunk.evidence_id: chunk for chunk in extraction_input.chunks}
    anchors: list[ValidatedEvidenceAnchor] = []
    canonical_candidates: list[PropositionCandidate] = []
    canonical_rejections: list[RejectedExtractionItem] = []
    for item in (*batch.candidates, *batch.rejections):
        identities: set[tuple[str, str, str]] = set()
        canonical_references: list[SourceEvidenceReference] = []
        for reference in item.evidence:
            chunk = chunks.get(reference.evidence_id)
            if chunk is None:
                raise DomainValidationError(
                    details={"reason": "unsupported_evidence_id", "client_id": item.client_id}
                )
            try:
                anchor = resolve_source_evidence_anchor(
                    chunk_text=chunk.text,
                    chunk_start_offset=chunk.start_offset,
                    proposed_excerpt=reference.excerpt,
                )
            except DomainValidationError as error:
                raise DomainValidationError(
                    details={
                        "reason": error.public_details.get("reason", "evidence_anchor_invalid"),
                        "client_id": item.client_id,
                    }
                ) from error
            identity = (reference.evidence_id, anchor.excerpt, reference.relation)
            if identity in identities:
                raise DomainValidationError(
                    details={"reason": "duplicate_evidence_reference", "client_id": item.client_id}
                )
            identities.add(identity)
            canonical_reference = reference.model_copy(update={"excerpt": anchor.excerpt})
            canonical_references.append(canonical_reference)
            anchors.append(
                ValidatedEvidenceAnchor(
                    client_id=str(item.client_id),
                    evidence_id=reference.evidence_id,
                    chunk_id=chunk.id,
                    relation=reference.relation,
                    anchor=anchor,
                )
            )
        canonical_item = item.model_copy(update={"evidence": canonical_references})
        if isinstance(item, RejectedExtractionItem):
            canonical_rejections.append(cast(RejectedExtractionItem, canonical_item))
        else:
            canonical_candidates.append(cast(PropositionCandidate, canonical_item))
    return ValidatedPropositionBatch(
        batch=batch.model_copy(
            update={
                "candidates": canonical_candidates,
                "rejections": canonical_rejections,
            }
        ),
        anchors=tuple(anchors),
    )
