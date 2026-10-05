"""Bounded proposition extraction, review-safe deduplication, and persistence."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from researchflow.api.schemas.proposition_extraction import (
    AmbiguityCandidate,
    AssumptionCandidate,
    ClaimCandidate,
    DuplicateComparisonResult,
    ImplementationRequirementCandidate,
    KnowledgeGapCandidate,
    PropositionCandidate,
    PropositionExtractionBatch,
)
from researchflow.application.deduplication import (
    DEDUPLICATION_POLICY_VERSION,
    DUPLICATE_COMPARISON_PROMPT_VERSION,
    DUPLICATE_COMPARISON_SCHEMA_NAME,
    DUPLICATE_COMPARISON_SCHEMA_VERSION,
    DUPLICATE_COMPARISON_TASK,
    MAX_STRUCTURED_COMPARISONS_PER_REQUEST,
    classify_exact_matches,
    comparison_fingerprint,
    comparison_request,
    shortlist_near_duplicates,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.application.grounding import assess_persisted_evidence
from researchflow.application.proposition_extraction_contracts import (
    EVIDENCE_ID_POLICY_VERSION,
    EXTRACTION_BATCHING_POLICY_VERSION,
    EXTRACTION_EVIDENCE_POLICY_VERSION,
    EXTRACTION_RELATIONSHIP_POLICY_VERSION,
    EXTRACTION_SELECTION_POLICY_VERSION,
    MAX_EXTRACTION_CHUNKS,
    MAX_EXTRACTION_CONTEXT_CODEPOINTS,
    PROPOSITION_EXTRACTION_PROMPT_VERSION,
    PROPOSITION_EXTRACTION_SCHEMA_NAME,
    PROPOSITION_EXTRACTION_SCHEMA_VERSION,
    PROPOSITION_EXTRACTION_TASK,
    PROVIDER_SCHEMA_POLICY_VERSION,
    PropositionExtractionChunk,
    PropositionExtractionContext,
    PropositionExtractionInput,
    ValidatedPropositionBatch,
    validate_proposition_batch,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.evidence import (
    EvidenceLinkRequest,
    EvidencePolicy,
    EvidenceSourceSelector,
    EvidenceTargetSelector,
)
from researchflow.domain.evidence_anchors import SOURCE_EVIDENCE_ANCHOR_POLICY_VERSION
from researchflow.domain.knowledge_catalog import (
    STATEMENT_NORMALIZATION_VERSION,
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
    statement_fingerprint,
)
from researchflow.domain.method_catalog import EvidenceRelation
from researchflow.domain.proposition_extraction import (
    ADMISSION_POLICY_VERSION,
    DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    LEXICAL_POLICY_VERSION,
    QUALITY_POLICY_VERSION,
)
from researchflow.domain.provenance import SourceChunkEvidenceProvenance
from researchflow.domain.relationships import EndpointType
from researchflow.errors import (
    DomainValidationError,
    ResearchFlowError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.providers import (
    JsonValue,
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
)
from researchflow.ports.repositories import (
    GenerationRecordData,
    KnowledgeObjectData,
    NewEvidenceLink,
    NewGroundingAssessment,
    NewKnowledgeCandidate,
    NewOperationEvent,
    SourceChunkData,
    SourceData,
    SourceProcessingRunData,
)
from researchflow.ports.unit_of_work import UnitOfWorkFactory

MAX_EXTRACTION_BATCHES_PER_REQUEST: Final = 16
EXTRACTION_CURSOR_VERSION: Final = "rf-extraction-cursor-v1"
EXTRACTION_PERSISTENCE_POLICY_VERSION: Final = "rf-extraction-persistence-v2"
EXTRACTED_EVIDENCE_MATERIALITY_POLICY_VERSION: Final = "rf-extracted-evidence-materiality-v1"
EXTRACTION_OPERATION: Final = "knowledge.extraction.batch.persist"
_IDENTITY_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/proposition-extraction"
)


@dataclass(frozen=True, slots=True)
class ExtractionSelection:
    page_from: int | None = None
    page_to: int | None = None
    sections: tuple[str, ...] = ()

    def identity(self) -> dict[str, object]:
        if self.page_from is not None and self.page_from < 1:
            raise DomainValidationError(details={"reason": "page_filter_invalid"})
        if self.page_to is not None and self.page_to < 1:
            raise DomainValidationError(details={"reason": "page_filter_invalid"})
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_from > self.page_to
        ):
            raise DomainValidationError(details={"reason": "page_filter_invalid"})
        sections = tuple(sorted({section.strip() for section in self.sections if section.strip()}))
        if len(sections) != len(self.sections):
            raise DomainValidationError(details={"reason": "section_filter_invalid"})
        return {
            "policy_version": EXTRACTION_SELECTION_POLICY_VERSION,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "sections": sections,
        }


@dataclass(frozen=True, slots=True)
class ExtractPropositionsCommand:
    project_id: UUID
    workflow_run_id: UUID
    source_processing_run_id: UUID
    selection: ExtractionSelection
    model_policy: StructuredGenerationModelPolicy
    max_batches: int = MAX_EXTRACTION_BATCHES_PER_REQUEST
    continuation_cursor: str | None = None
    explicit_rerun: bool = False
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateExtractionView:
    client_id: str
    object_type: str
    knowledge_object_id: UUID
    disposition: str
    evidence_link_ids: tuple[UUID, ...]
    grounding_status: str
    warnings: tuple[str, ...]

    def json_value(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "object_type": self.object_type,
            "knowledge_object_id": str(self.knowledge_object_id),
            "disposition": self.disposition,
            "evidence_link_ids": [str(item) for item in self.evidence_link_ids],
            "grounding_status": self.grounding_status,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ExtractionBatchView:
    batch_sequence: int
    chunk_ids: tuple[UUID, ...]
    status: str
    generation_record_id: UUID | None
    comparison_generation_record_ids: tuple[UUID, ...]
    candidates: tuple[CandidateExtractionView, ...]
    rejected_diagnostics: tuple[dict[str, object], ...]
    dedup_diagnostics: tuple[dict[str, object], ...]
    error_code: str | None = None
    error_detail: dict[str, object] | None = None

    def json_value(self) -> dict[str, object]:
        return {
            "batch_sequence": self.batch_sequence,
            "chunk_ids": [str(item) for item in self.chunk_ids],
            "status": self.status,
            "generation_record_id": (
                None if self.generation_record_id is None else str(self.generation_record_id)
            ),
            "comparison_generation_record_ids": [
                str(item) for item in self.comparison_generation_record_ids
            ],
            "candidates": [item.json_value() for item in self.candidates],
            "rejected_diagnostics": list(self.rejected_diagnostics),
            "dedup_diagnostics": list(self.dedup_diagnostics),
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }

    @classmethod
    def from_json_value(cls, value: dict[str, object]) -> ExtractionBatchView:
        raw_candidates = value.get("candidates", [])
        raw_sequence = value.get("batch_sequence")
        raw_chunk_ids = value.get("chunk_ids")
        raw_comparison_ids = value.get("comparison_generation_record_ids")
        raw_rejections = value.get("rejected_diagnostics")
        raw_dedup = value.get("dedup_diagnostics")
        raw_error_detail = value.get("error_detail")
        if (
            not isinstance(raw_candidates, list)
            or not isinstance(raw_sequence, int)
            or not isinstance(raw_chunk_ids, list)
            or not isinstance(raw_comparison_ids, list)
            or not isinstance(raw_rejections, list)
            or not isinstance(raw_dedup, list)
        ):
            raise DomainValidationError(details={"reason": "extraction_event_invalid"})
        candidates = tuple(
            CandidateExtractionView(
                client_id=str(item["client_id"]),
                object_type=str(item["object_type"]),
                knowledge_object_id=UUID(str(item["knowledge_object_id"])),
                disposition=str(item["disposition"]),
                evidence_link_ids=tuple(UUID(str(link)) for link in item["evidence_link_ids"]),
                grounding_status=str(item["grounding_status"]),
                warnings=tuple(str(warning) for warning in item["warnings"]),
            )
            for item in raw_candidates
            if isinstance(item, dict)
        )
        return cls(
            batch_sequence=raw_sequence,
            chunk_ids=tuple(UUID(str(item)) for item in raw_chunk_ids),
            status=str(value["status"]),
            generation_record_id=(
                None
                if value.get("generation_record_id") is None
                else UUID(str(value["generation_record_id"]))
            ),
            comparison_generation_record_ids=tuple(UUID(str(item)) for item in raw_comparison_ids),
            candidates=candidates,
            rejected_diagnostics=tuple(
                dict(item) for item in raw_rejections if isinstance(item, dict)
            ),
            dedup_diagnostics=tuple(dict(item) for item in raw_dedup if isinstance(item, dict)),
            error_code=None if value.get("error_code") is None else str(value["error_code"]),
            error_detail=(
                None if not isinstance(raw_error_detail, dict) else dict(raw_error_detail)
            ),
        )


@dataclass(frozen=True, slots=True)
class ExtractionRequestView:
    workflow_run_id: UUID
    source_processing_run_id: UUID
    status: str
    complete: bool
    eligible_chunk_count: int
    batches: tuple[ExtractionBatchView, ...]
    continuation_cursor: str | None

    def json_value(self) -> dict[str, object]:
        return {
            "workflow_run_id": str(self.workflow_run_id),
            "source_processing_run_id": str(self.source_processing_run_id),
            "status": self.status,
            "complete": self.complete,
            "eligible_chunk_count": self.eligible_chunk_count,
            "batches": [item.json_value() for item in self.batches],
            "continuation_cursor": self.continuation_cursor,
        }

    @classmethod
    def from_json_value(cls, value: dict[str, object]) -> ExtractionRequestView:
        raw_batches = value.get("batches")
        if not isinstance(raw_batches, list):
            raise DomainValidationError(details={"reason": "extraction_execution_event_invalid"})
        return cls(
            workflow_run_id=UUID(str(value["workflow_run_id"])),
            source_processing_run_id=UUID(str(value["source_processing_run_id"])),
            status=str(value["status"]),
            complete=bool(value["complete"]),
            eligible_chunk_count=int(str(value["eligible_chunk_count"])),
            batches=tuple(
                ExtractionBatchView.from_json_value(item)
                for item in raw_batches
                if isinstance(item, dict)
            ),
            continuation_cursor=(
                None
                if value.get("continuation_cursor") is None
                else str(value["continuation_cursor"])
            ),
        )


@dataclass(frozen=True, slots=True)
class ExtractionGenerationAttemptView:
    id: UUID
    task_name: str
    input_fingerprint: str
    status: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class _DedupDecision:
    candidate: PropositionCandidate
    disposition: str
    target: KnowledgeObjectData | None
    warnings: tuple[str, ...]
    diagnostic: dict[str, object]
    comparison_generation_record_id: UUID | None = None


class ExtractionService:
    """Own provider-free DB boundaries and one atomic persistence transaction per batch."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        generation_service: StructuredGenerationService,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def extract(self, command: ExtractPropositionsCommand) -> ExtractionRequestView:
        if not 1 <= command.max_batches <= MAX_EXTRACTION_BATCHES_PER_REQUEST:
            raise DomainValidationError(details={"reason": "max_batches_invalid"})
        context, source, run, eligible, selection_fingerprint = await self._load_context(command)
        start_index = _cursor_start(
            command.continuation_cursor,
            eligible=eligible,
            selection_fingerprint=selection_fingerprint,
        )
        planned = plan_extraction_batches(eligible)
        start_batch_sequence = next(
            index for index, batch in enumerate(planned) if batch[0].id == eligible[start_index].id
        )
        selected_batches = planned[
            start_batch_sequence : start_batch_sequence + command.max_batches
        ]
        batch_views: list[ExtractionBatchView] = []
        semantic_comparisons_used = 0
        earliest_unfinished_index: int | None = None
        cursor_index = start_index
        stop_after_transaction_failure = False

        for relative_sequence, chunks in enumerate(selected_batches):
            batch_sequence = start_batch_sequence + relative_sequence
            extraction_input = _contract_input(
                context=context,
                source=source,
                run=run,
                chunks=chunks,
                batch_sequence=batch_sequence,
                selection_fingerprint=selection_fingerprint,
            )
            try:
                generated = await self._generation.generate_structured(
                    GenerateStructuredCommand(
                        project_id=command.project_id,
                        workflow_run_id=command.workflow_run_id,
                        task_name=PROPOSITION_EXTRACTION_TASK,
                        input_fingerprint=extraction_input.fingerprint(),
                        prompt_version=PROPOSITION_EXTRACTION_PROMPT_VERSION,
                        schema_name=PROPOSITION_EXTRACTION_SCHEMA_NAME,
                        schema_version=PROPOSITION_EXTRACTION_SCHEMA_VERSION,
                        output_type=PropositionExtractionBatch,
                        messages=extraction_input.messages(),
                        model_policy=command.model_policy,
                        explicit_rerun=command.explicit_rerun,
                        provider_json_schema=cast(
                            dict[str, JsonValue], extraction_input.provider_schema()
                        ),
                        schema_variant_policy_version=PROVIDER_SCHEMA_POLICY_VERSION,
                        defer_application_domain_validation=True,
                    )
                )
            except ResearchFlowError as error:
                generation_id = await self._latest_generation_id(
                    command,
                    task_name=PROPOSITION_EXTRACTION_TASK,
                    input_fingerprint=extraction_input.fingerprint(),
                )
                batch_views.append(
                    ExtractionBatchView(
                        batch_sequence=batch_sequence,
                        chunk_ids=tuple(chunk.id for chunk in chunks),
                        status="provider_failed",
                        generation_record_id=generation_id,
                        comparison_generation_record_ids=(),
                        candidates=(),
                        rejected_diagnostics=(),
                        dedup_diagnostics=(),
                        error_code=error.code.value,
                        error_detail=dict(error.public_details),
                    )
                )
                if earliest_unfinished_index is None:
                    earliest_unfinished_index = cursor_index
                cursor_index += len(chunks)
                continue

            try:
                batch = PropositionExtractionBatch.model_validate_json(
                    generated.value.model_dump_json(),
                    strict=True,
                    context={
                        "repository_context_included": (
                            extraction_input.context.repository_context_included
                        )
                    },
                )
                validated_batch = validate_proposition_batch(extraction_input, batch)
                batch = validated_batch.batch
            except ValidationError as validation_error:
                issue_types = sorted({item["type"] for item in validation_error.errors()})
                domain_error = DomainValidationError(
                    details={
                        "reason": "proposition_domain_validation_failed",
                        "issue_count": validation_error.error_count(),
                        "issue_types": ",".join(issue_types),
                    }
                )
                batch_views.append(
                    ExtractionBatchView(
                        batch_sequence=batch_sequence,
                        chunk_ids=tuple(chunk.id for chunk in chunks),
                        status="validation_failed",
                        generation_record_id=generated.generation_record.id,
                        comparison_generation_record_ids=(),
                        candidates=(),
                        rejected_diagnostics=(),
                        dedup_diagnostics=(),
                        error_code=domain_error.code.value,
                        error_detail=dict(domain_error.public_details),
                    )
                )
                if earliest_unfinished_index is None:
                    earliest_unfinished_index = cursor_index
                cursor_index += len(chunks)
                continue
            except ResearchFlowError as error:
                batch_views.append(
                    ExtractionBatchView(
                        batch_sequence=batch_sequence,
                        chunk_ids=tuple(chunk.id for chunk in chunks),
                        status="validation_failed",
                        generation_record_id=generated.generation_record.id,
                        comparison_generation_record_ids=(),
                        candidates=(),
                        rejected_diagnostics=(),
                        dedup_diagnostics=(),
                        error_code=error.code.value,
                        error_detail=dict(error.public_details),
                    )
                )
                if earliest_unfinished_index is None:
                    earliest_unfinished_index = cursor_index
                cursor_index += len(chunks)
                continue

            decisions: list[_DedupDecision] = []
            for candidate in batch.candidates:
                decision, used = await self._deduplicate(
                    command,
                    candidate,
                    remaining_comparisons=(
                        MAX_STRUCTURED_COMPARISONS_PER_REQUEST - semantic_comparisons_used
                    ),
                )
                semantic_comparisons_used += used
                decisions.append(
                    replace(
                        decision,
                        warnings=(*candidate.quality_warnings(), *decision.warnings),
                    )
                )
            try:
                view = await self._persist_batch(
                    command=command,
                    context=context,
                    source=source,
                    run=run,
                    chunks=chunks,
                    generated=generated.generation_record,
                    batch=batch,
                    validated_batch=validated_batch,
                    decisions=tuple(decisions),
                    batch_sequence=batch_sequence,
                )
                batch_views.append(view)
            except ResearchFlowError as error:
                batch_views.append(
                    ExtractionBatchView(
                        batch_sequence=batch_sequence,
                        chunk_ids=tuple(chunk.id for chunk in chunks),
                        status="persistence_failed",
                        generation_record_id=generated.generation_record.id,
                        comparison_generation_record_ids=tuple(
                            item.comparison_generation_record_id
                            for item in decisions
                            if item.comparison_generation_record_id is not None
                        ),
                        candidates=(),
                        rejected_diagnostics=tuple(
                            item.model_dump(mode="json") for item in batch.rejections
                        ),
                        dedup_diagnostics=(
                            *(item.diagnostic for item in decisions),
                            {
                                "batch_error": error.code.value,
                                "details": dict(error.public_details),
                            },
                        ),
                        error_code=error.code.value,
                        error_detail=dict(error.public_details),
                    )
                )
                if earliest_unfinished_index is None:
                    earliest_unfinished_index = cursor_index
                stop_after_transaction_failure = True
            cursor_index += len(chunks)
            if stop_after_transaction_failure:
                break

        if earliest_unfinished_index is None and cursor_index < len(eligible):
            earliest_unfinished_index = cursor_index
        complete = earliest_unfinished_index is None
        if earliest_unfinished_index is None:
            continuation = None
        else:
            continuation = _encode_cursor(
                selection_fingerprint=selection_fingerprint,
                next_index=earliest_unfinished_index,
                next_chunk_id=eligible[earliest_unfinished_index].id,
            )
        succeeded = sum(view.status == "succeeded" for view in batch_views)
        has_diagnostics = any(
            view.rejected_diagnostics or view.dedup_diagnostics for view in batch_views
        )
        status = (
            "failed"
            if batch_views and not succeeded
            else "partial"
            if not complete
            else "completed_with_diagnostics"
            if has_diagnostics
            else "completed"
        )
        return ExtractionRequestView(
            workflow_run_id=command.workflow_run_id,
            source_processing_run_id=command.source_processing_run_id,
            status=status,
            complete=complete,
            eligible_chunk_count=len(eligible),
            batches=tuple(batch_views),
            continuation_cursor=continuation,
        )

    async def list_batches(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> tuple[ExtractionBatchView, ...]:
        async with self._uow_factory(project_id) as uow:
            if await uow.projects.get() is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            if await uow.workflows.get_exact(workflow_run_id) is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            events = await uow.events.list_for_workflow(
                workflow_run_id, operation_type=EXTRACTION_OPERATION
            )
            await uow.rollback()
        views: list[ExtractionBatchView] = []
        for event in events:
            if len(event.output_refs) != 1:
                raise DomainValidationError(details={"reason": "extraction_event_invalid"})
            value = event.output_refs[0].get("batch")
            if not isinstance(value, dict):
                raise DomainValidationError(details={"reason": "extraction_event_invalid"})
            views.append(ExtractionBatchView.from_json_value(value))
        return tuple(views)

    async def list_generation_attempts(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> tuple[ExtractionGenerationAttemptView, ...]:
        async with self._uow_factory(project_id) as uow:
            if await uow.projects.get() is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            if await uow.workflows.get_exact(workflow_run_id) is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            records = await uow.generations.list_for_workflow(workflow_run_id)
            await uow.rollback()
        return tuple(
            ExtractionGenerationAttemptView(
                id=record.id,
                task_name=record.task_name,
                input_fingerprint=record.input_fingerprint,
                status=record.status,
                error_code=record.error_code,
            )
            for record in records
            if record.task_name in {PROPOSITION_EXTRACTION_TASK, DUPLICATE_COMPARISON_TASK}
        )

    async def _load_context(
        self, command: ExtractPropositionsCommand
    ) -> tuple[
        PropositionExtractionContext,
        SourceData,
        SourceProcessingRunData,
        tuple[SourceChunkData, ...],
        str,
    ]:
        selection_identity = command.selection.identity()
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            if project is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            question = await uow.research_questions.get_current_primary()
            if question is None or question.id != project.primary_research_question_id:
                raise ResourceConflictError(details={"reason": "primary_question_not_current"})
            workflow = await uow.workflows.get_exact(command.workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            if workflow.primary_research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "workflow_question_mismatch"})
            relationship = await uow.sources.get_current_primary_paper()
            if relationship is None or relationship.research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "accepted_primary_paper_missing"})
            source = await uow.sources.get_exact(relationship.source_id)
            if source is None:
                raise ResourceNotFoundError(details={"resource": "source"})
            run = await uow.sources.get_processing_run_exact(command.source_processing_run_id)
            if run is None or run.source_id != source.id:
                raise ResourceNotFoundError(details={"resource": "source_processing_run"})
            if run.status != "succeeded" or source.content_hash != run.source_hash:
                raise ResourceConflictError(details={"reason": "processing_run_not_eligible"})
            chunks = await uow.sources.list_chunks(run.id)
            await uow.rollback()
        ordered = tuple(sorted(chunks, key=lambda item: (item.chunk_index, str(item.id))))
        eligible = tuple(
            chunk
            for chunk in ordered
            if (
                command.selection.page_from is None
                or chunk.page_number >= command.selection.page_from
            )
            and (
                command.selection.page_to is None or chunk.page_number <= command.selection.page_to
            )
            and (not command.selection.sections or chunk.section in set(command.selection.sections))
        )
        if not eligible:
            raise DomainValidationError(details={"reason": "no_eligible_source_chunks"})
        context = PropositionExtractionContext(
            project_id=project.id,
            primary_research_question_id=question.id,
            research_question_version=question.version,
            research_question=question.question_text,
            project_objective=project.target_outcome,
            scope=question.scope_text,
            research_reproduction_depth=project.reproduction_depth,
            question_source_relationship_id=relationship.id,
            relationship_type="primary_paper",
            relationship_rationale=relationship.rationale,
            relevance_scope=relationship.relevance_scope,
            relationship_review_status="accepted",
            relationship_lifecycle_status="current",
            source_id=source.id,
            source_processing_run_id=run.id,
        )
        selection_fingerprint = _fingerprint(
            {
                "selection": selection_identity,
                "context": context.model_dump(mode="json"),
                "eligible_chunks": [
                    {
                        "id": str(chunk.id),
                        "chunk_index": chunk.chunk_index,
                        "content_hash": chunk.content_hash,
                    }
                    for chunk in eligible
                ],
                "policy": _policy_identity(command.model_policy),
            }
        )
        return context, source, run, eligible, selection_fingerprint

    async def _deduplicate(
        self,
        command: ExtractPropositionsCommand,
        candidate: PropositionCandidate,
        *,
        remaining_comparisons: int,
    ) -> tuple[_DedupDecision, int]:
        object_type = str(candidate.object_type)
        statement = str(candidate.statement)
        normalized_hash = statement_fingerprint(statement)
        async with self._uow_factory(command.project_id) as uow:
            exact = await uow.knowledge.list_by_normalized_identity(
                object_type=object_type,
                statement_normalization_version=STATEMENT_NORMALIZATION_VERSION,
                normalized_statement_hash=normalized_hash,
            )
            active = await uow.knowledge.list_active_dedup_candidates(object_type=object_type)
            await uow.rollback()
        exact_result = classify_exact_matches(exact)
        historical_ids = [str(item.id) for item in exact_result.historical_matches]
        if exact_result.active_target is not None:
            target = exact_result.active_target
            return (
                _DedupDecision(
                    candidate=candidate,
                    disposition="exact_suppressed",
                    target=target,
                    warnings=(),
                    diagnostic=_dedup_diagnostic(
                        candidate, target, "exact", historical_ids=historical_ids
                    ),
                ),
                0,
            )
        shortlist = shortlist_near_duplicates(statement, active)
        if not shortlist:
            return (
                _DedupDecision(
                    candidate=candidate,
                    disposition="persisted",
                    target=None,
                    warnings=(),
                    diagnostic=_dedup_diagnostic(
                        candidate, None, "distinct", historical_ids=historical_ids
                    ),
                ),
                0,
            )
        if remaining_comparisons <= 0:
            return (
                _DedupDecision(
                    candidate=candidate,
                    disposition="persisted",
                    target=None,
                    warnings=("semantic_comparison_budget_exhausted",),
                    diagnostic=_dedup_diagnostic(
                        candidate,
                        None,
                        "comparison_budget_exhausted",
                        historical_ids=historical_ids,
                    ),
                ),
                0,
            )
        request = comparison_request(
            client_id=str(candidate.client_id),
            statement=statement,
            candidates=shortlist,
        )
        fingerprint = comparison_fingerprint(request)
        try:
            outcome = await self._generation.generate_structured(
                GenerateStructuredCommand(
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    task_name=DUPLICATE_COMPARISON_TASK,
                    input_fingerprint=fingerprint,
                    prompt_version=DUPLICATE_COMPARISON_PROMPT_VERSION,
                    schema_name=DUPLICATE_COMPARISON_SCHEMA_NAME,
                    schema_version=DUPLICATE_COMPARISON_SCHEMA_VERSION,
                    output_type=DuplicateComparisonResult,
                    messages=(
                        StructuredGenerationMessage(
                            role="user",
                            content=json.dumps(
                                request.model_dump(mode="json"),
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    ),
                    model_policy=command.model_policy,
                    explicit_rerun=command.explicit_rerun,
                )
            )
            result = outcome.value
            valid_ids = {item.object_id for item in shortlist}
            if (
                result.proposed_client_id != candidate.client_id
                or result.compared_object_id not in valid_ids
            ):
                raise DomainValidationError(
                    details={"reason": "duplicate_comparison_reference_invalid"}
                )
            target = next(item for item in active if item.id == result.compared_object_id)
            if result.decision == "same_meaning":
                disposition = "semantic_suppressed"
                persisted_target: KnowledgeObjectData | None = target
                warnings: tuple[str, ...] = ()
            elif result.decision == "materially_distinct":
                disposition = "persisted"
                persisted_target = None
                warnings = ()
            else:
                disposition = "persisted"
                persisted_target = None
                warnings = ("semantic_comparison_uncertain",)
            return (
                _DedupDecision(
                    candidate=candidate,
                    disposition=disposition,
                    target=persisted_target,
                    warnings=warnings,
                    diagnostic={
                        **_dedup_diagnostic(
                            candidate,
                            persisted_target,
                            result.decision,
                            historical_ids=historical_ids,
                        ),
                        "compared_object_id": str(result.compared_object_id),
                        "rationale": result.rationale,
                        "material_difference": result.material_difference,
                    },
                    comparison_generation_record_id=outcome.generation_record.id,
                ),
                1,
            )
        except ResearchFlowError as error:
            return (
                _DedupDecision(
                    candidate=candidate,
                    disposition="persisted",
                    target=None,
                    warnings=("semantic_comparison_failed",),
                    diagnostic={
                        **_dedup_diagnostic(
                            candidate,
                            None,
                            "comparison_failed",
                            historical_ids=historical_ids,
                        ),
                        "error_code": error.code.value,
                    },
                ),
                1,
            )

    async def _persist_batch(
        self,
        *,
        command: ExtractPropositionsCommand,
        context: PropositionExtractionContext,
        source: SourceData,
        run: SourceProcessingRunData,
        chunks: tuple[SourceChunkData, ...],
        generated: GenerationRecordData,
        batch: PropositionExtractionBatch,
        validated_batch: ValidatedPropositionBatch,
        decisions: tuple[_DedupDecision, ...],
        batch_sequence: int,
    ) -> ExtractionBatchView:
        idempotency_key = _fingerprint(
            {
                "policy_version": EXTRACTION_PERSISTENCE_POLICY_VERSION,
                "generation_record_id": str(generated.id),
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            existing = await uow.events.get_by_idempotency_key(
                EXTRACTION_OPERATION, idempotency_key
            )
            if existing is not None:
                await uow.rollback()
                value = existing.output_refs[0].get("batch") if existing.output_refs else None
                if not isinstance(value, dict):
                    raise DomainValidationError(details={"reason": "extraction_event_invalid"})
                return ExtractionBatchView.from_json_value(value)
            chunk_by_id = {chunk.id: chunk for chunk in chunks}
            candidate_views: list[CandidateExtractionView] = []
            for decision in decisions:
                candidate = decision.candidate
                target = decision.target
                if target is None:
                    object_id = uuid5(
                        _IDENTITY_NAMESPACE,
                        f"candidate:{generated.id}:{candidate.client_id}",
                    )
                    target = await uow.knowledge.add_candidate(
                        _new_candidate(
                            object_id=object_id,
                            project_id=command.project_id,
                            generation_record_id=generated.id,
                            candidate=candidate,
                        )
                    )
                else:
                    current = await uow.knowledge.get_exact(target.id)
                    if (
                        current is None
                        or current.lifecycle_status != "current"
                        or current.review_status not in {"candidate", "needs_revision", "accepted"}
                    ):
                        raise ResourceConflictError(
                            details={"reason": "dedup_target_no_longer_eligible"}
                        )
                    target = current
                evidence_ids: list[UUID] = []
                evidence_added = False
                for reference in candidate.evidence:
                    chunk = chunk_by_id[
                        validated_batch.chunk_id_for(
                            client_id=str(candidate.client_id), reference=reference
                        )
                    ]
                    anchor = validated_batch.anchor_for(
                        client_id=str(candidate.client_id), reference=reference
                    )
                    existing_link = await uow.knowledge.find_evidence_link(
                        knowledge_object_id=target.id,
                        source_chunk_id=chunk.id,
                        repository_component_id=None,
                        evidence_relation=reference.relation,
                        evidence_excerpt=reference.excerpt,
                    )
                    if existing_link is not None:
                        evidence_ids.append(existing_link.id)
                        continue
                    link_id = uuid5(
                        _IDENTITY_NAMESPACE,
                        "evidence:"
                        f"{generated.id}:{target.id}:{chunk.id}:{reference.relation}:"
                        f"{hashlib.sha256(reference.excerpt.encode()).hexdigest()}",
                    )
                    validated = EvidencePolicy.validate(
                        EvidenceLinkRequest(
                            id=link_id,
                            lineage_id=link_id,
                            version=1,
                            target=EvidenceTargetSelector(
                                project_id=command.project_id,
                                target_type=EndpointType(candidate.object_type),
                                target_version=target.version,
                                knowledge_object_id=target.id,
                            ),
                            source=EvidenceSourceSelector(
                                provenance=SourceChunkEvidenceProvenance(
                                    project_id=command.project_id,
                                    source_id=source.id,
                                    processing_run_id=run.id,
                                    source_hash=run.source_hash,
                                    parser_name=run.parser_name,
                                    parser_version=run.parser_version,
                                    chunker_name=run.chunker_name,
                                    chunker_version=run.chunker_version,
                                    normalization_version=run.normalization_version,
                                    chunk_id=chunk.id,
                                    chunk_content_hash=chunk.content_hash,
                                    chunk_text=chunk.chunk_text,
                                    current=True,
                                ),
                                source_chunk_id=chunk.id,
                            ),
                            relation=EvidenceRelation(reference.relation),
                            excerpt=reference.excerpt,
                            confidence=(
                                None
                                if reference.confidence is None
                                else Decimal(str(reference.confidence))
                            ),
                            review_status=KnowledgeReviewStatus.CANDIDATE,
                            lifecycle_status=KnowledgeLifecycleStatus.CURRENT,
                            material=True,
                            materiality_policy_version=(
                                EXTRACTED_EVIDENCE_MATERIALITY_POLICY_VERSION
                            ),
                            objective_context_fingerprint=_objective_fingerprint(context),
                        )
                    )
                    persisted_link = await uow.knowledge.add_evidence_link(
                        NewEvidenceLink(
                            id=validated.id,
                            project_id=validated.project_id,
                            lineage_id=validated.lineage_id,
                            version=validated.version,
                            knowledge_object_id=target.id,
                            source_chunk_id=chunk.id,
                            repository_component_id=None,
                            evidence_relation=validated.relation.value,
                            evidence_excerpt=validated.excerpt,
                            source_anchor_start_offset=anchor.start_offset,
                            source_anchor_end_offset=anchor.end_offset,
                            source_anchor_hash=anchor.excerpt_hash,
                            source_anchor_policy_version=anchor.policy_version,
                            confidence=validated.confidence,
                            review_status=validated.review_status.value,
                            lifecycle_status=validated.lifecycle_status.value,
                            generation_record_id=generated.id,
                        )
                    )
                    evidence_ids.append(persisted_link.id)
                    evidence_added = True
                all_evidence = await uow.knowledge.list_evidence_links(target.id)
                if target.grounding_assessment_id is None or evidence_added:
                    grounding = assess_persisted_evidence(all_evidence)
                    assessment_id = uuid5(
                        _IDENTITY_NAMESPACE,
                        f"grounding:{generated.id}:{target.id}:"
                        f"{grounding.evidence_set_fingerprint}",
                    )
                    await uow.knowledge.add_grounding_assessment(
                        NewGroundingAssessment(
                            id=assessment_id,
                            project_id=command.project_id,
                            knowledge_object_id=target.id,
                            evidence_set_fingerprint=grounding.evidence_set_fingerprint,
                            status=grounding.status.value,
                            supporting_count=grounding.supporting_count,
                            contradicting_count=grounding.contradicting_count,
                            qualifying_count=grounding.qualifying_count,
                            rationale=grounding.rationale,
                            validator_name=grounding.validator_name,
                            validator_version=grounding.validator_version,
                        )
                    )
                    target = await uow.knowledge.set_grounding_cache(
                        knowledge_object_id=target.id,
                        grounding_assessment_id=assessment_id,
                        grounding_status=grounding.status.value,
                    )
                candidate_views.append(
                    CandidateExtractionView(
                        client_id=str(candidate.client_id),
                        object_type=str(candidate.object_type),
                        knowledge_object_id=target.id,
                        disposition=decision.disposition,
                        evidence_link_ids=tuple(evidence_ids),
                        grounding_status=target.grounding_status,
                        warnings=decision.warnings,
                    )
                )
            view = ExtractionBatchView(
                batch_sequence=batch_sequence,
                chunk_ids=tuple(chunk.id for chunk in chunks),
                status="succeeded",
                generation_record_id=generated.id,
                comparison_generation_record_ids=tuple(
                    item.comparison_generation_record_id
                    for item in decisions
                    if item.comparison_generation_record_id is not None
                ),
                candidates=tuple(candidate_views),
                rejected_diagnostics=tuple(
                    item.model_dump(mode="json") for item in batch.rejections
                ),
                dedup_diagnostics=tuple(item.diagnostic for item in decisions),
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_IDENTITY_NAMESPACE, f"event:{generated.id}"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=EXTRACTION_OPERATION,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=(
                        {
                            "generation_record_id": str(generated.id),
                            "processing_run_id": str(run.id),
                            "chunk_ids": [str(chunk.id) for chunk in chunks],
                        },
                    ),
                    output_refs=({"batch": view.json_value()},),
                    reason="Persist validated proposition candidates and proposed evidence.",
                    idempotency_key=idempotency_key,
                    input_fingerprint=generated.input_fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        return view

    async def _latest_generation_id(
        self,
        command: ExtractPropositionsCommand,
        *,
        task_name: str,
        input_fingerprint: str,
    ) -> UUID | None:
        async with self._uow_factory(command.project_id) as uow:
            records = await uow.generations.list_for_workflow(command.workflow_run_id)
            await uow.rollback()
        matching = tuple(
            item
            for item in records
            if item.task_name == task_name and item.input_fingerprint == input_fingerprint
        )
        return matching[-1].id if matching else None


def plan_extraction_batches(
    chunks: tuple[SourceChunkData, ...],
) -> tuple[tuple[SourceChunkData, ...], ...]:
    batches: list[tuple[SourceChunkData, ...]] = []
    current: list[SourceChunkData] = []
    codepoints = 0
    for chunk in chunks:
        size = len(chunk.chunk_text)
        if size > MAX_EXTRACTION_CONTEXT_CODEPOINTS:
            raise DomainValidationError(details={"reason": "source_chunk_exceeds_batch_limit"})
        if current and (
            len(current) >= MAX_EXTRACTION_CHUNKS
            or codepoints + size > MAX_EXTRACTION_CONTEXT_CODEPOINTS
        ):
            batches.append(tuple(current))
            current = []
            codepoints = 0
        current.append(chunk)
        codepoints += size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _contract_input(
    *,
    context: PropositionExtractionContext,
    source: SourceData,
    run: SourceProcessingRunData,
    chunks: tuple[SourceChunkData, ...],
    batch_sequence: int,
    selection_fingerprint: str,
) -> PropositionExtractionInput:
    return PropositionExtractionInput(
        prompt_version="v4",
        schema_name="proposition_extraction_batch",
        schema_version="v3",
        statement_normalization_version=STATEMENT_NORMALIZATION_VERSION,
        admission_policy_version=ADMISSION_POLICY_VERSION,
        quality_policy_version=QUALITY_POLICY_VERSION,
        lexical_policy_version=LEXICAL_POLICY_VERSION,
        near_duplicate_threshold=DEFAULT_NEAR_DUPLICATE_THRESHOLD,
        batch_sequence=batch_sequence,
        selection_fingerprint=selection_fingerprint,
        context=context,
        chunks=[
            PropositionExtractionChunk(
                id=chunk.id,
                evidence_id=f"e{index:03d}",
                source_id=source.id,
                processing_run_id=run.id,
                page_number=chunk.page_number,
                section=chunk.section,
                subsection=chunk.subsection,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                content_hash=chunk.content_hash,
                text=chunk.chunk_text,
            )
            for index, chunk in enumerate(chunks, start=1)
        ],
    )


def _new_candidate(
    *,
    object_id: UUID,
    project_id: UUID,
    generation_record_id: UUID,
    candidate: PropositionCandidate,
) -> NewKnowledgeCandidate:
    payload = candidate.model_dump(mode="python")
    common = {
        "object_type",
        "client_id",
        "statement",
        "why_it_matters",
        "relevance",
        "origin",
        "epistemic_status",
        "confidence",
        "evidence",
        "interpretations",
        "single_interpretation_reason",
        "verification_criterion",
    }
    subtype = {key: value for key, value in payload.items() if key not in common}
    interpretations: tuple[dict[str, object], ...] = ()
    if isinstance(candidate, AmbiguityCandidate):
        interpretations = tuple(
            {
                "id": uuid5(
                    _IDENTITY_NAMESPACE,
                    f"interpretation:{object_id}:{interpretation.rank}",
                ),
                "title": interpretation.title,
                "description": interpretation.description,
                "implementation_consequences": interpretation.implementation_consequences,
                "rank": interpretation.rank,
            }
            for interpretation in candidate.interpretations
        )
    if not isinstance(
        candidate,
        (
            ClaimCandidate,
            AssumptionCandidate,
            KnowledgeGapCandidate,
            ImplementationRequirementCandidate,
            AmbiguityCandidate,
        ),
    ):
        raise DomainValidationError(details={"reason": "candidate_type_invalid"})
    return NewKnowledgeCandidate(
        id=object_id,
        project_id=project_id,
        lineage_id=object_id,
        version=1,
        object_type=str(candidate.object_type),
        statement=str(candidate.statement),
        statement_normalization_version=STATEMENT_NORMALIZATION_VERSION,
        normalized_statement_hash=statement_fingerprint(str(candidate.statement)),
        origin=str(candidate.origin),
        epistemic_status=str(candidate.epistemic_status),
        confidence=(None if candidate.confidence is None else Decimal(str(candidate.confidence))),
        subtype_payload=subtype,
        ambiguity_interpretations=interpretations,
        generation_record_id=generation_record_id,
    )


def _policy_identity(model_policy: StructuredGenerationModelPolicy) -> dict[str, object]:
    return {
        "task_name": PROPOSITION_EXTRACTION_TASK,
        "prompt_version": PROPOSITION_EXTRACTION_PROMPT_VERSION,
        "schema_name": PROPOSITION_EXTRACTION_SCHEMA_NAME,
        "schema_version": PROPOSITION_EXTRACTION_SCHEMA_VERSION,
        "batching_policy_version": EXTRACTION_BATCHING_POLICY_VERSION,
        "evidence_policy_version": EXTRACTION_EVIDENCE_POLICY_VERSION,
        "evidence_id_policy_version": EVIDENCE_ID_POLICY_VERSION,
        "provider_schema_policy_version": PROVIDER_SCHEMA_POLICY_VERSION,
        "source_anchor_policy_version": SOURCE_EVIDENCE_ANCHOR_POLICY_VERSION,
        "relationship_policy_version": EXTRACTION_RELATIONSHIP_POLICY_VERSION,
        "deduplication_policy_version": DEDUPLICATION_POLICY_VERSION,
        "statement_normalization_version": STATEMENT_NORMALIZATION_VERSION,
        "admission_policy_version": ADMISSION_POLICY_VERSION,
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "lexical_policy_version": LEXICAL_POLICY_VERSION,
        "near_duplicate_threshold": DEFAULT_NEAR_DUPLICATE_THRESHOLD,
        "max_chunks": MAX_EXTRACTION_CHUNKS,
        "max_codepoints": MAX_EXTRACTION_CONTEXT_CODEPOINTS,
        "max_batches": MAX_EXTRACTION_BATCHES_PER_REQUEST,
        "max_semantic_comparisons": MAX_STRUCTURED_COMPARISONS_PER_REQUEST,
        "provider": model_policy.provider,
        "model_version": model_policy.model_version,
        "model_policy_version": model_policy.policy_version,
        "reasoning_version": model_policy.reasoning_version,
        "temperature": model_policy.temperature,
        "configuration": dict(model_policy.configuration),
    }


def _objective_fingerprint(context: PropositionExtractionContext) -> str:
    return _fingerprint(context.model_dump(mode="json"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _encode_cursor(*, selection_fingerprint: str, next_index: int, next_chunk_id: UUID) -> str:
    raw = json.dumps(
        {
            "version": EXTRACTION_CURSOR_VERSION,
            "selection_fingerprint": selection_fingerprint,
            "next_index": next_index,
            "next_chunk_id": str(next_chunk_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_start(
    cursor: str | None,
    *,
    eligible: tuple[SourceChunkData, ...],
    selection_fingerprint: str,
) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()))
        next_index = int(value["next_index"])
        valid = (
            value["version"] == EXTRACTION_CURSOR_VERSION
            and value["selection_fingerprint"] == selection_fingerprint
            and 0 <= next_index < len(eligible)
            and value["next_chunk_id"] == str(eligible[next_index].id)
        )
        boundaries: set[int] = set()
        offset = 0
        for batch in plan_extraction_batches(eligible):
            boundaries.add(offset)
            offset += len(batch)
        valid = valid and next_index in boundaries
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        valid = False
        next_index = 0
    if not valid:
        raise ResourceConflictError(details={"reason": "continuation_cursor_invalid"})
    return next_index


def _dedup_diagnostic(
    candidate: PropositionCandidate,
    target: KnowledgeObjectData | None,
    decision: str,
    *,
    historical_ids: list[str],
) -> dict[str, object]:
    proposed = {
        "origin": str(candidate.origin),
        "epistemic_status": str(candidate.epistemic_status),
        "confidence": candidate.confidence,
        "subtype_payload": {
            key: value
            for key, value in candidate.model_dump(mode="json").items()
            if key
            not in {
                "object_type",
                "client_id",
                "statement",
                "why_it_matters",
                "relevance",
                "origin",
                "epistemic_status",
                "confidence",
                "evidence",
            }
        },
    }
    disagreement = (
        None
        if target is None
        else {
            "origin": target.origin != proposed["origin"],
            "epistemic_status": target.epistemic_status != proposed["epistemic_status"],
        }
    )
    return {
        "client_id": str(candidate.client_id),
        "decision": decision,
        "target_id": None if target is None else str(target.id),
        "historical_match_ids": historical_ids,
        "proposed_metadata": proposed,
        "metadata_disagreement": disagreement,
    }
