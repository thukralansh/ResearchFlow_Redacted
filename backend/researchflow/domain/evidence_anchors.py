"""Deterministic exact-source anchoring for model-proposed evidence excerpts."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Final

from researchflow.errors import DomainValidationError

SOURCE_EVIDENCE_ANCHOR_POLICY_VERSION: Final = "rf-source-evidence-anchor-v1"


@dataclass(frozen=True, slots=True)
class ResolvedSourceEvidenceAnchor:
    """One unique canonical substring within one immutable normalized SourceChunk."""

    excerpt: str
    start_offset: int
    end_offset: int
    excerpt_hash: str
    policy_version: str = SOURCE_EVIDENCE_ANCHOR_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class _ComparisonView:
    text: str
    spans: tuple[tuple[int, int], ...]


def resolve_source_evidence_anchor(
    *,
    chunk_text: str,
    chunk_start_offset: int,
    proposed_excerpt: str,
) -> ResolvedSourceEvidenceAnchor:
    """Resolve only one exact or whitespace-equivalent match in the cited chunk.

    Comparison permits the representation-only whitespace differences introduced
    when a model flattens canonical line breaks. The persisted excerpt always comes
    directly from ``chunk_text``; words, punctuation, hyphenation, and case are never
    repaired or guessed.
    """
    if chunk_start_offset < 0:
        raise DomainValidationError(details={"reason": "evidence_anchor_offset_invalid"})
    if "\r" in chunk_text or unicodedata.normalize("NFC", chunk_text) != chunk_text:
        raise DomainValidationError(details={"reason": "evidence_anchor_chunk_not_canonical"})
    if not chunk_text or not proposed_excerpt or proposed_excerpt != proposed_excerpt.strip():
        raise DomainValidationError(details={"reason": "evidence_anchor_excerpt_invalid"})

    haystack = _comparison_view(chunk_text)
    needle = _comparison_view(proposed_excerpt).text
    if not needle:  # pragma: no cover - guarded by nonblank stripped input above
        raise DomainValidationError(details={"reason": "evidence_anchor_excerpt_invalid"})
    matches = _all_occurrences(haystack.text, needle)
    anchors = {
        (haystack.spans[start][0], haystack.spans[start + len(needle) - 1][1]) for start in matches
    }
    if not anchors:
        raise DomainValidationError(details={"reason": "evidence_anchor_not_found"})
    if len(anchors) != 1:
        raise DomainValidationError(details={"reason": "evidence_anchor_ambiguous"})

    local_start, local_end = next(iter(anchors))
    canonical_excerpt = chunk_text[local_start:local_end]
    if _comparison_view(canonical_excerpt).text != needle:  # pragma: no cover - invariant
        raise DomainValidationError(details={"reason": "evidence_anchor_integrity_failure"})
    return ResolvedSourceEvidenceAnchor(
        excerpt=canonical_excerpt,
        start_offset=chunk_start_offset + local_start,
        end_offset=chunk_start_offset + local_end,
        excerpt_hash=hashlib.sha256(canonical_excerpt.encode("utf-8")).hexdigest(),
    )


def _comparison_view(value: str) -> _ComparisonView:
    canonical = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    output: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(canonical):
        if canonical[index].isspace():
            end = index + 1
            while end < len(canonical) and canonical[end].isspace():
                end += 1
            output.append(" ")
            spans.append((index, end))
            index = end
            continue
        output.append(canonical[index])
        spans.append((index, index + 1))
        index += 1
    return _ComparisonView(text="".join(output), spans=tuple(spans))


def _all_occurrences(value: str, needle: str) -> tuple[int, ...]:
    found: list[int] = []
    start = 0
    while True:
        index = value.find(needle, start)
        if index < 0:
            return tuple(found)
        found.append(index)
        start = index + 1
