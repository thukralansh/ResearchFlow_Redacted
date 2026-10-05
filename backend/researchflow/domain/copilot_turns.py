"""Versioned, bounded read-only Copilot turn contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.domain.copilot_actions import CopilotProposedAction
from researchflow.domain.copilot_context import CopilotItemKind, CopilotRetrievalIntent
from researchflow.errors import DomainValidationError

COPILOT_TURN_POLICY_VERSION: Final = "rf-copilot-turn-v1"
COPILOT_PROMPT_VERSION: Final = "v2"
COPILOT_INITIAL_TASK: Final = "copilot_read_only_initial"
COPILOT_FINAL_TASK: Final = "copilot_read_only_final"
COPILOT_INITIAL_SCHEMA: Final = "copilot_read_only_initial"
COPILOT_FINAL_SCHEMA: Final = "copilot_read_only_final"
COPILOT_SCHEMA_VERSION: Final = "v2"
COPILOT_TOKEN_ESTIMATOR_VERSION: Final = "rf-copilot-utf8-upper-bound-v1"


@dataclass(frozen=True, slots=True)
class CopilotTurnBounds:
    user_message_codepoints: int = 8_000
    provider_input_token_upper_bound: int = 24_000
    prompt_token_upper_bound_reserve: int = 2_000
    recent_history_messages: int = 12
    recent_history_token_upper_bound: int = 4_000
    final_output_tokens: int = 2_500
    provider_calls: int = 2
    retrieval_expansions: int = 1


COPILOT_TURN_BOUNDS: Final = CopilotTurnBounds()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CopilotCitation(_StrictModel):
    manifest_id: UUID
    item_id: UUID
    item_kind: CopilotItemKind
    explanation: Annotated[str, Field(min_length=1, max_length=1_000)]


class CopilotAllowedStructuredKind(StrEnum):
    PROJECT = CopilotItemKind.PROJECT.value
    RESEARCH_QUESTION = CopilotItemKind.RESEARCH_QUESTION.value
    SOURCE = CopilotItemKind.SOURCE.value
    REPOSITORY_RELATIONSHIP = CopilotItemKind.REPOSITORY_RELATIONSHIP.value
    KNOWLEDGE_OBJECT = CopilotItemKind.KNOWLEDGE_OBJECT.value
    METHOD_MODEL = CopilotItemKind.METHOD_MODEL.value
    METHOD_ELEMENT = CopilotItemKind.METHOD_ELEMENT.value
    MENTAL_MODEL = CopilotItemKind.MENTAL_MODEL.value
    PAPER_CODE_MAPPING = CopilotItemKind.PAPER_CODE_MAPPING.value
    JUDGMENT_REQUEST = CopilotItemKind.JUDGMENT_REQUEST.value
    HUMAN_JUDGMENT = CopilotItemKind.HUMAN_JUDGMENT.value
    BLUEPRINT = CopilotItemKind.BLUEPRINT.value
    BLUEPRINT_SECTION = CopilotItemKind.BLUEPRINT_SECTION.value
    BLUEPRINT_STEP = CopilotItemKind.BLUEPRINT_STEP.value
    VERIFICATION_CHECK = CopilotItemKind.VERIFICATION_CHECK.value
    WORKFLOW_RUN = CopilotItemKind.WORKFLOW_RUN.value
    ARTIFACT_DEPENDENCY = CopilotItemKind.ARTIFACT_DEPENDENCY.value


class CopilotRetrievalRequest(_StrictModel):
    intent: CopilotRetrievalIntent
    query: Annotated[str | None, Field(max_length=500)] = None
    target_ids: Annotated[tuple[UUID, ...], Field(max_length=8)] = ()
    allowed_kinds: Annotated[tuple[CopilotAllowedStructuredKind, ...], Field(max_length=24)] = ()


class CopilotInitialResponse(_StrictModel):
    response_kind: Literal["final", "retrieval"]
    answer: Annotated[str | None, Field(max_length=16_000)] = None
    citations: Annotated[tuple[CopilotCitation, ...], Field(max_length=24)] = ()
    uncertainties: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    limitations: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    action_proposals: Annotated[tuple[CopilotProposedAction, ...], Field(max_length=5)] = ()
    retrieval: CopilotRetrievalRequest | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> CopilotInitialResponse:
        if self.response_kind == "final":
            if self.answer is None or not self.answer.strip() or self.retrieval is not None:
                raise ValueError("final response requires only an answer")
        elif (
            self.retrieval is None
            or self.answer is not None
            or self.citations
            or self.action_proposals
        ):
            raise ValueError("retrieval response requires only a retrieval request")
        return self


class CopilotFinalResponse(_StrictModel):
    answer: Annotated[str, Field(min_length=1, max_length=16_000)]
    citations: Annotated[tuple[CopilotCitation, ...], Field(max_length=24)] = ()
    uncertainties: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    limitations: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    action_proposals: Annotated[tuple[CopilotProposedAction, ...], Field(max_length=5)] = ()


def conservative_token_upper_bound(value: str) -> int:
    """Bound tokens by UTF-8 bytes without claiming tokenizer equivalence."""
    return len(value.encode("utf-8"))


def validate_user_message(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > COPILOT_TURN_BOUNDS.user_message_codepoints:
        raise DomainValidationError(details={"field": "message"})
    return normalized
