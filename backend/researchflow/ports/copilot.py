"""Project-scoped persistence contracts for Copilot conversation and action audit."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CopilotStructuredItemData:
    kind: str
    id: UUID
    project_id: UUID
    content: str
    identity: dict[str, object]
    state: dict[str, object]
    provenance: dict[str, object]
    material: bool = False


@dataclass(frozen=True, slots=True)
class CopilotEvidenceItemData:
    kind: str
    id: UUID
    project_id: UUID
    content: str
    content_hash: str
    identity: dict[str, object]
    state: dict[str, object]
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class CopilotManifestData:
    id: UUID
    project_id: UUID
    kind: str
    parent_manifest_id: UUID | None
    selector: dict[str, object]
    selector_fingerprint: str
    state_fingerprint: str
    policy_version: str
    bounds_policy_version: str
    ordered_items: tuple[dict[str, object], ...]
    limitations: tuple[dict[str, object], ...]
    truncated: bool
    manifest_fingerprint: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewCopilotManifest:
    id: UUID
    project_id: UUID
    kind: str
    parent_manifest_id: UUID | None
    selector: dict[str, object]
    selector_fingerprint: str
    state_fingerprint: str
    policy_version: str
    bounds_policy_version: str
    ordered_items: tuple[dict[str, object], ...]
    limitations: tuple[dict[str, object], ...]
    truncated: bool
    manifest_fingerprint: str


@dataclass(frozen=True, slots=True)
class CopilotThreadData:
    id: UUID
    project_id: UUID
    created_by_id: UUID
    title: str | None
    status: str
    lock_version: int
    creation_command_id: UUID
    input_fingerprint: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewCopilotThread:
    id: UUID
    project_id: UUID
    created_by_id: UUID
    title: str | None
    creation_command_id: UUID
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class CopilotMessageData:
    id: UUID
    project_id: UUID
    thread_id: UUID
    ordinal: int
    role: str
    content: str
    reply_to_message_id: UUID | None
    semantic_status: str
    created_by_id: UUID | None
    creation_command_id: UUID
    input_fingerprint: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewCopilotMessage:
    id: UUID
    project_id: UUID
    thread_id: UUID
    ordinal: int
    role: str
    content: str
    reply_to_message_id: UUID | None
    created_by_id: UUID | None
    creation_command_id: UUID
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class CopilotTurnData:
    id: UUID
    project_id: UUID
    thread_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    workflow_run_id: UUID | None
    status: str
    context_manifest_fingerprint: str | None
    retrieval_manifest_fingerprint: str | None
    context_policy_version: str | None
    bounds_policy_version: str | None
    creation_command_id: UUID
    input_fingerprint: str
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewCopilotTurn:
    id: UUID
    project_id: UUID
    thread_id: UUID
    user_message_id: UUID
    workflow_run_id: UUID | None
    creation_command_id: UUID
    input_fingerprint: str
    context_manifest_fingerprint: str | None = None
    retrieval_manifest_fingerprint: str | None = None
    context_policy_version: str | None = None
    bounds_policy_version: str | None = None


@dataclass(frozen=True, slots=True)
class CopilotActionProposalData:
    id: UUID
    project_id: UUID
    thread_id: UUID
    turn_id: UUID
    generation_record_id: UUID
    action_type: str
    schema_version: str
    model_explanation: str
    payload: dict[str, object]
    payload_fingerprint: str
    expected_state: dict[str, object]
    expected_state_fingerprint: str
    context_manifest_fingerprint: str
    confirmation_class: str
    impact_preview_fingerprint: str | None
    action_policy_version: str
    status: str
    outcome_event_id: UUID | None
    invalidation_reason: str | None
    creation_command_id: UUID
    input_fingerprint: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewCopilotActionProposal:
    id: UUID
    project_id: UUID
    thread_id: UUID
    turn_id: UUID
    generation_record_id: UUID
    action_type: str
    schema_version: str
    model_explanation: str
    payload: dict[str, object]
    payload_fingerprint: str
    expected_state: dict[str, object]
    expected_state_fingerprint: str
    context_manifest_fingerprint: str
    confirmation_class: str
    impact_preview_fingerprint: str | None
    action_policy_version: str
    creation_command_id: UUID
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class CopilotActionConfirmationData:
    id: UUID
    project_id: UUID
    proposal_id: UUID
    actor_id: UUID
    proposal_fingerprint: str
    preview_fingerprint: str | None
    rationale: str | None
    command_id: UUID
    input_fingerprint: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewCopilotActionConfirmation:
    id: UUID
    project_id: UUID
    proposal_id: UUID
    actor_id: UUID
    proposal_fingerprint: str
    preview_fingerprint: str | None
    rationale: str | None
    command_id: UUID
    input_fingerprint: str


class CopilotRepository(Protocol):
    project_id: UUID

    async def add_thread(self, value: NewCopilotThread) -> CopilotThreadData: ...
    async def get_thread(self, thread_id: UUID) -> CopilotThreadData | None: ...
    async def get_thread_for_update(self, thread_id: UUID) -> CopilotThreadData | None: ...
    async def list_threads(self) -> tuple[CopilotThreadData, ...]: ...
    async def archive_thread(
        self, thread_id: UUID, *, expected_lock_version: int
    ) -> CopilotThreadData: ...

    async def add_message(self, value: NewCopilotMessage) -> CopilotMessageData: ...
    async def get_message(self, message_id: UUID) -> CopilotMessageData | None: ...
    async def list_messages(self, thread_id: UUID) -> tuple[CopilotMessageData, ...]: ...

    async def add_turn(self, value: NewCopilotTurn) -> CopilotTurnData: ...
    async def get_turn(self, turn_id: UUID) -> CopilotTurnData | None: ...
    async def get_turn_for_update(self, turn_id: UUID) -> CopilotTurnData | None: ...
    async def list_turns(self, thread_id: UUID) -> tuple[CopilotTurnData, ...]: ...
    async def bind_retrieval_manifest(
        self,
        turn_id: UUID,
        *,
        expected_status: str,
        retrieval_manifest_fingerprint: str,
    ) -> CopilotTurnData: ...
    async def finalize_turn(
        self,
        turn_id: UUID,
        *,
        expected_status: str,
        status: str,
        assistant_message_id: UUID | None,
        error_code: str | None,
    ) -> CopilotTurnData: ...

    async def add_proposal(self, value: NewCopilotActionProposal) -> CopilotActionProposalData: ...
    async def get_proposal(self, proposal_id: UUID) -> CopilotActionProposalData | None: ...
    async def get_proposal_for_update(
        self, proposal_id: UUID
    ) -> CopilotActionProposalData | None: ...
    async def list_proposals(self, turn_id: UUID) -> tuple[CopilotActionProposalData, ...]: ...
    async def transition_proposal(
        self,
        proposal_id: UUID,
        *,
        expected_status: str,
        status: str,
        outcome_event_id: UUID | None = None,
        invalidation_reason: str | None = None,
    ) -> CopilotActionProposalData: ...

    async def add_confirmation(
        self, value: NewCopilotActionConfirmation
    ) -> CopilotActionConfirmationData: ...
    async def list_confirmations(
        self, proposal_id: UUID
    ) -> tuple[CopilotActionConfirmationData, ...]: ...

    async def add_manifest(self, value: NewCopilotManifest) -> CopilotManifestData: ...
    async def get_manifest(self, manifest_id: UUID) -> CopilotManifestData | None: ...
    async def get_manifest_by_fingerprint(
        self, manifest_fingerprint: str
    ) -> CopilotManifestData | None: ...
    async def list_context_items(
        self, research_question_id: UUID
    ) -> tuple[CopilotStructuredItemData, ...]: ...
    async def get_structured_items(
        self, target_ids: tuple[UUID, ...]
    ) -> tuple[CopilotStructuredItemData, ...]: ...
    async def search_structured_items(
        self, *, query: str, allowed_kinds: tuple[str, ...], limit: int
    ) -> tuple[CopilotStructuredItemData, ...]: ...
    async def list_dependency_items(
        self, target_ids: tuple[UUID, ...]
    ) -> tuple[CopilotStructuredItemData, ...]: ...
    async def list_exact_evidence(
        self, target_ids: tuple[UUID, ...]
    ) -> tuple[CopilotEvidenceItemData, ...]: ...
    async def search_source_chunks(
        self, *, query: str, limit: int
    ) -> tuple[CopilotEvidenceItemData, ...]: ...
    async def search_repository_components(
        self, *, query: str, limit: int
    ) -> tuple[CopilotEvidenceItemData, ...]: ...
