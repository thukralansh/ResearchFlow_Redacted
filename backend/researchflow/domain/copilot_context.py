"""Deterministic, bounded Copilot context and retrieval policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from researchflow.errors import DomainValidationError

COPILOT_CONTEXT_POLICY_VERSION: Final = "rf-copilot-context-v1"
COPILOT_RETRIEVAL_POLICY_VERSION: Final = "rf-copilot-retrieval-v1"
COPILOT_BOUNDS_POLICY_VERSION: Final = "rf-copilot-bounds-v1"


@dataclass(frozen=True, slots=True)
class CopilotBounds:
    structured_objects: int = 24
    evidence_anchors: int = 8
    source_chunks: int = 4
    repository_components: int = 4
    excerpt_codepoints: int = 1_200


COPILOT_BOUNDS: Final = CopilotBounds()


class CopilotManifestKind(StrEnum):
    CONTEXT = "context"
    RETRIEVAL = "retrieval"


class CopilotRetrievalIntent(StrEnum):
    STRUCTURED_DETAIL = "structured_detail"
    DEPENDENCY_PROVENANCE = "dependency_provenance"
    STRUCTURED_SEARCH = "structured_search"
    SOURCE_CHUNK_SEARCH = "source_chunk_search"
    REPOSITORY_COMPONENT_SEARCH = "repository_component_search"
    EXACT_EVIDENCE = "exact_evidence"


class CopilotItemKind(StrEnum):
    PROJECT = "project"
    RESEARCH_QUESTION = "research_question"
    SOURCE = "source"
    REPOSITORY_RELATIONSHIP = "repository_relationship"
    KNOWLEDGE_OBJECT = "knowledge_object"
    METHOD_MODEL = "method_model"
    METHOD_ELEMENT = "method_element"
    MENTAL_MODEL = "mental_model"
    PAPER_CODE_MAPPING = "paper_code_mapping"
    JUDGMENT_REQUEST = "judgment_request"
    HUMAN_JUDGMENT = "human_judgment"
    BLUEPRINT = "implementation_blueprint"
    BLUEPRINT_SECTION = "blueprint_section"
    BLUEPRINT_STEP = "implementation_step"
    VERIFICATION_CHECK = "verification_check"
    WORKFLOW_RUN = "workflow_run"
    ARTIFACT_DEPENDENCY = "artifact_dependency"
    SOURCE_CHUNK = "source_chunk"
    REPOSITORY_COMPONENT = "repository_component"
    EVIDENCE_LINK = "evidence_link"


STRUCTURED_ITEM_KINDS: Final[frozenset[str]] = frozenset(
    kind.value
    for kind in CopilotItemKind
    if kind
    not in {
        CopilotItemKind.SOURCE_CHUNK,
        CopilotItemKind.REPOSITORY_COMPONENT,
        CopilotItemKind.EVIDENCE_LINK,
    }
)


def validate_retrieval_selector(
    *,
    intent: str,
    query: str | None,
    target_ids: tuple[str, ...],
    allowed_kinds: tuple[str, ...],
) -> None:
    try:
        parsed = CopilotRetrievalIntent(intent)
    except ValueError as error:
        raise DomainValidationError(details={"field": "intent"}) from error
    if len(target_ids) > COPILOT_BOUNDS.evidence_anchors:
        raise DomainValidationError(details={"reason": "evidence_anchor_bound_exceeded"})
    if any(kind not in STRUCTURED_ITEM_KINDS for kind in allowed_kinds):
        raise DomainValidationError(details={"reason": "retrieval_kind_not_allowed"})
    search_intents = {
        CopilotRetrievalIntent.STRUCTURED_SEARCH,
        CopilotRetrievalIntent.SOURCE_CHUNK_SEARCH,
        CopilotRetrievalIntent.REPOSITORY_COMPONENT_SEARCH,
    }
    target_intents = {
        CopilotRetrievalIntent.STRUCTURED_DETAIL,
        CopilotRetrievalIntent.DEPENDENCY_PROVENANCE,
        CopilotRetrievalIntent.EXACT_EVIDENCE,
    }
    if parsed in search_intents and (query is None or not query.strip()):
        raise DomainValidationError(details={"field": "query"})
    if parsed in target_intents and not target_ids:
        raise DomainValidationError(details={"field": "target_ids"})
    if query is not None and len(query.strip()) > 500:
        raise DomainValidationError(details={"field": "query"})
