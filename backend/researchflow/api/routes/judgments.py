"""Project-scoped Judgment Request classification, drafting, and inspection routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from researchflow.api.dependencies import (
    ActorDependency,
    PromptRegistryDependency,
    SettingsDependency,
    StructuredGenerationProviderDependency,
    UnitOfWorkFactoryDependency,
)
from researchflow.api.model_policy import configured_model_policy
from researchflow.api.schemas.judgments import (
    CreateJudgmentRequestBody,
    CreateJudgmentRequestResponse,
    HumanJudgmentHistoryItemResponse,
    HumanJudgmentResponse,
    JudgmentDependencyResponse,
    JudgmentEvidenceResponse,
    JudgmentOptionResponse,
    JudgmentRequestDetailResponse,
    JudgmentRequestRecordResponse,
    JudgmentRequestSummaryResponse,
    JudgmentResolutionResponse,
    ResolveHumanJudgmentBody,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.judgment_requests import (
    CreateJudgmentRequestCommand,
    JudgmentRequestDetail,
    JudgmentRequestQueryService,
    JudgmentRequestService,
)
from researchflow.application.judgments import (
    HumanJudgmentQueryService,
    HumanJudgmentService,
    OrderedJudgmentReassessor,
    ResolveJudgmentCommand,
)
from researchflow.application.mental_model_reassessment import MentalModelReassessmentService
from researchflow.application.method_model_reassessment import MethodModelReassessmentService
from researchflow.application.staleness import StalenessService

router = APIRouter(prefix="/projects/{project_id}/judgment-requests", tags=["judgments"])


@router.post("", response_model=CreateJudgmentRequestResponse)
async def create_judgment_request(
    project_id: UUID,
    body: CreateJudgmentRequestBody,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> CreateJudgmentRequestResponse:
    service = JudgmentRequestService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
    )
    outcome = await service.create(
        CreateJudgmentRequestCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            ambiguity_id=body.ambiguity_id,
            affected_dependencies=tuple(body.affected_dependencies),
            model_policy=configured_model_policy(settings),
            explicit_rerun=body.explicit_rerun,
            request_id=request_id,
        )
    )
    return CreateJudgmentRequestResponse(
        outcome=outcome.outcome,
        request=None if outcome.request is None else _detail_response(outcome.request),
        reason=outcome.reason,
        replayed=outcome.replayed,
    )


@router.get("", response_model=tuple[JudgmentRequestSummaryResponse, ...])
async def list_judgment_requests(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[JudgmentRequestSummaryResponse, ...]:
    values = await JudgmentRequestQueryService(actor=actor, uow_factory=uow_factory).list(
        project_id
    )
    return tuple(
        JudgmentRequestSummaryResponse(
            request=JudgmentRequestRecordResponse.model_validate(
                item.request, from_attributes=True
            ),
            option_count=item.option_count,
            highest_criticality=item.highest_criticality,
        )
        for item in values
    )


@router.get("/{request_id}", response_model=JudgmentRequestDetailResponse)
async def judgment_request_detail(
    project_id: UUID,
    request_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> JudgmentRequestDetailResponse:
    detail = await JudgmentRequestQueryService(actor=actor, uow_factory=uow_factory).detail(
        project_id, request_id
    )
    return _detail_response(detail)


def _detail_response(detail: JudgmentRequestDetail) -> JudgmentRequestDetailResponse:
    return JudgmentRequestDetailResponse(
        request=JudgmentRequestRecordResponse.model_validate(detail.request, from_attributes=True),
        ambiguity=detail.ambiguity,
        options=tuple(
            JudgmentOptionResponse.model_validate(item, from_attributes=True)
            for item in detail.options
        ),
        evidence=tuple(
            JudgmentEvidenceResponse(
                id=item.id,
                relation=item.evidence_relation,
                excerpt=item.evidence_excerpt,
                confidence=item.confidence,
                review_status=item.review_status,
                lifecycle_status=item.lifecycle_status,
                source_chunk_id=item.source_chunk_id,
                repository_component_id=item.repository_component_id,
            )
            for item in detail.evidence
        ),
        dependencies=tuple(
            JudgmentDependencyResponse.model_validate(item, from_attributes=True)
            for item in detail.dependencies
        ),
        recommendation=detail.recommendation,
        no_recommendation_reason=detail.no_recommendation_reason,
    )


@router.get(
    "/{request_id}/judgments",
    response_model=tuple[HumanJudgmentHistoryItemResponse, ...],
)
async def list_human_judgment_history(
    project_id: UUID,
    request_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[HumanJudgmentHistoryItemResponse, ...]:
    items = await HumanJudgmentQueryService(actor=actor, uow_factory=uow_factory).history(
        project_id, request_id
    )
    return tuple(
        HumanJudgmentHistoryItemResponse(
            judgment=HumanJudgmentResponse.model_validate(item.judgment, from_attributes=True),
            dependencies=tuple(
                JudgmentDependencyResponse.model_validate(value, from_attributes=True)
                for value in item.dependencies
            ),
            reassessment_status=item.reassessment_status,
            method_model_id=item.method_model_id,
            method_model_assessment_id=item.method_model_assessment_id,
            mental_model_id=item.mental_model_id,
            mental_model_assessment_id=item.mental_model_assessment_id,
            status_event_id=item.status_event_id,
            error=item.error,
        )
        for item in items
    )


@router.post("/{request_id}/resolve", response_model=JudgmentResolutionResponse)
async def resolve_human_judgment(
    project_id: UUID,
    request_id: UUID,
    body: ResolveHumanJudgmentBody,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> JudgmentResolutionResponse:
    generation = StructuredGenerationService(
        uow_factory=uow_factory,
        provider=provider,
        prompt_registry=prompts,
        max_context_bytes=settings.max_generation_context_bytes,
    )
    policy = configured_model_policy(settings)
    reassessor = OrderedJudgmentReassessor(
        actor=actor,
        uow_factory=uow_factory,
        model_policy=policy,
        method_service=MethodModelReassessmentService(
            actor=actor,
            uow_factory=uow_factory,
            generation_service=generation,
            staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
        ),
        mental_service=MentalModelReassessmentService(
            actor=actor,
            uow_factory=uow_factory,
            generation_service=generation,
            staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
        ),
    )
    result = await HumanJudgmentService(
        actor=actor,
        uow_factory=uow_factory,
        reassessor=reassessor,
        staleness_hook=StalenessService(actor=actor, uow_factory=uow_factory),
    ).resolve(
        ResolveJudgmentCommand(
            project_id=project_id,
            request_id=request_id,
            workflow_run_id=body.workflow_run_id,
            command_id=body.command_id,
            selected_option_id=body.selected_option_id,
            custom_decision=body.custom_decision,
            rationale=body.rationale,
            confidence=body.confidence,
            method_model_affected=body.method_model_affected,
            method_impact_rationale=body.method_impact_rationale,
            mapping_review_gate_event_id=body.mapping_review_gate_event_id,
            repository_applicable=body.repository_applicable,
            confirmed=body.confirmed,
            expected_current_judgment_id=body.expected_current_judgment_id,
        )
    )
    return JudgmentResolutionResponse(
        judgment=HumanJudgmentResponse.model_validate(result.judgment, from_attributes=True),
        reassessment_status=result.reassessment_status,
        method_model_id=result.method_model_id,
        method_model_assessment_id=result.method_model_assessment_id,
        mental_model_id=result.mental_model_id,
        mental_model_assessment_id=result.mental_model_assessment_id,
        replayed=result.replayed,
        error=result.error,
    )
