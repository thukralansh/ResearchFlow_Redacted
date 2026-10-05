"""Human-authoritative operational VerificationCheck result recording."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.actor import ActorContext
from researchflow.domain.implementation import canonical_fingerprint, validate_human_text
from researchflow.domain.verification import VERIFICATION_CONTRACT_VERSION, validate_result_status
from researchflow.errors import InternalError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    NewOperationEvent,
    OperationEventData,
    VerificationCheckData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/-verification")
_RESULT_RECORDED = "verification_check.result_recorded"


@dataclass(frozen=True, slots=True)
class RecordVerificationResultCommand:
    project_id: UUID
    check_id: UUID
    command_id: UUID
    expected_result_status: str | None
    result_status: str
    actual_result: str
    rationale: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    check: VerificationCheckData
    event: OperationEventData
    replayed: bool


class VerificationService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def record_result(self, command: RecordVerificationResultCommand) -> VerificationResult:
        status = validate_result_status(command.result_status)
        actual = validate_human_text(command.actual_result, reason="verification_result_invalid")
        rationale = validate_human_text(command.rationale, reason="review_rationale_invalid")
        fingerprint = canonical_fingerprint(
            {
                "policy_version": VERIFICATION_CONTRACT_VERSION,
                "check_id": command.check_id,
                "expected_result_status": command.expected_result_status,
                "result_status": status,
                "actual_result": actual,
                "rationale": rationale,
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            await _require_owned(uow, self._actor)
            replay = await uow.events.get_by_idempotency_key(
                _RESULT_RECORDED, str(command.command_id)
            )
            if replay is not None:
                result = await _replay(uow, replay, fingerprint)
                await uow.rollback()
                return result
            check = await uow.blueprints.get_check_for_update(command.check_id)
            if check is None:
                raise ResourceNotFoundError(details={"resource": "verification_check"})
            if check.lifecycle_status != "current" or check.freshness_status != "current":
                raise ResourceConflictError(details={"reason": "verification_check_not_current"})
            blueprint = await uow.blueprints.get_exact_for_update(check.blueprint_id)
            if blueprint is None or blueprint.status not in {
                "provisional",
                "review_ready",
                "accepted",
            }:
                raise ResourceConflictError(
                    details={"reason": "verification_blueprint_not_current"}
                )
            updated = await uow.blueprints.record_check_result(
                check.id,
                expected_result_status=command.expected_result_status,
                result_status=status,
                actual_result=actual,
                result_actor_id=self._actor.actor_id,
            )
            event = await uow.events.append(
                NewOperationEvent(
                    id=uuid5(_NAMESPACE, f"event:{command.command_id}"),
                    project_id=command.project_id,
                    workflow_run_id=None,
                    operation_type=_RESULT_RECORDED,
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=(
                        {
                            "type": "verification_check",
                            "id": str(check.id),
                            "expected_result_status": command.expected_result_status,
                        },
                    ),
                    output_refs=(
                        {
                            "type": "verification_check",
                            "id": str(updated.id),
                            "result_status": updated.result_status,
                            "actual_result": updated.actual_result,
                        },
                    ),
                    reason=rationale,
                    idempotency_key=str(command.command_id),
                    input_fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return VerificationResult(updated, event, False)


async def _replay(
    uow: UnitOfWork, event: OperationEventData, fingerprint: str
) -> VerificationResult:
    if event.input_fingerprint != fingerprint:
        raise ResourceConflictError(details={"reason": "idempotency_fingerprint_mismatch"})
    reference = next(
        (item for item in event.output_refs if item.get("type") == "verification_check"),
        None,
    )
    if reference is None:
        raise InternalError(details={"reason": "verification_result_replay_output_missing"})
    check = await uow.blueprints.get_check_exact(UUID(str(reference["id"])))
    if check is None:
        raise InternalError(details={"reason": "verification_result_replay_target_missing"})
    snapshot = replace(
        check,
        result_status=str(reference["result_status"]),
        actual_result=str(reference["actual_result"]),
    )
    return VerificationResult(snapshot, event, True)


async def _require_owned(uow: UnitOfWork, actor: ActorContext) -> None:
    project = await uow.projects.get()
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})
