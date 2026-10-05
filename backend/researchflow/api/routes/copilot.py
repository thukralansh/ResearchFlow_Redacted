"""Project-scoped Copilot turns, proposal previews, and confirmed actions."""

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
from researchflow.api.schemas.copilot import (
    BuildCopilotRetrievalRequest,
    ConfirmCopilotActionRequest,
    ConfirmedCopilotActionResponse,
    CopilotActionConfirmationResponse,
    CopilotActionProposalResponse,
    CopilotManifestResponse,
    CopilotProposalPreviewResponse,
    CopilotThreadAuditResponse,
    CopilotThreadResponse,
    CopilotTurnAuditResponse,
    CreateCopilotThreadRequest,
    ReadOnlyCopilotTurnResponse,
    RunReadOnlyCopilotTurnRequest,
)
from researchflow.application.blueprint_review import BlueprintReviewService
from researchflow.application.copilot_actions import (
    ConfirmActionCommand,
    CopilotActionService,
    ExistingHumanJudgmentAdapter,
)
from researchflow.application.copilot_audit import (
    CopilotAuditQueryService,
    CopilotAuditService,
    CreateCopilotThreadCommand,
)
from researchflow.application.copilot_context import (
    BuildCopilotContextCommand,
    BuildCopilotRetrievalCommand,
    CopilotContextService,
)
from researchflow.application.copilot_turns import (
    CopilotTurnService,
    RunReadOnlyCopilotTurnCommand,
)
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.judgments import (
    HumanJudgmentService,
    OrderedJudgmentReassessor,
)
from researchflow.application.mental_model_reassessment import MentalModelReassessmentService
from researchflow.application.method_model_reassessment import MethodModelReassessmentService
from researchflow.application.staleness import StalenessService

router = APIRouter(prefix="/projects/{project_id}/copilot", tags=["copilot-audit"])


def _action_service(
    *,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> CopilotActionService:
    generation = StructuredGenerationService(
        uow_factory=uow_factory,
        provider=provider,
        prompt_registry=prompts,
        max_context_bytes=settings.max_generation_context_bytes,
    )
    policy = configured_model_policy(settings)
    staleness = StalenessService(actor=actor, uow_factory=uow_factory)
    judgment_service = HumanJudgmentService(
        actor=actor,
        uow_factory=uow_factory,
        reassessor=OrderedJudgmentReassessor(
            actor=actor,
            uow_factory=uow_factory,
            model_policy=policy,
            method_service=MethodModelReassessmentService(
                actor=actor,
                uow_factory=uow_factory,
                generation_service=generation,
                staleness_hook=staleness,
            ),
            mental_service=MentalModelReassessmentService(
                actor=actor,
                uow_factory=uow_factory,
                generation_service=generation,
                staleness_hook=staleness,
            ),
        ),
        staleness_hook=staleness,
    )
    return CopilotActionService(
        actor=actor,
        uow_factory=uow_factory,
        judgment_executor=ExistingHumanJudgmentAdapter(
            service=judgment_service, uow_factory=uow_factory
        ),
        blueprint_service=BlueprintReviewService(
            actor=actor,
            uow_factory=uow_factory,
            generation_service=generation,
        ),
        blueprint_model_policy=policy,
    )


@router.post("/threads", response_model=CopilotThreadResponse, status_code=201)
async def create_copilot_thread(
    project_id: UUID,
    request: CreateCopilotThreadRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> CopilotThreadResponse:
    row = await CopilotAuditService(actor=actor, uow_factory=uow_factory).create_thread(
        CreateCopilotThreadCommand(
            project_id=project_id,
            command_id=request.command_id,
            title=request.title,
        )
    )
    return CopilotThreadResponse.model_validate(row)


@router.post(
    "/threads/{thread_id}/turns",
    response_model=ReadOnlyCopilotTurnResponse,
)
async def run_read_only_copilot_turn(
    project_id: UUID,
    thread_id: UUID,
    request: RunReadOnlyCopilotTurnRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> ReadOnlyCopilotTurnResponse:
    result = await CopilotTurnService(
        actor=actor,
        uow_factory=uow_factory,
        generation_service=StructuredGenerationService(
            uow_factory=uow_factory,
            provider=provider,
            prompt_registry=prompts,
            max_context_bytes=settings.max_generation_context_bytes,
        ),
        action_recorder=_action_service(
            actor=actor,
            settings=settings,
            uow_factory=uow_factory,
            provider=provider,
            prompts=prompts,
        ),
    ).run(
        RunReadOnlyCopilotTurnCommand(
            project_id=project_id,
            thread_id=thread_id,
            command_id=request.command_id,
            message=request.message,
            model_policy=configured_model_policy(settings),
        )
    )
    return ReadOnlyCopilotTurnResponse.model_validate(result)


@router.get(
    "/proposals/{proposal_id}/preview",
    response_model=CopilotProposalPreviewResponse,
)
async def preview_copilot_action(
    project_id: UUID,
    proposal_id: UUID,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> CopilotProposalPreviewResponse:
    result = await _action_service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).preview(project_id, proposal_id)
    return CopilotProposalPreviewResponse.model_validate(result)


@router.post(
    "/proposals/{proposal_id}/confirm",
    response_model=ConfirmedCopilotActionResponse,
)
async def confirm_copilot_action(
    project_id: UUID,
    proposal_id: UUID,
    body: ConfirmCopilotActionRequest,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> ConfirmedCopilotActionResponse:
    result = await _action_service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).confirm(
        ConfirmActionCommand(
            project_id=project_id,
            proposal_id=proposal_id,
            command_id=body.command_id,
            expected_proposal_fingerprint=body.expected_proposal_fingerprint,
            expected_preview_fingerprint=body.expected_preview_fingerprint,
            rationale=body.rationale,
            request_id=request_id,
        )
    )
    return ConfirmedCopilotActionResponse(
        proposal=CopilotActionProposalResponse.model_validate(result.proposal),
        confirmation=CopilotActionConfirmationResponse.model_validate(result.confirmation),
        outcome_event_id=result.outcome_event.id,
        operation_type=result.outcome_event.operation_type,
        replayed=result.replayed,
    )


@router.post(
    "/proposals/{proposal_id}/reconcile",
    response_model=ConfirmedCopilotActionResponse,
)
async def reconcile_copilot_action(
    project_id: UUID,
    proposal_id: UUID,
    actor: ActorDependency,
    settings: SettingsDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    provider: StructuredGenerationProviderDependency,
    prompts: PromptRegistryDependency,
) -> ConfirmedCopilotActionResponse:
    result = await _action_service(
        actor=actor,
        settings=settings,
        uow_factory=uow_factory,
        provider=provider,
        prompts=prompts,
    ).reconcile(project_id, proposal_id)
    return ConfirmedCopilotActionResponse(
        proposal=CopilotActionProposalResponse.model_validate(result.proposal),
        confirmation=CopilotActionConfirmationResponse.model_validate(result.confirmation),
        outcome_event_id=result.outcome_event.id,
        operation_type=result.outcome_event.operation_type,
        replayed=result.replayed,
    )


@router.post("/context-manifests", response_model=CopilotManifestResponse, status_code=201)
async def build_copilot_context_manifest(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> CopilotManifestResponse:
    row = await CopilotContextService(actor=actor, uow_factory=uow_factory).build_context(
        BuildCopilotContextCommand(project_id=project_id)
    )
    return CopilotManifestResponse.model_validate(row)


@router.post("/retrieval-manifests", response_model=CopilotManifestResponse, status_code=201)
async def build_copilot_retrieval_manifest(
    project_id: UUID,
    request: BuildCopilotRetrievalRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> CopilotManifestResponse:
    row = await CopilotContextService(actor=actor, uow_factory=uow_factory).build_retrieval(
        BuildCopilotRetrievalCommand(
            project_id=project_id,
            context_manifest_id=request.context_manifest_id,
            intent=request.intent,
            query=request.query,
            target_ids=request.target_ids,
            allowed_kinds=request.allowed_kinds,
        )
    )
    return CopilotManifestResponse.model_validate(row)


@router.get("/manifests/{manifest_id}", response_model=CopilotManifestResponse)
async def get_copilot_manifest(
    project_id: UUID,
    manifest_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> CopilotManifestResponse:
    row = await CopilotContextService(actor=actor, uow_factory=uow_factory).get_manifest(
        project_id, manifest_id
    )
    return CopilotManifestResponse.model_validate(row)


@router.get("/threads", response_model=tuple[CopilotThreadResponse, ...])
async def list_copilot_threads(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[CopilotThreadResponse, ...]:
    rows = await CopilotAuditQueryService(actor=actor, uow_factory=uow_factory).list_threads(
        project_id
    )
    return tuple(CopilotThreadResponse.model_validate(row) for row in rows)


@router.get("/threads/{thread_id}", response_model=CopilotThreadAuditResponse)
async def get_copilot_thread(
    project_id: UUID,
    thread_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> CopilotThreadAuditResponse:
    row = await CopilotAuditQueryService(actor=actor, uow_factory=uow_factory).get_thread(
        project_id, thread_id
    )
    return CopilotThreadAuditResponse.model_validate(row)


@router.get("/turns/{turn_id}", response_model=CopilotTurnAuditResponse)
async def get_copilot_turn(
    project_id: UUID,
    turn_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> CopilotTurnAuditResponse:
    row = await CopilotAuditQueryService(actor=actor, uow_factory=uow_factory).get_turn(
        project_id, turn_id
    )
    return CopilotTurnAuditResponse.model_validate(row)
