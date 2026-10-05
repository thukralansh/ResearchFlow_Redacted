"""Read-only Copilot audit response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CopilotThreadResponse(_Schema):
    id: UUID
    project_id: UUID
    created_by_id: UUID
    title: str | None
    status: str
    lock_version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class CopilotMessageResponse(_Schema):
    id: UUID
    thread_id: UUID
    ordinal: int
    role: str
    content: str
    reply_to_message_id: UUID | None
    semantic_status: str
    created_by_id: UUID | None
    created_at: datetime


class CopilotTurnResponse(_Schema):
    id: UUID
    thread_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    workflow_run_id: UUID | None
    status: str
    context_manifest_fingerprint: str | None
    retrieval_manifest_fingerprint: str | None
    context_policy_version: str | None
    bounds_policy_version: str | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


class CopilotGenerationResponse(_Schema):
    id: UUID
    copilot_turn_id: UUID | None
    task_name: str
    prompt_version: str
    schema_version: str
    provider: str
    model_version: str
    reasoning_version: str | None
    input_fingerprint: str
    status: str
    model_configuration: dict[str, object]
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime


class CopilotActionProposalResponse(_Schema):
    id: UUID
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
    proposal_fingerprint: str = Field(validation_alias="input_fingerprint")
    status: str
    outcome_event_id: UUID | None
    invalidation_reason: str | None
    created_at: datetime


class CopilotActionConfirmationResponse(_Schema):
    id: UUID
    proposal_id: UUID
    actor_id: UUID
    proposal_fingerprint: str
    preview_fingerprint: str | None
    rationale: str | None
    created_at: datetime


class CopilotTurnAuditResponse(_Schema):
    turn: CopilotTurnResponse
    generation_records: tuple[CopilotGenerationResponse, ...]
    proposals: tuple[CopilotActionProposalResponse, ...]
    confirmations: tuple[CopilotActionConfirmationResponse, ...]


class CopilotThreadAuditResponse(_Schema):
    thread: CopilotThreadResponse
    messages: tuple[CopilotMessageResponse, ...]
    turns: tuple[CopilotTurnAuditResponse, ...]


class BuildCopilotRetrievalRequest(_Schema):
    context_manifest_id: UUID
    intent: str
    query: str | None = None
    target_ids: tuple[UUID, ...] = ()
    allowed_kinds: tuple[str, ...] = ()


class CreateCopilotThreadRequest(_Schema):
    command_id: UUID
    title: str | None = None


class RunReadOnlyCopilotTurnRequest(_Schema):
    command_id: UUID
    message: str


class CopilotCitationResponse(_Schema):
    manifest_id: UUID
    item_id: UUID
    item_kind: str
    explanation: str


class CopilotManifestResponse(_Schema):
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


class ReadOnlyCopilotTurnResponse(_Schema):
    turn: CopilotTurnResponse
    answer: str
    citations: tuple[CopilotCitationResponse, ...]
    uncertainties: tuple[str, ...]
    limitations: tuple[str, ...]
    context_manifest: CopilotManifestResponse
    retrieval_manifest: CopilotManifestResponse | None
    generation_records: tuple[CopilotGenerationResponse, ...]
    action_proposals: tuple[CopilotActionProposalResponse, ...]
    replayed: bool


class CopilotImpactPreviewResponse(_Schema):
    policy_version: str
    scope: str
    complete: bool
    warning: str
    expected_state: dict[str, object]
    effects: tuple[dict[str, object], ...]
    fingerprint: str


class CopilotProposalPreviewResponse(_Schema):
    proposal: CopilotActionProposalResponse
    preview: CopilotImpactPreviewResponse
    replayed: bool


class ConfirmCopilotActionRequest(_Schema):
    command_id: UUID
    expected_proposal_fingerprint: str
    expected_preview_fingerprint: str
    rationale: str | None = None


class ConfirmedCopilotActionResponse(_Schema):
    proposal: CopilotActionProposalResponse
    confirmation: CopilotActionConfirmationResponse
    outcome_event_id: UUID
    operation_type: str
    replayed: bool
