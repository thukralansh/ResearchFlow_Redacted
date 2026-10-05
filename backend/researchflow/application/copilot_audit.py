"""FS-COP-01 short-transaction commands and read-only audit reconstruction."""

import hashlib
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.actor import ActorContext
from researchflow.domain.copilot import (
    COPILOT_AUDIT_POLICY_VERSION,
    COPILOT_MESSAGE_MAX_CHARACTERS,
    COPILOT_TITLE_MAX_CHARACTERS,
    CopilotMessageRole,
    CopilotThreadStatus,
    CopilotTurnStatus,
    validated_optional_text,
    validated_text,
)
from researchflow.errors import ResourceConflictError, ResourceNotFoundError
from researchflow.ports.copilot import (
    CopilotActionConfirmationData,
    CopilotActionProposalData,
    CopilotMessageData,
    CopilotThreadData,
    CopilotTurnData,
    NewCopilotActionConfirmation,
    NewCopilotActionProposal,
    NewCopilotMessage,
    NewCopilotThread,
    NewCopilotTurn,
)
from researchflow.ports.repositories import GenerationRecordData, ProjectData
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/fs-cop-01")


def copilot_turn_id_for_command(command_id: UUID) -> UUID:
    return uuid5(_NAMESPACE, f"turn:{command_id}")


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _owned(actor: ActorContext, project: ProjectData | None) -> None:
    if project is None or project.created_by_id != actor.actor_id:
        raise ResourceNotFoundError(details={"resource": "project"})


@dataclass(frozen=True, slots=True)
class CreateCopilotThreadCommand:
    project_id: UUID
    command_id: UUID
    title: str | None = None


@dataclass(frozen=True, slots=True)
class StartCopilotTurnCommand:
    project_id: UUID
    thread_id: UUID
    command_id: UUID
    message: str
    workflow_run_id: UUID | None = None
    context_manifest_fingerprint: str | None = None
    context_policy_version: str | None = None
    bounds_policy_version: str | None = None


@dataclass(frozen=True, slots=True)
class StartedCopilotTurn:
    thread: CopilotThreadData
    message: CopilotMessageData
    turn: CopilotTurnData
    replayed: bool


@dataclass(frozen=True, slots=True)
class FinalizeCopilotTurnCommand:
    project_id: UUID
    turn_id: UUID
    command_id: UUID
    assistant_message: str | None
    error_code: str | None = None
    terminal_generation_record_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RecordCopilotProposalCommand:
    project_id: UUID
    turn_id: UUID
    generation_record_id: UUID
    command_id: UUID
    action_type: str
    schema_version: str
    payload: dict[str, object]
    expected_state: dict[str, object]
    context_manifest_fingerprint: str
    confirmation_class: str
    impact_preview_fingerprint: str | None = None
    model_explanation: str = "Legacy audit-only proposal."


@dataclass(frozen=True, slots=True)
class RecordCopilotConfirmationCommand:
    project_id: UUID
    proposal_id: UUID
    command_id: UUID
    proposal_fingerprint: str
    preview_fingerprint: str | None
    rationale: str | None


@dataclass(frozen=True, slots=True)
class CopilotTurnAudit:
    turn: CopilotTurnData
    generation_records: tuple[GenerationRecordData, ...]
    proposals: tuple[CopilotActionProposalData, ...]
    confirmations: tuple[CopilotActionConfirmationData, ...]


@dataclass(frozen=True, slots=True)
class CopilotThreadAudit:
    thread: CopilotThreadData
    messages: tuple[CopilotMessageData, ...]
    turns: tuple[CopilotTurnAudit, ...]


class CopilotAuditService:
    """Create only conversation audit primitives; no retrieval or action semantics."""

    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def create_thread(self, command: CreateCopilotThreadCommand) -> CopilotThreadData:
        title = validated_optional_text(
            command.title, field="title", maximum=COPILOT_TITLE_MAX_CHARACTERS
        )
        fingerprint = _fingerprint({"actor_id": str(self._actor.actor_id), "title": title})
        thread_id = uuid5(_NAMESPACE, f"thread:{command.project_id}:{command.command_id}")
        async with self._uow_factory(command.project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            existing = await uow.copilot.get_thread(thread_id)
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                await uow.rollback()
                return existing
            thread = await uow.copilot.add_thread(
                NewCopilotThread(
                    id=thread_id,
                    project_id=command.project_id,
                    created_by_id=self._actor.actor_id,
                    title=title,
                    creation_command_id=command.command_id,
                    input_fingerprint=fingerprint,
                )
            )
            await uow.commit()
            return thread

    async def start_turn(self, command: StartCopilotTurnCommand) -> StartedCopilotTurn:
        content = validated_text(
            command.message, field="message", maximum=COPILOT_MESSAGE_MAX_CHARACTERS
        )
        fingerprint = _fingerprint(
            {
                "actor_id": str(self._actor.actor_id),
                "message": content,
                "thread_id": str(command.thread_id),
                "workflow_run_id": None
                if command.workflow_run_id is None
                else str(command.workflow_run_id),
                "context_manifest_fingerprint": command.context_manifest_fingerprint,
                "context_policy_version": command.context_policy_version,
                "bounds_policy_version": command.bounds_policy_version,
            }
        )
        message_id = uuid5(_NAMESPACE, f"user-message:{command.command_id}")
        turn_id = copilot_turn_id_for_command(command.command_id)
        async with self._uow_factory(command.project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            thread = await uow.copilot.get_thread_for_update(command.thread_id)
            if thread is None:
                raise ResourceNotFoundError(details={"resource": "copilot_thread"})
            if thread.status is not CopilotThreadStatus.ACTIVE and thread.status != "active":
                raise ResourceConflictError(details={"reason": "copilot_thread_archived"})
            existing = await uow.copilot.get_turn(turn_id)
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                message = await uow.copilot.get_message(existing.user_message_id)
                if message is None:
                    raise ResourceConflictError(details={"reason": "copilot_audit_incomplete"})
                await uow.rollback()
                return StartedCopilotTurn(thread, message, existing, True)
            if (
                command.workflow_run_id is not None
                and await uow.workflows.get_exact(command.workflow_run_id) is None
            ):
                raise ResourceNotFoundError(details={"resource": "workflow_run"})
            ordinal = len(await uow.copilot.list_messages(command.thread_id)) + 1
            message = await uow.copilot.add_message(
                NewCopilotMessage(
                    id=message_id,
                    project_id=command.project_id,
                    thread_id=command.thread_id,
                    ordinal=ordinal,
                    role=CopilotMessageRole.USER.value,
                    content=content,
                    reply_to_message_id=None,
                    created_by_id=self._actor.actor_id,
                    creation_command_id=command.command_id,
                    input_fingerprint=fingerprint,
                )
            )
            turn = await uow.copilot.add_turn(
                NewCopilotTurn(
                    id=turn_id,
                    project_id=command.project_id,
                    thread_id=command.thread_id,
                    user_message_id=message.id,
                    workflow_run_id=command.workflow_run_id,
                    creation_command_id=command.command_id,
                    input_fingerprint=fingerprint,
                    context_manifest_fingerprint=command.context_manifest_fingerprint,
                    context_policy_version=command.context_policy_version,
                    bounds_policy_version=command.bounds_policy_version,
                )
            )
            await uow.commit()
            return StartedCopilotTurn(thread, message, turn, False)

    async def finalize_turn(self, command: FinalizeCopilotTurnCommand) -> CopilotTurnData:
        succeeded = command.assistant_message is not None
        if succeeded == (command.error_code is not None):
            raise ResourceConflictError(details={"reason": "turn_result_invalid"})
        content = (
            validated_text(
                command.assistant_message,
                field="assistant_message",
                maximum=COPILOT_MESSAGE_MAX_CHARACTERS,
            )
            if command.assistant_message is not None
            else None
        )
        fingerprint = _fingerprint({"assistant_message": content, "error_code": command.error_code})
        message_id = uuid5(_NAMESPACE, f"assistant-message:{command.command_id}")
        async with self._uow_factory(command.project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            turn = await uow.copilot.get_turn_for_update(command.turn_id)
            if turn is None:
                raise ResourceNotFoundError(details={"resource": "copilot_turn"})
            if turn.status != CopilotTurnStatus.PENDING_GENERATION.value:
                if turn.status == (
                    CopilotTurnStatus.SUCCEEDED.value
                    if succeeded
                    else CopilotTurnStatus.FAILED.value
                ):
                    message = (
                        None
                        if turn.assistant_message_id is None
                        else await uow.copilot.get_message(turn.assistant_message_id)
                    )
                    prior = _fingerprint(
                        {
                            "assistant_message": None if message is None else message.content,
                            "error_code": turn.error_code,
                        }
                    )
                    if prior == fingerprint:
                        await uow.rollback()
                        return turn
                raise ResourceConflictError(details={"reason": "copilot_turn_state_changed"})
            generations = await uow.generations.list_for_copilot_turn(turn.id)
            if not 1 <= len(generations) <= 2:
                raise ResourceConflictError(
                    details={"reason": "copilot_generation_owner_incomplete"}
                )
            generation = (
                generations[0]
                if command.terminal_generation_record_id is None and len(generations) == 1
                else next(
                    (
                        item
                        for item in generations
                        if item.id == command.terminal_generation_record_id
                    ),
                    None,
                )
            )
            if generation is None:
                raise ResourceConflictError(
                    details={"reason": "copilot_terminal_generation_mismatch"}
                )
            expected_generation_status = "succeeded" if succeeded else None
            if (
                expected_generation_status is not None
                and generation.status != expected_generation_status
            ):
                raise ResourceConflictError(details={"reason": "turn_generation_mismatch"})
            assistant_id = None
            if content is not None:
                ordinal = len(await uow.copilot.list_messages(turn.thread_id)) + 1
                assistant = await uow.copilot.add_message(
                    NewCopilotMessage(
                        id=message_id,
                        project_id=command.project_id,
                        thread_id=turn.thread_id,
                        ordinal=ordinal,
                        role=CopilotMessageRole.ASSISTANT.value,
                        content=content,
                        reply_to_message_id=turn.user_message_id,
                        created_by_id=None,
                        creation_command_id=command.command_id,
                        input_fingerprint=fingerprint,
                    )
                )
                assistant_id = assistant.id
            finalized = await uow.copilot.finalize_turn(
                turn.id,
                expected_status=CopilotTurnStatus.PENDING_GENERATION.value,
                status=(
                    CopilotTurnStatus.SUCCEEDED.value
                    if succeeded
                    else CopilotTurnStatus.FAILED.value
                ),
                assistant_message_id=assistant_id,
                error_code=command.error_code,
            )
            await uow.commit()
            return finalized

    async def record_proposal(
        self, command: RecordCopilotProposalCommand
    ) -> CopilotActionProposalData:
        input_fingerprint = _fingerprint(
            {
                "action_type": command.action_type,
                "confirmation_class": command.confirmation_class,
                "context_manifest_fingerprint": command.context_manifest_fingerprint,
                "expected_state": command.expected_state,
                "generation_record_id": str(command.generation_record_id),
                "impact_preview_fingerprint": command.impact_preview_fingerprint,
                "model_explanation": command.model_explanation,
                "payload": command.payload,
                "schema_version": command.schema_version,
                "turn_id": str(command.turn_id),
            }
        )
        proposal_id = uuid5(_NAMESPACE, f"proposal:{command.command_id}")
        async with self._uow_factory(command.project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            turn = await uow.copilot.get_turn(command.turn_id)
            generation = await uow.generations.get_exact(command.generation_record_id)
            if turn is None or generation is None:
                raise ResourceNotFoundError(details={"resource": "copilot_turn_generation"})
            if generation.copilot_turn_id != turn.id or generation.status != "succeeded":
                raise ResourceConflictError(details={"reason": "proposal_generation_mismatch"})
            existing = await uow.copilot.get_proposal(proposal_id)
            if existing is not None:
                if existing.input_fingerprint != input_fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                await uow.rollback()
                return existing
            proposal = await uow.copilot.add_proposal(
                NewCopilotActionProposal(
                    id=proposal_id,
                    project_id=command.project_id,
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    generation_record_id=generation.id,
                    action_type=validated_text(
                        command.action_type, field="action_type", maximum=200
                    ),
                    schema_version=validated_text(
                        command.schema_version, field="schema_version", maximum=200
                    ),
                    model_explanation=validated_text(
                        command.model_explanation,
                        field="model_explanation",
                        maximum=4_000,
                    ),
                    payload=dict(command.payload),
                    payload_fingerprint=_fingerprint(command.payload),
                    expected_state=dict(command.expected_state),
                    expected_state_fingerprint=_fingerprint(command.expected_state),
                    context_manifest_fingerprint=command.context_manifest_fingerprint,
                    confirmation_class=validated_text(
                        command.confirmation_class,
                        field="confirmation_class",
                        maximum=200,
                    ),
                    impact_preview_fingerprint=command.impact_preview_fingerprint,
                    action_policy_version=COPILOT_AUDIT_POLICY_VERSION,
                    creation_command_id=command.command_id,
                    input_fingerprint=input_fingerprint,
                )
            )
            await uow.commit()
            return proposal

    async def record_confirmation(
        self, command: RecordCopilotConfirmationCommand
    ) -> CopilotActionConfirmationData:
        input_fingerprint = _fingerprint(
            {
                "actor_id": str(self._actor.actor_id),
                "preview_fingerprint": command.preview_fingerprint,
                "proposal_fingerprint": command.proposal_fingerprint,
                "proposal_id": str(command.proposal_id),
                "rationale": command.rationale,
            }
        )
        confirmation_id = uuid5(_NAMESPACE, f"confirmation:{command.command_id}")
        async with self._uow_factory(command.project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            proposal = await uow.copilot.get_proposal(command.proposal_id)
            if proposal is None:
                raise ResourceNotFoundError(details={"resource": "copilot_action_proposal"})
            if (
                command.proposal_fingerprint != proposal.input_fingerprint
                or command.preview_fingerprint != proposal.impact_preview_fingerprint
            ):
                raise ResourceConflictError(
                    details={"reason": "copilot_confirmation_identity_mismatch"}
                )
            prior = await uow.copilot.list_confirmations(proposal.id)
            for confirmation in prior:
                if confirmation.id == confirmation_id:
                    if confirmation.input_fingerprint != input_fingerprint:
                        raise ResourceConflictError(details={"reason": "command_payload_changed"})
                    await uow.rollback()
                    return confirmation
            confirmation = await uow.copilot.add_confirmation(
                NewCopilotActionConfirmation(
                    id=confirmation_id,
                    project_id=command.project_id,
                    proposal_id=proposal.id,
                    actor_id=self._actor.actor_id,
                    proposal_fingerprint=command.proposal_fingerprint,
                    preview_fingerprint=command.preview_fingerprint,
                    rationale=command.rationale,
                    command_id=command.command_id,
                    input_fingerprint=input_fingerprint,
                )
            )
            await uow.commit()
            return confirmation


class CopilotAuditQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def list_threads(self, project_id: UUID) -> tuple[CopilotThreadData, ...]:
        async with self._uow_factory(project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            rows = await uow.copilot.list_threads()
            await uow.rollback()
            return rows

    async def get_thread(self, project_id: UUID, thread_id: UUID) -> CopilotThreadAudit:
        async with self._uow_factory(project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            thread = await uow.copilot.get_thread(thread_id)
            if thread is None:
                raise ResourceNotFoundError(details={"resource": "copilot_thread"})
            messages = await uow.copilot.list_messages(thread_id)
            turns = []
            for turn in await uow.copilot.list_turns(thread_id):
                turns.append(await self._turn_audit(uow, turn))
            await uow.rollback()
            return CopilotThreadAudit(thread, messages, tuple(turns))

    async def get_turn(self, project_id: UUID, turn_id: UUID) -> CopilotTurnAudit:
        async with self._uow_factory(project_id) as uow:
            _owned(self._actor, await uow.projects.get())
            turn = await uow.copilot.get_turn(turn_id)
            if turn is None:
                raise ResourceNotFoundError(details={"resource": "copilot_turn"})
            result = await self._turn_audit(uow, turn)
            await uow.rollback()
            return result

    @staticmethod
    async def _turn_audit(uow: UnitOfWork, turn: CopilotTurnData) -> CopilotTurnAudit:
        proposals = await uow.copilot.list_proposals(turn.id)
        confirmations: list[CopilotActionConfirmationData] = []
        for proposal in proposals:
            confirmations.extend(await uow.copilot.list_confirmations(proposal.id))
        return CopilotTurnAudit(
            turn=turn,
            generation_records=await uow.generations.list_for_copilot_turn(turn.id),
            proposals=proposals,
            confirmations=tuple(confirmations),
        )
