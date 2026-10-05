"""Typed PDF processing and exact provenance chunk contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ProcessSourceRequest(BaseModel):
    research_question_id: UUID
    question_source_relationship_id: UUID


class SourceProcessingRunResponse(BaseModel):
    id: UUID
    source_id: UUID
    source_hash: str
    parser_name: str
    parser_version: str
    chunker_name: str
    chunker_version: str
    normalization_version: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None


class SourceChunkResponse(BaseModel):
    id: UUID
    processing_run_id: UUID
    page_number: int
    section: str | None
    subsection: str | None
    chunk_index: int
    start_offset: int
    end_offset: int
    chunk_text: str
    content_hash: str


class SourceProcessingResponse(BaseModel):
    run: SourceProcessingRunResponse
    chunks: tuple[SourceChunkResponse, ...]
    message: str | None = None
