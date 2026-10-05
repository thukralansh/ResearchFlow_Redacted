"""Review-safe exact and bounded semantic proposition deduplication policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal, cast

from researchflow.api.schemas.proposition_extraction import (
    DuplicateComparisonCandidate,
    DuplicateComparisonRequest,
)
from researchflow.domain.knowledge_catalog import statement_fingerprint
from researchflow.domain.proposition_extraction import (
    DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    LEXICAL_POLICY_VERSION,
    LexicalDuplicateClass,
    lexical_similarity,
)
from researchflow.ports.repositories import KnowledgeObjectData

DEDUPLICATION_POLICY_VERSION: Final = "rf-proposition-dedup-v1"
DUPLICATE_COMPARISON_TASK: Final = "compare_proposition_duplicates"
DUPLICATE_COMPARISON_PROMPT_VERSION: Final = "v1"
DUPLICATE_COMPARISON_SCHEMA_NAME: Final = "proposition_duplicate_comparison"
DUPLICATE_COMPARISON_SCHEMA_VERSION: Final = "v1"
MAX_DUPLICATE_SHORTLIST: Final = 20
MAX_STRUCTURED_COMPARISONS_PER_REQUEST: Final = 25
type _ObjectType = Literal[
    "claim", "assumption", "knowledge_gap", "implementation_requirement", "ambiguity"
]


@dataclass(frozen=True, slots=True)
class ExactDedupResult:
    active_target: KnowledgeObjectData | None
    historical_matches: tuple[KnowledgeObjectData, ...]


def classify_exact_matches(
    matches: tuple[KnowledgeObjectData, ...],
) -> ExactDedupResult:
    """Select only current, review-eligible objects as suppression targets."""
    active = tuple(
        item
        for item in matches
        if item.lifecycle_status == "current"
        and item.review_status in {"candidate", "needs_revision", "accepted"}
    )
    return ExactDedupResult(
        active_target=active[0] if active else None,
        historical_matches=tuple(item for item in matches if item not in active),
    )


def shortlist_near_duplicates(
    statement: str,
    candidates: tuple[KnowledgeObjectData, ...],
    *,
    threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> tuple[DuplicateComparisonCandidate, ...]:
    scored: list[DuplicateComparisonCandidate] = []
    exact_hash = statement_fingerprint(statement)
    for candidate in candidates:
        if candidate.normalized_statement_hash == exact_hash:
            continue
        result = lexical_similarity(statement, candidate.statement, threshold=threshold)
        if result.classification is LexicalDuplicateClass.NEAR:
            scored.append(
                DuplicateComparisonCandidate(
                    object_id=candidate.id,
                    object_type=cast(_ObjectType, candidate.object_type),
                    statement=candidate.statement,
                    lexical_similarity=result.score,
                )
            )
    scored.sort(key=lambda item: (-item.lexical_similarity, str(item.object_id)))
    return tuple(scored[:MAX_DUPLICATE_SHORTLIST])


def comparison_request(
    *,
    client_id: str,
    statement: str,
    candidates: tuple[DuplicateComparisonCandidate, ...],
    threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> DuplicateComparisonRequest:
    return DuplicateComparisonRequest(
        lexical_policy_version=LEXICAL_POLICY_VERSION,
        near_duplicate_threshold=threshold,
        proposed_client_id=client_id,
        proposed_statement=statement,
        candidates=list(candidates),
    )


def comparison_fingerprint(request: DuplicateComparisonRequest) -> str:
    payload = {
        "deduplication_policy_version": DEDUPLICATION_POLICY_VERSION,
        "request": request.model_dump(mode="json"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()
