"""Typed Primary Paper upload and metadata responses."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from researchflow.domain.sources import (
    RELATIONSHIP_RATIONALE_MAX,
    RELATIONSHIP_SCOPE_MAX,
    SOURCE_AUTHOR_MAX,
    SOURCE_AUTHORS_MAX,
    SOURCE_IDENTIFIER_KEY_MAX,
    SOURCE_IDENTIFIER_VALUE_MAX,
    SOURCE_IDENTIFIERS_MAX,
    SOURCE_TITLE_MAX,
)
from researchflow.errors import DomainValidationError


class SourceUploadMetadata(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=SOURCE_TITLE_MAX)
    authors: list[str] = Field(default_factory=list, max_length=SOURCE_AUTHORS_MAX)
    identifiers: dict[str, str] = Field(default_factory=dict, max_length=SOURCE_IDENTIFIERS_MAX)
    relationship_rationale: str = Field(
        min_length=1,
        max_length=RELATIONSHIP_RATIONALE_MAX,
    )
    relevance_scope: str | None = Field(default=None, max_length=RELATIONSHIP_SCOPE_MAX)
    confidence: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)

    @field_validator("authors")
    @classmethod
    def validate_authors(cls, authors: list[str]) -> list[str]:
        if any(not author or len(author) > SOURCE_AUTHOR_MAX for author in authors):
            raise ValueError("authors must be nonblank and bounded")
        return authors

    @field_validator("identifiers")
    @classmethod
    def validate_identifiers(cls, identifiers: dict[str, str]) -> dict[str, str]:
        if any(
            not key
            or len(key) > SOURCE_IDENTIFIER_KEY_MAX
            or not value
            or len(value) > SOURCE_IDENTIFIER_VALUE_MAX
            for key, value in identifiers.items()
        ):
            raise ValueError("identifiers must contain bounded nonblank strings")
        return identifiers

    @classmethod
    def from_form(
        cls,
        *,
        title: str,
        authors_json: str,
        identifiers_json: str,
        relationship_rationale: str,
        relevance_scope: str | None,
        confidence: Decimal | None,
    ) -> Self:
        try:
            authors = json.loads(authors_json)
            identifiers = json.loads(identifiers_json)
        except (json.JSONDecodeError, TypeError) as error:
            raise DomainValidationError(
                details={"field": "artifact_metadata", "reason": "invalid_json"}
            ) from error
        if not isinstance(authors, list) or not all(isinstance(item, str) for item in authors):
            raise DomainValidationError(
                details={"field": "authors", "reason": "string_list_required"}
            )
        if not isinstance(identifiers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in identifiers.items()
        ):
            raise DomainValidationError(
                details={"field": "identifiers", "reason": "string_map_required"}
            )
        try:
            return cls(
                title=title,
                authors=authors,
                identifiers=identifiers,
                relationship_rationale=relationship_rationale,
                relevance_scope=relevance_scope,
                confidence=confidence,
            )
        except ValidationError as error:
            raise DomainValidationError(
                details={"field": "artifact_metadata", "reason": "invalid"}
            ) from error


class SourceResponse(BaseModel):
    id: UUID
    project_id: UUID
    source_type: str
    title: str
    authors: tuple[str, ...]
    identifiers: dict[str, str]
    original_filename: str
    storage_bucket: str
    storage_object_key: str
    mime_type: str
    size_bytes: int
    content_hash: str
    processing_status: str
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime


class QuestionSourceRelationshipResponse(BaseModel):
    id: UUID
    project_id: UUID
    research_question_id: UUID
    source_id: UUID
    lineage_id: UUID
    version: int
    relationship_type: str
    relevance_scope: str | None
    rationale: str
    confidence: Decimal | None
    review_status: str
    lifecycle_status: str
    created_by_type: str
    derived_from_id: UUID | None
    supersedes_id: UUID | None
    created_at: datetime


class PrimaryPaperResponse(BaseModel):
    source: SourceResponse
    relationship: QuestionSourceRelationshipResponse
