"""Primary Paper artifact validation, identities, and relationship policy."""

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePath
from typing import Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from researchflow.domain.projects import command_fingerprint
from researchflow.errors import DomainValidationError, ResourceConflictError

PDF_MIME_TYPES: Final = frozenset({"application/pdf", "application/octet-stream"})
PDF_READ_CHUNK_BYTES: Final = 1024 * 1024
PDF_HEADER_SCAN_BYTES: Final = 1024
SOURCE_TITLE_MAX: Final = 500
SOURCE_FILENAME_MAX: Final = 255
SOURCE_AUTHORS_MAX: Final = 100
SOURCE_AUTHOR_MAX: Final = 300
SOURCE_IDENTIFIERS_MAX: Final = 20
SOURCE_IDENTIFIER_KEY_MAX: Final = 100
SOURCE_IDENTIFIER_VALUE_MAX: Final = 500
RELATIONSHIP_RATIONALE_MAX: Final = 4_000
RELATIONSHIP_SCOPE_MAX: Final = 4_000

SOURCE_ID_NAMESPACE: Final = uuid5(NAMESPACE_URL, "https://researchflow.local/ids/source")
PRIMARY_PAPER_RELATIONSHIP_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/primary-paper-relationship"
)
SOURCE_EVENT_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/source-operation-event"
)


class AsyncUpload(Protocol):
    """Framework-neutral async upload stream used by the application layer."""

    async def read(self, size: int = -1) -> bytes: ...

    async def seek(self, offset: int) -> None: ...


def _required(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise DomainValidationError(details={"field": field, "reason": "invalid_length"})
    return normalized


def _optional(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise DomainValidationError(details={"field": field, "reason": "invalid_length"})
    return normalized


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Validated artifact metadata with no question-relative semantic role."""

    title: str
    authors: tuple[str, ...]
    identifiers: dict[str, str]

    @classmethod
    def validate(
        cls,
        *,
        title: str,
        authors: tuple[str, ...],
        identifiers: dict[str, str],
    ) -> "SourceMetadata":
        if len(authors) > SOURCE_AUTHORS_MAX:
            raise DomainValidationError(details={"field": "authors", "reason": "too_many"})
        normalized_authors = tuple(
            _required(author, field="authors", maximum=SOURCE_AUTHOR_MAX) for author in authors
        )
        if len(set(normalized_authors)) != len(normalized_authors):
            raise DomainValidationError(details={"field": "authors", "reason": "duplicate"})

        if len(identifiers) > SOURCE_IDENTIFIERS_MAX:
            raise DomainValidationError(details={"field": "identifiers", "reason": "too_many"})
        normalized_identifiers = {
            _required(key, field="identifier_key", maximum=SOURCE_IDENTIFIER_KEY_MAX): _required(
                value,
                field="identifier_value",
                maximum=SOURCE_IDENTIFIER_VALUE_MAX,
            )
            for key, value in identifiers.items()
        }
        return cls(
            title=_required(title, field="title", maximum=SOURCE_TITLE_MAX),
            authors=normalized_authors,
            identifiers=normalized_identifiers,
        )


@dataclass(frozen=True, slots=True)
class PrimaryPaperRelationship:
    rationale: str
    relevance_scope: str | None
    confidence: Decimal | None

    @classmethod
    def validate(
        cls,
        *,
        rationale: str,
        relevance_scope: str | None,
        confidence: Decimal | None,
    ) -> "PrimaryPaperRelationship":
        if confidence is not None:
            if not confidence.is_finite() or confidence < 0 or confidence > 1:
                raise DomainValidationError(
                    details={"field": "confidence", "reason": "outside_unit_interval"}
                )
            exponent = confidence.as_tuple().exponent
            if not isinstance(exponent, int) or exponent < -4:
                raise DomainValidationError(
                    details={"field": "confidence", "reason": "too_many_decimal_places"}
                )
        return cls(
            rationale=_required(
                rationale,
                field="relationship_rationale",
                maximum=RELATIONSHIP_RATIONALE_MAX,
            ),
            relevance_scope=_optional(
                relevance_scope,
                field="relevance_scope",
                maximum=RELATIONSHIP_SCOPE_MAX,
            ),
            confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class ValidatedPdf:
    content: bytes
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str


async def read_and_validate_pdf(
    upload: AsyncUpload,
    *,
    original_filename: str,
    mime_type: str,
    maximum_bytes: int,
) -> ValidatedPdf:
    """Read a bounded stream once while computing its exact SHA-256 identity."""
    filename = _required(
        original_filename,
        field="original_filename",
        maximum=SOURCE_FILENAME_MAX,
    )
    if PurePath(filename).name != filename or "\\" in filename:
        raise DomainValidationError(
            details={"field": "original_filename", "reason": "basename_required"}
        )
    if PurePath(filename).suffix.casefold() != ".pdf":
        raise DomainValidationError(
            details={"field": "original_filename", "reason": "pdf_required"}
        )

    normalized_mime = mime_type.strip().casefold()
    if normalized_mime not in PDF_MIME_TYPES:
        raise DomainValidationError(details={"field": "mime_type", "reason": "pdf_required"})
    if maximum_bytes <= 0:
        raise DomainValidationError(details={"field": "maximum_bytes", "reason": "positive"})

    await upload.seek(0)
    hasher = hashlib.sha256()
    content = bytearray()
    while chunk := await upload.read(PDF_READ_CHUNK_BYTES):
        if len(content) + len(chunk) > maximum_bytes:
            raise DomainValidationError(details={"field": "content_size", "reason": "too_large"})
        hasher.update(chunk)
        content.extend(chunk)

    if not content:
        raise DomainValidationError(details={"field": "file", "reason": "empty"})
    if b"%PDF-" not in content[:PDF_HEADER_SCAN_BYTES]:
        raise DomainValidationError(details={"field": "file", "reason": "invalid_pdf_signature"})

    immutable_content = bytes(content)
    return ValidatedPdf(
        content=immutable_content,
        original_filename=filename,
        mime_type="application/pdf",
        size_bytes=len(immutable_content),
        sha256=hasher.hexdigest(),
    )


def primary_paper_object_key(*, project_id: UUID, source_id: UUID, sha256: str) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise DomainValidationError(details={"field": "sha256", "reason": "invalid"})
    return f"projects/{project_id}/sources/{source_id}/{sha256}.pdf"


@dataclass(frozen=True, slots=True)
class PrimaryPaperIdentities:
    source_id: UUID
    relationship_id: UUID
    event_id: UUID


def primary_paper_identities(
    *,
    actor_id: UUID,
    project_id: UUID,
    idempotency_key: str | None,
    input_fingerprint: str,
) -> PrimaryPaperIdentities:
    """Give exact replays stable IDs while isolating mismatched-key compensation paths."""
    if idempotency_key is None:
        return PrimaryPaperIdentities(uuid4(), uuid4(), uuid4())
    command_name = (
        f"{actor_id}:{project_id}:source.primary_paper.upload:{idempotency_key}:{input_fingerprint}"
    )
    return PrimaryPaperIdentities(
        source_id=uuid5(SOURCE_ID_NAMESPACE, command_name),
        relationship_id=uuid5(PRIMARY_PAPER_RELATIONSHIP_ID_NAMESPACE, command_name),
        event_id=uuid5(SOURCE_EVENT_ID_NAMESPACE, command_name),
    )


def primary_paper_fingerprint(
    *,
    actor_id: UUID,
    project_id: UUID,
    research_question_id: UUID,
    pdf: ValidatedPdf,
    metadata: SourceMetadata,
    relationship: PrimaryPaperRelationship,
    replace_current: bool,
) -> str:
    return command_fingerprint(
        {
            "actor_id": str(actor_id),
            "operation": "source.primary_paper.upload",
            "project_id": str(project_id),
            "research_question_id": str(research_question_id),
            "content_hash": pdf.sha256,
            "size_bytes": pdf.size_bytes,
            "original_filename": pdf.original_filename,
            "mime_type": pdf.mime_type,
            "title": metadata.title,
            "authors": metadata.authors,
            "identifiers": metadata.identifiers,
            "relationship_rationale": relationship.rationale,
            "relevance_scope": relationship.relevance_scope,
            "confidence": relationship.confidence,
            "replace_current": replace_current,
        }
    )


def validate_replacement(*, has_current: bool, replace_current: bool) -> None:
    if has_current and not replace_current:
        raise ResourceConflictError(details={"reason": "primary_paper_already_exists"})
    if not has_current and replace_current:
        raise ResourceConflictError(details={"reason": "primary_paper_replacement_target_missing"})
