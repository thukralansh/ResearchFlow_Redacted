"""Deterministic first-slice paper-to-code mapping policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType
from researchflow.errors import DomainValidationError

MAPPING_POLICY_VERSION: Final = "rf-paper-code-mapping-v1"
MAPPING_PROMPT_VERSION: Final = "v2"
MAPPING_SCHEMA_VERSION: Final = "v2"
MAPPING_TASK_NAME: Final = "generate_paper_code_mappings"
MAPPING_SCHEMA_NAME: Final = "paper_code_mapping_batch"
MAPPING_NAMESPACE: Final = uuid5(NAMESPACE_URL, "https://researchflow.local/ids/paper-code-mapping")

_METHOD_ANCHORS = frozenset(
    {
        PaperAnchorType.METHOD_MODEL,
        PaperAnchorType.METHOD_COMPONENT,
        PaperAnchorType.EQUATION,
        PaperAnchorType.METHOD_SYMBOL,
        PaperAnchorType.ALGORITHM_STEP,
        PaperAnchorType.DATA_FLOW,
        PaperAnchorType.VALIDITY_CONDITION,
    }
)


def validate_mapping_compatibility(
    *,
    anchor_type: PaperAnchorType,
    mapping_type: PaperCodeMappingType,
    knowledge_object_type: str | None = None,
    method_symbol_type: str | None = None,
) -> None:
    """Reject semantically invalid anchor/type pairs before persistence."""
    if anchor_type is PaperAnchorType.SOURCE_CHUNK:
        return
    if mapping_type in {
        PaperCodeMappingType.CONTRADICTS,
        PaperCodeMappingType.UNDOCUMENTED_IN_PAPER,
        PaperCodeMappingType.ABSENT_FROM_REPO,
    }:
        return
    allowed = False
    if anchor_type is PaperAnchorType.KNOWLEDGE_OBJECT:
        allowed = {
            PaperCodeMappingType.IMPLEMENTS: knowledge_object_type == "implementation_requirement",
            PaperCodeMappingType.CONFIGURES: False,
            PaperCodeMappingType.CONSUMES: knowledge_object_type == "implementation_requirement",
            PaperCodeMappingType.PRODUCES: knowledge_object_type == "implementation_requirement",
            PaperCodeMappingType.TESTS: knowledge_object_type
            in {"claim", "implementation_requirement"},
            PaperCodeMappingType.EVALUATES: knowledge_object_type
            in {"claim", "implementation_requirement"},
            PaperCodeMappingType.APPROXIMATES: knowledge_object_type == "claim",
        }.get(mapping_type, False)
    elif mapping_type is PaperCodeMappingType.CONFIGURES:
        allowed = anchor_type is PaperAnchorType.METHOD_SYMBOL and method_symbol_type == "parameter"
    elif mapping_type is PaperCodeMappingType.IMPLEMENTS:
        allowed = anchor_type in _METHOD_ANCHORS - {PaperAnchorType.METHOD_SYMBOL}
    elif mapping_type in {PaperCodeMappingType.CONSUMES, PaperCodeMappingType.PRODUCES}:
        allowed = anchor_type in {
            PaperAnchorType.METHOD_COMPONENT,
            PaperAnchorType.ALGORITHM_STEP,
            PaperAnchorType.DATA_FLOW,
        }
    elif mapping_type in {PaperCodeMappingType.TESTS, PaperCodeMappingType.EVALUATES}:
        allowed = anchor_type in _METHOD_ANCHORS
    elif mapping_type is PaperCodeMappingType.APPROXIMATES:
        allowed = anchor_type in {
            PaperAnchorType.METHOD_MODEL,
            PaperAnchorType.METHOD_COMPONENT,
            PaperAnchorType.EQUATION,
        }
    if not allowed:
        raise DomainValidationError(details={"reason": "mapping_anchor_type_incompatible"})


def canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class MappingIdentity:
    inspection_run_id: UUID
    anchor_type: PaperAnchorType
    anchor_id: UUID
    repository_component_id: UUID | None
    mapping_type: PaperCodeMappingType

    def fingerprint(self) -> str:
        return canonical_fingerprint(
            {
                "inspection_run_id": str(self.inspection_run_id),
                "anchor_type": self.anchor_type.value,
                "anchor_id": str(self.anchor_id),
                "repository_component_id": (
                    None
                    if self.repository_component_id is None
                    else str(self.repository_component_id)
                ),
                "mapping_type": self.mapping_type.value,
                "policy_version": MAPPING_POLICY_VERSION,
            }
        )

    def deterministic_id(self) -> UUID:
        return uuid5(MAPPING_NAMESPACE, self.fingerprint())
