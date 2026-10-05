"""Project-scoped deterministic Blueprint eligibility routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from researchflow.api.dependencies import ActorDependency, UnitOfWorkFactoryDependency
from researchflow.api.schemas.blueprint_eligibility import (
    AssessBlueprintEligibilityRequest,
    BlueprintEligibilityAssessmentResponse,
    BlueprintEligibilityCommandResponse,
    BlueprintEligibilityDetailResponse,
    BlueprintEligibilityItemResponse,
)
from researchflow.api.schemas.knowledge import OperationEventResponse
from researchflow.application.blueprint_eligibility import (
    AssessBlueprintEligibilityCommand,
    BlueprintEligibilityService,
)
from researchflow.errors import ResourceNotFoundError

router = APIRouter(
    prefix="/projects/{project_id}/blueprint-eligibility",
    tags=["blueprint-eligibility"],
)


@router.post("/assess", response_model=BlueprintEligibilityCommandResponse)
async def assess_blueprint_eligibility(
    project_id: UUID,
    body: AssessBlueprintEligibilityRequest,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
    request_id: Annotated[str | None, Header(alias="X-Request-ID", max_length=200)] = None,
) -> BlueprintEligibilityCommandResponse:
    result = await BlueprintEligibilityService(actor=actor, uow_factory=uow_factory).assess(
        AssessBlueprintEligibilityCommand(
            project_id=project_id,
            workflow_run_id=body.workflow_run_id,
            mapping_review_gate_event_id=body.mapping_review_gate_event_id,
            command_id=body.command_id,
            request_id=request_id,
        )
    )
    return BlueprintEligibilityCommandResponse(
        assessment=BlueprintEligibilityAssessmentResponse.model_validate(
            result.assessment, from_attributes=True
        ),
        items=tuple(
            BlueprintEligibilityItemResponse.model_validate(item, from_attributes=True)
            for item in result.items
        ),
        event=OperationEventResponse.model_validate(result.event, from_attributes=True),
        replayed=result.replayed,
    )


@router.get("", response_model=tuple[BlueprintEligibilityAssessmentResponse, ...])
async def list_blueprint_eligibility(
    project_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> tuple[BlueprintEligibilityAssessmentResponse, ...]:
    async with uow_factory(project_id) as uow:
        project = await uow.projects.get()
        if project is None or project.created_by_id != actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        rows = await uow.blueprints.list_eligibility_assessments()
        await uow.rollback()
    return tuple(
        BlueprintEligibilityAssessmentResponse.model_validate(row, from_attributes=True)
        for row in rows
    )


@router.get("/{assessment_id}", response_model=BlueprintEligibilityDetailResponse)
async def blueprint_eligibility_detail(
    project_id: UUID,
    assessment_id: UUID,
    actor: ActorDependency,
    uow_factory: UnitOfWorkFactoryDependency,
) -> BlueprintEligibilityDetailResponse:
    async with uow_factory(project_id) as uow:
        project = await uow.projects.get()
        if project is None or project.created_by_id != actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        assessment = await uow.blueprints.get_eligibility_exact(assessment_id)
        if assessment is None:
            raise ResourceNotFoundError(details={"resource": "blueprint_eligibility_assessment"})
        items = await uow.blueprints.list_eligibility_items(assessment_id)
        await uow.rollback()
    return BlueprintEligibilityDetailResponse(
        assessment=BlueprintEligibilityAssessmentResponse.model_validate(
            assessment, from_attributes=True
        ),
        items=tuple(
            BlueprintEligibilityItemResponse.model_validate(item, from_attributes=True)
            for item in items
        ),
    )
