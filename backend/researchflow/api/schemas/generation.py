"""Typed structured-generation response contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from researchflow.ports.repositories import GenerationRecordData


class GenerationRecordResponse(BaseModel):
    id: UUID
    project_id: UUID
    workflow_run_id: UUID
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
    raw_structured_output: object | None
    validation_summary: dict[str, object] | list[object] | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    created_at: datetime


def serialize_generation_record(record: GenerationRecordData) -> GenerationRecordResponse:
    """Map a framework-neutral record without exposing an ORM row."""
    return GenerationRecordResponse.model_validate(record, from_attributes=True)
