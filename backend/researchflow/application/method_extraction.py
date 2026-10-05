"""Bounded MethodModel extraction, accountable merge, and atomic persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.api.schemas.method_extraction import (
    MethodDataFlowCandidate,
    MethodElementCandidate,
    MethodEquationCandidate,
    MethodExtractionBatch,
    MethodMergeAccountability,
    MethodSymbolCandidate,
)
from researchflow.api.schemas.method_provider import (
    MethodProviderExtractionBatch,
    MethodProviderMergeResult,
)
from researchflow.application.generation import (
    GenerateStructuredCommand,
    StructuredGenerationService,
)
from researchflow.application.method_extraction_contracts import (
    MAX_METHOD_EXTRACTION_CHUNKS,
    MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS,
    METHOD_BATCHING_POLICY_VERSION,
    METHOD_EXTRACTION_PROMPT_VERSION,
    METHOD_EXTRACTION_SCHEMA_NAME,
    METHOD_EXTRACTION_SCHEMA_VERSION,
    METHOD_EXTRACTION_TASK,
    METHOD_MERGE_PROMPT_VERSION,
    METHOD_MERGE_SCHEMA_NAME,
    METHOD_MERGE_SCHEMA_VERSION,
    METHOD_MERGE_TASK,
    MethodExtractionChunk,
    MethodExtractionContext,
    MethodExtractionInput,
    MethodMergeConstituent,
    MethodMergeInput,
    validate_method_extraction_batch,
    validate_method_merge,
)
from researchflow.application.method_grounding import (
    InitialMethodMember,
    assess_initial_method_model,
    assess_method_evidence,
)
from researchflow.domain.evidence import (
    EvidenceLinkRequest,
    EvidencePolicy,
    EvidenceSourceSelector,
    EvidenceTargetSelector,
)
from researchflow.domain.grounding import METHOD_ASSESSMENT_POLICY_VERSION
from researchflow.domain.knowledge_catalog import (
    KnowledgeLifecycleStatus,
    KnowledgeReviewStatus,
)
from researchflow.domain.method_catalog import EvidenceRelation
from researchflow.domain.method_validation import (
    METHOD_EXTRACTION_VALIDATION_VERSION,
    METHOD_REQUIRED_POLICY_VERSION,
    METHOD_SECTION_POLICY_VERSION,
    method_element_endpoint_type,
)
from researchflow.domain.provenance import SourceChunkEvidenceProvenance
from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import StructuredGenerationModelPolicy
from researchflow.ports.repositories import (
    GenerationRecordData,
    MethodModelMemberData,
    NewMethodElementCandidate,
    NewMethodEvidenceLink,
    NewMethodGroundingAssessment,
    NewMethodModelAssessment,
    NewMethodModelCandidate,
    NewMethodModelMember,
    NewMethodObjectLink,
    NewOperationEvent,
    SourceChunkData,
    SourceData,
    SourceProcessingRunData,
)
from researchflow.ports.unit_of_work import UnitOfWorkFactory

METHOD_MERGE_POLICY_VERSION: Final = "rf-method-merge-v1"
METHOD_PERSISTENCE_POLICY_VERSION: Final = "rf-method-persistence-v1"
METHOD_DISPLAY_POLICY_VERSION: Final = "rf-method-section-display-v1"
METHOD_DISPLAY_PAGE_SIZE: Final = 32
METHOD_OPERATION: Final = "method_model.extraction.aggregate.persist"
_IDENTITY_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/method-extraction"
)


@dataclass(frozen=True, slots=True)
class ExtractMethodModelCommand:
    project_id: UUID
    workflow_run_id: UUID
    source_processing_run_id: UUID
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class MethodElementView:
    id: UUID
    client_id: str
    element_type: str
    review_section: str
    sequence_index: int | None
    grounding_status: str
    evidence_link_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class MethodExtractionView:
    workflow_run_id: UUID
    status: str
    method_model_id: UUID | None
    batch_generation_record_ids: tuple[UUID, ...]
    merge_generation_record_id: UUID | None
    elements: tuple[MethodElementView, ...]
    rejected_diagnostics: tuple[dict[str, object], ...]
    merge_accountability: tuple[dict[str, object], ...]
    assessment_id: UUID | None
    assessment_outcome: str | None
    error_code: str | None = None

    def json_value(self) -> dict[str, object]:
        return {
            "workflow_run_id": str(self.workflow_run_id),
            "status": self.status,
            "method_model_id": None if self.method_model_id is None else str(self.method_model_id),
            "batch_generation_record_ids": [str(item) for item in self.batch_generation_record_ids],
            "merge_generation_record_id": (
                None
                if self.merge_generation_record_id is None
                else str(self.merge_generation_record_id)
            ),
            "elements": [
                {
                    "id": str(item.id),
                    "client_id": item.client_id,
                    "element_type": item.element_type,
                    "review_section": item.review_section,
                    "sequence_index": item.sequence_index,
                    "grounding_status": item.grounding_status,
                    "evidence_link_ids": [str(link) for link in item.evidence_link_ids],
                }
                for item in self.elements
            ],
            "rejected_diagnostics": list(self.rejected_diagnostics),
            "merge_accountability": list(self.merge_accountability),
            "assessment_id": None if self.assessment_id is None else str(self.assessment_id),
            "assessment_outcome": self.assessment_outcome,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class MethodSectionPage:
    method_model_id: UUID
    review_section: str
    total_count: int
    returned_count: int
    has_more: bool
    next_cursor: str | None
    elements: tuple[MethodModelMemberData, ...]


@dataclass(frozen=True, slots=True)
class MethodGenerationAttemptView:
    id: UUID
    task_name: str
    input_fingerprint: str
    status: str
    error_code: str | None


class MethodExtractionService:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, generation_service: StructuredGenerationService
    ) -> None:
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def extract(self, command: ExtractMethodModelCommand) -> MethodExtractionView:
        context, source, run, chunks = await self._load_context(command)
        planned = plan_method_batches(chunks)
        generated: list[tuple[GenerationRecordData, MethodExtractionBatch]] = []
        for batch_chunks in planned:
            extraction_input = _contract_input(context, source, run, batch_chunks)
            batch_outcome = await self._generation.generate_structured(
                GenerateStructuredCommand(
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    task_name=METHOD_EXTRACTION_TASK,
                    input_fingerprint=extraction_input.fingerprint(),
                    prompt_version=METHOD_EXTRACTION_PROMPT_VERSION,
                    schema_name=METHOD_EXTRACTION_SCHEMA_NAME,
                    schema_version=METHOD_EXTRACTION_SCHEMA_VERSION,
                    output_type=MethodProviderExtractionBatch,
                    messages=extraction_input.messages(),
                    model_policy=command.model_policy,
                    explicit_rerun=command.explicit_rerun,
                )
            )
            generated.append(
                (
                    batch_outcome.generation_record,
                    validate_method_extraction_batch(extraction_input, batch_outcome.value),
                )
            )

        final_record: GenerationRecordData
        final: MethodExtractionBatch
        accountability: tuple[MethodMergeAccountability, ...]
        merge_record_id: UUID | None = None
        if len(generated) == 1:
            final_record, final = generated[0]
            accountability = tuple(
                MethodMergeAccountability(
                    constituent_generation_record_id=final_record.id,
                    input_client_id=element.client_id,
                    disposition="retained",
                    merged_client_id=element.client_id,
                    reason=None,
                )
                for element in final.elements
            )
        else:
            merge_input = MethodMergeInput(
                prompt_version=METHOD_MERGE_PROMPT_VERSION,
                schema_name="method_merge_result",
                schema_version=METHOD_MERGE_SCHEMA_VERSION,
                validation_policy_version=METHOD_EXTRACTION_VALIDATION_VERSION,
                required_element_policy_version=METHOD_REQUIRED_POLICY_VERSION,
                section_policy_version=METHOD_SECTION_POLICY_VERSION,
                merge_policy_version=METHOD_MERGE_POLICY_VERSION,
                batching_policy_version=METHOD_BATCHING_POLICY_VERSION,
                batch_chunk_limit=MAX_METHOD_EXTRACTION_CHUNKS,
                batch_context_codepoint_limit=MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS,
                grounding_policy_version="rf-grounding-policy-v1",
                assessment_policy_version=METHOD_ASSESSMENT_POLICY_VERSION,
                aggregate_element_limit=128,
                model_configuration=_model_policy_identity(command.model_policy),
                context=context,
                source_processing_run_id=run.id,
                constituents=[
                    MethodMergeConstituent(
                        batch_sequence=index,
                        generation_record_id=record.id,
                        generation_input_fingerprint=record.input_fingerprint,
                        output=batch,
                    )
                    for index, (record, batch) in enumerate(generated)
                ],
            )
            merge_outcome = await self._generation.generate_structured(
                GenerateStructuredCommand(
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    task_name=METHOD_MERGE_TASK,
                    input_fingerprint=merge_input.fingerprint(),
                    prompt_version=METHOD_MERGE_PROMPT_VERSION,
                    schema_name=METHOD_MERGE_SCHEMA_NAME,
                    schema_version=METHOD_MERGE_SCHEMA_VERSION,
                    output_type=MethodProviderMergeResult,
                    messages=merge_input.messages(),
                    model_policy=command.model_policy,
                    explicit_rerun=command.explicit_rerun,
                )
            )
            merged = validate_method_merge(
                merge_input,
                merge_outcome.value,
                chunks=tuple(_method_chunk(source, run, chunk) for chunk in chunks),
            )
            final_record = merge_outcome.generation_record
            final = MethodExtractionBatch.model_validate(
                merged.model_dump(exclude={"accountability"})
            )
            accountability = tuple(merged.accountability)
            merge_record_id = final_record.id
        return await self._persist(
            command,
            context,
            source,
            run,
            chunks,
            generated,
            final_record,
            final,
            accountability,
            merge_record_id,
        )

    async def _load_context(
        self, command: ExtractMethodModelCommand
    ) -> tuple[
        MethodExtractionContext, SourceData, SourceProcessingRunData, tuple[SourceChunkData, ...]
    ]:
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            if project is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            question = await uow.research_questions.get_current_primary()
            if question is None or question.id != project.primary_research_question_id:
                raise ResourceConflictError(details={"reason": "primary_question_not_current"})
            workflow = await uow.workflows.get_exact(command.workflow_run_id)
            if workflow is None or workflow.primary_research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "workflow_question_mismatch"})
            relationship = await uow.sources.get_current_primary_paper()
            if relationship is None or relationship.research_question_id != question.id:
                raise ResourceConflictError(details={"reason": "accepted_primary_paper_missing"})
            source = await uow.sources.get_exact(relationship.source_id)
            run = await uow.sources.get_processing_run_exact(command.source_processing_run_id)
            if source is None or run is None or run.source_id != relationship.source_id:
                raise ResourceNotFoundError(details={"resource": "source_processing_run"})
            if run.status != "succeeded" or source.content_hash != run.source_hash:
                raise ResourceConflictError(details={"reason": "processing_run_not_eligible"})
            chunks = await uow.sources.list_chunks(run.id)
            await uow.rollback()
        if not chunks:
            raise DomainValidationError(details={"reason": "no_eligible_source_chunks"})
        context = MethodExtractionContext(
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
        return (
            context,
            source,
            run,
            tuple(sorted(chunks, key=lambda item: (item.chunk_index, str(item.id)))),
        )

    async def _persist(
        self,
        command: ExtractMethodModelCommand,
        context: MethodExtractionContext,
        source: SourceData,
        run: SourceProcessingRunData,
        chunks: tuple[SourceChunkData, ...],
        generated: list[tuple[GenerationRecordData, MethodExtractionBatch]],
        final_record: GenerationRecordData,
        final: MethodExtractionBatch,
        accountability: tuple[MethodMergeAccountability, ...],
        merge_record_id: UUID | None,
    ) -> MethodExtractionView:
        idempotency_key = _fingerprint(
            {"policy": METHOD_PERSISTENCE_POLICY_VERSION, "generation": str(final_record.id)}
        )
        async with self._uow_factory(command.project_id) as uow:
            existing = await uow.events.get_by_idempotency_key(METHOD_OPERATION, idempotency_key)
            if existing is not None:
                await uow.rollback()
                return _view_from_event(existing.output_refs)
            model_id = uuid5(_IDENTITY_NAMESPACE, f"model:{final_record.id}")
            element_ids = {
                element.client_id: uuid5(
                    _IDENTITY_NAMESPACE, f"element:{final_record.id}:{element.client_id}"
                )
                for element in final.elements
            }
            composition = _composition_fingerprint(final, element_ids)
            await uow.method_models.add_candidate_model(
                NewMethodModelCandidate(
                    id=model_id,
                    project_id=command.project_id,
                    research_question_id=context.primary_research_question_id,
                    lineage_id=model_id,
                    version=1,
                    name=final.method_model.name,
                    objective=final.method_model.objective,
                    confidence=None,
                    composition_fingerprint=composition,
                    generation_record_id=final_record.id,
                )
            )
            for element in sorted(
                final.elements, key=lambda item: isinstance(item, MethodDataFlowCandidate)
            ):
                await uow.method_models.add_candidate_element(
                    _new_element(
                        project_id=command.project_id,
                        generation_record_id=final_record.id,
                        element=element,
                        element_ids=element_ids,
                    )
                )
            element_views: list[MethodElementView] = []
            assessment_members: list[InitialMethodMember] = []
            chunk_map = {chunk.id: chunk for chunk in chunks}
            objective_fingerprint = _fingerprint(context.model_dump(mode="json"))
            for element in final.elements:
                element_id = element_ids[element.client_id]
                await uow.method_models.add_member(
                    NewMethodModelMember(
                        id=uuid5(_IDENTITY_NAMESPACE, f"member:{model_id}:{element_id}"),
                        method_model_id=model_id,
                        method_element_id=element_id,
                        review_section=element.review_section.value,
                        member_role=element.member_role.value,
                        sequence_index=element.section_sequence_index,
                        required=element.required,
                        inclusion_rationale=element.inclusion_rationale,
                    )
                )
                if isinstance(element, MethodEquationCandidate):
                    for symbol_client_id in element.symbol_client_ids:
                        link_id = uuid5(
                            _IDENTITY_NAMESPACE,
                            f"equation-symbol:{element_id}:{element_ids[symbol_client_id]}",
                        )
                        await uow.method_models.add_object_link(
                            NewMethodObjectLink(
                                id=link_id,
                                project_id=command.project_id,
                                lineage_id=link_id,
                                version=1,
                                source_method_element_id=element_id,
                                relationship_type="uses",
                                target_method_element_id=element_ids[symbol_client_id],
                                rationale="The extracted equation references this MethodSymbol.",
                                confidence=element.confidence,
                                generation_record_id=final_record.id,
                            )
                        )
                evidence_ids: list[UUID] = []
                endpoint = method_element_endpoint_type(
                    element_type=element.element_type,
                    symbol_type=element.symbol_type
                    if isinstance(element, MethodSymbolCandidate)
                    else None,
                )
                for reference in element.evidence:
                    chunk = chunk_map[reference.chunk_id]
                    evidence_id = uuid5(
                        _IDENTITY_NAMESPACE,
                        f"evidence:{final_record.id}:{element_id}:{chunk.id}:{reference.relation}:{hashlib.sha256(reference.excerpt.encode()).hexdigest()}",
                    )
                    validated = EvidencePolicy.validate(
                        EvidenceLinkRequest(
                            id=evidence_id,
                            lineage_id=evidence_id,
                            version=1,
                            target=EvidenceTargetSelector(
                                project_id=command.project_id,
                                target_type=endpoint,
                                target_version=1,
                                method_element_id=element_id,
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
                            confidence=None
                            if reference.confidence is None
                            else Decimal(str(reference.confidence)),
                            review_status=KnowledgeReviewStatus.CANDIDATE,
                            lifecycle_status=KnowledgeLifecycleStatus.CURRENT,
                            material=True,
                            materiality_policy_version=METHOD_REQUIRED_POLICY_VERSION,
                            objective_context_fingerprint=objective_fingerprint,
                        )
                    )
                    persisted = await uow.method_models.add_evidence_link(
                        NewMethodEvidenceLink(
                            id=validated.id,
                            project_id=command.project_id,
                            lineage_id=validated.lineage_id,
                            version=validated.version,
                            method_element_id=element_id,
                            source_chunk_id=chunk.id,
                            repository_component_id=None,
                            evidence_relation=validated.relation.value,
                            evidence_excerpt=validated.excerpt,
                            confidence=validated.confidence,
                            review_status=validated.review_status.value,
                            lifecycle_status=validated.lifecycle_status.value,
                            generation_record_id=final_record.id,
                        )
                    )
                    evidence_ids.append(persisted.id)
                links = await uow.method_models.list_evidence_links(element_id)
                grounding = assess_method_evidence(
                    project_id=command.project_id,
                    element_id=element_id,
                    endpoint_type=endpoint,
                    links=links,
                )
                grounding_id = uuid5(
                    _IDENTITY_NAMESPACE,
                    f"grounding:{final_record.id}:{element_id}:{grounding.evidence_set_fingerprint}",
                )
                await uow.method_models.add_grounding_assessment(
                    NewMethodGroundingAssessment(
                        id=grounding_id,
                        project_id=command.project_id,
                        method_element_id=element_id,
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
                await uow.method_models.set_element_grounding_cache(
                    element_id=element_id,
                    assessment_id=grounding_id,
                    grounding_status=grounding.status.value,
                )
                assessment_members.append(
                    InitialMethodMember(
                        element_id=element_id,
                        required=element.required,
                        provenance_complete=bool(links),
                        endpoint_type=endpoint,
                        grounding=grounding,
                    )
                )
                element_views.append(
                    MethodElementView(
                        id=element_id,
                        client_id=element.client_id,
                        element_type=element.element_type.value,
                        review_section=element.review_section.value,
                        sequence_index=element.section_sequence_index,
                        grounding_status=grounding.status.value,
                        evidence_link_ids=tuple(evidence_ids),
                    )
                )
            aggregate_assessment = assess_initial_method_model(
                method_model_id=model_id,
                composition_fingerprint=composition,
                objective_context_fingerprint=objective_fingerprint,
                members=tuple(assessment_members),
            )
            assessment_id = uuid5(
                _IDENTITY_NAMESPACE,
                f"assessment:{final_record.id}:{aggregate_assessment.member_grounding_fingerprint}",
            )
            await uow.method_models.add_model_assessment(
                NewMethodModelAssessment(
                    id=assessment_id,
                    method_model_id=model_id,
                    composition_fingerprint=aggregate_assessment.composition_fingerprint,
                    member_grounding_fingerprint=aggregate_assessment.member_grounding_fingerprint,
                    source_evidence_fingerprint=aggregate_assessment.source_evidence_fingerprint,
                    repository_evidence_fingerprint=aggregate_assessment.repository_evidence_fingerprint,
                    unsupported_required_count=aggregate_assessment.unsupported_required_count,
                    mixed_evidence_count=aggregate_assessment.mixed_evidence_count,
                    contradicted_count=aggregate_assessment.contradicted_count,
                    missing_provenance_count=aggregate_assessment.missing_provenance_count,
                    unresolved_scientific_ambiguity_count=aggregate_assessment.unresolved_scientific_ambiguity_count,
                    outcome=aggregate_assessment.outcome.value,
                    rationale=aggregate_assessment.rationale,
                    generation_record_id=final_record.id,
                )
            )
            await uow.method_models.set_current_assessment(
                method_model_id=model_id, assessment_id=assessment_id
            )
            view = MethodExtractionView(
                workflow_run_id=command.workflow_run_id,
                status="completed_with_diagnostics" if final.rejections else "completed",
                method_model_id=model_id,
                batch_generation_record_ids=tuple(record.id for record, _ in generated),
                merge_generation_record_id=merge_record_id,
                elements=tuple(element_views),
                rejected_diagnostics=tuple(
                    item.model_dump(mode="json") for item in final.rejections
                ),
                merge_accountability=tuple(item.model_dump(mode="json") for item in accountability),
                assessment_id=assessment_id,
                assessment_outcome=aggregate_assessment.outcome.value,
            )
            await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_IDENTITY_NAMESPACE, f"event:{final_record.id}"),
                    project_id=command.project_id,
                    workflow_run_id=command.workflow_run_id,
                    operation_type=METHOD_OPERATION,
                    created_by_type="ai",
                    created_by_id=None,
                    input_refs=tuple(
                        {"generation_record_id": str(record.id)} for record, _ in generated
                    ),
                    output_refs=({"method_extraction": view.json_value()},),
                    reason="Persist one complete candidate MethodModel aggregate.",
                    idempotency_key=idempotency_key,
                    input_fingerprint=final_record.input_fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
        return view

    async def section_page(
        self, project_id: UUID, model_id: UUID, section: str, cursor: str | None
    ) -> MethodSectionPage:
        async with self._uow_factory(project_id) as uow:
            model = await uow.method_models.get_model_exact(model_id)
            if model is None:
                raise ResourceNotFoundError(details={"resource": "method_model"})
            members = tuple(
                item
                for item in await uow.method_models.list_members(model_id)
                if item.review_section == section
            )
            await uow.rollback()
        ordered = tuple(
            sorted(
                members,
                key=lambda item: (
                    item.sequence_index is None,
                    item.sequence_index or 0,
                    item.element.element_type,
                    str(item.element.id),
                ),
            )
        )
        start = _section_cursor_start(cursor, model_id=model_id, section=section, members=ordered)
        page = ordered[start : start + METHOD_DISPLAY_PAGE_SIZE]
        next_index = start + len(page)
        next_cursor = (
            None
            if next_index >= len(ordered)
            else _section_cursor(model_id, section, next_index, ordered[next_index].element.id)
        )
        return MethodSectionPage(
            model_id, section, len(ordered), len(page), next_cursor is not None, next_cursor, page
        )

    async def status(
        self, project_id: UUID, workflow_run_id: UUID
    ) -> tuple[MethodExtractionView | None, tuple[MethodGenerationAttemptView, ...]]:
        async with self._uow_factory(project_id) as uow:
            if await uow.projects.get() is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            if await uow.workflows.get_exact(workflow_run_id) is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            events = await uow.events.list_for_workflow(
                workflow_run_id, operation_type=METHOD_OPERATION
            )
            records = await uow.generations.list_for_workflow(workflow_run_id)
            await uow.rollback()
        view = None if not events else _view_from_event(events[-1].output_refs)
        attempts = tuple(
            MethodGenerationAttemptView(
                id=record.id,
                task_name=record.task_name,
                input_fingerprint=record.input_fingerprint,
                status=record.status,
                error_code=record.error_code,
            )
            for record in records
            if record.task_name in {METHOD_EXTRACTION_TASK, METHOD_MERGE_TASK}
        )
        return view, attempts


def plan_method_batches(
    chunks: tuple[SourceChunkData, ...],
) -> tuple[tuple[SourceChunkData, ...], ...]:
    batches: list[tuple[SourceChunkData, ...]] = []
    current: list[SourceChunkData] = []
    size = 0
    for chunk in chunks:
        chunk_size = len(chunk.chunk_text)
        if chunk_size > MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS:
            raise DomainValidationError(
                details={"reason": "source_chunk_exceeds_method_batch_limit"}
            )
        if current and (
            len(current) >= MAX_METHOD_EXTRACTION_CHUNKS
            or size + chunk_size > MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS
        ):
            batches.append(tuple(current))
            current = []
            size = 0
        current.append(chunk)
        size += chunk_size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _method_chunk(
    source: SourceData, run: SourceProcessingRunData, chunk: SourceChunkData
) -> MethodExtractionChunk:
    return MethodExtractionChunk(
        id=chunk.id,
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


def _contract_input(
    context: MethodExtractionContext,
    source: SourceData,
    run: SourceProcessingRunData,
    chunks: tuple[SourceChunkData, ...],
) -> MethodExtractionInput:
    return MethodExtractionInput(
        prompt_version=METHOD_EXTRACTION_PROMPT_VERSION,
        schema_name="method_extraction_batch",
        schema_version=METHOD_EXTRACTION_SCHEMA_VERSION,
        validation_policy_version=METHOD_EXTRACTION_VALIDATION_VERSION,
        required_element_policy_version=METHOD_REQUIRED_POLICY_VERSION,
        section_policy_version=METHOD_SECTION_POLICY_VERSION,
        batching_policy_version=METHOD_BATCHING_POLICY_VERSION,
        batch_chunk_limit=MAX_METHOD_EXTRACTION_CHUNKS,
        batch_context_codepoint_limit=MAX_METHOD_EXTRACTION_CONTEXT_CODEPOINTS,
        allow_data_flow_cycles=False,
        context=context,
        chunks=[_method_chunk(source, run, chunk) for chunk in chunks],
    )


def _new_element(
    *,
    project_id: UUID,
    generation_record_id: UUID,
    element: MethodElementCandidate,
    element_ids: dict[str, UUID],
) -> NewMethodElementCandidate:
    payload = element.model_dump(mode="python", exclude_none=True)
    common = {
        "element_type",
        "client_id",
        "epistemic_status",
        "confidence",
        "review_section",
        "member_role",
        "section_sequence_index",
        "required",
        "inclusion_rationale",
        "evidence",
        "symbol_client_ids",
        "source_client_id",
        "source_element_type",
        "target_client_id",
        "target_element_type",
    }
    subtype = {key: value for key, value in payload.items() if key not in common}
    if isinstance(element, MethodDataFlowCandidate):
        subtype["source_element_id"] = element_ids[element.source_client_id]
        subtype["target_element_id"] = element_ids[element.target_client_id]
    element_id = element_ids[element.client_id]
    return NewMethodElementCandidate(
        id=element_id,
        project_id=project_id,
        lineage_id=element_id,
        version=1,
        element_type=element.element_type.value,
        epistemic_status=element.epistemic_status.value,
        confidence=None if element.confidence is None else Decimal(str(element.confidence)),
        subtype_payload=subtype,
        generation_record_id=generation_record_id,
    )


def _composition_fingerprint(final: MethodExtractionBatch, element_ids: dict[str, UUID]) -> str:
    return _fingerprint(
        {
            "model": final.method_model.model_dump(mode="json"),
            "sections": [item.model_dump(mode="json") for item in final.sections],
            "members": [
                {
                    "id": str(element_ids[item.client_id]),
                    "section": item.review_section.value,
                    "role": item.member_role.value,
                    "sequence": item.section_sequence_index,
                    "required": item.required,
                }
                for item in final.elements
            ],
        }
    )


def _model_policy_identity(policy: StructuredGenerationModelPolicy) -> dict[str, object]:
    return {
        "provider": policy.provider,
        "model_version": policy.model_version,
        "policy_version": policy.policy_version,
        "reasoning_version": policy.reasoning_version,
        "temperature": policy.temperature,
        "configuration": dict(policy.configuration),
    }


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _view_from_event(refs: tuple[dict[str, object], ...]) -> MethodExtractionView:
    if len(refs) != 1 or not isinstance((raw := refs[0].get("method_extraction")), dict):
        raise DomainValidationError(details={"reason": "method_extraction_event_invalid"})
    return MethodExtractionView(
        workflow_run_id=UUID(str(raw["workflow_run_id"])),
        status=str(raw["status"]),
        method_model_id=UUID(str(raw["method_model_id"])),
        batch_generation_record_ids=tuple(
            UUID(str(item)) for item in raw["batch_generation_record_ids"]
        ),
        merge_generation_record_id=None
        if raw["merge_generation_record_id"] is None
        else UUID(str(raw["merge_generation_record_id"])),
        elements=tuple(
            MethodElementView(
                id=UUID(str(item["id"])),
                client_id=str(item["client_id"]),
                element_type=str(item["element_type"]),
                review_section=str(item["review_section"]),
                sequence_index=item["sequence_index"],
                grounding_status=str(item["grounding_status"]),
                evidence_link_ids=tuple(UUID(str(link)) for link in item["evidence_link_ids"]),
            )
            for item in raw["elements"]
        ),
        rejected_diagnostics=tuple(raw["rejected_diagnostics"]),
        merge_accountability=tuple(raw["merge_accountability"]),
        assessment_id=UUID(str(raw["assessment_id"])),
        assessment_outcome=str(raw["assessment_outcome"]),
        error_code=None,
    )


def _section_cursor(model_id: UUID, section: str, index: int, element_id: UUID) -> str:
    import base64

    raw = json.dumps(
        {
            "policy": METHOD_DISPLAY_POLICY_VERSION,
            "model_id": str(model_id),
            "section": section,
            "index": index,
            "element_id": str(element_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _section_cursor_start(
    cursor: str | None, *, model_id: UUID, section: str, members: tuple[MethodModelMemberData, ...]
) -> int:
    if cursor is None:
        return 0
    import base64

    try:
        value = json.loads(base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()))
        index = int(value["index"])
        valid = (
            value["policy"] == METHOD_DISPLAY_POLICY_VERSION
            and value["model_id"] == str(model_id)
            and value["section"] == section
            and 0 <= index < len(members)
            and value["element_id"] == str(members[index].element.id)
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        valid = False
        index = 0
    if not valid:
        raise ResourceConflictError(details={"reason": "method_section_cursor_invalid"})
    return index
