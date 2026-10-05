"""Deterministic page normalization and exact chunk provenance policy."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.errors import DomainValidationError

NORMALIZATION_VERSION: Final = "rf-page-text-v1"
MAX_NORMALIZED_PAGE_CODEPOINTS: Final = 2_000_000
IMAGE_ONLY_MESSAGE: Final = (
    "This MVP processes text-based PDFs only. Scanned or image-only PDFs require OCR, "
    "which is not included in the current MVP."
)
PROCESSING_RUN_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/source-processing-run"
)
PROCESSING_ARTIFACT_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/source-processing-artifact"
)

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")


@dataclass(frozen=True, slots=True)
class ParsedPage:
    page_number: int
    text: str
    image_count: int = 0


@dataclass(frozen=True, slots=True)
class NormalizedPage:
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    page_number: int
    section: str | None
    subsection: str | None
    chunk_index: int
    start_offset: int
    end_offset: int
    chunk_text: str
    content_hash: str


def normalize_page_text(text: str) -> str:
    """Apply immutable NFC page-text normalization without inferred repairs."""
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = _HORIZONTAL_WHITESPACE.sub(" ", normalized)
    lines = [line.strip() for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def normalize_pages(pages: tuple[ParsedPage, ...]) -> tuple[NormalizedPage, ...]:
    if not pages:
        raise DomainValidationError(details={"reason": "pdf_empty"})

    normalized: list[NormalizedPage] = []
    has_images = False
    for expected_number, page in enumerate(pages, start=1):
        if page.page_number != expected_number:
            raise DomainValidationError(details={"reason": "pdf_page_order_invalid"})
        page_text = normalize_page_text(page.text)
        if len(page_text) > MAX_NORMALIZED_PAGE_CODEPOINTS:
            raise DomainValidationError(details={"reason": "pdf_page_too_large"})
        normalized.append(NormalizedPage(page_number=page.page_number, text=page_text))
        has_images = has_images or page.image_count > 0

    if not any(page.text for page in normalized):
        reason = "pdf_image_only" if has_images else "pdf_empty_text"
        raise DomainValidationError(details={"reason": reason})
    return tuple(normalized)


def chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def processing_run_id(
    *,
    source_id: UUID,
    source_hash: str,
    parser_name: str,
    parser_version: str,
    chunker_name: str,
    chunker_version: str,
    normalization_version: str,
) -> UUID:
    identity = ":".join(
        (
            str(source_id),
            source_hash,
            parser_name,
            parser_version,
            chunker_name,
            chunker_version,
            normalization_version,
        )
    )
    return uuid5(PROCESSING_RUN_ID_NAMESPACE, identity)


def processing_artifact_id(run_id: UUID, artifact: str) -> UUID:
    return uuid5(PROCESSING_ARTIFACT_ID_NAMESPACE, f"{run_id}:{artifact}")


def validate_chunk_provenance(
    pages: tuple[NormalizedPage, ...],
    chunks: tuple[ChunkDraft, ...],
    *,
    maximum_chunk_codepoints: int,
) -> None:
    page_text = {page.page_number: page.text for page in pages}
    if not chunks:
        raise DomainValidationError(details={"reason": "pdf_chunks_empty"})

    for expected_index, chunk in enumerate(chunks):
        text = page_text.get(chunk.page_number)
        if text is None:
            raise DomainValidationError(details={"reason": "chunk_page_invalid"})
        if chunk.chunk_index != expected_index:
            raise DomainValidationError(details={"reason": "chunk_index_invalid"})
        if not 0 <= chunk.start_offset < chunk.end_offset <= len(text):
            raise DomainValidationError(details={"reason": "chunk_offsets_invalid"})
        if chunk.end_offset - chunk.start_offset > maximum_chunk_codepoints:
            raise DomainValidationError(details={"reason": "chunk_too_large"})
        if chunk.chunk_text != text[chunk.start_offset : chunk.end_offset]:
            raise DomainValidationError(details={"reason": "chunk_excerpt_mismatch"})
        if not chunk.chunk_text.strip():
            raise DomainValidationError(details={"reason": "chunk_text_empty"})
        if chunk.content_hash != chunk_hash(chunk.chunk_text):
            raise DomainValidationError(details={"reason": "chunk_hash_mismatch"})


@dataclass(frozen=True, slots=True)
class SourceChunkEvidenceProvenance:
    """Exact immutable processing identity behind one source-chunk excerpt."""

    project_id: UUID
    source_id: UUID
    processing_run_id: UUID
    source_hash: str
    parser_name: str
    parser_version: str
    chunker_name: str
    chunker_version: str
    normalization_version: str
    chunk_id: UUID
    chunk_content_hash: str
    chunk_text: str
    current: bool


@dataclass(frozen=True, slots=True)
class RepositoryComponentEvidenceProvenance:
    """Exact immutable inspection and commit identity behind repository evidence."""

    project_id: UUID
    repository_id: UUID
    repository_snapshot_id: UUID
    commit_sha: str
    inspection_run_id: UUID
    component_id: UUID
    path: str
    content_hash: str
    content_excerpt: str
    current: bool


EvidenceProvenance = SourceChunkEvidenceProvenance | RepositoryComponentEvidenceProvenance


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_lower_hex_digest(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def validate_evidence_excerpt(
    provenance: EvidenceProvenance,
    excerpt: str,
) -> str:
    """Resolve an exact excerpt against the immutable evidence container."""
    if not excerpt.strip():
        raise DomainValidationError(details={"reason": "evidence_excerpt_blank"})
    if not provenance.current:
        raise DomainValidationError(details={"reason": "evidence_endpoint_stale"})
    if isinstance(provenance, SourceChunkEvidenceProvenance):
        if (
            not _is_lower_hex_digest(provenance.source_hash, 64)
            or not provenance.parser_name.strip()
            or not provenance.parser_version.strip()
            or not provenance.chunker_name.strip()
            or not provenance.chunker_version.strip()
            or not provenance.normalization_version.strip()
        ):
            raise DomainValidationError(details={"reason": "source_processing_identity_invalid"})
        if provenance.chunk_content_hash != _sha256(provenance.chunk_text):
            raise DomainValidationError(details={"reason": "evidence_chunk_hash_mismatch"})
        container = provenance.chunk_text
    else:
        if not _is_lower_hex_digest(provenance.commit_sha, 40):
            raise DomainValidationError(details={"reason": "repository_commit_identity_invalid"})
        if not provenance.path.strip():
            raise DomainValidationError(details={"reason": "repository_component_path_blank"})
        if provenance.content_hash != _sha256(provenance.content_excerpt):
            raise DomainValidationError(details={"reason": "repository_component_hash_mismatch"})
        container = provenance.content_excerpt
    if excerpt not in container:
        raise DomainValidationError(details={"reason": "evidence_excerpt_mismatch"})
    return excerpt


def evidence_provenance_identity(provenance: EvidenceProvenance) -> dict[str, object]:
    """Return the exact versioned identity used in evidence fingerprints."""
    if isinstance(provenance, SourceChunkEvidenceProvenance):
        return {
            "kind": "source_chunk",
            "project_id": str(provenance.project_id),
            "source_id": str(provenance.source_id),
            "processing_run_id": str(provenance.processing_run_id),
            "source_hash": provenance.source_hash,
            "parser_name": provenance.parser_name,
            "parser_version": provenance.parser_version,
            "chunker_name": provenance.chunker_name,
            "chunker_version": provenance.chunker_version,
            "normalization_version": provenance.normalization_version,
            "chunk_id": str(provenance.chunk_id),
            "chunk_content_hash": provenance.chunk_content_hash,
        }
    return {
        "kind": "repository_component",
        "project_id": str(provenance.project_id),
        "repository_id": str(provenance.repository_id),
        "repository_snapshot_id": str(provenance.repository_snapshot_id),
        "commit_sha": provenance.commit_sha,
        "inspection_run_id": str(provenance.inspection_run_id),
        "component_id": str(provenance.component_id),
        "path": provenance.path,
        "content_hash": provenance.content_hash,
    }
