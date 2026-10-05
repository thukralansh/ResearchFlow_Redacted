""" deterministic Blueprint eligibility application boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final
from uuid import UUID, uuid4, uuid5

from researchflow.application.mental_model_reassessment import (
    mental_model_assessment_covers_current_state,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.blueprint_catalog import (
    DependencyCriticality,
    EligibilityItemOutcome,
)
from researchflow.domain.blueprint_eligibility import (
    BLUEPRINT_ELIGIBILITY_RULE_VERSION,
    EligibilityFinding,
    EligibilityReason,
    eligibility_input_fingerprint,
    evaluate_eligibility,
)
from researchflow.domain.knowledge_catalog import KnowledgeLifecycleStatus, KnowledgeReviewStatus
from researchflow.domain.method_catalog import (
    MethodMemberRole,
    MethodReviewSection,
    SectionReviewOutcome,
)
from researchflow.domain.method_review import (
    MethodMemberSnapshot,
    MethodReviewPolicy,
    SectionReviewSnapshot,
)
from researchflow.errors import (
    DomainValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    BlueprintEligibilityAssessmentData,
    BlueprintEligibilityItemData,
    NewBlueprintEligibilityAssessment,
    NewBlueprintEligibilityItem,
    NewOperationEvent,
    OperationEventData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_ELIGIBILITY_NAMESPACE: Final = UUID("4edfc311-9c31-50dc-bdad-730619a9f8d9")
_OPERATION_TYPE: Final = "blueprint_eligibility.assessed"


@dataclass(frozen=True, slots=True)
class AssessBlueprintEligibilityCommand:
    project_id: UUID
    workflow_run_id: UUID
    mapping_review_gate_event_id: UUID
    command_id: UUID
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class BlueprintEligibilityResult:
    assessment: BlueprintEligibilityAssessmentData
    items: tuple[BlueprintEligibilityItemData, ...]
    event: OperationEventData
    replayed: bool


class BlueprintEligibilityService:
    """Persist one exact deterministic assessment without generating a Blueprint."""

    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def assess(
        self, command: AssessBlueprintEligibilityCommand
    ) -> BlueprintEligibilityResult:
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get_for_update()
            if project is None:
                raise ResourceNotFoundError(details={"resource": "project"})
            if project.created_by_id != self._actor.actor_id:
                raise ResourceNotFoundError(details={"resource": "project"})
            workflow = await uow.workflows.get_for_update(command.workflow_run_id)
            if workflow is None:
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            question = await uow.research_questions.get_current_primary_for_update()
            paper = await uow.sources.get_current_primary_paper_for_update()
            if question is None:
                raise ResourceConflictError(details={"reason": "current_question_missing"})
            if paper is None:
                raise ResourceConflictError(details={"reason": "primary_paper_missing"})
            runs = await uow.sources.list_processing_runs(paper.source_id)
            run = next((item for item in runs if item.status == "succeeded"), None)
            if run is None:
                raise ResourceConflictError(details={"reason": "primary_paper_processing_missing"})
            if (
                workflow.primary_research_question_id != question.id
                or workflow.primary_question_source_relationship_id != paper.id
                or workflow.primary_source_processing_run_id != run.id
            ):
                raise ResourceConflictError(details={"reason": "workflow_primary_inputs_stale"})
            method = await uow.method_models.get_accepted_current_model_for_update(question.id)
            if method is None:
                raise ResourceConflictError(details={"reason": "accepted_method_model_missing"})
            if method.current_assessment_id is None:
                raise ResourceConflictError(details={"reason": "method_model_assessment_missing"})
            method_assessment = await uow.method_models.get_assessment_exact(
                method.current_assessment_id
            )
            if method_assessment is None:
                raise ResourceConflictError(details={"reason": "method_model_assessment_missing"})
            method_members = await uow.method_models.list_members_for_update(method.id)
            section_reviews = await uow.method_models.list_current_section_reviews(
                method.id, for_update=True
            )
            mental = await uow.mental_models.get_current_for_update()
            if mental is None:
                raise ResourceConflictError(details={"reason": "mental_model_missing"})
            mental_assessment = await uow.mental_models.latest_assessment_for_model(mental.id)
            if mental_assessment is None:
                raise ResourceConflictError(details={"reason": "mental_model_assessment_missing"})
            gate = await uow.events.get_exact(command.mapping_review_gate_event_id)
            if (
                gate is None
                or gate.workflow_run_id != workflow.id
                or gate.operation_type
                not in {
                    "mapping.review_gate.completed",
                    "mapping.review_gate.not_applicable",
                }
            ):
                raise ResourceConflictError(details={"reason": "mapping_review_gate_invalid"})

            knowledge = await uow.knowledge.list_accepted_current()
            requests = tuple(
                item
                for item in await uow.judgments.list_requests()
                if item.lifecycle_status == "current"
            )
            judgments = await uow.judgments.list_current()
            mappings = await uow.mappings.list_accepted_current()
            relationship = (
                await uow.repositories.get_accepted_current_relationship()
                if gate.operation_type == "mapping.review_gate.completed"
                else None
            )

            findings, mapping_coverage, fingerprint_payload = await _derive(
                uow=uow,
                project=project,
                question=question,
                paper=paper,
                run=run,
                method=method,
                method_assessment=method_assessment,
                method_members=method_members,
                section_reviews=section_reviews,
                mental=mental,
                mental_assessment=mental_assessment,
                gate=gate,
                relationship=relationship,
                mappings=mappings,
                knowledge=knowledge,
                requests=requests,
                judgments=judgments,
            )
            evaluation = evaluate_eligibility(findings)
            fingerprint = eligibility_input_fingerprint(fingerprint_payload)
            existing = await uow.blueprints.get_eligibility_by_input(
                input_fingerprint=fingerprint,
                eligibility_rule_version=BLUEPRINT_ELIGIBILITY_RULE_VERSION,
                reproduction_depth=project.reproduction_depth,
            )
            if existing is not None:
                event = await uow.events.get_by_idempotency_key(_OPERATION_TYPE, fingerprint)
                if event is None:
                    raise ResourceConflictError(
                        details={"reason": "eligibility_replay_event_missing"}
                    )
                replay_items = await uow.blueprints.list_eligibility_items(existing.id)
                await uow.rollback()
                return BlueprintEligibilityResult(existing, replay_items, event, True)

            assessment_id = uuid4()
            assessment = await uow.blueprints.add_eligibility(
                NewBlueprintEligibilityAssessment(
                    id=assessment_id,
                    project_id=command.project_id,
                    research_question_id=question.id,
                    method_model_id=method.id,
                    method_model_assessment_id=method_assessment.id,
                    mental_model_id=mental.id,
                    mental_model_assessment_id=mental_assessment.id,
                    paper_repository_relationship_id=(
                        None if relationship is None else relationship.id
                    ),
                    repository_snapshot_id=(
                        None if relationship is None else relationship.repository_snapshot_id
                    ),
                    reproduction_depth=project.reproduction_depth,
                    input_fingerprint=fingerprint,
                    eligibility_rule_version=BLUEPRINT_ELIGIBILITY_RULE_VERSION,
                    mapping_coverage=mapping_coverage,
                    status=evaluation.status.value,
                )
            )
            items: list[BlueprintEligibilityItemData] = []
            dependency_inputs: dict[tuple[str, UUID], DependencyCriticality] = {}
            for index, finding in enumerate(evaluation.findings, 1):
                item = await uow.blueprints.add_eligibility_item(
                    NewBlueprintEligibilityItem(
                        id=uuid5(_ELIGIBILITY_NAMESPACE, f"{assessment_id}:item:{index}"),
                        assessment_id=assessment_id,
                        sequence_index=index,
                        expected_dependency_type=finding.expected_dependency_type,
                        target_id=finding.target_id,
                        criticality=finding.criticality.value,
                        outcome=finding.outcome.value,
                        reason_code=finding.reason_code.value,
                        explanation=finding.explanation,
                    )
                )
                items.append(item)
                if finding.target_id is not None:
                    key = (finding.expected_dependency_type, finding.target_id)
                    prior = dependency_inputs.get(key)
                    priority = {
                        DependencyCriticality.INFORMATIONAL: 0,
                        DependencyCriticality.MATERIAL: 1,
                        DependencyCriticality.BLOCKING: 2,
                    }
                    if prior is None or priority[finding.criticality] > priority[prior]:
                        dependency_inputs[key] = finding.criticality
            for dependency_index, ((dependency_type, dependency_id), criticality) in enumerate(
                sorted(dependency_inputs.items(), key=lambda item: (item[0][0], str(item[0][1]))),
                1,
            ):
                await uow.dependencies.append_dependency(
                    ArtifactDependencyData(
                        id=uuid5(
                            _ELIGIBILITY_NAMESPACE,
                            f"{assessment_id}:dep:{dependency_index}",
                        ),
                        project_id=command.project_id,
                        artifact_type="blueprint_eligibility_assessment",
                        artifact_id=assessment_id,
                        dependency_type=dependency_type,
                        dependency_id=dependency_id,
                        dependency_role="derivation_input",
                        criticality=criticality.value,
                    )
                )
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid4(),
                    project_id=command.project_id,
                    workflow_run_id=workflow.id,
                    operation_type=_OPERATION_TYPE,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=tuple(
                        {"type": dependency_type, "id": str(dependency_id)}
                        for dependency_type, dependency_id in sorted(
                            dependency_inputs, key=lambda item: (item[0], str(item[1]))
                        )
                    ),
                    output_refs=(
                        {"type": "blueprint_eligibility_assessment", "id": str(assessment.id)},
                        {"type": "eligibility_status", "id": assessment.status},
                    ),
                    reason="Deterministic Blueprint eligibility assessment.",
                    idempotency_key=fingerprint,
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return BlueprintEligibilityResult(assessment, tuple(items), event, False)


async def _derive(
    *, uow: UnitOfWork, **values: Any
) -> tuple[tuple[EligibilityFinding, ...], dict[str, object], dict[str, object]]:
    project = values["project"]
    question = values["question"]
    paper = values["paper"]
    run = values["run"]
    method = values["method"]
    method_assessment = values["method_assessment"]
    method_members = values["method_members"]
    section_reviews = values["section_reviews"]
    mental = values["mental"]
    mental_assessment = values["mental_assessment"]
    gate = values["gate"]
    relationship = values["relationship"]
    mappings = values["mappings"]
    knowledge = values["knowledge"]
    requests = values["requests"]
    judgments = values["judgments"]
    findings: list[EligibilityFinding] = []

    def add(
        dependency_type: str,
        target_id: UUID | None,
        criticality: DependencyCriticality,
        outcome: EligibilityItemOutcome,
        reason: EligibilityReason,
        explanation: str,
    ) -> None:
        findings.append(
            EligibilityFinding(
                dependency_type, target_id, criticality, outcome, reason, explanation
            )
        )

    add(
        "research_question",
        question.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED
        if question.lifecycle_status == "current"
        else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.QUESTION_CURRENT,
        "The exact Primary Research Question is current.",
    )
    paper_ok = (
        paper.research_question_id == question.id
        and paper.review_status == "accepted"
        and paper.lifecycle_status == "current"
    )
    add(
        "research_question_source",
        paper.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED if paper_ok else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.PRIMARY_PAPER_CURRENT,
        "The PDF-first slice has an accepted current Primary Paper relationship.",
    )
    add(
        "source_processing_run",
        run.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED
        if run.status == "succeeded"
        else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.PRIMARY_PAPER_PROCESSED,
        "The required Primary Paper has a successful immutable processing run.",
    )

    method_invalidations = await uow.dependencies.list_unresolved_invalidations(
        "method_model", method.id
    )
    method_ok = (
        method.research_question_id == question.id
        and method.review_status == "accepted"
        and method.lifecycle_status == "current"
        and method.freshness_status == "current"
        and not method_invalidations
    )
    add(
        "method_model",
        method.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED if method_ok else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.METHOD_MODEL_CURRENT,
        "The accepted MethodModel addresses the exact current question and has no unresolved invalidation.",
    )
    try:
        MethodReviewPolicy.validate_model_acceptance(
            composition_members=tuple(
                MethodMemberSnapshot(
                    method_element_id=item.method_element_id,
                    element_lineage_id=item.element.lineage_id,
                    element_version=item.element.version,
                    review_section=MethodReviewSection(item.review_section),
                    member_role=MethodMemberRole(item.member_role),
                    sequence_index=item.sequence_index,
                    required=item.required,
                    inclusion_rationale=item.inclusion_rationale,
                    review_status=KnowledgeReviewStatus(item.element.review_status),
                    lifecycle_status=KnowledgeLifecycleStatus(item.element.lifecycle_status),
                )
                for item in method_members
            ),
            current_section_reviews=tuple(
                SectionReviewSnapshot(
                    review_section=MethodReviewSection(item.review_section),
                    outcome=SectionReviewOutcome(item.outcome),
                    member_set_fingerprint=item.member_set_fingerprint,
                    rationale=item.rationale,
                )
                for item in section_reviews
            ),
        )
        section_ok = all(
            (not item.required)
            or item.element.grounding_status not in {"unsupported", "contradicted"}
            for item in method_members
        )
    except (DomainValidationError, ResourceConflictError, ValueError):
        section_ok = False
    add(
        "method_model",
        method.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED if section_ok else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.METHOD_SECTIONS_REVIEWED,
        "Every MethodModel section and required member is resolved and review-current.",
    )
    method_assessment_ok = (
        method.current_assessment_id == method_assessment.id
        and method_assessment.method_model_id == method.id
        and method_assessment.outcome != "insufficient"
    )
    add(
        "method_model_assessment",
        method_assessment.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED
        if method_assessment_ok
        else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.METHOD_ASSESSMENT_CURRENT,
        "The MethodModel points to its latest sufficient assessment.",
    )
    if method_assessment.outcome == "sufficient_with_warnings":
        add(
            "method_model_assessment",
            method_assessment.id,
            DependencyCriticality.MATERIAL,
            EligibilityItemOutcome.INDETERMINATE,
            EligibilityReason.METHOD_ASSESSMENT_WARNINGS,
            "The current MethodModel assessment is sufficient with disclosed warnings.",
        )

    mental_invalidations = await uow.dependencies.list_unresolved_invalidations(
        "mental_model", mental.id
    )
    mental_ok = (
        mental.research_question_id == question.id
        and mental.method_model_id == method.id
        and mental.status == "current"
        and not mental_invalidations
    )
    add(
        "mental_model",
        mental.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED if mental_ok else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.MENTAL_MODEL_CURRENT,
        "The project-level Mental Model matches the exact question and accepted MethodModel.",
    )
    mental_assessment_ok = (
        mental_assessment.research_question_id == question.id
        and mental_assessment.method_model_id == method.id
        and mental_assessment.method_model_assessment_id == method_assessment.id
        and (
            mental_assessment.assessed_model_id == mental.id
            or mental_assessment.resulting_model_id == mental.id
        )
        and await mental_model_assessment_covers_current_state(
            uow, project, mental, mental_assessment
        )
    )
    add(
        "mental_model_assessment",
        mental_assessment.id,
        DependencyCriticality.BLOCKING,
        EligibilityItemOutcome.SATISFIED
        if mental_assessment_ok
        else EligibilityItemOutcome.UNSATISFIED,
        EligibilityReason.MENTAL_ASSESSMENT_CURRENT,
        "The latest MentalModel assessment covers the exact current accepted dependency chain.",
    )

    applicable = gate.operation_type == "mapping.review_gate.completed"
    if not applicable:
        add(
            "paper_repository_relationship",
            None,
            DependencyCriticality.INFORMATIONAL,
            EligibilityItemOutcome.NOT_APPLICABLE,
            EligibilityReason.REPOSITORY_NOT_APPLICABLE,
            "Repository evidence was explicitly adjudicated not applicable for this workflow path.",
        )
        mapping_coverage: dict[str, object] = {
            "applicable": False,
            "status": "not_applicable",
            "accepted_mapping_count": 0,
        }
    else:
        repo_ok = relationship is not None and relationship.repository_snapshot_id is not None
        add(
            "paper_repository_relationship",
            None if relationship is None else relationship.id,
            DependencyCriticality.BLOCKING,
            EligibilityItemOutcome.SATISFIED if repo_ok else EligibilityItemOutcome.UNSATISFIED,
            EligibilityReason.REPOSITORY_CURRENT,
            "The applicable repository branch has an accepted current relationship and immutable snapshot.",
        )
        if relationship is not None and relationship.repository_snapshot_id is not None:
            add(
                "repository_snapshot",
                relationship.repository_snapshot_id,
                DependencyCriticality.BLOCKING,
                EligibilityItemOutcome.SATISFIED,
                EligibilityReason.REPOSITORY_CURRENT,
                "The applicable repository evidence is bound to an immutable current snapshot.",
            )
        current_mappings = tuple(
            item
            for item in mappings
            if relationship is not None
            and item.repository_snapshot_id == relationship.repository_snapshot_id
            and item.freshness_status == "current"
        )
        if current_mappings:
            for current_mapping in current_mappings:
                add(
                    "paper_code_mapping",
                    current_mapping.id,
                    DependencyCriticality.INFORMATIONAL,
                    EligibilityItemOutcome.SATISFIED,
                    EligibilityReason.MAPPING_REVIEW_COMPLETE,
                    "Accepted current mapping is part of the applicable bounded repository evidence branch.",
                )
        else:
            add(
                "paper_code_mapping",
                None,
                DependencyCriticality.MATERIAL,
                EligibilityItemOutcome.INDETERMINATE,
                EligibilityReason.MAPPING_COVERAGE_PARTIAL,
                "Applicable mapping review completed without an accepted current mapping; Blueprint output must disclose bounded coverage.",
            )
        mapping_coverage = {
            "applicable": True,
            "status": "reviewed" if current_mappings else "partial",
            "accepted_mapping_count": len(current_mappings),
            "mapping_ids": [str(item.id) for item in current_mappings],
        }

    for item in knowledge:
        if item.object_type == "implementation_requirement":
            add(
                "knowledge_object",
                item.id,
                DependencyCriticality.INFORMATIONAL,
                EligibilityItemOutcome.SATISFIED,
                EligibilityReason.REQUIREMENT_CURRENT,
                "Accepted current project Requirement is an exact Blueprint input.",
            )
        elif item.object_type == "ambiguity":
            object_payload = await uow.knowledge.get_payload(item.id)
            resolution = (
                None
                if object_payload is None
                else object_payload.subtype_payload.get("resolution_status")
            )
            if resolution != "resolved":
                request = next(
                    (candidate for candidate in requests if candidate.ambiguity_id == item.id), None
                )
                dependencies = (
                    ()
                    if request is None
                    else await uow.dependencies.list_dependencies("judgment_request", request.id)
                )
                criticality = (
                    DependencyCriticality.BLOCKING
                    if any(edge.criticality == "blocking" for edge in dependencies)
                    else DependencyCriticality.MATERIAL
                )
                add(
                    "knowledge_object",
                    item.id,
                    criticality,
                    EligibilityItemOutcome.INDETERMINATE,
                    EligibilityReason.JUDGMENT_OPEN
                    if request is not None
                    else EligibilityReason.AMBIGUITY_UNRESOLVED,
                    "A current Judgment Request remains open for this exact Ambiguity."
                    if request is not None
                    else "An accepted unresolved Ambiguity remains visible; it blocks only when its persisted dependency scope is blocking.",
                )
    for judgment in judgments:
        add(
            "human_judgment",
            judgment.id,
            DependencyCriticality.INFORMATIONAL,
            EligibilityItemOutcome.SATISFIED,
            EligibilityReason.JUDGMENT_CURRENT,
            "Current immutable Human Judgment is an exact Blueprint input.",
        )

    fingerprint_payload: dict[str, object] = {
        "project": {
            "id": str(project.id),
            "lock_version": project.lock_version,
            "target_outcome": project.target_outcome,
            "reproduction_depth": project.reproduction_depth,
        },
        "question": {
            "id": str(question.id),
            "version": question.version,
            "text": question.question_text,
            "scope": question.scope_text,
        },
        "primary_paper": {
            "relationship_id": str(paper.id),
            "version": paper.version,
            "processing_run_id": str(run.id),
        },
        "method_model": {
            "id": str(method.id),
            "version": method.version,
            "composition_fingerprint": method.composition_fingerprint,
            "assessment": asdict(method_assessment),
        },
        "mental_model": {
            "id": str(mental.id),
            "version": mental.version,
            "composition_fingerprint": mental.composition_fingerprint,
            "assessment": asdict(mental_assessment),
        },
        "mapping_gate": {
            "id": str(gate.id),
            "fingerprint": gate.input_fingerprint,
            "coverage": mapping_coverage,
        },
        "knowledge": [
            {
                "id": str(item.id),
                "version": item.version,
                "review": item.review_status,
                "lifecycle": item.lifecycle_status,
                "grounding": item.grounding_status,
            }
            for item in knowledge
        ],
        "judgments": [
            {"id": str(item.id), "version": item.version, "lifecycle": item.lifecycle_status}
            for item in judgments
        ],
        "findings": [
            {
                **asdict(item),
                "target_id": None if item.target_id is None else str(item.target_id),
                "criticality": item.criticality.value,
                "outcome": item.outcome.value,
                "reason_code": item.reason_code.value,
            }
            for item in findings
        ],
    }
    return tuple(findings), mapping_coverage, fingerprint_payload
