"""Persistence-only Copilot audit vocabulary frozen by  / FS-COP-01."""

from enum import StrEnum
from typing import Final

from researchflow.errors import DomainValidationError

COPILOT_AUDIT_POLICY_VERSION: Final = "rf-copilot-audit-v1"
COPILOT_MESSAGE_MAX_CHARACTERS: Final = 32_000
COPILOT_TITLE_MAX_CHARACTERS: Final = 200


class CopilotThreadStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CopilotMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class CopilotTurnStatus(StrEnum):
    PENDING_GENERATION = "pending_generation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CopilotProposalStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED_PENDING = "confirmed_pending"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    EXECUTED = "executed"


def validated_text(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise DomainValidationError(details={"field": field})
    return normalized


def validated_optional_text(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return validated_text(value, field=field, maximum=maximum)
