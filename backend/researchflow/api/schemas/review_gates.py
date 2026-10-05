"""Transport contracts for the combined primary-source review gate."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.api.schemas.knowledge import OperationEventResponse

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CompletePrimarySourceReviewRequest(_StrictModel):
    method_model_id: UUID
    expected_gate_fingerprint: Fingerprint
    confirmed: Literal[True]


class PrimarySourceReviewGateResponse(_StrictModel):
    method_model_id: UUID
    workflow_run_id: UUID | None
    gate_fingerprint: Fingerprint
    ready: bool
    blocking_reasons: tuple[str, ...]
    proposition_total: int
    proposition_adjudicated: int
    resolved_section_count: int
    method_model_accepted: bool
    completed_event: OperationEventResponse | None


class PrimarySourceReviewCompletionResponse(_StrictModel):
    event: OperationEventResponse
    gate_fingerprint: Fingerprint
    accepted_method_model_id: UUID
    replayed: bool
